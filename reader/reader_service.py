"""
Reader: 服务层 — 书架/文档 CRUD + 翻译管线 + MinIO 持久化
=========================================================
"""
import asyncio
import hashlib
import httpx
import json
import re
import uuid as uuid_mod
from datetime import UTC, datetime
from typing import Any

import psycopg2
import psycopg2.extras

from core.db import get_conn_sync
from core.storage import minio_get, minio_put
from .content_parser import analyze_url as _analyze_url

# ── MinIO 配置 ────────────────────────────────────────────────
from core.storage import MINIO_BUCKET


def _ensure_bucket():
    """确保 reader bucket 存在（不存在则创建）"""
    from core.storage import MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY
    import urllib.request
    try:
        req = urllib.request.Request(f"{MINIO_ENDPOINT}/{MINIO_BUCKET}/reader/", method="PUT", data=b"")
        req.add_header("User-Agent", "MiniAgent/1.0")
        # 用同样的 AWS4 签名
        from core.storage import _aws4_sign
        headers = _aws4_sign("PUT", f"{MINIO_BUCKET}/reader/", b"", "application/octet-stream")
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


def _chapter_html_key(doc_id: str, sec_i: int) -> str:
    """整章 HTML 内容的 MinIO key"""
    return f"{doc_id}/ch{sec_i}.html"


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
    """删除文档（级联删除段落 + MinIO 内容）"""
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

        # 获取文档图片路径
        cur.execute(
            "SELECT image_path FROM reader_images WHERE document_id = %s::uuid",
            (doc_id,),
        )
        img_paths = [r["image_path"] for r in cur.fetchall() if r.get("image_path")]

        # 删除文档（级联删除段落）
        cur.execute(
            "DELETE FROM documents WHERE id = %s::uuid AND user_id = %s",
            (doc_id, user_id),
        )
        deleted = cur.rowcount > 0

        # 清理 MinIO 中的内容（忽略失败）
        if deleted:
            from core.storage import minio_delete
            # 清理段落/章节 HTML
            for orig_key, trans_key in keys:
                try:
                    if orig_key:
                        minio_delete(f"{MINIO_BUCKET}/reader/{orig_key}")
                    if trans_key:
                        minio_delete(f"{MINIO_BUCKET}/reader/{trans_key}")
                except Exception:
                    pass

            # 清理图片
            for img_path in img_paths:
                try:
                    minio_delete(img_path)
                except Exception:
                    pass

            # 清理图片记录
            try:
                cur.execute("DELETE FROM reader_images WHERE document_id = %s::uuid", (doc_id,))
            except Exception:
                pass

        return deleted
    finally:
        cur.close()
        conn.close()


# ══════════════════════════════════════════════════════════════
# 图片 -> MinIO
# ══════════════════════════════════════════════════════════════

_IMAGE_EXT = re.compile(r"\.(jpg|jpeg|png|gif|webp|svg|bmp|ico)(\?|$)", re.I)

def _image_filename(url: str, idx: int) -> str:
    """生成唯一图片文件名: MD5(URL) 保留后缀"""
    ext = "jpg"
    m = _IMAGE_EXT.search(url)
    if m:
        ext = m.group(1).lower()
    h = hashlib.md5(url.encode()).hexdigest()[:12]
    return f"{h}.{ext}"

