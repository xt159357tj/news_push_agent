from pathlib import Path
from typing import List
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class RssFeedConfig(BaseModel):
    """A single RSS feed entry."""

    name: str
    url: str
    category: str


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "每日 AI 新闻助手"
    app_env: str = "development"
    debug: bool = True

    # Database (MySQL)
    # Format: mysql+asyncmy://user:password@host:port/dbname?charset=utf8mb4
    database_url: str = (
        "mysql+asyncmy://root:password@127.0.0.1:3306/news_agent?charset=utf8mb4"
    )

    workspace_root: str = str(BASE_DIR / "workspace")

    # LLM (OpenAI-compatible). DeepSeek-compatible defaults.
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = "replace-me"
    llm_model: str = "deepseek-v4-flash"
    # "openai" (type/function wrapper) or "flat" (name/description/parameters) for DeepSeek compat
    llm_tools_format: str = "flat"
    agent_max_steps: int = 12
    agent_timeout_seconds: float = 120.0
    # bash 工具默认关闭；设为 true 才允许执行白名单内的只读命令
    bash_enabled: bool = False

    news_provider: str = "rss"
    request_timeout_seconds: float = 15.0
    max_article_chars: int = 5000
    max_redirects: int = 5

    # ---- Hybrid retrieval: FAISS (vector) + BM25 (lexical) ----
    # Search mode: "hybrid" | "vector" | "bm25"
    hybrid_search_mode: str = "hybrid"
    # Weight (0.0~1.0) for the vector score in the "convex" scoring mode:
    #   score = vector_weight * cosine + (1 - vector_weight) * bm25_norm
    # "rrf" mode ignores this and uses Reciprocal Rank Fusion.
    vector_weight: float = 0.6
    # "rrf" (Reciprocal Rank Fusion, robust) or "convex" (weighted score blend)
    hybrid_scoring: str = "rrf"
    hybrid_top_k: int = 40  # candidates fetched from each index before fusion

    # Qwen embedding model via OpenAI-compatible API.
    # Default model name follows the request: qwen3.7-text-embedding.
    # Point EMBEDDING_BASE_URL at whichever gateway exposes that model id
    # (e.g. DashScope compatible-mode or a self-hosted vLLM/OneAPI gateway).
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key: str = ""
    embedding_model: str = "qwen3.7-text-embedding"
    embedding_dimensions: int = 1024
    # Some gateways reject a "dimensions" field; set to False to omit it.
    embedding_dimensions_enabled: bool = False
    embedding_batch_size: int = 32
    embedding_timeout_seconds: float = 30.0

    # FAISS index persistence (empty string = in-memory only).
    faiss_index_path: str = str(BASE_DIR / "data" / "faiss_index.pkl")

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "news-agent@example.com"

    admin_token: str = "dev-token"

    rss_feeds: List[RssFeedConfig] = Field(
        default_factory=lambda: [
            RssFeedConfig(name="机器之心", url="https://www.jiqizhixin.com/rss", category="chinese_ai"),
            RssFeedConfig(name="量子位", url="https://rsshub.app/qbitai", category="chinese_ai"),
            RssFeedConfig(name="36氪", url="https://36kr.com/feed", category="tech_business"),
            RssFeedConfig(name="TechCrunch AI", url="https://techcrunch.com/category/artificial-intelligence/feed/", category="global_ai"),
            RssFeedConfig(name="MIT Technology Review AI", url="https://www.technologyreview.com/topic/artificial-intelligence/feed/", category="global_ai"),
            RssFeedConfig(name="arXiv cs.AI", url="https://rss.arxiv.org/rss/cs.AI", category="research"),
        ]
    )


settings = Settings()
