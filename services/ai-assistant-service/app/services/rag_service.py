from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RAGService:
    """Retrieve product context using Voyage embeddings and Qdrant vector search."""

    def __init__(self, voyage_client: Any, qdrant_client: Any, settings: Any) -> None:
        self.voyage_client = voyage_client
        self.qdrant_client = qdrant_client
        self.settings = settings

    async def retrieve_context(self, query: str, limit: int = 5) -> list[dict]:
        """Embed *query* with Voyage and search Qdrant for the top *limit* products.

        Returns a list of dicts with keys:
            product_id, name, description, price, score
        """
        try:
            # Embed the query using the Voyage async client
            embed_response = await self.voyage_client.embed(
                [query],
                model=self.settings.EMBEDDING_MODEL,
                input_type="query",
            )
            # voyageai returns an EmbeddingsObject; extract the first vector
            vector: list[float] = embed_response.embeddings[0]

            # Search Qdrant for nearest neighbours
            search_results = await self.qdrant_client.search(
                collection_name=self.settings.QDRANT_PRODUCTS_COLLECTION,
                query_vector=vector,
                limit=limit,
            )

            results: list[dict] = []
            for hit in search_results:
                payload = hit.payload or {}
                results.append(
                    {
                        "product_id": payload.get("product_id", str(hit.id)),
                        "name": payload.get("name", "Unknown"),
                        "description": payload.get("description", ""),
                        "price": payload.get("price", 0.0),
                        "score": float(hit.score),
                    }
                )
            return results

        except Exception as exc:  # noqa: BLE001
            logger.error("RAG retrieval failed for query %r: %s", query, exc)
            return []

    def format_context(self, results: list[dict]) -> str:
        """Format retrieved products as a numbered list for injection into a prompt."""
        if not results:
            return "No relevant products found in the catalog."

        lines: list[str] = []
        for i, item in enumerate(results, start=1):
            lines.append(
                f"{i}. {item['name']} (ID: {item['product_id']}) - ${item['price']}\n"
                f"   {item['description']}\n"
                f"   Similarity: {item['score']:.2f}"
            )
        return "\n".join(lines)
