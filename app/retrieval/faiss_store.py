"""FAISS vector store for news items.

Uses a flat inner-product index over L2-normalized vectors, i.e. cosine
similarity. The store is process-local and optionally persisted to disk
(``FAISS_INDEX_PATH``). Text-to-vector conversion is done by the Qwen
embedding API (see ``embedder.py``).
"""

from __future__ import annotations

import logging
import os
import pickle
import threading
from pathlib import Path
from typing import Sequence

import faiss
import numpy as np

logger = logging.getLogger(__name__)


class FaissStore:
    """Thread-safe FAISS index with a parallel id list."""

    def __init__(
        self,
        dimension: int,
        *,
        index_path: str | None = None,
        ids: list[int] | None = None,
        index: faiss.Index | None = None,
    ) -> None:
        self.dimension = dimension
        self.index_path = Path(index_path) if index_path else None
        self._ids: list[int] = list(ids or [])
        if index is not None:
            self.index = index
        else:
            self.index = faiss.IndexFlatIP(dimension)
        self._lock = threading.RLock()

    # ----- mutation -----------------------------------------------------
    def add(self, vectors: Sequence[Sequence[float]], ids: Sequence[int]) -> None:
        """Add vectors (doc ids must be unique and in the same order)."""
        if not vectors:
            return
        arr = np.asarray(vectors, dtype="float32")
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        faiss.normalize_L2(arr)
        if arr.shape[1] != self.dimension:
            raise ValueError(
                f"vector dimension {arr.shape[1]} != index dimension {self.dimension}"
            )
        new_ids = [int(i) for i in ids]
        if len(new_ids) != arr.shape[0]:
            raise ValueError("ids and vectors length mismatch")
        with self._lock:
            self.index.add(arr)
            self._ids.extend(new_ids)
        logger.info("faiss add: %d vectors, total=%d", arr.shape[0], self.index.ntotal)

    # ----- query --------------------------------------------------------
    def search(self, query_vector: Sequence[float], top_k: int = 10) -> list[tuple[int, float]]:
        """Return [(doc_id, cosine_score), ...] sorted by descending score."""
        if self.index.ntotal == 0:
            return []
        q = np.asarray([query_vector], dtype="float32")
        faiss.normalize_L2(q)
        top_k = min(top_k, self.index.ntotal)
        with self._lock:
            scores, idx = self.index.search(q, top_k)
        results: list[tuple[int, float]] = []
        for score, pos in zip(scores[0], idx[0]):
            if pos < 0 or pos >= len(self._ids):
                continue
            results.append((self._ids[int(pos)], float(score)))
        return results

    # ----- persistence --------------------------------------------------
    def save(self, path: str | None = None) -> None:
        target = Path(path) if path else self.index_path
        if target is None:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "dimension": self.dimension,
            "ids": self._ids,
            "index": faiss.serialize_index(self.index),
        }
        tmp = target.with_suffix(target.suffix + ".tmp")
        with tmp.open("wb") as fh:
            pickle.dump(payload, fh, protocol=4)
        os.replace(tmp, target)
        logger.info("faiss index saved to %s (%d vectors)", target, len(self._ids))

    @classmethod
    def load(cls, path: str) -> "FaissStore | None":
        """Load a previously saved store, or None when the file is missing/corrupt."""
        target = Path(path)
        if not target.exists():
            return None
        try:
            with target.open("rb") as fh:
                payload = pickle.load(fh)  # noqa: S301 - local trusted file
            index = faiss.deserialize_index(payload["index"])
            store = cls(
                payload.get("dimension", index.d),
                index_path=path,
                ids=payload.get("ids", []),
                index=index,
            )
            logger.info("faiss index loaded from %s (%d vectors)", target, store.index.ntotal)
            return store
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to load faiss index from %s: %s", target, exc)
            return None

    def __len__(self) -> int:
        with self._lock:
            return self.index.ntotal

    @property
    def ids(self) -> list[int]:
        """Snapshot of the document ids in insertion order."""
        with self._lock:
            return list(self._ids)
