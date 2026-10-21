# 数据库脚本

## 全新安装

```bash
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS news_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -u root -p news_agent < sql/schema.sql
```

`schema.sql` 与 `app/db/models.py` 一一对应。应用启动时也会执行
SQLAlchemy `create_all`，因此空数据库不执行脚本也能自动建表；生产环境建议
使用本目录的 SQL 脚本显式建表。

## 已有数据库升级

当前版本没有破坏性表结构变更；混合检索使用 FAISS 文件
（`data/faiss_index.pkl`）与进程内 BM25 语料缓存，不需要新增数据表。
如需从旧版本升级，只需补充 `.env` 中的 `EMBEDDING_*` 与
`HYBRID_*` 配置并重启应用。
