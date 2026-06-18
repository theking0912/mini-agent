"""
模型配置加载器 — 从 JSON 配置读取多模型定义
───────────────────────────────────────────────
设计思路：
  - 所有模型配置集中在一个 JSON 文件（config/models.json）
  - API Key 按以下优先级查找：
    1. 环境变量（${ENV_VAR_NAME} 语法引用）
    2. 加密 Key 存储（core.keyring — 基于 machine code 解密）
  - 支持：运行时切换模型、列出可用模型、动态刷新配置

配置文件格式：
    {
        "default": "deepseek",
        "models": {
            "deepseek": {
                "api_key": "${DEEPSEEK_API_KEY}",  # ← 环境变量引用
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat",
                "description": "DeepSeek V3"
            }
        }
    }
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# ── 路径 ──────────────────────────────────────────────────────
# 配置文件位置：项目根目录下的 config/models.json
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
CONFIG_FILE = CONFIG_DIR / "models.json"


@dataclass
class ModelConfig:
    """单个模型的运行时配置（env var 已被解析）"""
    name: str
    api_key: str
    base_url: str
    model: str
    description: str = ""


@dataclass
class AppConfig:
    """完整配置"""
    default_model: str = "deepseek"
    models: dict[str, ModelConfig] = field(default_factory=dict)
    _current: str = "deepseek"

    @property
    def current_model(self) -> ModelConfig:
        """获取当前选中的模型"""
        return self.models.get(self._current, list(self.models.values())[0])

    @current_model.setter
    def current_model(self, name: str):
        if name not in self.models:
            raise KeyError(f"未知模型: '{name}'，可用: {', '.join(self.models.keys())}")
        self._current = name

    def switch(self, name: str) -> str:
        """切换当前模型，返回描述文本"""
        old = self._current
        self.current_model = name
        m = self.current_model
        return f"🔄 已切换: {old} → {name} ({m.description}) [{m.model}]"


# ── Env Var 插值 ──────────────────────────────────────────────
_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env(value: str) -> str:
    """
    解析 ${VAR_NAME} 占位符为环境变量值。
    如果环境变量未设置，保留原样（不报错，让运行时调用时自然地报 API Key 缺失）。
    """
    def _replacer(m: re.Match) -> str:
        var_name = m.group(1)
        val = os.environ.get(var_name)
        return val if val is not None else m.group(0)
    return _ENV_PATTERN.sub(_replacer, value)


def _resolve_api_key(model_name: str, raw_value: str) -> str:
    """
    解析 API Key，优先级：
    1. 环境变量（${VAR} 插值后非空）
    2. 加密 Key 存储（core.keyring）
    """
    # 先试环境变量
    env_val = _resolve_env(raw_value)
    if env_val and not env_val.startswith("${"):
        return env_val

    # 再试加密 key 存储
    try:
        from .keyring import load_key
        kr_val = load_key(model_name)
        if kr_val:
            return kr_val
    except Exception:
        pass

    return ""


def _has_api_key(model_name: str, raw_value: str) -> bool:
    """检查某个模型是否有可用的 Key（不暴露具体值）"""
    return bool(_resolve_api_key(model_name, raw_value))


# ── 加载器 ────────────────────────────────────────────────────
def load_config() -> AppConfig:
    """
    从 config/models.json 加载配置。
    若文件不存在，创建默认配置。
    若文件解析失败，回退到环境变量模式（兼容旧版）。
    """
    # 首次运行：如果配置文件不存在，创建默认的
    if not CONFIG_FILE.exists():
        _create_default_config()

    # 加载
    try:
        import json
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠️  配置文件加载失败 ({e})，回退到环境变量模式")
        return _legacy_config()

    # 解析
    app_cfg = AppConfig()
    app_cfg.default_model = raw.get("default", "deepseek")
    app_cfg._current = app_cfg.default_model

    for name, cfg in raw.get("models", {}).items():
        api_key_raw = cfg.get("api_key", "")
        base_url_raw = cfg.get("base_url", "https://api.openai.com/v1")
        model_raw = cfg.get("model", name)

        m = ModelConfig(
            name=name,
            api_key=_resolve_api_key(name, api_key_raw),
            base_url=_resolve_env(base_url_raw),
            model=_resolve_env(model_raw),
            description=cfg.get("description", name),
        )
        app_cfg.models[name] = m

    # 确保 default 存在，不存在则用第一个
    if app_cfg.default_model not in app_cfg.models:
        if app_cfg.models:
            app_cfg.default_model = list(app_cfg.models.keys())[0]
            app_cfg._current = app_cfg.default_model

    return app_cfg


def _create_default_config():
    """首次运行时创建默认配置模板"""
    default_content = """{
  "//": "多模型配置文件。API Key 使用 ${ENV_VAR_NAME} 引用环境变量，或通过 /key 命令存入加密存储。",
  "default": "deepseek",
  "models": {
    "deepseek": {
      "api_key": "${DEEPSEEK_API_KEY}",
      "base_url": "https://api.deepseek.com/v1",
      "model": "deepseek-chat",
      "description": "DeepSeek V3 (性价比之选)"
    },
    "deepseek-reasoner": {
      "api_key": "${DEEPSEEK_API_KEY}",
      "base_url": "https://api.deepseek.com/v1",
      "model": "deepseek-reasoner",
      "description": "DeepSeek R1 (深度推理)"
    },
    "gpt4o": {
      "api_key": "${OPENAI_API_KEY}",
      "base_url": "https://api.openai.com/v1",
      "model": "gpt-4o",
      "description": "OpenAI GPT-4o (通用最强)"
    },
    "gpt4o-mini": {
      "api_key": "${OPENAI_API_KEY}",
      "base_url": "https://api.openai.com/v1",
      "model": "gpt-4o-mini",
      "description": "OpenAI GPT-4o Mini (轻量快速)"
    }
  }
}
"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(default_content, encoding="utf-8")
    print(f"📝 已创建默认配置文件: {CONFIG_FILE}")


def _legacy_config() -> AppConfig:
    """回退：从环境变量读取（兼容旧版 OPENAI_API_KEY / LLM_MODEL / OPENAI_BASE_URL）"""
    app_cfg = AppConfig()
    m = ModelConfig(
        name="default",
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=os.environ.get("LLM_MODEL", "gpt-4o"),
        description="环境变量模式 (OPENAI_API_KEY)",
    )
    app_cfg.models["default"] = m
    app_cfg.default_model = "default"
    app_cfg._current = "default"
    return app_cfg


# ── 全局单例 ──────────────────────────────────────────────────
# 模块级缓存，避免每次调用都重新读文件
_config: AppConfig | None = None


def get_config() -> AppConfig:
    """获取全局配置（懒加载）"""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> AppConfig:
    """重新加载配置（例如用户手动修改了 models.json）"""
    global _config
    _config = load_config()
    return _config
