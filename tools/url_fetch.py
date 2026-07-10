"""
URL 内容提取工具 — 抓取网页、提取正文、按标题分割章节
=====================================================
用于翻译功能的核心模块。
"""
import re
from urllib.parse import urljoin

import httpx
from lxml import html as lxml_html


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
]


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
        # 编码检测
        ct = resp.headers.get("content-type", "")
        if "charset=" in ct:
            enc = ct.split("charset=")[-1].split(";")[0].strip()
            return resp.content.decode(enc, errors="replace")
        return resp.text


# ── HTML 清理 ──────────────────────────────────────────────────
_REMOVE_TAGS = {
    "script", "style", "noscript", "iframe", "svg",
    "form", "button", "nav", "footer",
}


def _clean_text(text: str) -> str:
    """清理多余空白"""
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _extract_text(el) -> str:
    """提取元素纯文本，跳过隐藏和无关标签"""
    text_parts = []
    for node in el.iter():
        tag = node.tag if isinstance(node.tag, str) else ""
        # 跳过移除标签
        if tag in _REMOVE_TAGS:
            continue
        # 跳过隐藏元素
        style = node.get("style")
        if style and "display:none" in style.replace(" ", ""):
            continue
        # text tail 和 text
        if tag and node.text:
            text_parts.append(node.text)
        if node.tail:
            text_parts.append(node.tail)
    text = "".join(text_parts)
    return _clean_text(text)


# ── 查找主内容 ────────────────────────────────────────────────
def _find_main(doc) -> list:
    """找到主体内容中的所有块级章节 (section / div.section 等)"""
    # 方式1：按选择器找主元素
    for sel in _MAIN_SELECTORS:
        if sel.startswith("."):
            els = doc.find_class(sel[1:])
            if els:
                return els[0].getchildren()
        elif sel.startswith("["):
            attr = sel[1:-1]
            for el in doc.iter():
                if el.get(attr) is not None:
                    return el.getchildren()
        else:
            els = list(doc.iter(sel))
            if els:
                return els[0].getchildren()

    # 方式2：直接用 body 的子元素
    body = doc.find(".//body")
    if body is not None:
        children = [c for c in body.iterchildren() if c.tag not in _REMOVE_TAGS]
        if children:
            return children

    # 方式3：整个文档
    return list(doc.iterchildren())


# ── 提取 TOC（改进版） ────────────────────────────────────────
def _extract_toc(doc) -> list[dict]:
    """从页面提取目录结构"""
    chapters = []
    seen = set()

    def add(title, href="#"):
        key = title.strip().lower()
        if key not in seen and len(title.strip()) > 2:
            seen.add(key)
            chapters.append({"title": title.strip(), "href": href})

    # 方式1：侧栏 nav > a
    for nav in doc.iter("nav"):
        for a in nav.iter("a"):
            t = a.text_content().strip()
            h = a.get("href", "")
            if t and len(t) > 2 and not h.startswith(("#", "javascript")):
                add(t, h)

    # 方式2：侧栏 ul > li > a
    for ul in doc.iter("ul"):
        # 检查是否在侧栏区域
        parent_classes = ""
        p = ul.getparent()
        if p is not None:
            parent_classes = (p.get("class") or "") + " " + (p.get("role") or "")
        if any(x in parent_classes for x in ["sidebar", "nav", "toc", "menu", "list"]):
            for li in ul.iter("li"):
                a = li.find(".//a")
                if a is not None:
                    t = a.text_content().strip()
                    h = a.get("href", "")
                    if t and len(t) > 2:
                        add(t, h)

    # 方式3：如果都为空，用正文 h1/h2/h3
    if not chapters:
        for tag in ("h1", "h2", "h3"):
            for h in doc.iter(tag):
                t = h.text_content().strip()
                if t and len(t) > 3 and len(t) < 120:
                    add(t)

    return chapters


# ── 按标题分割内容（改进版） ──────────────────────────────────
def _split_sections(children: list) -> list[dict]:
    """将扁平的内容子元素按 h2/h3 分割成章节"""
    sections = []
    current = None
    current_texts = []

    def flush():
        nonlocal current, current_texts
        if current and current_texts:
            text = _clean_text("\n".join(current_texts))
            text = _clean_text(text)
            if text:
                current["content"] = text
                current["char_count"] = len(text)
                sections.append(current)
        current = None
        current_texts = []

    for child in children:
        tag = child.tag if isinstance(child.tag, str) else ""
        if tag in ("h1", "h2", "h3", "h4"):
            flush()
            title = child.text_content().strip()
            if title:
                # 跳过短标题（可能是导航项）
                if len(title) < 4 and tag not in ("h1",):
                    continue
                current = {"title": title, "tag": tag, "content": "", "char_count": 0}
        elif current is not None and tag not in _REMOVE_TAGS:
            t = _extract_text(child)
            if t:
                current_texts.append(t)

    flush()
    return sections


# ── 公开 API ──────────────────────────────────────────────────
async def analyze_url(url: str) -> dict:
    """
    分析 URL：抓取 → 提取正文 → 分割章节
    返回: { title, chapters: [...], sections: [{title, content, char_count}], ... }
    """
    html = await fetch_url(url)
    doc = lxml_html.fromstring(html)

    # 标题
    title = ""
    title_el = doc.find(".//title")
    if title_el is not None:
        title = title_el.text_content().strip()

    # 提取 TOC
    toc = _extract_toc(doc)

    # 找主要内容并分割
    children = _find_main(doc)
    sections = _split_sections(children)

    # 如果没有分割出章节，用一个整段
    if not sections:
        if children:
            text = _clean_text("\n".join(_extract_text(c) for c in children if c.tag not in _REMOVE_TAGS))
        else:
            text = _clean_text(doc.text_content())
        text = _clean_text(text)
        if text:
            sections = [{"title": title or "全文", "content": text, "char_count": len(text)}]

    return {
        "url": url,
        "title": title,
        "chapters": toc or [{"title": s["title"], "href": "#"} for s in sections],
        "sections": sections,
        "total_chars": sum(s.get("char_count", 0) for s in sections),
        "total_sections": len(sections),
    }


async def translate_section(
    section: dict,
    api_key: str,
    base_url: str,
    model: str,
    lang: str = "中文",
) -> str:
    """翻译单个章节内容"""
    from core.llm import chat_async

    system_prompt = f"""你是一个专业的内容翻译助手。请将以下内容翻译成{lang}。

要求：
- 用通俗易懂的口语化{lang}，避免翻译腔
- 技术术语保留英文原文并在括号内附中文解释
- 长句拆短句，保持段落结构
- 代码块和专有名词保留原样不翻译
- 如果内容是代码或纯技术文档，保持准确性优先"""

    from core.config import ModelConfig
    cfg = ModelConfig(
        name="translate",
        api_key=api_key,
        base_url=base_url,
        model=model,
        description="翻译模型",
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"请翻译以下内容（章节标题：{section['title']}）：\n\n{section['content']}",
        },
    ]

    resp = await chat_async(messages=messages, model_cfg=cfg, temperature=0.3)
    return resp.content
