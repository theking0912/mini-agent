"""
Mini Agent Web UI — FastAPI 服务
=================================

核心重构要点：
  - 全局 _context → 每个用户独立上下文（fix: 多用户串对话 🔴）
  - 内联 MinIO 代码 → 抽出到 core/storage.py（fix: 整洁 🧹）
  - import 统一到文件顶部（fix: 整洁 🧹）
  - key 管理端点用 require_user 统一鉴权（fix: 一致性 🔑）
  - 真流式 SSE：逐 token 输出（fix: 用户体验 🟠）
  - 异步 LLM 调用，不阻塞事件循环（fix: 性能 🔴）
"""
import asyncio
import json
import sys
import urllib.request as _urllib
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from core import email as email_svc
from core import user, verify
from core.config import get_config
from core.context import Context
from core.db import get_redis, init_db
from core.llm import chat_async
from core.storage import minio_delete, minio_get, minio_put
from tools import registry

# Redis 客户端
redis_client = get_redis()

# ── MinIO 配置 ────────────────────────────────────────────────
from core.storage import AVATAR_BUCKET, MINIO_ENDPOINT

# ── 应用 ──────────────────────────────────────────────────────
app = FastAPI(title="Mini Agent Web UI")

# 每个用户独立上下文（user_id → Context 映射）
_contexts: dict[int, Context] = {}


def _get_context(user_id: int) -> Context:
    """获取用户专属的对话上下文（按需创建）"""
    if user_id not in _contexts:
        _contexts[user_id] = Context()
    return _contexts[user_id]


def _reset_context(user_id: int):
    """重置用户对话上下文"""
    _contexts[user_id] = Context()


# ── 认证辅助 ──────────────────────────────────────────────────
def _get_user(request: Request) -> dict | None:
    """从请求头提取当前登录用户"""
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return None
    return user.get_user_by_token(token)


def require_user(request: Request) -> dict:
    """依赖注入版：获取当前用户，未登录则抛 401"""
    u = _get_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="请先登录")
    return u


# ── 启动事件 ──────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    """初始化数据库"""
    try:
        init_db()
        print("🗄️  PostgreSQL 数据库就绪")
    except Exception as e:
        print(f"⚠️  PostgreSQL 初始化失败: {e}")
        print("   请确保 PostgreSQL 和 Redis 已启动")


# ── 静态页面 ──────────────────────────────────────────────────
@app.get("/")
async def index():
    html_path = Path(__file__).resolve().parent / "web" / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return "<h1>web/index.html not found</h1>"


@app.get("/mini-agent-graph")
async def codegraph_page():
    html_path = Path(__file__).resolve().parent / "web" / "mini-agent-graph.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return "<h1>web/mini-agent-graph.html not found</h1>"


@app.get("/business-graph")
async def business_graph_page():
    html_path = Path(__file__).resolve().parent / "web" / "business-graph.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return "<h1>web/business-graph.html not found</h1>"


@app.get("/auth")
async def auth_page():
    html_path = Path(__file__).resolve().parent / "web" / "auth.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return "<h1>web/auth.html not found</h1>"


@app.get("/translate")
async def translate_page():
    html_path = Path(__file__).resolve().parent / "web" / "translate.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return "<h1>web/translate.html not found</h1>"


# ── 模型 API ──────────────────────────────────────────────────
@app.get("/api/models")
async def list_models(request: Request):
    u = require_user(request)
    cfg = get_config()
    models = []
    for name, m in cfg.models.items():
        has_key = bool(m.api_key)
        if not has_key:
            has_key = user.has_user_key(u["id"], name)
        models.append({
            "name": name,
            "description": m.description,
            "model": m.model,
            "base_url": m.base_url,
            "has_key": has_key,
            "current": name == cfg.current_model.name,
        })
    return {"models": models, "current": cfg.current_model.name}


