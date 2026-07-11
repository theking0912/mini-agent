"""
数据库连接中心 — PostgreSQL 连接池 + Redis 客户端
===============================================
配置来源：config/db.json（和环境变量完全脱钩）

用法：
    from core.db import get_conn_sync, get_redis, init_db
"""

import json
from pathlib import Path

import psycopg2
import psycopg2.extras
import redis as redis_module

# ── 配置加载 ──────────────────────────────────────────────────
_DB_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "db.json"
try:
    with open(_DB_CONFIG_PATH, encoding="utf-8") as _f:
        _cfg = json.load(_f)
except Exception as _e:
    raise RuntimeError(f"无法加载 {_DB_CONFIG_PATH}: {_e}")

# PostgreSQL
_pg_cfg = _cfg["postgresql"]
PG_HOST = _pg_cfg["host"]
PG_PORT = _pg_cfg["port"]
PG_USER = _pg_cfg["user"]
PG_PASSWORD = _pg_cfg["password"]
PG_DATABASE = _pg_cfg["database"]

# Redis
_redis_cfg = _cfg["redis"]
_redis_client = redis_module.Redis(
    host=_redis_cfg["host"],
    port=_redis_cfg["port"],
    db=_redis_cfg.get("db", 0),
    password=_redis_cfg.get("password", None) or None,
    decode_responses=True,
)

print(f"[DB] PG: {PG_HOST}:{PG_PORT}/{PG_DATABASE}")
print(f"[DB] Redis: {_redis_cfg['host']}:{_redis_cfg['port']}")

# ── PostgreSQL 连接 ──────────────────────────────────────────
def get_conn_sync():
    """获取同步数据库连接"""
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname=PG_DATABASE,
    )


# ── Redis ────────────────────────────────────────────────────
def get_redis():
    """获取 Redis 客户端"""
    return _redis_client


# ── 数据库初始化 ────────────────────────────────────────────
def init_db():
    """初始化数据库表结构"""
    # 连到 mini_agent 建表
    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(128) NOT NULL,
            display_name VARCHAR(64) DEFAULT '',
            verified BOOLEAN DEFAULT FALSE,
            token VARCHAR(64) DEFAULT '',
            api_keys JSONB DEFAULT '{}',
            created_at TIMESTAMP DEFAULT NOW(),
            last_login TIMESTAMP
        )
    """)

    # 兼容旧表
    try:
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS api_keys JSONB DEFAULT '{}'")
    except Exception:
        pass

    # 旧验证码表（迁移后可手动删除）
    try:
        cur.execute("DROP TABLE IF EXISTS verification_codes")
        print("[DB] 已移除旧 verification_codes 表（验证码已迁移到 Redis）")
    except Exception:
        pass

    # ── 阅读器：书架 ──────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS book_collections (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            icon TEXT DEFAULT '📕',
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    # ── 阅读器：文档 ──────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            collection_id UUID REFERENCES book_collections(id) ON DELETE SET NULL,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            url TEXT,
            source_type TEXT DEFAULT 'web',
            total_chars INTEGER DEFAULT 0,
            total_sections INTEGER DEFAULT 0,
            sections_done INTEGER DEFAULT 0,
            status TEXT DEFAULT 'analyzed',
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    # ── 阅读器：段落 ──────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sections (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            sec_index INTEGER NOT NULL,
            paragraph_index INTEGER NOT NULL,
            title TEXT DEFAULT '',
            html_content_key TEXT DEFAULT '',
            translated_html_key TEXT DEFAULT '',
            text_content TEXT DEFAULT '',
            skip_translate BOOLEAN DEFAULT FALSE,
            status TEXT DEFAULT 'wait',
            char_count INTEGER DEFAULT 0,
            UNIQUE(document_id, sec_index, paragraph_index)
        )
    """)
    # 检查 sections 表是否有 html_content 列
    try:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='sections' AND column_name='html_content'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE sections ADD COLUMN IF NOT EXISTS html_content TEXT DEFAULT ''")
            cur.execute("ALTER TABLE sections ADD COLUMN IF NOT EXISTS translated_html TEXT DEFAULT ''")
            print("[DB] 已添加 html_content/translated_html 列到 sections 表")
    except Exception:
        pass

    # 创建 MinIO reader bucket（忽略已存在错误）
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_sections_doc
        ON sections(document_id, sec_index, paragraph_index)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_documents_user
        ON documents(user_id, collection_id)
    """)

    # ── 系统配置表（持久化系统级 API Key，不依赖 salt）────────────
    from core.config_store import create_table as create_config_table
    create_config_table()

    cur.close()
    conn.close()
    print("[DB] 数据库表结构就绪")

    # 迁移存量明文 Key 到加密存储
    _migrate_plaintext_keys()

    # 迁移当前环境变量 Key 到数据库
    _seed_system_keys_to_db()


def _migrate_plaintext_keys():
    """将存量明文 API Key 迁移为加密存储"""
    import json
    try:
        from core import keyring
    except Exception:
        return  # keyring 未就绪时跳过

    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT id, api_keys FROM users WHERE api_keys IS NOT NULL AND api_keys != '{}'::jsonb")
    rows = cur.fetchall()

    migrated = 0
    for row in rows:
        raw = dict(row["api_keys"])
        changed = {}
        for model, val in raw.items():
            if isinstance(val, str) and val.startswith("gAAAAAB"):
                continue  # 已是密文
            try:
                changed[model] = keyring.encrypt_value(val)
            except Exception:
                pass
        if changed:
            cur.execute(
                "UPDATE users SET api_keys = %s::jsonb WHERE id = %s",
                (json.dumps(changed), row["id"]),
            )
            migrated += 1

    if migrated:
        print(f"[DB] 已加密 {migrated} 个用户的 API Key")

    cur.close()
    conn.close()


def _seed_system_keys_to_db():
    """将当前环境变量中的系统 Key 写入 system_config 表（持久化，不依赖 salt）"""
    try:
        from core.config_store import get_system_key, set_system_key
        from core.config import get_config
    except Exception:
        return

    try:
        cfg = get_config()
        seeded = 0
        for name, m in cfg.models.items():
            if m.api_key and not get_system_key(name):
                set_system_key(name, m.api_key)
                seeded += 1
        if seeded:
            print(f"[DB] 已将 {seeded} 个系统 API Key 写入数据库持久化")
    except Exception as e:
        print(f"[DB] 写入系统 Key 到数据库时出错（非致命）: {e}")
