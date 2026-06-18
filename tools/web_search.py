"""
网络搜索工具 — 通过 DuckDuckGo 进行搜索（无需 API Key）
"""
from .registry import register

try:
    from duckduckgo_search import DDGS
    HAS_DDGS = True
except ImportError:
    HAS_DDGS = False


def _search_ddg(query: str, max_results: int = 5) -> list[dict]:
    """使用 DuckDuckGo 搜索"""
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))
    return results


@register(
    name="web_search",
    description="搜索网络信息，返回相关网页链接和摘要",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词",
            },
        },
        "required": ["query"],
    },
)
def web_search(args: dict) -> str:
    query = args["query"]

    if not HAS_DDGS:
        return "❌ 未安装 duckduckgo_search 库，请运行: pip install duckduckgo-search"

    try:
        results = _search_ddg(query)
        if not results:
            return f"🔍 搜索 '{query}' 没有找到结果"

        lines = [f"🔍 搜索 '{query}' 的结果：\n"]
        for i, r in enumerate(results[:5], 1):
            title = r.get("title", "无标题")
            snippet = r.get("body", "")[:200]
            href = r.get("href", "")
            lines.append(f"{i}. [{title}]({href})")
            lines.append(f"   {snippet}\n")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 搜索失败: {e}"
