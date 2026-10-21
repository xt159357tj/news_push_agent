"""Pure-Python BM25 scorer used for the lexical half of hybrid retrieval.

Kept dependency-free so it works offline for both Chinese and English text.
Tokenization is deliberately simple (lower-case + alphanumeric run splitting)
and works reasonably well for CJK text because we also split every character
bigram for short tokens, mirroring how common CJK BM25 implementations behave.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from typing import Iterable, Sequence

_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)

K1 = 1.5
B = 0.75


def _char_bigrams(token: str) -> list[str]:
    if len(token) <= 1:
        return [token]
    return [token[i : i + 2] for i in range(len(token) - 1)]


def tokenize(text: str) -> list[str]:
    """Normalize + split text into BM25 tokens (words plus CJK char bigrams)."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    tokens: list[str] = []
    for word in _TOKEN_RE.findall(text):
        if any("\u4e00" <= ch <= "\u9fff" for ch in word):
            tokens.extend(_char_bigrams(word))
        else:
            tokens.append(word)
    return tokens


def _idf(n: int, df: int) -> float:
    # Smooth idf so terms in every document are not penalized to -inf.
    return math.log(1.0 + (n - df + 0.5) / (df + 0.5))


class BM25Index:
    """BM25 over a fixed corpus. Supports incremental add() and query()."""

    def __init__(self, k1: float = K1, b: float = B) -> None:
        self.k1 = k1
        self.b = b
        self._docs: list[list[str]] = []
        self._doc_lens: list[float] = []
        self._avgdl = 0.0
        self._n = 0
        self._df: Counter[str] = Counter()
        self._postings: dict[str, list[tuple[int, float]]] = {}
        self._doc_freq: dict[str, float] = {}

    def add(self, text: str) -> None:
        tokens = tokenize(text)
        self._docs.append(tokens)
        self._doc_lens.append(float(len(tokens)) if tokens else 1.0)
        self._n = len(self._docs)
        self._avgdl = sum(self._doc_lens) / self._n
        counts = Counter(tokens)
        for term, tf in counts.items():
            self._df[term] += 1
            self._postings.setdefault(term, []).append((self._n - 1, tf))
        # Recompute idf lazily on query to keep incremental adds cheap.
        self._doc_freq = dict(self._df)

    def add_many(self, texts: Iterable[str]) -> None:
        for text in texts:
            self.add(text)

    def query(self, text: str, *, top_k: int = 10) -> list[tuple[int, float]]:
        """Return [(doc_index, bm25_score), ...] sorted by descending score."""
        if self._n == 0:
            return []
        terms = Counter(tokenize(text))
        scores: dict[int, float] = {}
        for term, qtf in terms.items():
            df = self._df.get(term, 0)
            if df == 0:
                continue
            idf = _idf(self._n, df)
            for doc_idx, tf in self._postings.get(term, []):
                doc_len = self._doc_lens[doc_idx]
                denom = tf + self.k1 * (1 - self.b + self.b * doc_len / self._avgdl)
                scores[doc_idx] = scores.get(doc_idx, 0.0) + idf * (tf * (self.k1 + 1)) / denom
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:top_k]

    def __len__(self) -> int:
        return self._n
