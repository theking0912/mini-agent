"""
加密 Key 管理器 — 使用 machine code 派生密钥，加密存储 API Key
=================================================================
设计原则：
  - API Key 永不落盘明文
  - 加密密钥从机器特征（machine-id + hostname）派生
  - 同一台机器加密/解密一致，换机器后无法解密
  - 更换 Key 只需删除旧的加密条目重新设定

用法：
    >>> from core.keyring import save_key, load_key, delete_key, list_keys
    >>> save_key("deepseek", "sk-xxx...")
    >>> key = load_key("deepseek")
    >>> delete_key("deepseek")
"""
import base64
import hashlib
import json
import os
from pathlib import Path

# ── 存储路径 ──────────────────────────────────────────────────
# 可通过 MINI_AGENT_DATA_DIR 环境变量覆盖（Docker volume 挂载用）
_data_dir = os.environ.get("MINI_AGENT_DATA_DIR")
if _data_dir:
    KEYRING_DIR = Path(_data_dir)
else:
    KEYRING_DIR = Path.home() / ".mini-agent"
KEYRING_FILE = KEYRING_DIR / "keys.enc"


# ── Machine Code ──────────────────────────────────────────────
SALT_FILE = KEYRING_DIR / "salt"


def get_machine_code() -> str:
    """
    生成机器码用于派生加密密钥
    
    策略（按优先级）：
    1. 持久化 salt 文件（KEYRING_DIR/salt）—— Docker 友好，跨容器重启
    2. /etc/machine-id + hostname —— 裸机 Linux
    3. hostid + hostname —— 传统 fallback（自动生成 salt 保持跨重启）
    """
    # 优先使用持久化 salt（在 KEYRING_DIR 中，随 volume 保留）
    salt_path = KEYRING_DIR / "salt"
    if salt_path.exists():
        salt = salt_path.read_text().strip()
        hostname = os.uname().nodename
        raw = f"{salt}:{hostname}"
        result = hashlib.sha256(raw.encode()).hexdigest()
        # 确保 salt 文件和 key 文件在同一个路径下
        KEYRING_DIR.mkdir(parents=True, exist_ok=True)
        return result

    machine_id = ""
    try:
        machine_id = Path("/etc/machine-id").read_text().strip()
    except (FileNotFoundError, PermissionError, OSError):
        pass

    hostname = os.uname().nodename

    # 没有 /etc/machine-id → Docker 或容器环境 → 用随机 salt 取代
    if not machine_id:
        salt = _ensure_salt()
        raw = f"{salt}:{hostname}"
        return hashlib.sha256(raw.encode()).hexdigest()

    # 裸机 Linux：用 machine-id + hostname
    raw = f"{machine_id}:{hostname}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _ensure_salt():
    """生成持久化 salt 写入 KEYRING_DIR/salt"""
    KEYRING_DIR.mkdir(parents=True, exist_ok=True)
    salt = hashlib.sha256(os.urandom(32)).hexdigest()
    SALT_FILE.write_text(salt)
    SALT_FILE.chmod(0o600)
    return salt


# ── 密钥派生 ──────────────────────────────────────────────────
def _derive_fernet_key(machine_code: str) -> bytes:
    """
    从 machine code 派生 Fernet 兼容的 32 字节 Key（base64 URL-safe）
    
    Fernet 要求 key = base64.urlsafe_b64encode(32_random_bytes)
    我们用 SHA256(machine_code) 作为那 32 字节 → 确定性 key
    """
    key_bytes = hashlib.sha256(machine_code.encode()).digest()
    return base64.urlsafe_b64encode(key_bytes)


def _get_fernet():
    """获取 Fernet 加密器"""
    from cryptography.fernet import Fernet
    key = _derive_fernet_key(get_machine_code())
    return Fernet(key)


# ── CRUD ──────────────────────────────────────────────────────
def save_key(model_name: str, api_key: str):
    """
    加密保存一个 API Key
    
    文件结构（加密存储，整体是 JSON 但值全是密文）：
        {
            "deepseek": "gAAAAAB...===",
            "gpt4o":    "gAAAAAB...===",
        }
    """
    KEYRING_DIR.mkdir(parents=True, exist_ok=True)
    f = _get_fernet()
    token = f.encrypt(api_key.encode())

    data = _read_store()
    data[model_name] = token.decode()
    KEYRING_FILE.write_text(json.dumps(data, indent=2))
    # 设置权限：仅当前用户可读写
    KEYRING_FILE.chmod(0o600)


# ── 通用加密 / 解密（供 user.py 等模块复用）────────────────────
def encrypt_value(plaintext: str) -> str:
    """用机器绑定的 Fernet 密钥加密一段文本，返回 base64 密文字符串"""
    KEYRING_DIR.mkdir(parents=True, exist_ok=True)
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str | None:
    """解密上述密文，失败返回 None（跨机器 / key 损坏）"""
    try:
        f = _get_fernet()
        return f.decrypt(ciphertext.encode()).decode()
    except Exception:
        return None


def load_key(model_name: str) -> str | None:
    """
    解密获取指定模型的 API Key
    
    换机器时 machine code 不同 → 派生不出同样的 Fernet key
    → decrypt 抛出 InvalidToken → 返回 None
    """
    data = _read_store()
    token = data.get(model_name)
    if not token:
        return None
    try:
        f = _get_fernet()
        return f.decrypt(token.encode()).decode()
    except Exception:
        # 解密失败（换机器 / key 损坏）
        return None


def delete_key(model_name: str) -> bool:
    """删除指定模型的 Key，返回是否实际删除了"""
    data = _read_store()
    if model_name not in data:
        return False
    del data[model_name]
    _write_store(data)
    return True


def list_keys() -> list[str]:
    """列出所有已保存 Key 的模型名（不暴露 Key 本身）"""
    return list(_read_store().keys())


def clear_keys():
    """删除所有 Key 文件"""
    if KEYRING_FILE.exists():
        KEYRING_FILE.unlink()


def has_key(model_name: str) -> bool:
    """检查某个模型是否有保存的 Key"""
    return model_name in _read_store()


def num_keys() -> int:
    """已存储的 Key 数量"""
    return len(_read_store())


# ── 内部 ──────────────────────────────────────────────────────
def _read_store() -> dict:
    """读取加密存储文件"""
    if not KEYRING_FILE.exists():
        return {}
    try:
        return json.loads(KEYRING_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_store(data: dict):
    """写入加密存储文件"""
    KEYRING_DIR.mkdir(parents=True, exist_ok=True)
    KEYRING_FILE.write_text(json.dumps(data, indent=2))
    KEYRING_FILE.chmod(0o600)
