"""
PostgreSQL 数据库模块 — 用户注册/登录/验证码存储
================================================
依赖环境变量（或 keyring）：
    PG_HOST=172.18.0.1  PG_PORT=5433  PG_USER=leroy
    PG_PASSWORD=LeroyLee  PG_DATABASE=mini_agent
"""

import os
import hashlib
import secrets
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool

# ── 数据库配置 ──────────────────────────────────────────────────
PG_HOST = os.environ.get("PG_HOST", "172.18.0.1")
PG_PORT = int(os.environ.get("PG_PORT", "5433"))
PG_USER = os.environ.get("PG_USER", "leroy")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "LeroyLee")
PG_DATABASE = os.environ.get("PG_DATABASE", "mini_agent")

_pool = None


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1, maxconn=10,
            host=PG_HOST, port=PG_PORT,
            user=PG_USER, password=PG_PASSWORD,
            dbname=PG_DATABASE,
        )
    return _pool


@asynccontextmanager
async def get_conn():
    """获取数据库连接（异步上下文管理器）"""
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def init_db():
    """初始化数据库表结构"""
    # 先连默认数据库（leroy），创建 mini_agent 数据库
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname="leroy",
    )
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM pg_database WHERE datname = 'mini_agent'")
    if not cur.fetchone():
        cur.execute("CREATE DATABASE mini_agent")
        print("🗄️  已创建数据库 mini_agent")
    cur.close()
    conn.close()

    # ── 连到 mini_agent 建表 ──
    conn2 = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname="mini_agent",
    )
    conn2.autocommit = True
    cur2 = conn2.cursor()

    cur2.execute("""
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

    # 迁移：给已有表加 api_keys 列（兼容旧表）
    try:
        cur2.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS api_keys JSONB DEFAULT '{}'")
    except Exception:
        pass

    cur2.execute("""
        CREATE TABLE IF NOT EXISTS verification_codes (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) NOT NULL,
            code VARCHAR(8) NOT NULL,
            purpose VARCHAR(16) DEFAULT 'register',
            used BOOLEAN DEFAULT FALSE,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    cur2.execute("""
        CREATE INDEX IF NOT EXISTS idx_verification_email
        ON verification_codes(email, purpose)
    """)

    cur2.close()
    conn2.close()


# ── 密码处理 ──────────────────────────────────────────────────
def hash_password(password: str) -> str:
    """SHA256 加盐哈希（后续可升级为 bcrypt）"""
    salt = secrets.token_hex(16)
    h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{h}"


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码"""
    try:
        salt, h = password_hash.split(":", 1)
        return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest() == h
    except (ValueError, AttributeError):
        return False


def generate_token() -> str:
    """生成登录令牌"""
    return secrets.token_hex(32)


def generate_code() -> str:
    """生成 6 位数字验证码"""
    return f"{secrets.randbelow(1000000):06d}"


# ── 用户 CRUD ──────────────────────────────────────────────────
def create_user(email: str, password: str) -> dict:
    """创建用户（未验证状态），返回用户信息"""
    pw_hash = hash_password(password)
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname=PG_DATABASE,
    )
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id, email, verified, created_at",
            (email, pw_hash)
        )
        user = dict(cur.fetchone())
        return user
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise ValueError("该邮箱已注册")
    finally:
        cur.close()
        conn.close()


def get_user_by_email(email: str) -> dict | None:
    """通过邮箱查找用户"""
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname=PG_DATABASE,
    )
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()
        conn.close()


def get_user_by_token(token: str) -> dict | None:
    """通过 token 查找用户"""
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname=PG_DATABASE,
    )
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT id, email, display_name, verified, created_at FROM users WHERE token = %s", (token,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()
        conn.close()


def verify_user(email: str) -> bool:
    """标记用户已验证"""
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname=PG_DATABASE,
    )
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET verified = TRUE WHERE email = %s", (email,))
        return cur.rowcount > 0
    finally:
        cur.close()
        conn.close()


def set_user_token(email: str, token: str) -> bool:
    """设置用户登录 token"""
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname=PG_DATABASE,
    )
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE users SET token = %s, last_login = NOW() WHERE email = %s",
            (token, email)
        )
        return cur.rowcount > 0
    finally:
        cur.close()
        conn.close()


def login_user(email: str, password: str) -> dict | None:
    """登录验证，成功返回用户信息"""
    user = get_user_by_email(email)
    if not user:
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    if not user["verified"]:
        raise ValueError("邮箱未验证")
    token = generate_token()
    set_user_token(email, token)
    return {
        "id": user["id"],
        "email": user["email"],
        "display_name": user.get("display_name", ""),
        "verified": user["verified"],
        "token": token,
    }


# ── 验证码 CRUD ──────────────────────────────────────────────────
def save_verification_code(email: str, code: str, purpose: str = "register", ttl_minutes: int = 10):
    """保存验证码到数据库"""
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname=PG_DATABASE,
    )
    conn.autocommit = True
    cur = conn.cursor()
    try:
        # 使旧码失效
        cur.execute(
            "UPDATE verification_codes SET used = TRUE WHERE email = %s AND purpose = %s",
            (email, purpose)
        )
        cur.execute(
            "INSERT INTO verification_codes (email, code, purpose, expires_at) VALUES (%s, %s, %s, NOW() + INTERVAL '%s minutes')",
            (email, code, purpose, ttl_minutes)
        )
    finally:
        cur.close()
        conn.close()


def verify_code(email: str, code: str, purpose: str = "register") -> bool:
    """验证验证码是否正确且在有效期内"""
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname=PG_DATABASE,
    )
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """SELECT id FROM verification_codes
               WHERE email = %s AND code = %s AND purpose = %s
               AND used = FALSE AND expires_at > NOW()
               ORDER BY created_at DESC LIMIT 1""",
            (email, code, purpose)
        )
        row = cur.fetchone()
        if not row:
            return False
        # 标记为已使用
        cur.execute("UPDATE verification_codes SET used = TRUE WHERE id = %s", (row["id"],))
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


# ── 用户 Key 管理（每个用户独立）──────────────────────────────────
def get_user_keys(user_id: int) -> dict:
    """获取用户保存的所有 API Key"""
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname=PG_DATABASE,
    )
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT api_keys FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if row and row.get("api_keys"):
            return dict(row["api_keys"])
        return {}
    finally:
        cur.close()
        conn.close()


def set_user_key(user_id: int, model_name: str, api_key: str):
    """保存用户的某个模型 Key"""
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname=PG_DATABASE,
    )
    conn.autocommit = True
    cur = conn.cursor()
    try:
        import json
        cur.execute(
            "UPDATE users SET api_keys = jsonb_set(COALESCE(api_keys, '{}'::jsonb), %s, %s::jsonb) WHERE id = %s",
            (f"{{{model_name}}}", json.dumps(api_key), user_id)
        )
    finally:
        cur.close()
        conn.close()


def delete_user_key(user_id: int, model_name: str) -> bool:
    """删除用户的某个模型 Key"""
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname=PG_DATABASE,
    )
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE users SET api_keys = api_keys - %s WHERE id = %s",
            (model_name, user_id)
        )
        return cur.rowcount > 0
    finally:
        cur.close()
        conn.close()


def list_user_keys(user_id: int) -> list[str]:
    """列出用户已保存 Key 的模型名"""
    keys = get_user_keys(user_id)
    return list(keys.keys())


def has_user_key(user_id: int, model_name: str) -> bool:
    """检查用户是否有某个模型的 Key"""
    keys = get_user_keys(user_id)
    return model_name in keys


def get_user_api_key(user_id: int, model_name: str) -> str | None:
    """获取用户指定模型的 Key"""
    keys = get_user_keys(user_id)
    return keys.get(model_name)