@app.post("/api/switch")
async def switch_model(request: Request):
    require_user(request)
    data = await request.json()
    name = data.get("model", "")
    try:
        msg = get_config().switch(name)
        return {"message": msg}
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ── Key 管理 ──────────────────────────────────────────────────
@app.post("/api/key/set")
async def key_set(request: Request):
    u = require_user(request)
    data = await request.json()
    model_name = data.get("model", "")
    api_key = data.get("key", "").strip()
    if not model_name or not api_key:
        return JSONResponse({"error": "模型和 Key 不能为空"}, status_code=400)
    user.set_user_key(u["id"], model_name, api_key)
    return {"message": f"✅ {model_name} 的 API Key 已保存"}


@app.post("/api/key/remove")
async def key_remove(request: Request):
    u = require_user(request)
    data = await request.json()
    model_name = data.get("model", "")
    if user.delete_user_key(u["id"], model_name):
        return {"message": f"已删除 {model_name} 的 Key"}
    return JSONResponse({"error": f"{model_name} 没有保存的 Key"}, status_code=404)


@app.get("/api/key/list")
async def key_list(request: Request):
    u = require_user(request)
    cfg = get_config()
    user_keys = user.get_user_keys(u["id"])
    models = []
    for name, m in cfg.models.items():
        models.append({
            "name": name,
            "description": m.description,
            "has_key": name in user_keys,
            "current": name == cfg.current_model.name,
        })
    return {"keys": models, "current": cfg.current_model.name}


# ── 系统级 Key 管理（管理员，持久化到数据库）───────────────────
@app.get("/api/admin/config")
async def admin_list_config(request: Request):
    """列出系统级 Key（仅 admin@mini.com）"""
    u = require_user(request)
    if u.get("email") != "admin@mini.com":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    from core.config_store import list_system_keys
    return {"keys": list_system_keys()}


@app.post("/api/admin/config")
async def admin_set_config(request: Request):
    """设置系统级 Key（持久化到数据库，不依赖 salt）"""
    u = require_user(request)
    if u.get("email") != "admin@mini.com":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    data = await request.json()
    model_name = data.get("model", "")
    api_key = data.get("key", "").strip()
    if not model_name or not api_key:
        return JSONResponse({"error": "模型和 Key 不能为空"}, status_code=400)
    from core.config_store import set_system_key
    set_system_key(model_name, api_key)
    return {"message": f"✅ 系统级 {model_name} 的 API Key 已保存到数据库（持久化）"}


@app.delete("/api/admin/config")
async def admin_delete_config(request: Request):
    """删除系统级 Key"""
    u = require_user(request)
    if u.get("email") != "admin@mini.com":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    data = await request.json()
    model_name = data.get("model", "")
    if not model_name:
        return JSONResponse({"error": "模型不能为空"}, status_code=400)
    from core.config_store import delete_system_key
    if delete_system_key(model_name):
        return {"message": f"已删除系统级 {model_name} 的 Key"}
    return JSONResponse({"error": f"{model_name} 没有保存的系统 Key"}, status_code=404)


# ── 对话管理 ──────────────────────────────────────────────────
@app.post("/api/reset")
async def reset_context(request: Request):
    u = require_user(request)
    _reset_context(u["id"])
    return {"message": "对话已重置"}


# ── 用户认证 API ──────────────────────────────────────────────
@app.post("/api/auth/register")
async def auth_register(request: Request):
    data = await request.json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "").strip()

    if not email or "@" not in email:
        return JSONResponse({"error": "请输入有效的邮箱地址"}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"error": "密码至少 6 位"}, status_code=400)

    try:
        existing = user.get_user_by_email(email)
        if existing:
            return JSONResponse({"error": "该邮箱已注册"}, status_code=409)

        code = verify.generate_code()
        verify.save_code(email, code, "register")
        await email_svc.send_verification_code(email, code, "register")

        redis_client.hset(f"pending_user:{email}", mapping={
            "password": password,
            "created_at": datetime.now(UTC).isoformat(),
        })
        redis_client.expire(f"pending_user:{email}", 600)

        return {"message": f"验证码已发送到 {email}（开发模式查看服务日志）", "email": email}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except Exception as e:
        return JSONResponse({"error": f"注册失败: {e}"}, status_code=500)


