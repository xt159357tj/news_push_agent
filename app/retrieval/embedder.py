"""Qwen embedding client.

The embedding model is called over an OpenAI-compatible HTTP API (like the LLM
itself). It is configured through the ``EMBEDDING_*`` settings:

- ``EMBEDDING_BASE_URL`` / ``EMBEDDING_API_KEY``: HTTP endpoint + key.
- ``EMBEDDING_MODEL``: model name, defaults to ``text-embedding-v3`` (the public
  DashScope model id). Set it to ``qwen3.7-text-embedding`` when your gateway
  exposes that model id.
"""

from __future__ import annotations

import asyncio
from typing import Sequence

import httpx

from app.config import settings


class EmbeddingError(RuntimeError):
    """Raised when the embedding API cannot produce vectors."""


class EmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.embedding_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.embedding_api_key
        self.model = model or settings.embedding_model
        self.dimensions = dimensions or settings.embedding_dimensions
        self.timeout = timeout or settings.embedding_timeout_seconds

    async def _embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        url = f"{self.base_url}/embeddings"
        payload: dict = {"model": self.model, "input": list(texts)}
        if self.dimensions and settings.embedding_dimensions_enabled:
            payload["dimensions"] = self.dimensions
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"embedding API request failed: {exc}") from exc

        try:
            return [item["embedding"] for item in data["data"]]
        except (KeyError, TypeError) as exc:
            raise EmbeddingError(f"unexpected embedding response shape: {exc}") from exc

    async def embed(
        self, texts: Sequence[str], *, batch_size: int = 64
    ) -> list[list[float]]:
        """Embed a list of texts, batching into chunks."""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            vectors.extend(await self._embed_texts(batch))
        return vectors

    async def embed_one(self, text: str) -> list[float]:
        """Embed a single text, returning a flat vector."""
        vecs = await self.embed([text])
        if not vecs:
            raise EmbeddingError("empty embedding response")
        return vecs[0]

    async def embed_many(self, texts: Sequence[str], *, batch_size: int = 64) -> list[list[float]]:
        """Alias of :meth:`embed` kept for callers that prefer this name."""
        return await self.embed(texts, batch_size=batch_size)


def _default_client() -> EmbeddingClient:
    return EmbeddingClient()


async def embed_texts(texts: Sequence[str], *, client: EmbeddingClient | None = None) -> list[list[float]]:
    """Module-level convenience: embed a list of texts."""
    c = client or _default_client()
    return await c.embed(texts)


async def embed_query(text: str, *, client: EmbeddingClient | None = None) -> list[float]:
    """Module-level convenience: embed a single query."""
    c = client or _default_client()
    return await c.embed_one(text)


async def check_embedding_health(*, client: EmbeddingClient | None = None) -> tuple[bool, str]:
    """Probe the embedding endpoint with a tiny request."""
    c = client or _default_client()
    try:
        vec = await asyncio.wait_for(c.embed_one("ping"), timeout=min(c.timeout, 30.0))
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if not vec:
        return False, "empty vector returned"
    return True, f"dim={len(vec)}"
