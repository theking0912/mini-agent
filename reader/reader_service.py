"""
Reader: 服务层 — 书架/文档 CRUD + 翻译管线 + MinIO 持久化
=========================================================
"""
import json
import uuid as uuid_mod
from datetime import UTC, datetime
from typing import Any

import psycopg2
import psycopg2.extras

from core.db import get_conn_sync
from core.storage import minio_get, minio_put
from .content_parser import analyze_url as _analyze_url

# ── MinIO 配置 ────────────────────────────────────────────────
READER_BUCKET = "reader"


def _ensure_bucket():
    """确保 reader bucket 存在（不存在则创建）"""
    from core.storage import MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY
    import urllib.request
    try:
        req = urllib.request.Request(f"{MINIO_ENDPOINT}/{READER_BUCKET}/", method="PUT", data=b"")
        req.add_header("User-Agent", "MiniAgent/1.0")
        # 用同样的 AWS4 签名
        from core.storage import _aws4_sign
        headers = _aws4_sign("PUT", f"{READER_BUCKET}/", b"", "application/octet-stream")
        for k, v in headers.items():
            req.add_header(k, v)
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as e:
        if e.code != 409:  # 409 = 已存在，正常
            print(f"[Reader] Bucket create warning: {e.code}")
    except Exception as e:
        print(f"[Reader] Bucket create error: {e}")


# 启动时自动确保 bucket 存在
_ensure_bucket()


# ── ✅ 段落内容 MinIO 路径 ────────────────────────────────────
def _para_orig_key(doc_id: str, sec_i: int, para_i: int) -> str:
    """原文段落的 MinIO key"""
    return f"{doc_id}/s{sec_i}_p{para_i}_orig.html"


def _para_trans_key(doc_id: str, sec_i: int, para_i: int) -> str:
    """译文段落的 MinIO key"""
    return f"{doc_id}/s{sec_i}_p{para_i}_trans.html"


# ── 辅助 ──────────────────────────────────────────────────────
def _dict_row(cur, row) -> dict:
    """将 RealDictRow 转为普通 dict"""
    if row is None:
        return None
    d = dict(row)
    # 处理 datetime
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    # UUID → str
    for k, v in d.items():
        if hasattr(v, "hex"):
            d[k] = str(v)
    return d


# ══════════════════════════════════════════════════════════════
# 书架 (BookCollection)
# ══════════════════════════════════════════════════════════════

def list_collections(user_id: int) -> list[dict]:
    """获取用户的所有书架（含文档数量）"""
    conn = get_conn_sync()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT bc.*, COUNT(d.id) AS doc_count
            FROM book_collections bc
            LEFT JOIN documents d ON d.collection_id = bc.id
            WHERE bc.user_id = %s
            GROUP BY bc.id
            ORDER BY bc.sort_order, bc.created_at
        """, (user_id,))
        return [_dict_row(cur, r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def create_collection(user_id: int, name: str, icon: str = "📕") -> dict:
    """创建新书架"""
    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            INSERT INTO book_collections (user_id, name, icon)
            VALUES (%s, %s, %s)
            RETURNING *
        """, (user_id, name, icon))
        return _dict_row(cur, cur.fetchone())
    finally:
        cur.close()
        conn.close()


def delete_collection(collection_id: str, user_id: int) -> bool:
    """删除书架（级联删除文档和段落）"""
    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM book_collections WHERE id = %s::uuid AND user_id = %s",
            (collection_id, user_id),
        )
        return cur.rowcount > 0
    finally:
        cur.close()
        conn.close()


def rename_collection(collection_id: str, user_id: int, name: str, icon: str = None) -> dict | None:
    """重命名书架"""
    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        if icon:
            cur.execute(
                "UPDATE book_collections SET name = %s, icon = %s WHERE id = %s::uuid AND user_id = %s RETURNING *",
                (name, icon, collection_id, user_id),
            )
        else:
            cur.execute(
                "UPDATE book_collections SET name = %s WHERE id = %s::uuid AND user_id = %s RETURNING *",
                (name, collection_id, user_id),
            )
        return _dict_row(cur, cur.fetchone())
    finally:
        cur.close()
        conn.close()


# ══════════════════════════════════════════════════════════════
# 文档 (Document)
# ══════════════════════════════════════════════════════════════