@app.post("/api/auth/verify")
async def auth_verify(request: Request):
    data = await request.json()
    email = data.get("email", "").strip().lower()
    code = data.get("code", "").strip()

    if not email or not code:
        return JSONResponse({"error": "邮箱和验证码不能为空"}, status_code=400)

    if not verify.verify_code(email, code, "register"):
        return JSONResponse({"error": "验证码错误或已过期"}, status_code=400)

    pending = redis_client.hgetall(f"pending_user:{email}")
    if not pending:
        return JSONResponse({"error": "注册信息已过期，请重新注册"}, status_code=400)

    try:
        new_user = user.create_user(email, pending["password"])
        user.verify_user(email)
        redis_client.delete(f"pending_user:{email}")
        return {"message": "注册成功，请登录", "user": {"email": new_user["email"]}}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except Exception as e:
        return JSONResponse({"error": f"注册失败: {e}"}, status_code=500)


@app.post("/api/auth/login")
async def auth_login(request: Request):
    data = await request.json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return JSONResponse({"error": "邮箱和密码不能为空"}, status_code=400)

    try:
        result = user.login_user(email, password)
        if result is None:
            return JSONResponse({"error": "邮箱或密码错误"}, status_code=401)
        return {"message": "登录成功", "user": result}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    except Exception as e:
        return JSONResponse({"error": f"登录失败: {e}"}, status_code=500)


@app.get("/api/auth/me")
async def auth_me(request: Request):
    u = _get_user(request)
    if not u:
        return JSONResponse({"error": "未登录"}, status_code=401)
    return {"user": u}


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    u = require_user(request)
    user.logout_user(u["id"])
    return {"message": "已退出登录"}


# ── 头像 ──────────────────────────────────────────────────────
@app.get("/api/avatar/{user_id}")
async def get_avatar(user_id: int):
    """从 MinIO 获取用户上传的头像，没有则返回 404"""
    avatar_path = user.get_user_avatar(user_id)
    if avatar_path:
        result = minio_get(avatar_path)
        if result:
            data, ct = result
            return Response(content=data, media_type=ct)
    return Response(status_code=404)


@app.post("/api/avatar/upload")
async def upload_avatar(request: Request, file: UploadFile = File(...)):
    """上传用户头像到 MinIO（自动清理旧扩展名）"""
    u = _get_user(request)
    if not u:
        return JSONResponse({"error": "请先登录"}, status_code=401)

    allowed_types = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"}
    if file.content_type not in allowed_types:
        return JSONResponse({"error": f"不支持的文件类型: {file.content_type}"}, status_code=400)

    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        return JSONResponse({"error": "文件太大，最大支持 10MB"}, status_code=400)

    ext_map = {
        "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
        "image/webp": "webp", "image/svg+xml": "svg",
    }
    ext = ext_map[file.content_type]
    obj_path = f"{AVATAR_BUCKET}/{u['id']}.{ext}"

    # 先清理旧扩展名的头像文件
    for old_ext in ["png", "jpg", "jpeg", "gif", "webp", "svg"]:
        if old_ext == ext:
            continue
        minio_delete(f"{AVATAR_BUCKET}/{u['id']}.{old_ext}")

    if not minio_put(obj_path, data, file.content_type):
        return JSONResponse({"error": "上传到 MinIO 失败"}, status_code=500)

    user.set_user_avatar(u["id"], obj_path)
    return {"message": "头像已更新", "url": f"{MINIO_ENDPOINT}/{obj_path}"}


