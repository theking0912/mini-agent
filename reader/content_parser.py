"""
Reader: 内容解析器 — 抓取网页, 保留 HTML 结构, 按章节/段落拆分
================================================================
核心思路：
  - 抓取 URL → 清理无用标签 → 提取主内容区（lxml 树）
  - 按 h2/h3 分割为「章节」(section)
  - 每个章节内按块级标签分割为「段落」(paragraph)
  - 每个段落保留完整 HTML 结构（代码块标记 skip_translate）
"""
import re

import httpx
from lxml import html as lxml_html
from lxml import etree

# ── 用户代理 ──────────────────────────────────────────────────
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# 正文选择器（按优先级）
_MAIN_SELECTORS = [
    "article",
    "main",
    "[role='main']",
    ".markdown-body",
    ".document-content",
    ".post-content",
    ".article-content",
    ".wiki-content",
    ".content",
    ".doc-content",
    ".prose",
]

# 需要完全移除的标签（及其子树）
_REMOVE_TAGS = {
    "script", "style", "noscript", "iframe", "svg",
    "form", "button", "nav", "footer", "input", "select",
    "textarea", "label", "header", "aside",
}

# 块级标签 — 这些作为段落分割点
_BLOCK_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "pre", "blockquote", "ul", "ol", "table",
    "hr", "div", "section", "figure",
}


# ── 抓取 ──────────────────────────────────────────────────────
async def fetch_url(url: str, timeout: int = 30) -> str:
    """异步抓取 URL，返回 HTML 文本"""
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": _DEFAULT_UA},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if "charset=" in ct:
            enc = ct.split("charset=")[-1].split(";")[0].strip()
            return resp.content.decode(enc, errors="replace")
        return resp.text


# ── 树清理 ────────────────────────────────────────────────────
def _is_hidden(el) -> bool:
    """检查元素是否隐藏"""
    style = (el.get("style") or "").replace(" ", "")
    return any(x in style for x in ["display:none", "visibility:hidden", "opacity:0"])


def _clean_node(el, depth=0) -> etree._Element | None:
    """递归清理 lxml 树：移除无用标签及其子树，隐藏元素等。

    返回清理后的元素（或 None 表示应删除）。
    修改是 in-place 的，但会创建浅拷贝以避免影响原始树。
    """
    if depth > 60:
        return None

    tag = el.tag if isinstance(el.tag, str) else None
    if tag is None or tag == "comment":
        return None
    if tag in _REMOVE_TAGS:
        return None
    if _is_hidden(el):
        return None

    # 递归处理子节点
    children = list(el)
    for child in children:
        cleaned = _clean_node(child, depth + 1)
        if cleaned is None:
            el.remove(child)

    return el


def _node_to_html(el) -> str:
    """将 lxml 元素序列化为干净的 HTML 字符串"""
    if el is None:
        return ""
    # 用 etree.tostring 序列化
    html = etree.tostring(el, encoding="unicode", method="html")
    # 清理多余空白行
    html = re.sub(r"\n\s*\n", "\n", html)
    return html.strip()


def _element_text(el) -> str:
    """获取元素纯文本"""
    return (el.text_content() or "").strip()


# ── 查找主内容 ────────────────────────────────────────────────
def _find_main(doc) -> etree._Element | None:
    """找到页面的主内容区域，返回清理后的子树"""
    main_el = None
    for sel in _MAIN_SELECTORS:
        try:
            if sel.startswith("."):
                els = doc.find_class(sel[1:])
                if els:
                    main_el = els[0]
                    break
            elif sel.startswith("["):
                attr = sel[1:-1]
                for el in doc.iter():
                    if el.get(attr) is not None:
                        main_el = el
                        break
                if main_el:
                    break
            else:
                els = list(doc.iter(sel))
                if els:
                    main_el = els[0]
                    break
        except Exception:
            continue

    if main_el is None:
        main_el = doc.find(".//body")

    if main_el is None:
        main_el = doc

    # 清理树
    _clean_node(main_el, 0)
    return main_el


# ── 章节/段落拆分 ──────────────────────────────────────────────
_HEADING_TAGS = {"h1", "h2", "h3", "h4"}


def _is_heading(tag: str) -> bool:
    return tag in _HEADING_TAGS