async def _download_and_store_images(sections: list[dict], doc_id: str):
    """扫描段落 HTML 中的所有图片，下载到 MinIO，替换 src 为本地路径"""
    img_map = {}
    img_urls = []
    for sec in sections:
        for para in sec.get("paragraphs", []):
            for key in ("html", "orig_html", "trans_html"):
                html = para.get(key, "")
                if not html:
                    continue
                for match in re.finditer(r'<img[^>]+src=(["\'])(.+?)\1', html):
                    url = match.group(2)
                    if url not in img_map and not url.startswith("data:"):
                        img_map[url] = ""
                        img_urls.append(url)

    if not img_urls:
        return

    sem = asyncio.Semaphore(5)
    async def fetch_one(url: str) -> tuple[str, bytes | None]:
        async with sem:
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
                    resp = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
                    if resp.status_code == 200 and resp.content:
                        return url, resp.content
            except Exception:
                pass
            return url, None

    tasks = [fetch_one(url) for url in img_urls]
    results = await asyncio.gather(*tasks)

    for url, data in results:
        if data is None:
            continue
        idx = img_urls.index(url)
        filename = _image_filename(url, idx)
        minio_path = f"{MINIO_BUCKET}/reader/images/{doc_id}/{filename}"
        try:
            ext = filename.rsplit(".", 1)[-1].lower()
            ct_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                      "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
                      "bmp": "image/bmp", "ico": "image/x-icon"}
            ct = ct_map.get(ext, "image/jpeg")
            ok = minio_put(minio_path, data, ct)
            if ok:
                img_map[url] = f"/api/reader/images/{doc_id}/{filename}"
                # 记录图片路径到 DB，用于删除时清理
                try:
                    from core.db import get_conn_sync as _get_img_conn
                    _ic = _get_img_conn()
                    _icur = _ic.cursor()
                    _icur.execute(
                        "INSERT INTO reader_images (document_id, image_path) VALUES (%s::uuid, %s) ON CONFLICT DO NOTHING",
                        (doc_id, minio_path)
                    )
                    _ic.commit()
                    _icur.close()
                    _ic.close()
                except Exception:
                    pass
        except Exception:
            pass

    for sec in sections:
        for para in sec.get("paragraphs", []):
            for key in ("html", "orig_html", "trans_html"):
                html = para.get(key, "")
                if not html:
                    continue
                for orig_url, local_path in img_map.items():
                    if not local_path:
                        continue
                    html = html.replace(f'src="{orig_url}"', f'src="{local_path}"')
                    html = html.replace(f"src='{orig_url}'", f"src='{local_path}'")
                para[key] = html


# ══════════════════════════════════════════════════════════════
# 导入分析
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

        # ── 检测是否有章节 ──
        detected_chapters = result.get("detected_chapters", [])
        has_chapters = bool(detected_chapters)

        if not has_chapters:
            # 无章节：正常存储主页内容
            await _download_and_store_images(sections, doc_id)

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
                            f"{MINIO_BUCKET}/reader/{orig_key}",
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
        doc["detected_chapters"] = result.get("detected_chapters", [])

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    return doc


# ══════════════════════════════════════════════════════════════
# 合并章节
# ══════════════════════════════════════════════════════════════

