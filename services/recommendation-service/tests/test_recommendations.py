"""
Tests for the Recommendation Service.

All external I/O (Qdrant, Redis, Anthropic, httpx) is replaced with
AsyncMock / MagicMock so tests run without real infrastructure.
"""

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import jwt
from fastapi.testclient import TestClient

from app.schemas.recommendation import RecommendationResult
from app.services.recommendation_service import RecommendationService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_SECRET = "recommendation-service-test-secret"


def _make_token(sub: str = "user-123", roles: list[str] | None = None) -> str:
    payload: dict = {"sub": sub}
    if roles:
        payload["roles"] = roles
    return jwt.encode(payload, _TEST_SECRET, algorithm="HS256")


def _make_settings(**overrides):
    defaults = dict(
        REDIS_URL="redis://localhost:6379",
        QDRANT_URL="http://localhost:6333",
        VOYAGE_API_KEY="",
        ANTHROPIC_API_KEY="",
        LLM_MODEL="claude-opus-4-8",
        EMBEDDING_MODEL="voyage-3",
        EMBEDDING_DIMENSION=4,  # small for tests
        JWT_PUBLIC_KEY_PATH="/nonexistent/key.pem",
        ORDER_SERVICE_URL="http://order-service:8003",
        PRODUCT_SERVICE_URL="http://product-service:8002",
        RECOMMENDATION_CACHE_TTL=3600,
        QDRANT_PRODUCTS_COLLECTION="products",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_qdrant_point(product_id: str, vector: list[float], extra_payload: dict | None = None):
    payload = {"product_id": product_id, "name": f"Product {product_id}", "description": "A great product"}
    if extra_payload:
        payload.update(extra_payload)
    point = SimpleNamespace(
        id=product_id,
        vector=vector,
        payload=payload,
        score=0.95,
    )
    return point


def _make_search_hit(product_id: str, score: float = 0.9):
    return SimpleNamespace(
        id=product_id,
        score=score,
        payload={"product_id": product_id, "name": f"Product {product_id}", "description": "desc"},
        vector=None,
    )


def _build_service(
    *,
    redis: AsyncMock | None = None,
    qdrant: AsyncMock | None = None,
    anthropic: AsyncMock | None = None,
    http: AsyncMock | None = None,
    settings=None,
) -> RecommendationService:
    return RecommendationService(
        voyage_client=MagicMock(),
        qdrant_client=qdrant or AsyncMock(),
        anthropic_client=anthropic or AsyncMock(),
        redis=redis or AsyncMock(),
        http_client=http or AsyncMock(),
        settings=settings or _make_settings(),
    )


# ---------------------------------------------------------------------------
# 1. get_similar_products
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_similar_products():
    source_vec = [0.1, 0.2, 0.3, 0.4]
    source_point = _make_qdrant_point("prod-A", source_vec)
    hit1 = _make_search_hit("prod-B", score=0.88)
    hit2 = _make_search_hit("prod-C", score=0.75)

    qdrant = AsyncMock()
    # scroll returns (points, next_cursor)
    qdrant.scroll = AsyncMock(return_value=([source_point], None))
    qdrant.search = AsyncMock(return_value=[hit1, hit2])

    svc = _build_service(qdrant=qdrant)
    results = await svc.get_similar_products("prod-A", limit=10)

    # Scroll was called with a filter for prod-A
    qdrant.scroll.assert_awaited_once()
    scroll_kwargs = qdrant.scroll.call_args.kwargs
    assert scroll_kwargs["with_vectors"] is True

    # Search was called after extracting the vector
    qdrant.search.assert_awaited_once()
    search_kwargs = qdrant.search.call_args.kwargs
    assert search_kwargs["query_vector"] == source_vec

    assert len(results) == 2
    assert results[0].product_id == "prod-B"
    assert results[0].score == pytest.approx(0.88)
    assert results[0].rank == 1
    assert results[1].product_id == "prod-C"
    assert results[1].rank == 2


@pytest.mark.asyncio
async def test_get_similar_products_not_found():
    qdrant = AsyncMock()
    qdrant.scroll = AsyncMock(return_value=([], None))

    svc = _build_service(qdrant=qdrant)
    results = await svc.get_similar_products("missing-prod", limit=5)

    assert results == []
    qdrant.search.assert_not_awaited()


# ---------------------------------------------------------------------------
# 2. get_user_recommendations — cache hit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_recommendations_cache_hit():
    cached_data = [
        {"product_id": "prod-X", "score": 0.9, "rank": 1, "reason": "Popular"},
        {"product_id": "prod-Y", "score": 0.8, "rank": 2, "reason": None},
    ]

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps(cached_data))

    qdrant = AsyncMock()
    http = AsyncMock()

    svc = _build_service(redis=redis, qdrant=qdrant, http=http)
    results = await svc.get_user_recommendations("user-1", limit=10)

    # Redis was checked
    redis.get.assert_awaited_once_with("recs:user-1")

    # No Qdrant or HTTP calls made
    qdrant.scroll.assert_not_awaited()
    qdrant.search.assert_not_awaited()
    http.get.assert_not_awaited()

    assert len(results) == 2
    assert results[0].product_id == "prod-X"
    assert results[1].product_id == "prod-Y"