def _is_block(tag: str) -> bool:
    return tag in _BLOCK_TAGS


def _is_code_block(el) -> bool:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag == "pre" or tag == "code"


def _split_document(main_el) -> list[dict]:
    """从清理后的 lxml 树中提取章节和段落。

    返回:
    [{
        "title": str,
        "paragraphs": [{
            "html": str,         # 保留结构的 HTML
            "text": str,         # 纯文本
            "skip_translate": bool,
            "char_count": int,
        }]
    }]
    """
    sections = []
    current_sec_title = "全文"
    current_paras = []

    def flush_section():
        nonlocal current_paras
        if current_paras:
            sections.append({"title": current_sec_title, "paragraphs": current_paras})
            current_paras = []

    def add_paragraph(el):
        """从元素生成段落并添加到当前章节"""
        if el is None:
            return
        tag = el.tag if isinstance(el.tag, str) else ""

        # 跳过移除标签
        if tag in _REMOVE_TAGS:
            return

        html = _node_to_html(el)
        if not html:
            return

        text = _element_text(el)
        if not text:
            return

        skip = _is_code_block(el)
        current_paras.append({
            "html": html,
            "text": text,
            "skip_translate": skip,
            "char_count": len(text),
        })

    # 遍历 main_el 的直接子节点
    for child in main_el:
        tag = child.tag if isinstance(child.tag, str) else ""
        if tag in _REMOVE_TAGS:
            continue

        if tag == "comment" or tag is None:
            continue

        # 标题 → 新章节
        if _is_heading(tag):
            flush_section()
            title = _element_text(child) or "无标题"
            if len(title) < 2 and tag != "h1":
                continue
            # 标题本身也作为一个段落
            current_sec_title = title
            # 把标题当作一个段落添加（但不用在 title 后加新章节标记）
            add_paragraph(child)
            continue

        # 块级元素 → 段落
        if _is_block(tag):
            if tag in ("div", "section", "figure"):
                # 递归展开：如果有子块级元素，展开处理（支持深层嵌套）
                div_children = list(child)
                _expand_div(child, add_paragraph, flush_section)
            else:
                add_paragraph(child)
        # 其他（如纯文本、内联标签等）— 忽略

    flush_section()
    return sections


def _expand_div(el, add_paragraph, flush_section):
    """递归展开 div/section 元素，找到实际内容并添加为段落"""
    children = list(el)
    has_block = any(
        isinstance(c.tag, str) and _is_block(c.tag)
        for c in children if c.tag not in _REMOVE_TAGS
    )
    if not has_block:
        add_paragraph(el)
        return

    for sub in children:
        subtag = sub.tag if isinstance(sub.tag, str) else ""
        if subtag in _REMOVE_TAGS:
            continue
        if _is_heading(subtag):
            flush_section()
            add_paragraph(sub)
        elif subtag in ("div", "section", "figure"):
            _expand_div(sub, add_paragraph, flush_section)
        elif _is_block(subtag):
            add_paragraph(sub)


# ── 公开 API ──────────────────────────────────────────────────
async def analyze_url(url: str) -> dict:
    """
    分析 URL：抓取 → 提取主内容 → 保留 HTML 结构 → 按章节/段落拆分

    返回:
    {
        "title": str,               # 页面标题
        "sections": [...],
        "total_chars": int,
        "total_paragraphs": int
    }
    """
    html = await fetch_url(url)
    doc = lxml_html.fromstring(html)

    # 页面标题
    page_title = ""
    title_el = doc.find(".//title")
    if title_el is not None:
        page_title = title_el.text_content().strip()

    # 查找主内容区域
    main_el = _find_main(doc)
    if main_el is None:
        return {"url": url, "title": page_title, "sections": [], "total_chars": 0, "total_paragraphs": 0}

    # 拆分为章节和段落
    sections = _split_document(main_el)

    total_chars = 0
    total_paragraphs = 0
    for sec in sections:
        for para in sec["paragraphs"]:
            total_chars += para["char_count"]
            total_paragraphs += 1

    return {
        "url": url,
        "title": page_title,
        "sections": sections,
        "total_chars": total_chars,
        "total_paragraphs": total_paragraphs,
    }