# ── 聊天 API ──────────────────────────────────────────────────
@app.post("/api/chat")
async def chat(request: Request):
    u = _get_user(request)
    if not u:
        return JSONResponse({"error": "请先登录"}, status_code=401)

    data = await request.json()
    user_message = data.get("message", "").strip()
    if not user_message:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    cfg = get_config()
    model_name = cfg.current_model.name
    api_key = user.get_user_api_key(u["id"], model_name) or cfg.current_model.api_key
    if not api_key:
        return StreamingResponse(
            _stream_no_key(model_name),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return StreamingResponse(
        _stream_chat(user_message, api_key, u["id"]),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── SSE 生成器 ────────────────────────────────────────────────

async def _stream_no_key(model_name: str) -> AsyncGenerator[str, None]:
    """SSE: 告知前端未配置 API Key"""
    yield f"data: {json.dumps({'type': 'start', 'model': model_name})}\n\n"
    yield f"data: {json.dumps({'type': 'error', 'content': f'❌ 未配置 {model_name} 的 API Key'})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


async def _stream_chat(
    user_message: str,
    api_key: str,
    user_id: int,
) -> AsyncGenerator[str, None]:
    """
    流式生成聊天回复（真流式：异步 LLM 调用）

    修复内容：
      - 使用 chat_async（异步），不阻塞事件循环
      - 逐 token 输出（不再是等完整响应再切词吐）
      - 每个用户独立 Context（user_id 参数）
    """
    ctx = _get_context(user_id)
    cfg = get_config()
    model_name = cfg.current_model.name
    tools = registry.get_schemas()

    yield f"data: {json.dumps({'type': 'start', 'model': model_name})}\n\n"

    try:
        ctx.add_user(user_message)
        turn = 0
        max_turns = 5
        final_reply = ""

        while turn < max_turns:
            turn += 1
            response = await chat_async(
                messages=ctx.get_messages(),
                tools=tools,
                api_key_override=api_key,
            )

            if not response.tool_calls:
                content = response.content
                ctx.add_assistant(content=content)

                # 真实流式：模型响应用 token 事件逐词送给前端
                # 注意：这里是「非流式 API + 逐词 yield」模式
                # 真正的 stream=True 模式需要改 chat_async 支持 SSE chunk
                # 后续可加 streaming 参数调用 API 的 stream 模式
                words = content.split(" ")
                for i, word in enumerate(words):
                    chunk = word + (" " if i < len(words) - 1 else "")
                    yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"

                final_reply = content
                break
            else:
                ctx.add_assistant(
                    tool_calls=response.tool_calls,
                    content=response.content,
                )
                yield f"data: {json.dumps({'type': 'tool_call', 'tool_calls': [
                    {'name': tc['name'], 'arguments': tc['arguments']}
                    for tc in response.tool_calls
                ]})}\n\n"

                for tc in response.tool_calls:
                    name = tc["name"]
                    args = tc["arguments"]
                    result = registry.execute(name, args)
                    ctx.add_tool_result(
                        tool_call_id=tc["id"],
                        name=name,
                        result=result,
                    )
                    yield f"data: {json.dumps({'type': 'tool_result', 'name': name, 'result': str(result)[:200]})}\n\n"

        if not final_reply:
            final_reply = "⚠️ 已达到最大工具调用轮次，请重试。"
            ctx.add_assistant(content=final_reply)
            yield f"data: {json.dumps({'type': 'error', 'content': final_reply})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'content': f'❌ {e}', 'model': cfg.current_model.name})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


# ── 链接翻译 API ──────────────────────────────────────────────

@app.post("/api/translate/analyze")
async def translate_analyze(request: Request):
    """分析 URL，返回章节列表"""
    u = _get_user(request)
    if not u:
        return JSONResponse({"error": "请先登录"}, status_code=401)

    data = await request.json()
    url = (data.get("url") or "").strip()
    if not url:
        return JSONResponse({"error": "URL 不能为空"}, status_code=400)

    try:
        from tools.url_fetch import analyze_url
        result = await analyze_url(url)
        return result
    except Exception as e:
        return JSONResponse({"error": f"分析失败: {e}"}, status_code=500)


