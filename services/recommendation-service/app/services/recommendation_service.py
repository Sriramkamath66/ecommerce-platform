import json
import logging
import time
from typing import Any

from app.schemas.recommendation import RecommendationResult

logger = logging.getLogger(__name__)


def _mean_vector(vectors: list[list[float]]) -> list[float]:
    """Compute element-wise mean of a list of equal-length vectors."""
    if not vectors:
        return []
    dim = len(vectors[0])
    totals = [0.0] * dim
    for vec in vectors:
        for i, val in enumerate(vec):
            totals[i] += val
    n = len(vectors)
    return [t / n for t in totals]


class RecommendationService:
    def __init__(
        self,
        voyage_client: Any,
        qdrant_client: Any,
        anthropic_client: Any,
        redis: Any,
        http_client: Any,
        settings: Any,
    ) -> None:
        self.voyage = voyage_client
        self.qdrant = qdrant_client
        self.anthropic = anthropic_client
        self.redis = redis
        self.http = http_client
        self.settings = settings

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_similar_products(
        self, product_id: str, limit: int = 10
    ) -> list[RecommendationResult]:
        """Return products whose embedding is nearest to *product_id*'s embedding."""

        # 1. Fetch the source product from Qdrant by payload filter
        scroll_result = await self.qdrant.scroll(
            collection_name=self.settings.QDRANT_PRODUCTS_COLLECTION,
            scroll_filter={
                "must": [
                    {
                        "key": "product_id",
                        "match": {"value": product_id},
                    }
                ]
            },
            with_vectors=True,
            limit=1,
        )

        points, _next = scroll_result
        if not points:
            logger.warning("Product %s not found in Qdrant", product_id)
            return []

        # 2. Extract the vector
        source_point = points[0]
        source_vector: list[float] = (
            source_point.vector
            if isinstance(source_point.vector, list)
            else list(source_point.vector.values())[0]
        )

        # 3. Search for nearest neighbours, excluding the source product itself
        search_results = await self.qdrant.search(
            collection_name=self.settings.QDRANT_PRODUCTS_COLLECTION,
            query_vector=source_vector,
            query_filter={
                "must_not": [
                    {
                        "key": "product_id",
                        "match": {"value": product_id},
                    }
                ]
            },
            limit=limit,
            with_payload=True,
        )

        # 4. Build result list
        results: list[RecommendationResult] = []
        for rank, hit in enumerate(search_results, start=1):
            payload = hit.payload or {}
            results.append(
                RecommendationResult(
                    product_id=payload.get("product_id", str(hit.id)),
                    score=float(hit.score),
                    rank=rank,
                )
            )
        return results

    async def get_user_recommendations(
        self, user_id: str, limit: int = 10
    ) -> list[RecommendationResult]:
        """Return personalised recommendations for *user_id*."""

        cache_key = f"recs:{user_id}"

        # 1. Check Redis cache
        cached = await self.redis.get(cache_key)
        if cached:
            raw = json.loads(cached)
            return [RecommendationResult(**item) for item in raw]

        # 2. Fetch order history from Order Service
        purchased_product_ids: list[str] = []
        user_history_meta: list[dict] = []
        try:
            resp = await self.http.get(
                f"{self.settings.ORDER_SERVICE_URL}/orders",
                params={"user_id": user_id},
                timeout=5.0,
            )
            if resp.status_code == 200:
                orders = resp.json()
                seen: set[str] = set()
                for order in orders:
                    for item in order.get("items", []):
                        pid = item.get("product_id")
                        if pid and pid not in seen:
                            seen.add(pid)
                            purchased_product_ids.append(pid)
                            user_history_meta.append(
                                {
                                    "product_id": pid,
                                    "name": item.get("name", pid),
                                    "description": item.get("description", ""),
                                }
                            )
        except Exception as exc:
            logger.warning("Failed to fetch orders for user %s: %s", user_id, exc)

        # 4. Fallback: no purchase history — return popular / random products
        if not purchased_product_ids:
            return await self._get_fallback_recommendations(limit)

        # 5. Fetch vectors for each purchased product from Qdrant
        vectors: list[list[float]] = []
        for pid in purchased_product_ids:
            try:
                scroll_result = await self.qdrant.scroll(
                    collection_name=self.settings.QDRANT_PRODUCTS_COLLECTION,
                    scroll_filter={
                        "must": [
                            {"key": "product_id", "match": {"value": pid}}
                        ]
                    },
                    with_vectors=True,
                    limit=1,
                )
                points, _ = scroll_result
                if points:
                    vec = points[0].vector
                    vectors.append(
                        vec if isinstance(vec, list) else list(vec.values())[0]
                    )
            except Exception as exc:
                logger.warning("Could not fetch vector for product %s: %s", pid, exc)

        if not vectors:
            return await self._get_fallback_recommendations(limit)

        # 6. Compute mean vector
        mean_vec = _mean_vector(vectors)

        # 7. Search Qdrant, exclude already-purchased products
        must_not_filters = [
            {"key": "product_id", "match": {"value": pid}}
            for pid in purchased_product_ids
        ]
        search_results = await self.qdrant.search(
            collection_name=self.settings.QDRANT_PRODUCTS_COLLECTION,
            query_vector=mean_vec,
            query_filter={"must_not": must_not_filters},
            limit=limit * 2,  # fetch extra for Claude reranking
            with_payload=True,
        )

        candidates: list[dict] = []
        for hit in search_results:
            payload = hit.payload or {}
            candidates.append(
                {
                    "product_id": payload.get("product_id", str(hit.id)),
                    "name": payload.get("name", ""),
                    "description": payload.get("description", ""),
                    "score": float(hit.score),
                }
            )

        # 8. Rerank with Claude
        if candidates:
            candidates = await self._rank_with_claude(candidates, user_history_meta)

        # Build final result list (trim to limit)
        results: list[RecommendationResult] = [
            RecommendationResult(
                product_id=c["product_id"],
                score=c.get("score", 0.0),
                rank=c.get("rank"),
                reason=c.get("reason"),
            )
            for c in candidates[:limit]
        ]

        # 9. Cache in Redis
        try:
            await self.redis.setex(
                cache_key,
                self.settings.RECOMMENDATION_CACHE_TTL,
                json.dumps([r.model_dump() for r in results]),
            )
        except Exception as exc:
            logger.warning("Failed to cache recommendations for user %s: %s", user_id, exc)

        return results

    async def _rank_with_claude(
        self, candidates: list[dict], user_history: list[dict]
    ) -> list[dict]:
        """Use Claude to rerank *candidates* given the user's purchase *user_history*."""

        def _format_products(products: list[dict]) -> str:
            lines = []
            for i, p in enumerate(products, start=1):
                name = p.get("name") or p.get("product_id", "")
                desc = p.get("description", "")
                pid = p.get("product_id", "")
                lines.append(f"{i}. [{pid}] {name}: {desc}")
            return "\n".join(lines)

        user_history_formatted = _format_products(user_history)
        candidates_formatted = _format_products(candidates)

        prompt = (
            "You are a product recommendation engine. Based on the user's purchase "
            "history and candidate products, rank the candidates from most to least relevant.\n\n"
            f"User's purchased products:\n{user_history_formatted}\n\n"
            f"Candidate products to rank:\n{candidates_formatted}\n\n"
            'Return a JSON array of objects with keys: "product_id", "rank", "reason" '
            "(one sentence why this is recommended).\n"
            "Only return valid JSON, no other text."
        )

        try:
            response = await self.anthropic.messages.create(
                model=self.settings.LLM_MODEL,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.content[0].text.strip()

            # Strip markdown code fences if present
            if content.startswith("```"):
                lines = content.splitlines()
                # Remove first and last fence lines
                content = "\n".join(
                    line for line in lines if not line.startswith("```")
                )

            ranked: list[dict] = json.loads(content)

            # Merge ranking metadata back into candidates lookup
            candidate_map = {c["product_id"]: c for c in candidates}
            result: list[dict] = []
            for item in ranked:
                pid = item.get("product_id")
                if pid in candidate_map:
                    entry = candidate_map[pid].copy()
                    entry["rank"] = item.get("rank")
                    entry["reason"] = item.get("reason")
                    result.append(entry)

            # Append any candidates not returned by Claude (preserve original order)
            ranked_ids = {item.get("product_id") for item in ranked}
            for c in candidates:
                if c["product_id"] not in ranked_ids:
                    result.append(c)

            return result

        except json.JSONDecodeError as exc:
            logger.warning("Claude returned non-JSON response: %s", exc)
            return candidates
        except Exception as exc:
            logger.warning("Claude reranking failed: %s", exc)
            return candidates

    async def track_event(
        self, user_id: str, product_id: str, event_type: str
    ) -> None:
        """Record a user interaction event and invalidate caches on purchase."""
        timestamp = time.time()
        event_key = f"user_events:{user_id}"

        # Store in a Redis sorted set keyed by event type
        # Member format: "event_type:product_id" so multiple event types are tracked
        member = f"{event_type}:{product_id}"
        await self.redis.zadd(event_key, {member: timestamp})

        # Invalidate the recommendation cache whenever the user makes a purchase
        if event_type == "purchase":
            cache_key = f"recs:{user_id}"
            await self.redis.delete(cache_key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_fallback_recommendations(
        self, limit: int
    ) -> list[RecommendationResult]:
        """Return popular/random products when user has no purchase history."""
        zero_vector = [0.0] * self.settings.EMBEDDING_DIMENSION
        try:
            search_results = await self.qdrant.search(
                collection_name=self.settings.QDRANT_PRODUCTS_COLLECTION,
                query_vector=zero_vector,
                limit=limit,
                with_payload=True,
            )
            results: list[RecommendationResult] = []
            for rank, hit in enumerate(search_results, start=1):
                payload = hit.payload or {}
                results.append(
                    RecommendationResult(
                        product_id=payload.get("product_id", str(hit.id)),
                        score=float(hit.score),
                        rank=rank,
                    )
                )
            return results
        except Exception as exc:
            logger.warning("Fallback recommendation search failed: %s", exc)
            return []
