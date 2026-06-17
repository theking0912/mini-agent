"""
LLM 通信层 — 直接调用 OpenAI 兼容 API，展示底层协议细节
不依赖 openai SDK，用 httpx 裸调 HTTP 接口，让你看到完整的 API 协议

支持多模型配置：通过 core.config 传入 ModelConfig，动态切换 provider。
"""
import json
import os
from dataclasses import dataclass
from typing import Any, Optional
import httpx

from .config import get_config, ModelConfig


# ── 旧的全局配置（只保留做 fallback，建议用 config/models.json） ──
_LEGACY_API_KEY = os.environ.get("OPENAI_API_KEY", "")
_LEGACY_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
_LEGACY_MODEL = os.environ.get("LLM_MODEL", "gpt-4o")


@dataclass
class LLMResponse:
    """一次 LLM 调用的标准化返回"""
    content: str           # 模型生成的文本回复（不调工具时有用）
    tool_calls: list      # 模型要调用的工具列表，每个元素是 {"id", "name", "arguments": dict}
    raw: dict             # 完整的原始 API 响应（供学习用）
    usage: dict | None    # Token 用量


def _get_active_cfg() -> ModelConfig:
    """
    获取当前生效的模型配置。
    优先使用 config/models.json 中的配置，回退到环境变量。
    """
    try:
        return get_config().current_model
    except Exception:
        # 如果 config 模块有异常，回退到旧的环境变量
        return ModelConfig(
            name="default",
            api_key=_LEGACY_API_KEY,
            base_url=_LEGACY_BASE_URL,
            model=_LEGACY_MODEL,
            description="环境变量模式",
        )


def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float = 0.7,
    model_cfg: Optional[ModelConfig] = None,
) -> LLMResponse:
    """
    最核心的函数：一次 LLM Chat Completion 调用
    ────────────────────────────────────────────
    参数：
      messages    — 对话上下文
      tools       — 工具定义（可用的工具清单）
      temperature — 温度参数
      model_cfg   — 模型配置（可选）。不传则使用 config/models.json 中的当前模型，
                    或者回退到环境变量 OPENAI_API_KEY / LLM_MODEL / OPENAI_BASE_URL
    
    参数 raw 到 HTTP 请求的映射：
      messages → body["messages"]    —— 对话上下文
      tools    → body["tools"]       —— 工具定义（可用的工具清单）
    
    返回值解析：
      choices[0].message.content     —— 文本回复（不调工具时）
      choices[0].message.tool_calls  —— 工具调用（调工具时）
    """
    # 确定使用哪个模型配置
    cfg = model_cfg or _get_active_cfg()

    if not cfg.api_key:
        raise ValueError(
            f"模型 '{cfg.name}' 未设置 API Key。\n"
            f"请确保环境变量已设置（config/models.json 引用了 ${cfg.api_key[:20] or '???'}），\n"
            f"或直接设置 OPENAI_API_KEY 环境变量。\n"
            f"例如: export {_find_env_var_for(cfg.name)}='sk-xxx'"
        )

    # 构建请求体 — 这就是你在 API 文档里看到的那个 JSON
    body: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": temperature,
    }

    # 如果有工具定义，注入到请求中
    # 注意：tools 参数来自 tools/registry.py 的 get_schemas()
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"  # 让模型自主决定是否调用工具

    # 发起 HTTP 调用
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{cfg.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

    # 提取回复
    choice = data["choices"][0]
    msg = choice["message"]

    # 解析 tool_calls（如果有）
    tool_calls = []
    if msg.get("tool_calls"):
        for tc in msg["tool_calls"]:
            try:
                args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({
                "id": tc["id"],
                "name": tc["function"]["name"],
                "arguments": args,
            })

    return LLMResponse(
        content=msg.get("content") or "",
        tool_calls=tool_calls,
        raw=data,
        usage=data.get("usage"),
    )


def _find_env_var_for(model_name: str) -> str:
    """根据模型名猜测对应的环境变量名（提示用）"""
    mapping = {
        "deepseek": "DEEPSEEK_API_KEY",
        "deepseek-reasoner": "DEEPSEEK_API_KEY",
        "gpt4o": "OPENAI_API_KEY",
        "gpt4o-mini": "OPENAI_API_KEY",
        "claude": "ANTHROPIC_API_KEY",
        "claude-sonnet": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }
    return mapping.get(model_name, f"{model_name.upper()}_API_KEY")
