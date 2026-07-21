from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import voyageai

if TYPE_CHECKING:
    from app.models.product import Product

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Wraps the VoyageAI SDK to produce dense embeddings for products."""

    def __init__(self, api_key: str, model: str, dimension: int) -> None:
        self._model = model
        self._dimension = dimension
        # The VoyageAI client is synchronous; we run it in a thread executor.
        self._client = voyageai.Client(api_key=api_key)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def embed_text(self, text: str) -> list[float]:
        """Embed a raw text string and return the float vector."""
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._client.embed([text], model=self._model),
        )
        vector: list[float] = result.embeddings[0]
        if len(vector) != self._dimension:
            logger.warning(
                "Expected embedding dimension %d, got %d",
                self._dimension,
                len(vector),
            )
        return vector

    async def embed_product(self, product: "Product") -> list[float]:
        """Combine product fields into a single document and embed it."""
        tags_str = ", ".join(product.tags) if product.tags else ""
        document = f"{product.name}. {product.description}. Tags: {tags_str}"
        return await self.embed_text(document)
