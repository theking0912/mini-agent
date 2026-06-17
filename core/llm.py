"""
LLM 通信层 — 直接调用 OpenAI 兼容 API，展示底层协议细节
不依赖 openai SDK，用 httpx 裸调 HTTP 接口，让你看到完整的 API 协议
"""
import os
from dataclasses import dataclass
from typing import Any
import httpx


# ── 配置 ──────────────────────────────────────────────────────
# 从环境变量读取，兼容 OpenAI / DeepSeek / vLLM 等所有兼容接口
API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL = os.environ.get("LLM_MODEL", "gpt-4o")


# ── 一个完整的 Chat Completion 请求长什么样 ──
#
# POST /v1/chat/completions
# {
#   "model": "gpt-4o",
#   "messages": [
#     {"role": "system", "content": "你是一个助手"},
#     {"role": "user", "content": "1+1=?"}
#   ],
#   "tools": [                    # ← 工具定义，由 tools/registry.py 生成
#     {
#       "type": "function",
#       "function": {
#         "name": "calculator",
#         "description": "计算数学表达式",
#         "parameters": {...}     # JSON Schema 格式
#       }
#     }
#   ],
#   "tool_choice": "auto"         # 让模型自己决定是否调用工具
# }
#
# 返回：
# {
#   "choices": [{
#     "message": {
#       "role": "assistant",
#       "content": "我来帮你计算...",
#       "tool_calls": [           # ← 如果模型决定调用工具
#         {
#           "id": "call_xxx",
#           "type": "function",
#           "function": {
#             "name": "calculator",
#             "arguments": "{\"expr\": \"1+1\"}"
#           }
#         }
#       ]
#     }
#   }]
# }


@dataclass
class LLMResponse:
    """一次 LLM 调用的标准化返回"""
    content: str           # 模型生成的文本回复（不调工具时有用）
    tool_calls: list      # 模型要调用的工具列表，每个元素是 {"id", "name", "arguments": dict}
    raw: dict             # 完整的原始 API 响应（供学习用）
    usage: dict | None    # Token 用量


def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float = 0.7,
) -> LLMResponse:
    """
    最核心的函数：一次 LLM Chat Completion 调用
    ────────────────────────────────────────────
    参数 raw 到 HTTP 请求的映射：
      messages → body["messages"]    —— 对话上下文
      tools    → body["tools"]       —— 工具定义（可用的工具清单）
    
    返回值解析：
      choices[0].message.content     —— 文本回复（不调工具时）
      choices[0].message.tool_calls  —— 工具调用（调工具时）
    """
    if not API_KEY:
        raise ValueError(
            "请设置 OPENAI_API_KEY 环境变量\n"
            "例如: export OPENAI_API_KEY='sk-xxx'"
        )

    # 构建请求体 — 这就是你在 API 文档里看到的那个 JSON
    body: dict[str, Any] = {
        "model": MODEL,
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
            f"{BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
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
            import json
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
