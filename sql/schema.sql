-- ============================================================
-- 每日 AI 新闻助手 - MySQL 8 建表脚本
-- 说明: 该 Schema 与 app/db/models.py 的 SQLAlchemy 模型一一对应。
--       启动应用前先执行本脚本，或直接运行 uvicorn（模型会自动建表）。
-- 用法:  mysql -u root -p news_agent < sql/schema.sql
-- 注意: ENUM 存大写值（SQLAlchemy Enum 默认存枚举 name），应用层会正确读写。
-- ============================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ------------------------------------------------------------
-- 用户（单用户 MVP，按邮箱唯一）
-- ------------------------------------------------------------
CREATE TABLE users (
    id          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    name        VARCHAR(80)   NOT NULL,
    email       VARCHAR(255)  NOT NULL,
    timezone    VARCHAR(64)   NOT NULL DEFAULT 'Asia/Shanghai',
    created_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- 订阅偏好（话题/关键词/排除词用逗号分隔）
-- ------------------------------------------------------------
CREATE TABLE subscriptions (
    id            INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    user_id       INT UNSIGNED  NOT NULL,
    status        ENUM('ACTIVE','PAUSED') NOT NULL DEFAULT 'ACTIVE',
    topics        TEXT          NOT NULL,
    keywords      TEXT          NOT NULL,
    exclude       TEXT          NOT NULL,
    language      VARCHAR(8)    NOT NULL DEFAULT 'zh',
    max_items     INT           NOT NULL DEFAULT 10,
    delivery_time VARCHAR(8)    NOT NULL DEFAULT '08:00',   -- HH:MM，按用户时区判断
    notify_email  TINYINT(1)    NOT NULL DEFAULT 1,
    created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_subscriptions_user_id (user_id),
    CONSTRAINT fk_subscriptions_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- 新闻条目（去重、来源、摘要）
-- ------------------------------------------------------------
CREATE TABLE news_items (
    id              INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    url             VARCHAR(512)  NOT NULL,
    source_name     VARCHAR(200)  NOT NULL DEFAULT '',
    source_category VARCHAR(100)  NOT NULL DEFAULT '',
    title           TEXT          NOT NULL,
    summary         TEXT          NOT NULL,
    content_snippet TEXT          NOT NULL,
    published_at    DATETIME      NULL,
    fetched_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fingerprint     VARCHAR(128)  NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_news_items_url (url),
    KEY idx_news_items_fingerprint (fingerprint)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- 简报
-- ------------------------------------------------------------
CREATE TABLE digests (
    id              INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    user_id         INT UNSIGNED  NOT NULL,
    subscription_id INT UNSIGNED  NULL,
    title           VARCHAR(300)  NOT NULL DEFAULT 'AI 新闻简报',
    language        VARCHAR(8)    NOT NULL DEFAULT 'zh',
    overview        TEXT          NOT NULL,
    generated_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `trigger`       VARCHAR(20)   NOT NULL DEFAULT 'manual',   -- manual | scheduled
    is_draft        TINYINT(1)    NOT NULL DEFAULT 0,
    day_key         VARCHAR(64)   NOT NULL DEFAULT '',          -- 幂等键: sub:{id}:{YYYY-MM-DD}
    PRIMARY KEY (id),
    KEY idx_digests_user_id (user_id),
    KEY idx_digests_day_key (day_key),
    CONSTRAINT fk_digests_user   FOREIGN KEY (user_id) REFERENCES users (id),
    CONSTRAINT fk_digests_sub    FOREIGN KEY (subscription_id) REFERENCES subscriptions (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- 简报条目（rank 为保留字，需反引号）
-- ------------------------------------------------------------
CREATE TABLE digest_items (
    id          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    digest_id   INT UNSIGNED  NOT NULL,
    news_id     INT UNSIGNED  NULL,
    `rank`      INT           NOT NULL DEFAULT 0,
    title       TEXT          NOT NULL,
    summary     TEXT          NOT NULL,
    reason      TEXT          NOT NULL,
    url         VARCHAR(512)  NOT NULL,
    PRIMARY KEY (id),
    KEY idx_digest_items_digest_id (digest_id),
    CONSTRAINT fk_digest_items_digest FOREIGN KEY (digest_id) REFERENCES digests (id),
    CONSTRAINT fk_digest_items_news   FOREIGN KEY (news_id) REFERENCES news_items (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- 投递记录（邮件；SMTP 未配置时记录 SKIPPED）
-- ------------------------------------------------------------
CREATE TABLE deliveries (
    id          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    digest_id   INT UNSIGNED  NOT NULL,
    channel     VARCHAR(40)   NOT NULL DEFAULT 'email',        -- email | web
    status      ENUM('QUEUED','SENT','FAILED','SKIPPED') NOT NULL DEFAULT 'QUEUED',
    recipient   VARCHAR(255)  NOT NULL DEFAULT '',
    error       TEXT          NOT NULL,
    created_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_deliveries_digest_id (digest_id),
    CONSTRAINT fk_deliveries_digest FOREIGN KEY (digest_id) REFERENCES digests (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- 生成任务（手动 + 定时共用）
-- ------------------------------------------------------------
CREATE TABLE generation_jobs (
    id              INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    subscription_id INT UNSIGNED  NOT NULL,
    user_id         INT UNSIGNED  NOT NULL,
    status          ENUM('PENDING','RUNNING','SUCCEEDED','FAILED','SKIPPED') NOT NULL DEFAULT 'PENDING',
    `trigger`       VARCHAR(20)   NOT NULL DEFAULT 'manual',   -- manual | scheduled
    error           TEXT          NOT NULL,
    steps           INT           NOT NULL DEFAULT 0,
    created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at      DATETIME      NULL,
    finished_at     DATETIME      NULL,
    digest_id       INT UNSIGNED  NULL,
    PRIMARY KEY (id),
    KEY idx_generation_jobs_subscription_id (subscription_id),
    KEY idx_generation_jobs_user_id (user_id),
    CONSTRAINT fk_jobs_subscription FOREIGN KEY (subscription_id) REFERENCES subscriptions (id),
    CONSTRAINT fk_jobs_user         FOREIGN KEY (user_id) REFERENCES users (id),
    CONSTRAINT fk_jobs_digest       FOREIGN KEY (digest_id) REFERENCES digests (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- 工具审计日志（Agent 每次工具调用）
-- ------------------------------------------------------------
CREATE TABLE tool_audit_logs (
    id             INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    job_id         INT UNSIGNED  NULL,
    tool_name      VARCHAR(100)  NOT NULL,
    arguments      TEXT          NOT NULL,
    result_summary TEXT          NOT NULL,
    ok             TINYINT(1)    NOT NULL DEFAULT 1,
    duration_ms    INT           NOT NULL DEFAULT 0,
    created_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_tool_audit_logs_job_id (job_id),
    CONSTRAINT fk_audit_job FOREIGN KEY (job_id) REFERENCES generation_jobs (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- 对话记录（对话模式）
-- ------------------------------------------------------------
CREATE TABLE chat_messages (
    id         INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    user_id    INT UNSIGNED  NOT NULL,
    session_id VARCHAR(64)   NOT NULL DEFAULT 'default',
    role       VARCHAR(16)   NOT NULL,           -- user | assistant
    content    TEXT          NOT NULL,
    tool_trace TEXT          NOT NULL,           -- JSON: 本轮工具调用
    created_at DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_chat_messages_user_id (user_id),
    KEY idx_chat_messages_session_id (session_id),
    CONSTRAINT fk_chat_messages_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;
