# 每日 AI 新闻助手 Agent

一个按用户偏好自动整理并推送 AI 新闻简报的 Agent：LLM 通过 function calling / ReAct 循环
自主决定调用哪些工具、按什么顺序、调用几次；新闻检索采用 **向量 + BM25 混合检索**
（向量模型 `qwen3.7-text-embedding` 通过 API 调用，向量库使用 **FAISS**）。

---

## 5 分钟快速启动

前置要求：Python 3.11+、MySQL 8。全部命令在 Windows PowerShell / macOS / Linux 通用（仅激活虚拟环境命令略有差异）。

### 1. 拉代码并安装依赖（约 1 分钟）

```bash
git clone <your-repo> && cd news
python -m venv .venv
# Windows:  .venv\Scripts\activate    |    macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
```

依赖清单见 [requirements.txt](requirements.txt)（运行时）与 [requirements-dev.txt](requirements-dev.txt)（测试）。

### 2. 准备 MySQL（约 1 分钟）

```sql
CREATE DATABASE news_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

建表（提供两种方式，任选其一）：

```bash
# 方式 A：执行建表脚本（推荐）
mysql -u root -p news_agent < sql/schema.sql

# 方式 B：不执行脚本，应用启动时自动建表（app/main.py lifespan 里 create_all）
```

建表脚本见 [sql/schema.sql](sql/schema.sql)，与 `app/db/models.py` 一一对应。

### 3. 配置环境变量（约 1 分钟）

```bash
cp .env.example .env
```

编辑 `.env`，填入真实值：

```env
# 数据库
DATABASE_URL=mysql+asyncmy://root:你的密码@127.0.0.1:3306/news_agent?charset=utf8mb4

# LLM（OpenAI 兼容）
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=sk-xxxxxxxx
LLM_MODEL=deepseek-v4-flash

# 嵌入模型（Qwen，OpenAI 兼容 API）
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=sk-xxxxxxxx
EMBEDDING_MODEL=qwen3.7-text-embedding
EMBEDDING_DIMENSIONS=1024

# 邮件（可选，不配置也能正常生成简报，仅跳过发送）
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=news-agent@example.com
```

> 嵌入与 LLM 可以共用同一个 OpenAI 兼容网关；`EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY`
> 指向能提供 `qwen3.7-text-embedding` 模型 id 的网关即可（如 DashScope compatible-mode、vLLM/OneAPI）。

### 4. 启动（约 1 分钟）

```bash
uvicorn app.main:app --reload
```

打开 http://127.0.0.1:8000 ：

1. 填写昵称、邮箱、时区，保存身份；
2. 设置关注话题/关键词、简报时间，保存订阅；
3. 点击「立即生成简报」，等待 Agent 检索并整理（可能耗时数十秒）；
4. 在「历史简报」中查看结果。

### 5. 跑测试（可选）

```bash
pip install -r requirements-dev.txt
pytest
```

测试使用独立 SQLite（data/e2e_test.db），不依赖本地 MySQL，也不发真实网络请求。

---

## 定时生成

- APScheduler 每分钟扫描一次，按**订阅所属用户的时区** + `delivery_time`（HH:MM）判断是否到点；
- 到点创建任务，由 Agent 生成并保存简报，然后投递（SMTP 未配置则记录 `skipped`，不影响生成）；
- 幂等：同一订阅同一本地日期只生成一份；服务重启后补跑遗留任务。

## 混合检索（向量 + BM25）

`search_news(query)` 的检索链路：

1. **BM25**：纯 Python 词法索引，支持中文（字符二元组分词），离线可用；
2. **向量**：Qwen 嵌入 API 生成 query 向量，在 **FAISS**（`IndexFlatIP` + L2 归一化 = 余弦相似度）中检索；
3. **融合**：默认 RRF（Reciprocal Rank Fusion）；可选 `convex` 加权模式；
4. **降级**：嵌入 API 不可用时自动回退 BM25-only。
5. **持久化**：FAISS 索引与语料元数据写入 `FAISS_INDEX_PATH` 及其 `.meta.pkl`，
   服务重启后无需重新抓取即可继续检索。

关键配置（`.env`）：

```env
HYBRID_SEARCH_MODE=hybrid        # hybrid | vector | bm25
HYBRID_SCORING=rrf               # rrf(推荐) | convex(加权融合)
VECTOR_WEIGHT=0.6                # convex 模式下的向量权重（0~1）
FAISS_INDEX_PATH=./data/faiss_index.pkl   # 留空 = 仅内存索引
EMBEDDING_BATCH_SIZE=32
EMBEDDING_DIMENSIONS_ENABLED=false   # 网关拒绝 dimensions 字段时保持 false
```

实现位置：

- `app/retrieval/embedder.py`：Qwen 嵌入客户端（OpenAI 兼容 API）
- `app/retrieval/faiss_store.py`：FAISS 向量库（可持久化）
- `app/retrieval/bm25.py`：BM25 词法索引
- `app/retrieval/hybrid.py`：混合检索器（RRF/加权 + 降级）

工作流：Agent 先 `collect_rss` 采集候选并写入检索语料，再 `search_news` 在该语料上做混合检索。

## RSS 配置

默认源见 `app/config.py` 的 `rss_feeds`：

| 名称 | URL |
| --- | --- |
| 机器之心 | https://www.jiqizhixin.com/rss |
| 量子位 | https://rsshub.app/qbitai |
| 36氪 | https://36kr.com/feed |
| TechCrunch AI | https://techcrunch.com/category/artificial-intelligence/feed/ |
| MIT Technology Review AI | https://www.technologyreview.com/topic/artificial-intelligence/feed/ |
| arXiv cs.AI | https://rss.arxiv.org/rss/cs.AI |

## 工具

基础工具：`list_dir` / `read_file` / `search_content` / `write_file` / `bash`（默认禁用）。
新闻工具：`collect_rss`（采集并写入语料）/ `search_news`（混合检索）/ `fetch_article`（提取正文）。
工具调用顺序完全由 LLM 决定（`app/agent/runtime.py`），代码不写死流程。

## 项目结构

```text
app/
  api/            # FastAPI 路由（profile / subscriptions / digests）
  agent/          # Agent Runtime、提示词、输出 Schema
  retrieval/      # 混合检索：embedder / faiss_store / bm25 / hybrid
  services/       # 调度器、任务执行、投递、简报服务
  tools/          # 工具注册表与实现
  db/             # SQLAlchemy 模型与会话
sql/schema.sql    # MySQL 建表脚本
tests/            # pytest 测试（工具/检索/调度/投递/e2e）
```

## 说明

- MVP 推送渠道为 Web 历史列表 + SMTP 邮件（配置后启用）。
- bash 工具默认关闭；生产部署前建议加入登录、限流和独立 worker。