async def merge_to_document(doc_id: str, user_id: int, url: str) -> dict:
    """将新URL的内容作为新章节合并到已有文档中"""
    from reader.content_parser import analyze_url

    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # 1. 验证文档存在且属于该用户
        cur.execute(
            "SELECT * FROM documents WHERE id = %s::uuid AND user_id = %s",
            (doc_id, user_id),
        )
        doc = cur.fetchone()
        if not doc:
            raise ValueError("文档不存在或无权限")

        # 2. 解析新 URL
        result = await analyze_url(url)
        new_sections = result.get("sections", [])
        if not new_sections:
            raise ValueError("未能从该URL提取到任何内容")

        page_title = result.get("title", url.split("/")[-1])

        # 3. 获取当前文档已有最大 sec_index
        cur.execute(
            "SELECT COALESCE(MAX(sec_index), -1) + 1 AS next_sec FROM sections WHERE document_id = %s::uuid",
            (doc_id,),
        )
        next_sec = (cur.fetchone() or {})["next_sec"]

        # 4. 获取当前文档最大 para_index（跨所有章节）
        cur.execute(
            "SELECT COALESCE(MAX(paragraph_index), -1) + 1 AS next_para FROM sections WHERE document_id = %s::uuid",
            (doc_id,),
        )
        next_para = (cur.fetchone() or {})["next_para"]

        added_chars = 0
        added_paragraphs = 0
        added_sections = 0

        # 下载新章节的图片到 MinIO
        await _download_and_store_images(new_sections, doc_id)

        # 5. 写入新章节/段落
        for sec_idx_offset, sec in enumerate(new_sections):
            sec_title = sec.get("title", f"第{next_sec + sec_idx_offset + 1}节")
            paras = sec.get("paragraphs", [])
            cur_sec_idx = next_sec + sec_idx_offset

            for para_offset, para in enumerate(paras):
                para_html = para.get("html", "")
                para_text = para.get("text", "")
                char_count = para.get("char_count", 0)
                skip_translate = para.get("skip_translate", False)
                cur_para_idx = next_para + added_paragraphs + para_offset

                # 写入 MinIO（失败则静默跳过，DB有回退）
                orig_key = _para_orig_key(doc_id, cur_sec_idx, cur_para_idx)
                try:
                    from core.storage import minio_put
                    minio_put(
                        f"reader/{orig_key}",
                        para_html.encode("utf-8"),
                        "text/html; charset=utf-8",
                    )
                except Exception:
                    orig_key = ""

                # 插入段落记录
                cur.execute("""
                    INSERT INTO sections
                        (document_id, sec_index, paragraph_index, title,
                         html_content_key, html_content, text_content,
                         skip_translate, status, char_count)
                    VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    doc_id, cur_sec_idx, cur_para_idx, sec_title,
                    orig_key, para_html, para_text,
                    skip_translate,
                    "skip" if skip_translate else "wait",
                    char_count,
                ))

                added_chars += char_count
                added_paragraphs += 1

            added_sections += 1

        # 6. 更新文档统计
        new_total_chars = (doc["total_chars"] or 0) + added_chars
        new_total_sections = (doc["total_sections"] or 0) + added_sections
        cur.execute("""
            UPDATE documents SET
                total_chars = %s,
                total_sections = %s,
                updated_at = NOW()
            WHERE id = %s::uuid
        """, (new_total_chars, new_total_sections, doc_id))

        return {
            "doc_id": doc_id,
            "title": doc["title"],
            "added_sections": added_sections,
            "added_paragraphs": added_paragraphs,
            "added_chars": added_chars,
            "new_total_sections": new_total_sections,
            "new_total_paragraphs": (doc.get("total_paragraphs") or 0) + added_paragraphs,
            "new_total_chars": new_total_chars,
            "merged_title": page_title,
        }

    except ValueError:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# ══════════════════════════════════════════════════════════════
# 批量导入章节
# ══════════════════════════════════════════════════════════════

async def import_chapters(
    doc_id: str, user_id: int, chapters: list[dict], mode: str = "merge"
) -> dict:
    """批量导入章节 URL 到文档。

    mode='merge'      → 每个 URL 作为新 section 追加到同一个文档（全量加载）
    mode='structure'  → 只保存章节元数据（标题+URL），不抓取内容，按需加载
    mode='separate'   → 每个 URL 作为独立文档（放在同个书架）

    返回: {mode, total, succeeded, failed: [{url, error}], results: [...]}
    """
    if mode == "merge":
        return await _import_chapters_merge(doc_id, user_id, chapters)
    elif mode == "structure":
        return _import_chapters_structure(doc_id, chapters)
    else:
        return await _import_chapters_separate(doc_id, user_id, chapters)


def _import_chapters_structure(doc_id: str, chapters: list[dict]) -> dict:
    """结构模式：只保存章节标题和 URL，不抓取内容"""
    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT COALESCE(MAX(sec_index), -1) + 1 AS next_sec FROM sections WHERE document_id = %s::uuid",
            (doc_id,))
        row = cur.fetchone()
        next_sec = row["next_sec"] if row else 0

        succeeded = 0
        results = []
        for ch in chapters:
            title = ch.get("title", "")
            url = ch.get("url", "")
            sec_idx = next_sec
            next_sec += 1

            cur.execute("""
                INSERT INTO sections
                    (document_id, sec_index, paragraph_index, title,
                     html_content_key, text_content, source_url,
                     skip_translate, status, char_count)
                VALUES (%s::uuid, %s, 0, %s, '', '', %s, false, 'pending', 0)
            """, (doc_id, sec_idx, title, url))

            # 更新文档统计
            cur.execute("""
                UPDATE documents SET
                    total_sections = COALESCE(total_sections, 0) + 1,
                    updated_at = NOW()
                WHERE id = %s::uuid
            """, (doc_id,))

            succeeded += 1
            results.append({
                "title": title, "url": url, "status": "ok",
                "sec_index": sec_idx, "char_count": 0,
            })

        return {
            "mode": "structure",
            "doc_id": doc_id,
            "total": len(chapters),
            "succeeded": succeeded,
            "failed": [],
            "results": results,
        }
    finally:
        cur.close()
        conn.close()


async def lazy_fetch_chapter(doc_id: str, sec_index: int) -> dict:
    """懒加载章节：获取 pending 章节的内容，存储到 MinIO，更新 DB

    返回: {title, source_url, html, status}
    """
    from reader.content_parser import fetch_url
    from lxml import html as lxml_html, etree
    from core.storage import minio_put

    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT id, title, source_url, html_content_key, status
            FROM sections
            WHERE document_id = %s::uuid AND sec_index = %s
        """, (doc_id, sec_index))
        row = cur.fetchone()
        if not row:
            return {"error": "章节不存在"}

        # 如果已经有内容，直接返回
        if row["html_content_key"] or row.get("status") == "done":
            html_key = row["html_content_key"] or ""
            if html_key:
                from core.storage import minio_get
                data = minio_get(f"{MINIO_BUCKET}/reader/{html_key}")
                if data:
                    return {
                        "title": row["title"],
                        "source_url": row.get("source_url") or "",
                        "html": data[0].decode("utf-8", errors="replace"),
                        "status": "done",
                    }
            return {"error": "章节内容不存在"}

        # 抓取内容
        source_url = row.get("source_url") or ""
        if not source_url:
            return {"error": "章节没有来源 URL"}

        html_text = await fetch_url(source_url)
        doc = lxml_html.fromstring(html_text)

        from reader.content_parser import _find_main
        main_el = _find_main(doc)
        if main_el is None:
            raise ValueError("未能提取到内容")

        chapter_html = etree.tostring(main_el, encoding="unicode", method="html")
        chapter_html = re.sub(r"\n\s*\n", "\n", chapter_html).strip()
        char_count = len(chapter_html)

        # 存 MinIO
        minio_key = _chapter_html_key(doc_id, sec_index)
        minio_put(
            f"{MINIO_BUCKET}/reader/{minio_key}",
            chapter_html.encode("utf-8"),
            "text/html; charset=utf-8",
        )

        # 更新 DB
        cur.execute("""
            UPDATE sections SET
                html_content_key = %s,
                char_count = %s,
                status = 'imported'
            WHERE id = %s
        """, (minio_key, char_count, row["id"]))

        cur.execute("""
            UPDATE documents SET
                total_chars = COALESCE(total_chars, 0) + %s,
                updated_at = NOW()
            WHERE id = %s::uuid
        """, (char_count, doc_id))

        return {
            "title": row["title"],
            "source_url": source_url,
            "html": chapter_html,
            "status": "imported",
            "sec_index": sec_index,
            "char_count": char_count,
        }
    except Exception as e:
        return {"error": f"懒加载章节失败: {e}"}
    finally:
        cur.close()
        conn.close()


