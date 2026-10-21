from __future__ import annotations

import asyncio
import logging
import os
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from app.config import settings
from app.retrieval import bm25 as bm25_mod
from app.retrieval.embedder import EmbeddingClient
from app.retrieval.faiss_store import FaissStore

logger = logging.getLogger(__name__)


@dataclass
class RetrievalHit:
    doc_id: int
    item: dict
    score: float
    sources: list[str] = field(default_factory=list)


def _rrf_merge(
    vector_hits: list[tuple[int, float]],
    bm25_hits: list[tuple[int, float]],
    *,
    k: int = 60,
    top_k: int = 10,
) -> list[tuple[int, float]]:
    """Fuse two ranked id lists (doc_id, raw_score) via Reciprocal Rank Fusion."""
    acc: dict[int, float] = {}
    for ranked in (vector_hits, bm25_hits):
        for rank, (doc_id, _score) in enumerate(ranked, start=1):
            acc[doc_id] = acc.get(doc_id, 0.0) + 1.0 / (k + rank)
    fused = sorted(acc.items(), key=lambda kv: kv[1], reverse=True)
    return fused[:top_k]


def _normalize_scores(hits: list[tuple[int, float]]) -> dict[int, float]:
    if not hits:
        return {}
    values = [score for _, score in hits]
    low, high = min(values), max(values)
    if high == low:
        return {doc_id: 1.0 for doc_id, _ in hits}
    return {doc_id: (score - low) / (high - low) for doc_id, score in hits}


def _convex_merge(
    vector_hits: list[tuple[int, float]],
    bm25_hits: list[tuple[int, float]],
    *,
    weight: float,
    top_k: int,
) -> list[tuple[int, float]]:
    """Weighted blend of min-max normalized vector and BM25 scores."""
    vector = _normalize_scores(vector_hits)
    lexical = _normalize_scores(bm25_hits)
    combined = {
        doc_id: weight * vector.get(doc_id, 0.0)
        + (1.0 - weight) * lexical.get(doc_id, 0.0)
        for doc_id in set(vector) | set(lexical)
    }
    return sorted(combined.items(), key=lambda kv: kv[1], reverse=True)[:top_k]


