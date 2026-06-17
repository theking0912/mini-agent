"""
工具注册中心 — 管理所有工具的定义和执行
这是 Agent 的「工具箱」，每个工具需要提供两样东西：
1. schema — OpenAI 兼容的 JSON Schema 定义（告诉 LLM 这个工具怎么用）
2. execute — 实际的 Python 函数（做真正的工作）
"""
import re

# 工具注册表：{name: {"schema": {...}, "fn": callable}}
_registry: dict[str, dict] = {}


def register(name: str, description: str, parameters: dict):
    """
    装饰器：注册一个工具
    
    用法：
        @register("calculator", "计算数学表达式", {
            "type": "object",
            "properties": {"expr": {"type": "string", "description": "..."}},
            "required": ["expr"],
        })
        def calculator(args: dict) -> str:
            ...
    """
    def decorator(fn):
        _registry[name] = {
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            },
            "fn": fn,
        }
        return fn
    return decorator


def get_schemas() -> list[dict]:
    """返回 OpenAI-compatible tool schemas（给 llm.chat 的 tools 参数）"""
    return [entry["schema"] for entry in _registry.values()]


def execute(name: str, arguments: dict) -> str:
    """
    执行工具调用
    
    这是工具执行环的关键节点：
    LLM 说 "我要调用 calculator(expr='1+1')"
    → execute("calculator", {"expr": "1+1"}) 返回 "2"
    → 结果通过 add_tool_result() 送回 LLM
    """
    entry = _registry.get(name)
    if not entry:
        return f"错误：未知工具 '{name}'，可用工具: {', '.join(_registry.keys())}"
    try:
        result = entry["fn"](arguments)
        return str(result)
    except Exception as e:
        return f"工具执行错误 ({name}): {e}"


def list_tools() -> str:
    """列出所有可用工具（给用户看）"""
    lines = ["📦 已注册工具："]
    for name, entry in _registry.items():
        schema = entry["schema"]["function"]
        props = schema.get("parameters", {}).get("properties", {})
        params = ", ".join(props.keys()) if props else "无参数"
        lines.append(f"  • {name}({params}) — {schema['description']}")
    return "\n".join(lines)