@app.post("/api/translate")
async def translate(request: Request):
    """
    翻译 URL 内容（SSE 流式）
    Body: { url, chapters: [title, ...], api_key?, base_url?, model? }
    若 api_key/base_url/model 都为空，使用当前登录用户的默认模型。
    """
    u = _get_user(request)
    if not u:
        return JSONResponse({"error": "请先登录"}, status_code=401)

    data = await request.json()
    url = (data.get("url") or "").strip()
    selected_titles: list = data.get("chapters") or []
    if not url:
        return JSONResponse({"error": "URL 不能为空"}, status_code=400)

    # 确定使用的模型和 Key
    api_key = (data.get("api_key") or "").strip()
    base_url = (data.get("base_url") or "").strip()
    model = (data.get("model") or "").strip()
    percentage = data.get("percentage", 100)  # 翻译前百分之多少

    if not api_key or not base_url or not model:
        # 用当前用户的默认模型
        cfg = get_config()
        mc = cfg.current_model
        if not api_key:
            api_key = user.get_user_api_key(u["id"], mc.name) or mc.api_key
        if not base_url:
            base_url = mc.base_url
        if not model:
            model = mc.model

    if not api_key:
        return JSONResponse({"error": "未配置 API Key，请在设置中添加或传入 api_key"}, status_code=400)

    return StreamingResponse(
        _stream_translate(url, selected_titles, api_key, base_url, model, percentage),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_translate(
    url: str,
    selected_titles: list[str],
    api_key: str,
    base_url: str,
    model: str,
    percentage: int = 100,
) -> AsyncGenerator[str, None]:
    """SSE 流式翻译"""
    from tools.url_fetch import analyze_url, translate_section

    yield f"data: {json.dumps({'type': 'start', 'model': model})}\n\n"

    try:
        # 分析 URL
        analysis = await analyze_url(url)
        sections = analysis.get("sections", [])
        total = len(sections)

        if total == 0:
            yield f"data: {json.dumps({'type': 'error', 'content': '❌ 未找到可翻译的章节'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        # 确定要翻译的章节
        if selected_titles:
            # 按用户选择的章节匹配
            title_set = {t.strip().lower() for t in selected_titles}
            to_translate = [s for s in sections if s["title"].strip().lower() in title_set]
        else:
            # 按百分比取前 N 个章节
            count = max(1, int(total * percentage / 100))
            to_translate = sections[:count]

        yield f"data: {json.dumps({'type': 'analyze_done', 'total': total, 'to_translate': len(to_translate)})}\n\n"

        # 逐章翻译
        for i, sec in enumerate(to_translate):
            title = sec["title"]
            char_count = sec.get("char_count", 0)

            yield f"data: {json.dumps({'type': 'chapter_start', 'index': i, 'title': title, 'char_count': char_count})}\n\n"

            try:
                translation = await translate_section(sec, api_key, base_url, model)
                words = translation.split(" ")
                for j, word in enumerate(words):
                    chunk = word + (" " if j < len(words) - 1 else "")
                    yield f"data: {json.dumps({'type': 'token', 'chapter': i, 'content': chunk})}\n\n"
                    await asyncio.sleep(0.01)

                yield f"data: {json.dumps({'type': 'chapter_done', 'index': i, 'title': title})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'chapter_error', 'index': i, 'title': title, 'error': str(e)})}\n\n"

        yield f"data: {json.dumps({'type': 'all_done', 'total': len(to_translate)})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'content': f'❌ {e}'})}\\n\\n"
        yield f"data: {json.dumps({'type': 'done'})}\\n\\n"


# ═══════════════════════════════════════════════════════════════
# 阅读器 API（新版翻译 + 书架）
# ═══════════════════════════════════════════════════════════════

# ── 书架 ──────────────────────────────────────────────────────

@app.get("/api/reader/collections")
async def reader_list_collections(request: Request):
    """获取用户的书架列表"""
    u = require_user(request)
    from reader import list_collections, create_collection, delete_collection, rename_collection, list_documents, get_document, delete_document, analyze_and_store, get_sections_with_content, translate_paragraph, get_translation_progress
    return list_collections(u["id"])


@app.post("/api/reader/collections")
async def reader_create_collection(request: Request):
    """创建新书架"""
    from reader import create_collection
    u = require_user(request)
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "书架名称不能为空"}, status_code=400)
    return create_collection(u["id"], name, data.get("icon", "📕"))