class HybridRetriever:
    """Corpus + FAISS + BM25 with a lazy, one-at-a-time index build."""

    def __init__(
        self,
        *,
        client: EmbeddingClient | None = None,
        index_path: str | None = None,
        k1: float = bm25_mod.K1,
        b: float = bm25_mod.B,
    ) -> None:
        self.client = client or EmbeddingClient()
        self.index_path = index_path or settings.faiss_index_path
        self.mode = settings.hybrid_search_mode
        self._items: dict[int, dict] = {}  # doc_id -> item
        self._by_fingerprint: dict[str, int] = {}
        self._bm25 = bm25_mod.BM25Index(k1=k1, b=b)
        self._faiss: FaissStore | None = None
        self._next_id = 0
        self._build_lock = asyncio.Lock()
        self._last_error: str = ""
        self._pending: list[int] = []
        self._vectorized: set[int] = set()
        self._meta_path = (
            Path(f"{self.index_path}.meta.pkl") if self.index_path else None
        )

    # ----- corpus management -------------------------------------------
    def upsert(self, item: dict) -> int:
        """Insert or refresh an item, returning its doc_id."""
        fp = item.get("fingerprint") or ""
        doc_id = self._by_fingerprint.get(fp)
        if doc_id is None:
            doc_id = self._next_id
            self._next_id += 1
            self._by_fingerprint[fp] = doc_id
            self._items[doc_id] = item
            self._bm25.add(self._text_of(item))
            self._pending.append(doc_id)
        else:
            self._items[doc_id] = item  # refresh content
        return doc_id

    def upsert_many(self, items: Sequence[dict]) -> list[int]:
        return [self.upsert(it) for it in items]

    @property
    def corpus_size(self) -> int:
        return len(self._items)

    @property
    def vectorized_count(self) -> int:
        return len(self._vectorized)

    def _text_of(self, item: dict) -> str:
        return f"{item.get('title', '')}\n{item.get('summary', '')}"

    # ----- index build --------------------------------------------------
    async def _ensure_faiss(self) -> FaissStore | None:
        if self._faiss is not None:
            return self._faiss
        loaded = FaissStore.load(self.index_path) if self.index_path else None
        if loaded is not None:
            self._faiss = loaded
            self._load_corpus(loaded)
            return loaded
        self._faiss = FaissStore(self.client.dimensions, index_path=self.index_path)
        return self._faiss

    def _load_corpus(self, store: FaissStore) -> None:
        """Restore corpus metadata that pairs with a persisted FAISS index."""
        if self._meta_path is None or not self._meta_path.exists():
            return
        try:
            with self._meta_path.open("rb") as fh:
                payload = pickle.load(fh)  # noqa: S301 - local trusted file
            self._items = {int(k): v for k, v in payload.get("items", {}).items()}
            self._by_fingerprint = dict(payload.get("by_fingerprint", {}))
            self._next_id = int(payload.get("next_id", 0))
            for item in self._items.values():
                self._bm25.add(self._text_of(item))
            self._vectorized = set(store.ids)
            self._pending = [
                doc_id for doc_id in self._items if doc_id not in self._vectorized
            ]
            logger.info("corpus restored: %d items", len(self._items))
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to restore corpus metadata: %s", exc)

    def _save_corpus(self) -> None:
        if self._meta_path is None:
            return
        self._meta_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "items": self._items,
            "by_fingerprint": self._by_fingerprint,
            "next_id": self._next_id,
        }
        tmp = self._meta_path.with_suffix(self._meta_path.suffix + ".tmp")
        with tmp.open("wb") as fh:
            pickle.dump(payload, fh, protocol=4)
        os.replace(tmp, self._meta_path)

    async def index_pending(self) -> dict[str, int | str]:
        """Embed + add all pending docs to FAISS (guarded by a lock)."""
        async with self._build_lock:
            store = await self._ensure_faiss()
            if store is None:
                return {"ok": 0, "error": "faiss store unavailable"}
            todo = [d for d in self._pending if d not in self._vectorized]
            if not todo:
                return {"ok": 0, "error": ""}
            try:
                texts = [self._text_of(self._items[d]) for d in todo]
                vectors = await self.client.embed(texts, batch_size=settings.embedding_batch_size)
                actual_dim = len(vectors[0]) if vectors else 0
                if actual_dim and store.index.ntotal == 0 and store.dimension != actual_dim:
                    store = FaissStore(actual_dim, index_path=self.index_path)
                    self._faiss = store
                store.add(vectors, todo)
                self._vectorized.update(todo)
                self._pending = [d for d in self._pending if d not in self._vectorized]
                if self.index_path:
                    store.save()
                    self._save_corpus()
                self._last_error = ""
                return {"ok": len(todo), "error": ""}
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                logger.warning("vector index build failed (%d docs): %s", len(todo), exc)
                return {"ok": 0, "error": str(exc)}

    async def ensure_indexed(self) -> None:
        """Best-effort index of any pending docs before searching."""
        if not self._pending:
            return
        try:
            await self.index_pending()
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)

    # ----- search -------------------------------------------------------
    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        top_faiss: int = 40,
        top_bm25: int = 40,
    ) -> dict:
        """Run hybrid (or configured single-mode) retrieval over the corpus."""
        if self.corpus_size == 0:
            return {
                "ok": True, "items": [], "count": 0, "mode": self.mode, "degraded": True,
                "note": "corpus empty — call collect_rss first",
            }

        await self.ensure_indexed()

        vector_hits: list[tuple[int, float]] = []
        vector_ok = self.mode in {"hybrid", "vector"} and self._faiss is not None
        if vector_ok and len(self._vectorized) > 0:
            try:
                qvec = await self.client.embed_one(query)
                vector_hits = self._faiss.search(qvec, top_k=top_faiss)
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                logger.warning("vector search failed, falling back to BM25: %s", exc)
                vector_hits = []

        bm25_hits: list[tuple[int, float]] = []
        if self.mode in {"hybrid", "bm25"} and len(self._bm25) > 0:
            bm25_hits = self._bm25.query(query, top_k=top_bm25)

        if self.mode == "hybrid":
            if settings.hybrid_scoring == "convex":
                fused = _convex_merge(
                    vector_hits,
                    bm25_hits,
                    weight=settings.vector_weight,
                    top_k=top_k,
                )
            else:
                fused = _rrf_merge(vector_hits, bm25_hits, top_k=top_k)
            sources = (["vector"] if vector_hits else []) + (["bm25"] if bm25_hits else [])
            degraded = bool(vector_hits) != bool(bm25_hits)  # one side missing
        elif self.mode == "vector":
            fused = vector_hits[:top_k]
            sources, degraded = ["vector"], False
        else:  # bm25
            fused = bm25_hits[:top_k]
            sources, degraded = ["bm25"], False

        items = []
        for doc_id, score in fused:
            if doc_id not in self._items:
                continue
            items.append(
                {
                    **self._items[doc_id],
                    "retrieval_score": round(score, 6),
                    "retrieval_sources": list(sources),
                }
            )
        return {
            "ok": True,
            "query": query,
            "count": len(items),
            "mode": self.mode,
            "degraded": degraded,
            "corpus_size": self.corpus_size,
            "vectorized": self.vectorized_count,
            "items": items,
        }


_default_retriever: HybridRetriever | None = None


def get_retriever() -> HybridRetriever:
    """Process-wide default retriever (lazy singleton)."""
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = HybridRetriever()
    return _default_retriever


def reset_retriever() -> None:
    """Clear the cached singleton (mainly for tests)."""
    global _default_retriever
    _default_retriever = None
