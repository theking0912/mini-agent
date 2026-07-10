"""
文件读取工具 — 安全读取文件内容
"""
from pathlib import Path

from .registry import register


@register(
    name="read_file",
    description="读取指定路径的文件内容（纯文本文件），限制最大 2000 字",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要读取的文件路径（绝对路径或相对路径）",
            },
            "max_chars": {
                "type": "integer",
                "description": "最大读取字符数，默认 2000",
            },
        },
        "required": ["path"],
    },
)
def read_file(args: dict) -> str:
    path = args["path"]
    max_chars = args.get("max_chars", 2000)

    # 安全检查：白名单模式 — 只允许读取项目目录下的文件
    path_obj = Path(path).resolve()
    project_root = Path(__file__).resolve().parent.parent
    try:
        path_obj.relative_to(project_root)
    except ValueError:
        return f"❌ 安全限制：只允许读取项目目录内的文件（{project_root}）"

    if not path_obj.exists():
        return f"❌ 文件不存在: {path}"
    if not path_obj.is_file():
        return f"❌ 不是文件: {path}"

    try:
        content = path_obj.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n...（截断，共 {len(content)} 字符，仅显示前 {max_chars}）"
        return f"📄 {path_obj} ({len(content)} 字符)\n---\n{content}"
    except Exception as e:
        return f"❌ 读取失败: {e}"