def list_documents(user_id: int, collection_id: str = None) -> list[dict]:
    """获取用户的文档列表"""
    conn = get_conn_sync()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        if collection_id:
            cur.execute("""
                SELECT * FROM documents
                WHERE user_id = %s AND collection_id = %s::uuid
                ORDER BY updated_at DESC
            """, (user_id, collection_id))
        else:
            cur.execute("""
                SELECT * FROM documents
                WHERE user_id = %s
                ORDER BY updated_at DESC
            """, (user_id,))
        return [_dict_row(cur, r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def get_document(doc_id: str, user_id: int) -> dict | None:
    """获取单个文档基本信息"""
    conn = get_conn_sync()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT * FROM documents WHERE id = %s::uuid AND user_id = %s",
            (doc_id, user_id),
        )
        return _dict_row(cur, cur.fetchone())
    finally:
        cur.close()
        conn.close()


def delete_document(doc_id: str, user_id: int) -> bool:
    """删除文档（级联删除段落）"""
    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # 获取 MinIO keys 以便清理
        cur.execute(
            "SELECT html_content_key, translated_html_key FROM sections WHERE document_id = %s::uuid",
            (doc_id,),
        )
        keys = [(r["html_content_key"], r["translated_html_key"]) for r in cur.fetchall() if r]

        # 删除文档（级联删除段落）
        cur.execute(
            "DELETE FROM documents WHERE id = %s::uuid AND user_id = %s",
            (doc_id, user_id),
        )
        deleted = cur.rowcount > 0

        # 清理 MinIO 中的内容（忽略失败）
        if deleted:
            for orig_key, trans_key in keys:
                try:
                    from core.storage import minio_delete
                    if orig_key:
                        minio_delete(f"{READER_BUCKET}/{orig_key}")
                    if trans_key:
                        minio_delete(f"{READER_BUCKET}/{trans_key}")
                except Exception:
                    pass

        return deleted
    finally:
        cur.close()
        conn.close()


# ══════════════════════════════════════════════════════════════
# 分析 + 存储
# ══════════════════════════════════════════════════════════════

async def analyze_and_store(url: str, user_id: int, collection_id: str = None) -> dict:
    """抓取 URL → 分析 → 存储到 MinIO → 写入 DB → 返回文档信息"""
    result = await _analyze_url(url)

    if not result.get("sections"):
        raise ValueError("未能从该页面提取到任何内容")

    page_title = result["title"] or url.split("/")[-1] or "未命名文档"
    doc_id = str(uuid_mod.uuid4())

    # ── 段落到 MinIO ──
    total_chars = 0
    total_paragraphs = 0
    total_sections = 0

    sections = result["sections"]

    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # 1. 插入文档
        cur.execute("""
            INSERT INTO documents (id, collection_id, user_id, title, url,
                source_type, total_chars, total_sections)
            VALUES (%s::uuid, %s::uuid, %s, %s, %s, 'web', 0, 0)
            RETURNING *
        """, (doc_id, collection_id, user_id, page_title, url))
        doc = _dict_row(cur, cur.fetchone())

        # 2. 逐章节逐段落写入
        for sec_idx, sec in enumerate(sections):
            sec_title = sec.get("title", f"第{sec_idx + 1}节")
            paras = sec.get("paragraphs", [])

            for para_idx, para in enumerate(paras):
                para_html = para.get("html", "")
                para_text = para.get("text", "")
                char_count = para.get("char_count", 0)
                skip_translate = para.get("skip_translate", False)

                # 写入 MinIO（失败则静默跳过，DB 有回退）
                orig_key = _para_orig_key(doc_id, sec_idx, para_idx)
                try:
                    minio_put(
                        f"{READER_BUCKET}/{orig_key}",
                        para_html.encode("utf-8"),
                        "text/html; charset=utf-8",
                    )
                except Exception:
                    orig_key = ""  # MinIO 不可用，标记为空

                # 插入段落记录（html_content 作为 DB 回退）
                cur.execute("""
                    INSERT INTO sections
                        (document_id, sec_index, paragraph_index, title,
                         html_content_key, html_content, text_content,
                         skip_translate, status, char_count)
                    VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    doc_id, sec_idx, para_idx, sec_title,
                    orig_key, para_html, para_text,
                    skip_translate,
                    "skip" if skip_translate else "wait",
                    char_count,
                ))

                total_chars += char_count
                total_paragraphs += 1

            total_sections += 1

        # 3. 更新文档统计
        cur.execute("""
            UPDATE documents SET
                total_chars = %s,
                total_sections = %s,
                updated_at = NOW()
            WHERE id = %s::uuid
        """, (total_chars, total_sections, doc_id))

        doc["total_chars"] = total_chars
        doc["total_sections"] = total_sections
        doc["total_paragraphs"] = total_paragraphs
        doc["sections_done"] = 0

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    return doc


# ══════════════════════════════════════════════════════════════
# 读取段落
# ══════════════════════════════════════════════════════════════

def get_sections_with_content(doc_id: str, user_id: int) -> list[dict]:
    """获取文档的所有章节和段落内容（从 MinIO 读取原文 HTML）

    返回:
    [{
        "sec_index": int,
        "title": str,
        "paragraphs": [{
            "id": str,
            "para_index": int,
            "orig_html": str,       # 从 MinIO 读取
            "trans_html": str,      # 从 MinIO 读取（可能为空）
            "skip_translate": bool,
            "status": str,          # wait | done | error | skip
            "char_count": int,
        }]
    }, ...]
    """
    conn = get_conn_sync()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # 先验证文档归属
        cur.execute(
            "SELECT id FROM documents WHERE id = %s::uuid AND user_id = %s",
            (doc_id, user_id),
        )
        if not cur.fetchone():
            return None

        # 获取所有段落
        cur.execute("""
            SELECT id, sec_index, paragraph_index, title,
                   html_content_key, translated_html_key,
                   skip_translate, status, char_count
            FROM sections
            WHERE document_id = %s::uuid
            ORDER BY sec_index, paragraph_index
        """, (doc_id,))

        rows = cur.fetchall()
        if not rows:
            return []

        # 分组为章节
        sections_map = {}
        for row in rows:
            sec_i = row["sec_index"]
            if sec_i not in sections_map:
                sections_map[sec_i] = {
                    "sec_index": sec_i,
                    "title": row["title"],
                    "paragraphs": [],
                }

            # 从 MinIO 读取原文 HTML（失败则回退到 DB）
            orig_key = row["html_content_key"] or ""
            orig_html = row.get("html_content") or ""
            if orig_key and not orig_html:
                try:
                    data = minio_get(f"{READER_BUCKET}/{orig_key}")
                    if data:
                        orig_html = data[0].decode("utf-8", errors="replace")
                except Exception:
                    pass

            # 从 MinIO 读取译文 HTML（失败则回退到 DB）
            trans_key = row["translated_html_key"] or ""
            trans_html = row.get("translated_html") or ""
            if trans_key and not trans_html:
                try:
                    data = minio_get(f"{READER_BUCKET}/{trans_key}")
                    if data:
                        trans_html = data[0].decode("utf-8", errors="replace")
                except Exception:
                    pass

            sections_map[sec_i]["paragraphs"].append({
                "id": str(row["id"]),
                "para_index": row["paragraph_index"],
                "orig_html": orig_html,
                "trans_html": trans_html,
                "skip_translate": row["skip_translate"],
                "status": row["status"],
                "char_count": row["char_count"],
            })

        return [sections_map[k] for k in sorted(sections_map.keys())]

    finally:
        cur.close()
        conn.close()


# ══════════════════════════════════════════════════════════════
# 翻译管线（带缓存）
# ══════════════════════════════════════════════════════════════

async def translate_paragraph(
    section_id: str,
    orig_html: str,
    text_content: str,
    api_key: str,
    base_url: str,
    model: str,
    lang: str = "中文",
) -> str:
    """翻译单个段落。返回翻译后的 HTML（保留标签）"""
    from core.llm import chat_async
    from core.config import ModelConfig

    system_prompt = f"""你是一个专业的技术文档翻译助手。请将以下 HTML 内容翻译成{lang}。

规则：
1. 只翻译标签之间的文本内容，**保留所有 HTML 标签不变**
2. 代码块内容（<pre> 和 <code> 内部）不翻译，保持原样
3. 技术术语首次出现时在括号内保留英文
4. 长句拆短句，保持段落结构
5. 链接 <a> 的 href 属性不修改，只翻译显示文本
6. 列表 <li>、表格 <td>/<th> 各自独立翻译

返回完整的 HTML 内容，标签结构必须与原文完全一致。"""

    cfg = ModelConfig(
        name="translate",
        api_key=api_key,
        base_url=base_url,
        model=model,
        description="翻译模型",
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"翻译以下 HTML（保持所有标签不变）：\n\n{orig_html}"},
    ]

    resp = await chat_async(messages=messages, model_cfg=cfg, temperature=0.3)
    return resp.content


async def translate_document(
    doc_id: str,
    user_id: int,
    api_key: str,
    base_url: str,
    model: str,
    lang: str = "中文",
) -> int:
    """翻译文档中所有未翻译的段落（带缓存：跳过已翻译的）。

    返回本次翻译的段落数。"""
    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # 验证归属
        cur.execute(
            "SELECT id FROM documents WHERE id = %s::uuid AND user_id = %s",
            (doc_id, user_id),
        )
        if not cur.fetchone():
            raise ValueError("文档不存在或无权限")

        # 获取所有待翻译段落
        cur.execute("""
            SELECT id, sec_index, paragraph_index, html_content_key,
                   text_content, char_count, skip_translate
            FROM sections
            WHERE document_id = %s::uuid AND status = 'wait'
            ORDER BY sec_index, paragraph_index
        """, (doc_id,))

        pending = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    if not pending:
        return 0

    translated_count = 0
    for row in pending:
        sec_id = str(row["id"])
        html_key = row["html_content_key"]

        # 从 MinIO 读取原文
        if not html_key:
            orig_html = row.get("html_content") or ""
            if not orig_html:
                continue
        else:
            try:
                data = minio_get(f"{READER_BUCKET}/{html_key}")
                orig_html = data[0].decode("utf-8", errors="replace") if data else (row.get("html_content") or "")
            except Exception:
                orig_html = row.get("html_content") or ""
            if not orig_html:
                continue

        try:
            # 调用 LLM 翻译
            trans_html = await translate_paragraph(
                sec_id, orig_html,
                row["text_content"],
                api_key, base_url, model, lang,
            )

            # 写入 MinIO
            trans_key = _para_trans_key(doc_id, row["sec_index"], row["paragraph_index"])
            minio_put(
                f"{READER_BUCKET}/{trans_key}",
                trans_html.encode("utf-8"),
                "text/html; charset=utf-8",
            )

            # 更新 DB
            conn2 = get_conn_sync()
            conn2.autocommit = True
            cur2 = conn2.cursor()
            try:
                cur2.execute("""
                    UPDATE sections SET
                        translated_html_key = %s,
                        status = 'done'
                    WHERE id = %s::uuid AND status = 'wait'
                """, (trans_key, sec_id))
            finally:
                cur2.close()
                conn2.close()

            translated_count += 1

        except Exception as e:
            # 标记为错误
            conn2 = get_conn_sync()
            conn2.autocommit = True
            cur2 = conn2.cursor()
            try:
                cur2.execute(
                    "UPDATE sections SET status = 'error' WHERE id = %s::uuid",
                    (sec_id,),
                )
            finally:
                cur2.close()
                conn2.close()
            print(f"[Reader] 段落 {sec_id} 翻译失败: {e}")

    # 更新文档翻译进度
    conn3 = get_conn_sync()
    conn3.autocommit = True
    cur3 = conn3.cursor()
    try:
        cur3.execute("""
            UPDATE documents SET
                sections_done = (
                    SELECT COUNT(*) FROM sections
                    WHERE document_id = %s::uuid AND status = 'done'
                ),
                status = CASE
                    WHEN (SELECT COUNT(*) FROM sections WHERE document_id = %s::uuid AND status = 'wait') = 0
                    THEN 'complete'
                    ELSE 'partial'
                END,
                updated_at = NOW()
            WHERE id = %s::uuid
        """, (doc_id, doc_id, doc_id))
    finally:
        cur3.close()
        conn3.close()

    return translated_count


def get_translation_progress(doc_id: str, user_id: int) -> dict:
    """获取文档翻译进度"""
    conn = get_conn_sync()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'done') AS done,
                COUNT(*) FILTER (WHERE status = 'wait') AS pending,
                COUNT(*) FILTER (WHERE status = 'error') AS errors,
                COUNT(*) FILTER (WHERE status = 'skip') AS skipped
            FROM sections
            WHERE document_id = %s::uuid
        """, (doc_id,))
        row = cur.fetchone()
        return _dict_row(cur, row) if row else {"total": 0, "done": 0, "pending": 0, "errors": 0, "skipped": 0}
    finally:
        cur.close()
        conn.close()
