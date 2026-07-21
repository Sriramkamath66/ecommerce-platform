from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

logger = logging.getLogger(__name__)


class QdrantService:
    """Manages product vectors in a Qdrant collection."""

    def __init__(self, url: str, collection: str, dimension: int) -> None:
        self._collection = collection
        self._dimension = dimension
        self._client = AsyncQdrantClient(url=url)

    # ------------------------------------------------------------------
    # Collection lifecycle
    # ------------------------------------------------------------------

    async def ensure_collection(self) -> None:
        """Create the collection if it does not already exist."""
        existing = await self._client.get_collections()
        names = {c.name for c in existing.collections}
        if self._collection not in names:
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=qmodels.VectorParams(
                    size=self._dimension,
                    distance=qmodels.Distance.COSINE,
                ),
            )
            logger.info(
                "Created Qdrant collection '%s' (dim=%d, cosine)",
                self._collection,
                self._dimension,
            )
        else:
            logger.debug("Qdrant collection '%s' already exists.", self._collection)

    # ------------------------------------------------------------------
    # CRUD helpers
    # ------------------------------------------------------------------

    async def upsert_product(
        self,
        product_id: uuid.UUID,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        """Insert or update a single product vector."""
        await self._client.upsert(
            collection_name=self._collection,
            points=[
                qmodels.PointStruct(
                    id=str(product_id),
                    vector=vector,
                    payload=payload,
                )
            ],
        )
        logger.debug("Upserted product %s into Qdrant.", product_id)

    async def search_products(
        self,
        query_vector: list[float],
        limit: int,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """Return up to *limit* nearest products.

        Each item in the returned list has keys: ``id``, ``score``, ``payload``.
        """
        qdrant_filter: Optional[qmodels.Filter] = None
        if filters:
            must_conditions: list[qmodels.Condition] = []
            if "category_id" in filters and filters["category_id"] is not None:
                must_conditions.append(
                    qmodels.FieldCondition(
                        key="category_id",
                        match=qmodels.MatchValue(value=str(filters["category_id"])),
                    )
                )
            if "min_price" in filters and filters["min_price"] is not None:
                must_conditions.append(
                    qmodels.FieldCondition(
                        key="price",
                        range=qmodels.Range(gte=float(filters["min_price"])),
                    )
                )
            if "max_price" in filters and filters["max_price"] is not None:
                must_conditions.append(
                    qmodels.FieldCondition(
                        key="price",
                        range=qmodels.Range(lte=float(filters["max_price"])),
                    )
                )
            if must_conditions:
                qdrant_filter = qmodels.Filter(must=must_conditions)

        hits = await self._client.search(
            collection_name=self._collection,
            query_vector=query_vector,
            query_filter=qdrant_filter,
            limit=limit,
            with_payload=True,
        )
        return [
            {"id": hit.id, "score": hit.score, "payload": hit.payload or {}}
            for hit in hits
        ]

    async def delete_product(self, product_id: uuid.UUID) -> None:
        """Remove a product vector by its UUID."""
        await self._client.delete(
            collection_name=self._collection,
            points_selector=qmodels.PointIdsList(
                points=[str(product_id)]
            ),
        )
        logger.debug("Deleted product %s from Qdrant.", product_id)
