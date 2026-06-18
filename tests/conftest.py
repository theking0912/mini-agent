"""Shared test fixtures and configuration."""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolate_keyring():
    """每个测试使用独立的 keyring 文件，互不干扰。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["MINI_AGENT_DATA_DIR"] = tmpdir
        yield
        os.environ.pop("MINI_AGENT_DATA_DIR", None)


@pytest.fixture(autouse=True)
def _clear_env_vars():
    """确保 CI 环境下不会意外读取到宿主机环境变量。"""
    for key in ["DEEPSEEK_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "LLM_MODEL"]:
        os.environ.pop(key, None)


@pytest.fixture
def sample_models_json(tmp_path):
    """创建一个临时 models.json 配置文件。"""
    content = {
        "default": "test-model",
        "models": {
            "test-model": {
                "api_key": "${TEST_API_KEY}",
                "base_url": "https://api.test.com/v1",
                "model": "test-model-v1",
                "description": "测试用模型"
            }
        }
    }
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "models.json"
    config_file.write_text(json.dumps(content, indent=2), encoding="utf-8")
    return config_file