# ---------------------------------------------------------------------------
# 3. get_user_recommendations — no purchase history (fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_recommendations_no_history():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    # Order service returns empty list
    http_response = MagicMock()
    http_response.status_code = 200
    http_response.json = MagicMock(return_value=[])
    http = AsyncMock()
    http.get = AsyncMock(return_value=http_response)

    # Fallback search returns a couple of hits
    fallback_hit1 = _make_search_hit("pop-1", score=0.5)
    fallback_hit2 = _make_search_hit("pop-2", score=0.4)
    qdrant = AsyncMock()
    qdrant.search = AsyncMock(return_value=[fallback_hit1, fallback_hit2])

    svc = _build_service(redis=redis, qdrant=qdrant, http=http)
    results = await svc.get_user_recommendations("user-new", limit=10)

    # A fallback search was issued (zero vector)
    qdrant.search.assert_awaited_once()
    search_kwargs = qdrant.search.call_args.kwargs
    # The zero vector has length equal to EMBEDDING_DIMENSION
    assert len(search_kwargs["query_vector"]) == 4
    assert all(v == 0.0 for v in search_kwargs["query_vector"])

    assert len(results) == 2
    assert results[0].product_id == "pop-1"


# ---------------------------------------------------------------------------
# 4. get_user_recommendations — full flow with history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_recommendations_with_history():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    # Orders from order service
    http_response = MagicMock()
    http_response.status_code = 200
    http_response.json = MagicMock(
        return_value=[
            {
                "order_id": "ord-1",
                "items": [
                    {"product_id": "bought-1", "name": "Widget", "description": "A widget"},
                ],
            }
        ]
    )
    http = AsyncMock()
    http.get = AsyncMock(return_value=http_response)

    # Qdrant: scroll for bought-1 returns a point with a vector
    bought_point = _make_qdrant_point("bought-1", [0.1, 0.2, 0.3, 0.4])
    qdrant = AsyncMock()
    qdrant.scroll = AsyncMock(return_value=([bought_point], None))

    # Qdrant: search returns candidate hits
    cand1 = _make_search_hit("rec-1", score=0.85)
    cand2 = _make_search_hit("rec-2", score=0.80)
    qdrant.search = AsyncMock(return_value=[cand1, cand2])

    # Anthropic: returns ranked JSON
    claude_ranked = json.dumps([
        {"product_id": "rec-2", "rank": 1, "reason": "Best match"},
        {"product_id": "rec-1", "rank": 2, "reason": "Good match"},
    ])
    claude_message = MagicMock()
    claude_message.content = [SimpleNamespace(text=claude_ranked)]
    anthropic = AsyncMock()
    anthropic.messages.create = AsyncMock(return_value=claude_message)

    svc = _build_service(redis=redis, qdrant=qdrant, anthropic=anthropic, http=http)
    results = await svc.get_user_recommendations("user-buyer", limit=10)

    # Redis was queried and then populated
    redis.get.assert_awaited_once_with("recs:user-buyer")
    redis.setex.assert_awaited_once()

    # Claude was consulted
    anthropic.messages.create.assert_awaited_once()

    # Results ordered by Claude's ranking
    assert len(results) == 2
    assert results[0].product_id == "rec-2"
    assert results[0].rank == 1
    assert results[0].reason == "Best match"
    assert results[1].product_id == "rec-1"
    assert results[1].rank == 2


