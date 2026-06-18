"""
用户模块 — 注册 / 登录 / API Key 管理
=====================================
"""
import hashlib
import secrets

import psycopg2
import psycopg2.extras

from core import keyring
from core.db import get_conn_sync


# ── 密码处理 ──────────────────────────────────────────────────
def hash_password(password: str) -> str:
    """SHA256 加盐哈希"""
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


# ── Token ──────────────────────────────────────────────────────
def generate_token() -> str:
    return secrets.token_hex(32)


# ── 用户 CRUD ──────────────────────────────────────────────────
def get_user_by_email(email: str) -> dict | None:
    conn = get_conn_sync()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()
        conn.close()


def get_user_by_token(token: str) -> dict | None:
    conn = get_conn_sync()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT id, email, display_name, avatar, verified, created_at FROM users WHERE token = %s",
            (token,)
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()
        conn.close()


def get_user_by_id(user_id: int) -> dict | None:
    conn = get_conn_sync()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT id, email, display_name, avatar, verified, created_at FROM users WHERE id = %s",
            (user_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()
        conn.close()


def create_user(email: str, password: str) -> dict:
    pw_hash = hash_password(password)
    conn = get_conn_sync()
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


def verify_user(email: str) -> bool:
    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET verified = TRUE WHERE email = %s", (email,))
        return cur.rowcount > 0
    finally:
        cur.close()
        conn.close()


def set_user_token(email: str, token: str) -> bool:
    conn = get_conn_sync()
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
        "avatar": user.get("avatar", ""),
        "verified": user["verified"],
        "token": token,
    }


def logout_user(user_id: int) -> bool:
    """清除用户的登录 token（退出登录）"""
    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET token = '' WHERE id = %s", (user_id,))
        return cur.rowcount > 0
    finally:
        cur.close()
        conn.close()


# ── API Key 管理（每个用户独立，加密存储）────────────────────────
def get_user_keys(user_id: int) -> dict:
    """获取用户所有模型 Key，密文字段自动解密（兼容旧版明文）"""
    conn = get_conn_sync()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT api_keys FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if row and row.get("api_keys"):
            raw = dict(row["api_keys"])
            # 解密每个 Key，失败则视为明文（旧数据兼容）
            decrypted = {}
            for model_name, val in raw.items():
                plain = keyring.decrypt_value(val)
                decrypted[model_name] = plain if plain is not None else val
            return decrypted
        return {}
    finally:
        cur.close()
        conn.close()


def set_user_key(user_id: int, model_name: str, api_key: str):
    """加密保存用户 API Key 到 PostgreSQL"""
    import json
    encrypted = keyring.encrypt_value(api_key)
    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE users SET api_keys = jsonb_set(COALESCE(api_keys, '{}'::jsonb), %s, %s::jsonb) WHERE id = %s",
            (f"{{{model_name}}}", json.dumps(encrypted), user_id)
        )
    finally:
        cur.close()
        conn.close()


def delete_user_key(user_id: int, model_name: str) -> bool:
    conn = get_conn_sync()
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
    return list(get_user_keys(user_id).keys())


def has_user_key(user_id: int, model_name: str) -> bool:
    return model_name in get_user_keys(user_id)


def get_user_api_key(user_id: int, model_name: str) -> str | None:
    return get_user_keys(user_id).get(model_name)


# ── 头像管理 ──────────────────────────────────────────────────
def get_user_avatar(user_id: int) -> str:
    """获取用户头像路径（存的是 MinIO obj path，空字符串表示用默认头像）"""
    conn = get_conn_sync()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT avatar FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return row["avatar"] if row and row.get("avatar") else ""
    finally:
        cur.close()
        conn.close()


def set_user_avatar(user_id: int, avatar_path: str):
    """设置用户头像路径"""
    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET avatar = %s WHERE id = %s", (avatar_path, user_id))
    finally:
        cur.close()
        conn.close()