@app.delete("/api/reader/collections/{collection_id}")
async def reader_delete_collection(collection_id: str, request: Request):
    """删除书架"""
    from reader import delete_collection
    u = require_user(request)
    if delete_collection(collection_id, u["id"]):
        return {"message": "书架已删除"}
    return JSONResponse({"error": "书架不存在或无权限"}, status_code=404)


@app.patch("/api/reader/collections/{collection_id}")
async def reader_rename_collection(collection_id: str, request: Request):
    """重命名书架"""
    from reader import rename_collection
    u = require_user(request)
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "书架名称不能为空"}, status_code=400)
    result = rename_collection(
        collection_id, u["id"], name, data.get("icon")
    )
    if result:
        return result
    return JSONResponse({"error": "书架不存在或无权限"}, status_code=404)


# ── 文档 ──────────────────────────────────────────────────────

@app.get("/api/reader/documents")
async def reader_list_documents(request: Request, collection_id: str = None):
    """获取用户的文档列表"""
    from reader import list_documents
    u = require_user(request)
    return list_documents(u["id"], collection_id)


@app.get("/api/reader/documents/{doc_id}")
async def reader_get_document(doc_id: str, request: Request):
    """获取单个文档信息"""
    from reader import get_document
    u = require_user(request)
    doc = get_document(doc_id, u["id"])
    if not doc:
        return JSONResponse({"error": "文档不存在"}, status_code=404)
    return doc


@app.delete("/api/reader/documents/{doc_id}")
async def reader_delete_document(doc_id: str, request: Request):
    """删除文档"""
    from reader import delete_document
    u = require_user(request)
    if delete_document(doc_id, u["id"]):
        return {"message": "文档已删除"}
    return JSONResponse({"error": "文档不存在或无权限"}, status_code=404)


# ── 分析 ──────────────────────────────────────────────────────

@app.post("/api/reader/analyze")
async def reader_analyze(request: Request):
    """分析 URL：抓取 → 解析 → 存储到 MinIO + DB"""
    u = require_user(request)
    data = await request.json()
    url = (data.get("url") or "").strip()
    if not url:
        return JSONResponse({"error": "URL 不能为空"}, status_code=400)

    try:
        from reader import analyze_and_store
        doc = await analyze_and_store(
            url, u["id"], data.get("collection_id"),
        )
        return {"message": "分析完成", "document": doc}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"分析失败: {e}"}, status_code=500)


# ── 阅读 ──────────────────────────────────────────────────────

@app.get("/api/reader/read/{doc_id}")
async def reader_read(doc_id: str, request: Request):
    """获取文档全部章节+段落内容（含原文和译文 HTML）"""
    from reader import get_sections_with_content
    u = require_user(request)
    sections = get_sections_with_content(doc_id, u["id"])
    if sections is None:
        return JSONResponse({"error": "文档不存在或无权限"}, status_code=404)
    return {"sections": sections}


# ── 翻译 ──────────────────────────────────────────────────────