async def _import_chapters_merge(doc_id: str, user_id: int, chapters: list[dict]) -> dict:
    """合并模式：每章内容存为单个 MinIO HTML blob，DB 只存元数据"""
    from reader.content_parser import fetch_url
    from lxml import html as lxml_html, etree
    from core.storage import minio_put

    total = len(chapters)
    succeeded = 0
    failed = []
    results = []

    conn = get_conn_sync()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT COALESCE(MAX(sec_index), -1) + 1 AS next_sec FROM sections WHERE document_id = %s::uuid",
            (doc_id,))
        row = cur.fetchone()
        next_sec = row["next_sec"] if row else 0

        for ch in chapters:
            title = ch.get("title", "")
            url = ch.get("url", "")
            sec_idx = next_sec
            next_sec += 1

            try:
                html_text = await fetch_url(url)
                doc = lxml_html.fromstring(html_text)

                # 提取主内容
                from reader.content_parser import _find_main
                main_el = _find_main(doc)
                if main_el is None:
                    raise ValueError("未能提取到内容")

                # 序列化为完整 HTML
                chapter_html = etree.tostring(main_el, encoding="unicode", method="html")
                chapter_html = re.sub(r"\n\s*\n", "\n", chapter_html).strip()
                char_count = len(chapter_html)

                # 存 MinIO
                minio_key = _chapter_html_key(doc_id, sec_idx)
                minio_put(
                    f"{MINIO_BUCKET}/reader/{minio_key}",
                    chapter_html.encode("utf-8"),
                    "text/html; charset=utf-8",
                )

                # 存 DB（一行一节）
                cur.execute("""
                    INSERT INTO sections
                        (document_id, sec_index, paragraph_index, title,
                         html_content_key, text_content, source_url,
                         skip_translate, status, char_count)
                    VALUES (%s::uuid, %s, 0, %s, %s, %s, %s, false, %s, %s)
                """, (doc_id, sec_idx, title,
                      minio_key, title, url,
                      'imported', char_count))

                # 更新文档统计
                cur.execute("""
                    UPDATE documents SET
                        total_chars = COALESCE(total_chars, 0) + %s,
                        total_sections = COALESCE(total_sections, 0) + 1,
                        updated_at = NOW()
                    WHERE id = %s::uuid
                """, (char_count, doc_id))

                succeeded += 1
                results.append({
                    "title": title, "url": url, "status": "ok",
                    "sec_index": sec_idx, "char_count": char_count,
                })

            except Exception as e:
                failed.append({"title": title, "url": url, "error": str(e)})
                results.append({"title": title, "url": url, "status": "error", "error": str(e)})

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    return {
        "mode": "merge",
        "doc_id": doc_id,
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }


