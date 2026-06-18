"""Tests for config loading and keyring."""
import tempfile
from pathlib import Path

import pytest


def test_env_var_interpolation(monkeypatch):
    """测试 ${VAR} 语法被正确解析为环境变量值。"""
    monkeypatch.setenv("TEST_API_KEY", "sk-test-key-12345")
    from core.config import _resolve_env

    result = _resolve_env("${TEST_API_KEY}")
    assert result == "sk-test-key-12345"


def test_env_var_keeps_placeholder_if_unset():
    """测试环境变量未设置时保留占位符。"""
    from core.config import _resolve_env

    result = _resolve_env("${NONEXISTENT_VAR_XYZ}")
    assert result == "${NONEXISTENT_VAR_XYZ}"


def test_keyring_save_and_load():
    """测试加密 Key 存储的保存和读取。"""
    from core.keyring import load_key, save_key

    save_key("test-model", "sk-test-secret")
    loaded = load_key("test-model")
    assert loaded == "sk-test-secret"


def test_keyring_delete():
    """测试删除加密 Key。"""
    from core.keyring import delete_key, load_key, save_key

    save_key("test-model", "sk-test-secret")
    assert delete_key("test-model") is True
    assert load_key("test-model") is None


def test_keyring_list_keys():
    """测试列出所有已保存的 Key。"""
    from core.keyring import clear_keys, list_keys, save_key

    clear_keys()
    save_key("model-a", "key-a")
    save_key("model-b", "key-b")
    keys = list_keys()
    assert "model-a" in keys
    assert "model-b" in keys


def test_keyring_cross_machine_does_not_decrypt():
    """模拟跨机器场景：salt 不同则无法解密。"""
    from core.keyring import KEYRING_DIR, load_key, save_key

    save_key("test-model", "secret-value")

    # 清除 salt 文件，下次 get_machine_code 会重新生成 → 不同 key
    salt_file = KEYRING_DIR / "salt"
    if salt_file.exists():
        salt_file.unlink()

    loaded = load_key("test-model")
    assert loaded is None, "换机器后应无法解密"


def test_config_loads_default_when_no_file(monkeypatch):
    """测试配置文件不存在时自动创建默认配置。"""
    tmpdir = Path(tempfile.mkdtemp())
    monkeypatch.setattr("core.config.CONFIG_DIR", tmpdir / "config")
    monkeypatch.setattr("core.config.CONFIG_FILE", tmpdir / "config" / "models.json")

    from core.config import load_config
    cfg = load_config()
    assert cfg.default_model == "deepseek"
    assert "deepseek" in cfg.models


def test_config_switch_model():
    """测试切换模型。"""
    from core.config import AppConfig, ModelConfig

    cfg = AppConfig()
    cfg.models["a"] = ModelConfig(name="a", api_key="k1", base_url="u1", model="m1", description="Model A")
    cfg.models["b"] = ModelConfig(name="b", api_key="k2", base_url="u2", model="m2", description="Model B")
    cfg.default_model = "a"
    cfg._current = "a"

    msg = cfg.switch("b")
    assert cfg._current == "b"
    assert "a" in msg and "b" in msg


def test_config_switch_unknown_raises():
    """测试切换到未知模型抛出 KeyError。"""
    from core.config import AppConfig, ModelConfig

    cfg = AppConfig()
    cfg.models["a"] = ModelConfig(name="a", api_key="k1", base_url="u1", model="m1", description="A")
    with pytest.raises(KeyError):
        cfg.switch("nonexistent")