# ---------------------------------------------------------------------------
# 5. _rank_with_claude — JSON parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rank_with_claude_valid_json():
    ranked_json = json.dumps([
        {"product_id": "p1", "rank": 1, "reason": "Top pick"},
        {"product_id": "p2", "rank": 2, "reason": "Runner up"},
    ])
    claude_message = MagicMock()
    claude_message.content = [SimpleNamespace(text=ranked_json)]
    anthropic = AsyncMock()
    anthropic.messages.create = AsyncMock(return_value=claude_message)

    svc = _build_service(anthropic=anthropic)
    candidates = [
        {"product_id": "p1", "name": "Prod 1", "description": "desc", "score": 0.9},
        {"product_id": "p2", "name": "Prod 2", "description": "desc", "score": 0.8},
    ]
    result = await svc._rank_with_claude(candidates, [])

    assert result[0]["product_id"] == "p1"
    assert result[0]["rank"] == 1
    assert result[0]["reason"] == "Top pick"
    assert result[1]["product_id"] == "p2"


@pytest.mark.asyncio
async def test_rank_with_claude_invalid_json_falls_back():
    claude_message = MagicMock()
    claude_message.content = [SimpleNamespace(text="not valid json at all")]
    anthropic = AsyncMock()
    anthropic.messages.create = AsyncMock(return_value=claude_message)

    svc = _build_service(anthropic=anthropic)
    candidates = [
        {"product_id": "p1", "name": "Prod 1", "description": "desc", "score": 0.9},
    ]
    # Should return candidates unchanged on parse error
    result = await svc._rank_with_claude(candidates, [])
    assert result == candidates


@pytest.mark.asyncio
async def test_rank_with_claude_strips_markdown_fences():
    ranked_json = json.dumps([
        {"product_id": "p1", "rank": 1, "reason": "Great"},
    ])
    fenced = f"```json\n{ranked_json}\n```"
    claude_message = MagicMock()
    claude_message.content = [SimpleNamespace(text=fenced)]
    anthropic = AsyncMock()
    anthropic.messages.create = AsyncMock(return_value=claude_message)

    svc = _build_service(anthropic=anthropic)
    candidates = [{"product_id": "p1", "name": "P1", "description": "d", "score": 0.9}]
    result = await svc._rank_with_claude(candidates, [])
    assert result[0]["product_id"] == "p1"
    assert result[0]["rank"] == 1


# ---------------------------------------------------------------------------
# 6. track_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_track_event_view_does_not_invalidate_cache():
    redis = AsyncMock()
    redis.zadd = AsyncMock()
    redis.delete = AsyncMock()

    svc = _build_service(redis=redis)
    await svc.track_event(user_id="u1", product_id="p1", event_type="view")

    redis.zadd.assert_awaited_once()
    zadd_args = redis.zadd.call_args
    # First positional arg is the key
    assert zadd_args.args[0] == "user_events:u1"
    # The member should encode the event type and product id
    member_map = zadd_args.args[1]
    assert "view:p1" in member_map

    # Cache should NOT be deleted for non-purchase events
    redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_track_event_purchase_invalidates_cache():
    redis = AsyncMock()
    redis.zadd = AsyncMock()
    redis.delete = AsyncMock()

    svc = _build_service(redis=redis)
    await svc.track_event(user_id="u1", product_id="p2", event_type="purchase")

    redis.zadd.assert_awaited_once()
    # Cache key must be deleted on purchase
    redis.delete.assert_awaited_once_with("recs:u1")


