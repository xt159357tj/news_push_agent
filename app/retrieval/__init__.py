"""Retrieval package: hybrid FAISS + BM25 search over the news corpus.

- embedder.py: Qwen embedding model via OpenAI-compatible HTTP API.
- faiss_store.py: FAISS flat cosine index with disk persistence.
- bm25.py: dependency-free BM25 lexical index.
- hybrid.py: fuses both with RRF, degrades gracefully.
"""