async def _import_chapters_separate(doc_id: str, user_id: int, chapters: list[dict]) -> dict:
    """独立模式：每个章节作为独立文档"""
    # 获取原始文档的书架 ID
    conn = get_conn_sync()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT collection_id FROM documents WHERE id = %s::uuid AND user_id = %s",
            (doc_id, user_id),
        )
        doc = cur.fetchone()
        collection_id = doc["collection_id"] if doc else None
        cur.close()
    finally:
        conn.close()

    total = len(chapters)
    succeeded = 0
    failed = []
    results = []

    for ch in chapters:
        title = ch.get("title", "")
        url = ch.get("url", "")
        try:
            doc_result = await analyze_and_store(url, user_id, collection_id)
            succeeded += 1
            results.append({
                "title": title,
                "url": url,
                "status": "ok",
                "doc_id": doc_result.get("id"),
            })
        except Exception as e:
            failed.append({"title": title, "url": url, "error": str(e)})
            results.append({"title": title, "url": url, "status": "error", "error": str(e)})

    return {
        "mode": "separate",
        "collection_id": collection_id,
        "doc_id": doc_id,
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }


# ══════════════════════════════════════════════════════════════
# 文档章节列表（侧边栏用）
# ══════════════════════════════════════════════════════════════

def get_document_chapters(doc_id: str, user_id: int) -> list[dict]:
    """获取文档的章节列表，用于侧边栏展示"""
    conn = get_conn_sync()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT id FROM documents WHERE id = %s::uuid AND user_id = %s",
            (doc_id, user_id),
        )
        if not cur.fetchone():
            return {"error": "文档不存在或无权限", "chapters": []}

        cur.execute("""
            SELECT sec_index, title, source_url, html_content_key, status, char_count
            FROM sections
            WHERE document_id = %s::uuid
            ORDER BY sec_index
        """, (doc_id,))
        rows = cur.fetchall()

        chapters = []
        for row in rows:
            has_content = bool(row["html_content_key"]) or row.get("status") == "done"
            chapters.append({
                "sec_index": row["sec_index"],
                "title": row.get("title") or f"章节 {row['sec_index'] + 1}",
                "source_url": row.get("source_url") or "",
                "has_content": has_content,
                "status": row.get("status", "pending"),
                "char_count": row.get("char_count", 0),
            })

        return {"chapters": chapters}
    finally:
        cur.close()
        conn.close()


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
        # 验证文档归属 + 获取 URL
        cur.execute(
            "SELECT id, url FROM documents WHERE id = %s::uuid AND user_id = %s",
            (doc_id, user_id),
        )
        doc_row = cur.fetchone()
        if not doc_row:
            return None
        doc_url = doc_row["url"] or ""

        # 获取所有段落
        cur.execute("""
            SELECT id, sec_index, paragraph_index, title,
                   html_content_key, translated_html_key,
                   skip_translate, status, char_count, source_url
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
                    "source_url": row.get("source_url") or "",
                    "chapter_mode": bool(row.get("source_url")),
                    "paragraphs": [],
                }
            # 如果 source_url 非空，这是整章 HTML（段落级 != 0 的段落有数据）

            # 从 MinIO 读取原文 HTML（失败则回退到 DB）
            orig_key = row["html_content_key"] or ""
            orig_html = row.get("html_content") or ""
            if orig_key and not orig_html:
                try:
                    data = minio_get(f"{MINIO_BUCKET}/reader/{orig_key}")
                    if data:
                        orig_html = data[0].decode("utf-8", errors="replace")
                except Exception:
                    pass

            # 从 MinIO 读取译文 HTML（失败则回退到 DB）
            trans_key = row["translated_html_key"] or ""
            trans_html = row.get("translated_html") or ""
            if trans_key and not trans_html:
                try:
                    data = minio_get(f"{MINIO_BUCKET}/reader/{trans_key}")
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

            # 如果这是整章 HTML（单段落保存完整内容），直接用段落原文
            # 否则合并该章节所有段落原文为连续 HTML
            sec = sections_map[sec_i]
            if sec["chapter_mode"] and len(sec["paragraphs"]) == 1:
                sec["merged_orig_html"] = sec["paragraphs"][0]["orig_html"]
                sec["merged_trans_html"] = sec["paragraphs"][0].get("trans_html", "") if sec["paragraphs"][0].get("trans_html") else ""
            else:
                merged_orig = []
                merged_trans = []
                for p in sec["paragraphs"]:
                    if p["orig_html"]:
                        merged_orig.append(p["orig_html"])
                    if p.get("trans_html"):
                        merged_trans.append(p["trans_html"])
                sec["merged_orig_html"] = "\n".join(merged_orig)
                sec["merged_trans_html"] = "\n".join(merged_trans)

        # 解析所有相对 URL（兼容旧数据 + 二次保护）
        if doc_url:
            from reader.content_parser import _resolve_urls
            result = [sections_map[k] for k in sorted(sections_map.keys())]
            _resolve_urls(result, doc_url)
        else:
            result = [sections_map[k] for k in sorted(sections_map.keys())]
        return result

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
    engine: str = "tencent",
    tencent_id: str = "",
    tencent_key: str = "",
) -> str:
    """翻译单个段落。返回翻译后的 HTML（保留标签）

    引擎选择：
      - "tencent"（默认）→ 腾讯翻译 API，失败时 LLM 兜底
      - "llm"          → 直接走大模型翻译（HTML 感知）
    """
    # Tencent 引擎：剥离 HTML 翻译纯文本
    if engine == "tencent" and tencent_id and tencent_key and text_content:
        from tools.tencent_translate import translate_text as tmt_translate
        import re

        # 剥离 HTML 标签，取纯文本
        plain = re.sub(r"<[^>]+>", "", text_content).strip()
        if plain:
            target_lang = "zh" if "中文" in lang else lang
            result = await tmt_translate(plain, tencent_id, tencent_key, target=target_lang)
            if result is not None:
                # 用 <p> 包裹（保留段落结构）
                paras = result.split("\n")
                return "".join(f"<p>{p}</p>" for p in paras if p.strip())

    # LLM 引擎（默认或兜底）— HTML 感知翻译
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
                data = minio_get(f"{MINIO_BUCKET}/reader/{html_key}")
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
                f"{MINIO_BUCKET}/reader/{trans_key}",
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