# ---------------------------------------------------------------------------
# 7. FastAPI endpoint — GET /recommendations/product/{id}/similar
# ---------------------------------------------------------------------------


def _build_test_app(recommendation_service):
    """Return a minimal FastAPI app wired with a mocked recommendation_service."""
    from fastapi import FastAPI
    from app.routers.recommendations import router

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.state.recommendation_service = recommendation_service
    return test_app


def test_similar_products_endpoint_success():
    mock_svc = MagicMock()
    mock_svc.get_similar_products = AsyncMock(
        return_value=[
            RecommendationResult(product_id="p-near-1", score=0.91, rank=1),
            RecommendationResult(product_id="p-near-2", score=0.83, rank=2),
        ]
    )

    app = _build_test_app(mock_svc)
    client = TestClient(app)

    response = client.get("/recommendations/product/prod-A/similar?limit=5")
    assert response.status_code == 200
    body = response.json()
    assert body["product_id"] == "prod-A"
    assert len(body["recommendations"]) == 2
    assert body["recommendations"][0]["product_id"] == "p-near-1"
    assert body["recommendations"][0]["score"] == pytest.approx(0.91)

    mock_svc.get_similar_products.assert_awaited_once_with(
        product_id="prod-A", limit=5
    )


def test_similar_products_endpoint_empty():
    mock_svc = MagicMock()
    mock_svc.get_similar_products = AsyncMock(return_value=[])

    app = _build_test_app(mock_svc)
    client = TestClient(app)

    response = client.get("/recommendations/product/unknown/similar")
    assert response.status_code == 200
    body = response.json()
    assert body["recommendations"] == []


def test_track_event_endpoint_requires_auth():
    mock_svc = MagicMock()
    app = _build_test_app(mock_svc)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/recommendations/track",
        json={"product_id": "p1", "event_type": "view"},
    )
    # No auth header → 403 (HTTPBearer returns 403 when header is absent)
    assert response.status_code == 403


def test_track_event_endpoint_with_token():
    mock_svc = MagicMock()
    mock_svc.track_event = AsyncMock(return_value=None)

    app = _build_test_app(mock_svc)
    client = TestClient(app)

    token = _make_token(sub="user-42")
    response = client.post(
        "/recommendations/track",
        json={"product_id": "p99", "event_type": "click"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    mock_svc.track_event.assert_awaited_once_with(
        user_id="user-42",
        product_id="p99",
        event_type="click",
    )


def test_track_event_endpoint_invalid_event_type():
    mock_svc = MagicMock()
    mock_svc.track_event = AsyncMock(return_value=None)

    app = _build_test_app(mock_svc)
    client = TestClient(app)

    token = _make_token(sub="user-42")
    response = client.post(
        "/recommendations/track",
        json={"product_id": "p99", "event_type": "wishlist"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


def test_user_recommendations_endpoint_forbidden_for_other_user():
    mock_svc = MagicMock()
    mock_svc.get_user_recommendations = AsyncMock(return_value=[])

    app = _build_test_app(mock_svc)
    client = TestClient(app, raise_server_exceptions=False)

    # Token sub is user-1, but requesting user-2's recommendations
    token = _make_token(sub="user-1")
    response = client.get(
        "/recommendations/user/user-2",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_user_recommendations_endpoint_admin_can_access_any():
    mock_svc = MagicMock()
    mock_svc.get_user_recommendations = AsyncMock(
        return_value=[RecommendationResult(product_id="p1", score=0.9)]
    )

    app = _build_test_app(mock_svc)
    client = TestClient(app)

    # Admin token accessing another user's recommendations
    token = _make_token(sub="admin-user", roles=["admin"])
    response = client.get(
        "/recommendations/user/some-other-user",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "some-other-user"
    assert len(body["recommendations"]) == 1