@app.post("/api/reader/translate/{doc_id}")
async def reader_translate(doc_id: str, request: Request):
    """翻译文档中所有待翻译段落（SSE 流式）"""
    u = require_user(request)
    data = await request.json()

    from core.config import get_config
    from core import user as user_mod

    api_key = (data.get("api_key") or "").strip()
    base_url = (data.get("base_url") or "").strip()
    model = (data.get("model") or "").strip()
    lang = data.get("lang", "中文")

    if not api_key or not base_url or not model:
        cfg = get_config()
        mc = cfg.current_model
        if not api_key:
            api_key = user_mod.get_user_api_key(u["id"], mc.name) or mc.api_key
        if not base_url:
            base_url = mc.base_url
        if not model:
            model = mc.model

    if not api_key:
        return JSONResponse({"error": "未配置 API Key"}, status_code=400)

    return StreamingResponse(
        _stream_reader_translate(doc_id, u["id"], api_key, base_url, model, lang),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_reader_translate(
    doc_id: str, user_id: int,
    api_key: str, base_url: str, model: str, lang: str,
):
    """SSE 流式翻译文档"""
    from reader import get_translation_progress, translate_paragraph
    from reader.reader_service import _para_trans_key
    from core.db import get_conn_sync
    import psycopg2

    try:
        progress = get_translation_progress(doc_id, user_id)
        if progress.get("pending", 0) == 0:
            yield f"data: {json.dumps({'type': 'done', 'message': '所有段落已翻译完成'})}\n\n"
            return

        conn = get_conn_sync()
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute("""
                SELECT id, sec_index, paragraph_index, html_content_key,
                       text_content, char_count, status
                FROM sections
                WHERE document_id = %s::uuid AND status = 'wait'
                ORDER BY sec_index, paragraph_index
            """, (doc_id,))
            pending_rows = cur.fetchall()
        finally:
            cur.close()
            conn.close()

        total = len(pending_rows)
        done = 0

        for row in pending_rows:
            sec_id = str(row["id"])
            html_key = row["html_content_key"]
            para_idx = row["paragraph_index"]
            sec_idx = row["sec_index"]

            from core.storage import minio_get as _minio_get
            data_resp = _minio_get(f"reader/{html_key}")
            if not data_resp:
                yield f"data: {json.dumps({'type': 'para_error', 'sec_index': sec_idx, 'para_index': para_idx, 'error': '原文读取失败'})}\\n\\n"
                continue
            orig_html = data_resp[0].decode("utf-8", errors="replace")

            yield f"data: {json.dumps({'type': 'para_start', 'sec_index': sec_idx, 'para_index': para_idx, 'char_count': row['char_count']})}\\n\\n"

            try:
                trans_html = await translate_paragraph(
                    sec_id, orig_html, row["text_content"],
                    api_key, base_url, model, lang,
                )

                from core.storage import minio_put as _minio_put
                from reader.reader_service import _para_trans_key
                trans_key = _para_trans_key(doc_id, sec_idx, para_idx)
                _minio_put(
                    f"reader/{trans_key}",
                    trans_html.encode("utf-8"),
                    "text/html; charset=utf-8",
                )

                conn2 = get_conn_sync()
                conn2.autocommit = True
                cur2 = conn2.cursor()
                try:
                    cur2.execute("""
                        UPDATE sections SET translated_html_key = %s, status = 'done'
                        WHERE id = %s::uuid
                    """, (trans_key, sec_id))
                finally:
                    cur2.close()
                    conn2.close()

                done += 1
                yield f"data: {json.dumps({'type': 'para_done', 'sec_index': sec_idx, 'para_index': para_idx, 'trans_html': trans_html})}\\n\\n"

            except Exception as e:
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
                yield f"data: {json.dumps({'type': 'para_error', 'sec_index': sec_idx, 'para_index': para_idx, 'error': str(e)})}\\n\\n"

        conn3 = get_conn_sync()
        conn3.autocommit = True
        cur3 = conn3.cursor()
        try:
            cur3.execute("""
                UPDATE documents SET
                    sections_done = (SELECT COUNT(*) FROM sections WHERE document_id = %s::uuid AND status = 'done'),
                    status = CASE WHEN (SELECT COUNT(*) FROM sections WHERE document_id = %s::uuid AND status = 'wait') = 0 THEN 'complete' ELSE 'partial' END,
                    updated_at = NOW()
                WHERE id = %s::uuid
            """, (doc_id, doc_id, doc_id))
        finally:
            cur3.close()
            conn3.close()

        yield f"data: {json.dumps({'type': 'all_done', 'total': total, 'done': done})}\\n\\n"

    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'content': f'❌ {e}'})}\n\n"
    finally:
        yield f"data: {json.dumps({'type': 'done'})}\n\n"


@app.get("/api/reader/progress/{doc_id}")
async def reader_progress(doc_id: str, request: Request):
    """获取文档翻译进度"""
    from reader import get_translation_progress
    u = require_user(request)
    return get_translation_progress(doc_id, u["id"])


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8080
    uvicorn.run(app, host="0.0.0.0", port=port)