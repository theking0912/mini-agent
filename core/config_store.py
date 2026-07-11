"""
系统配置存储 — PostgreSQL 持久化，不依赖机器 salt
===============================================
存储系统级配置（如 API Key），跨容器重启不丢失。

用法：
    from core.config_store import get_system_key, set_system_key
    key = get_system_key("deepseek")
    set_system_key("deepseek", "sk-xxx...")
"""
import json

import psycopg2
import psycopg2.extras

from core.db import get_conn_sync


# ── 表结构 ──────────────────────────────────────────────────
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS system_config (
    key   VARCHAR(255) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW()
)
"""

SET_CONFIG_SQL = """
INSERT INTO system_config (key, value, updated_at)
VALUES (%s, %s, NOW())
ON CONFLICT (key) DO UPDATE SET
    value = EXCLUDED.value,
    updated_at = NOW()
"""

GET_CONFIG_SQL = "SELECT value FROM system_config WHERE key = %s"
DELETE_CONFIG_SQL = "DELETE FROM system_config WHERE key = %s"
LIST_KEYS_SQL = "SELECT key, updated_at FROM system_config ORDER BY key"


def create_table():
    """确保表存在（由 init_db 调用）"""
    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(CREATE_TABLE_SQL)
    finally:
        cur.close()
        conn.close()


# ── CRUD ──────────────────────────────────────────────────────


def set_system_key(model_name: str, api_key: str):
    """保存系统级 API Key 到数据库（明文存储，不依赖 salt）"""
    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(SET_CONFIG_SQL, (f"api_key:{model_name}", api_key))
    finally:
        cur.close()
        conn.close()


def get_system_key(model_name: str) -> str | None:
    """从数据库获取系统级 API Key"""
    conn = get_conn_sync()
    cur = conn.cursor()
    try:
        cur.execute(GET_CONFIG_SQL, (f"api_key:{model_name}",))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()
        conn.close()


def delete_system_key(model_name: str) -> bool:
    """删除系统级 API Key"""
    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(DELETE_CONFIG_SQL, (f"api_key:{model_name}",))
        return cur.rowcount > 0
    finally:
        cur.close()
        conn.close()


def list_system_keys() -> list[str]:
    """列出所有已保存系统 Key 的模型名"""
    conn = get_conn_sync()
    cur = conn.cursor()
    try:
        cur.execute(LIST_KEYS_SQL)
        rows = cur.fetchall()
        return [r[0].replace("api_key:", "", 1) for r in rows if r[0].startswith("api_key:")]
    finally:
        cur.close()
        conn.close()


# ── 通用 key-value（未来扩展用）─────────────────────────────────


def set_config(key: str, value: str):
    """保存任意系统配置"""
    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(SET_CONFIG_SQL, (key, value))
    finally:
        cur.close()
        conn.close()


def get_config(key: str) -> str | None:
    """获取任意系统配置"""
    conn = get_conn_sync()
    cur = conn.cursor()
    try:
        cur.execute(GET_CONFIG_SQL, (key,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()
        conn.close()


def delete_config(key: str) -> bool:
    """删除任意系统配置"""
    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(DELETE_CONFIG_SQL, (key,))
        return cur.rowcount > 0
    finally:
        cur.close()
        conn.close()
