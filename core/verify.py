"""
验证码模块 — 基于 Redis，2 分钟过期
====================================
"""
import logging
import secrets

from core.db import get_redis

logger = logging.getLogger("mini-agent.verify")

_VERIFY_PREFIX = "verify"
_VERIFY_TTL = 120  # 2 分钟


def _key(email: str, purpose: str) -> str:
    return f"{_VERIFY_PREFIX}:{purpose}:{email}"


def generate_code() -> str:
    """生成 6 位数字验证码"""
    return f"{secrets.randbelow(1000000):06d}"


def save_code(email: str, code: str, purpose: str = "register") -> None:
    """保存验证码到 Redis（自动覆盖旧码，2 分钟过期）"""
    r = get_redis()
    r.set(_key(email, purpose), code, ex=_VERIFY_TTL)
    logger.info(f"[验证码] 已保存 {email} ({purpose})，有效期 2 分钟")


def verify_code(email: str, code: str, purpose: str = "register") -> bool:
    """验证验证码：匹配 + 删除（一次性使用）"""
    r = get_redis()
    saved = r.get(_key(email, purpose))
    if saved is None:
        return False
    if saved != code:
        return False
    r.delete(_key(email, purpose))
    return True


def delete_code(email: str, purpose: str = "register") -> None:
    """主动删除验证码（如用户取消操作）"""
    r = get_redis()
    r.delete(_key(email, purpose))
