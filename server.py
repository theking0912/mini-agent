"""
Mini Agent Web UI — FastAPI 服务
=================================
用法：
    python server.py              # 启动服务，默认 http://0.0.0.0:8080
    python server.py --port 3000  # 自定义端口

API 端点：
    GET  /              → 聊天页面
    GET  /api/models    → 模型列表
    POST /api/switch    → 切换模型  {"model": "deepseek"}
    POST /api/chat      → SSE 流式聊天 {"message": "..."}
    POST /api/key/set   → 保存 Key  {"model": "deepseek", "key": "sk-xxx"}
    POST /api/key/remove → 删除 Key {"model": "deepseek"}
    GET  /api/key/list  → 已保存 Key 列表
    POST /api/reset     → 重置对话
"""
import sys
import os
import json
import asyncio
from pathlib import Path
from typing import AsyncGenerator
from datetime import datetime, timezone

# FastAPI
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
import uvicorn

# Mini Agent 核心
from core.config import get_config, reload_config
from core.context import Context
from core.tool_runner import run_agent
from core import keyring, database as db
from core import email as email_svc
from tools import registry
import redis as redis_module

# Redis 连接（从 config/db.json 加载）
import json as _json
from pathlib import Path as _Path
_DB_CFG_PATH = _Path(__file__).resolve().parent / "config" / "db.json"
try:
    with open(_DB_CFG_PATH, encoding="utf-8") as _f:
        _redis_cfg = _json.load(_f)["redis"]
    redis_client = redis_module.Redis(
        host=_redis_cfg["host"],
        port=_redis_cfg["port"],
        db=_redis_cfg.get("db", 0),
        password=_redis_cfg.get("password", None) or None,
        decode_responses=False,
    )
    print(f"[Redis] {_redis_cfg['host']}:{_redis_cfg['port']} (from config/db.json)")
except Exception as _e:
    print(f"[Redis] 配置加载失败: {_e}")
    redis_client = None

# ── 应用 ──────────────────────────────────────────────────────
app = FastAPI(title="Mini Agent Web UI")

# 每个会话独立上下文（简单起见用单个，后续可扩展为多会话）
_context = Context()


# ── 认证辅助 ──────────────────────────────────────────────────
def _get_user(request: Request) -> dict | None:
    """从请求头提取当前登录用户"""
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return None
    return db.get_user_by_token(token)


@app.on_event("startup")
async def startup():
    """初始化数据库"""
    try:
        db.init_db()
        print("🗄️  PostgreSQL 数据库就绪")
    except Exception as e:
        print(f"⚠️  PostgreSQL 初始化失败: {e}")
        print("   请确保 PostgreSQL (172.18.0.1:5433) 和 Redis (172.18.0.1:6379) 已启动")


# ── 静态页面 ──────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    """返回聊天页面"""
    html_path = Path(__file__).resolve().parent / "web" / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>web/index.html not found</h1>"


@app.get("/auth", response_class=HTMLResponse)
async def auth_page():
    """返回注册/登录页面"""
    html_path = Path(__file__).resolve().parent / "web" / "auth.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>web/auth.html not found</h1>"


# ── 模型 API ──────────────────────────────────────────────────
@app.get("/api/models")
async def list_models(request: Request):
    """获取所有模型及状态（按当前用户 Key）"""
    cfg = get_config()
    user = _get_user(request)
    user_id = user["id"] if user else None
    models = []
    for name, m in cfg.models.items():
        has_key = bool(m.api_key)
        # 如果全局没 Key，检查用户的 Key
        if not has_key and user_id:
            has_key = db.has_user_key(user_id, name)
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
    """切换当前模型"""
    data = await request.json()
    name = data.get("model", "")
    cfg = get_config()
    if name not in cfg.models:
        return JSONResponse({"error": f"未知模型: {name}"}, status_code=400)
    msg = cfg.switch(name)
    return {"message": msg, "current": name}


# ── 聊天 API（SSE 流式） ──────────────────────────────────────
@app.post("/api/chat")
async def chat(request: Request):
    """SSE 流式聊天（需登录）"""
    data = await request.json()
    user_message = data.get("message", "").strip()
    if not user_message:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    # 验证登录 + 获取用户 Key
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "请先登录"}, status_code=401)
    user_api_key = db.get_user_api_key(user["id"], get_config().current_model.name)

    return StreamingResponse(
        _stream_chat(user_message, user_api_key),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_chat(user_message: str, user_api_key: str | None = None) -> AsyncGenerator[str, None]:
    """流式生成聊天回复"""
    cfg = get_config()
    model_name = cfg.current_model.name

    # 检查 Key（优先用户 Key，其次全局 Key）
    api_key = user_api_key or cfg.current_model.api_key
    if not api_key:
        yield f"data: {json.dumps({'type': 'no_key', 'model': model_name})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    # 发送开始信号
    yield f"data: {json.dumps({'type': 'start', 'model': model_name})}\n\n"

    try:
        # 执行 agent
        global _context
        _context.add_user(user_message)
        tools = registry.get_schemas()

        from core import llm

        turn = 0
        max_turns = 5
        final_reply = ""

        while turn < max_turns:
            turn += 1
            response = llm.chat(
                messages=_context.get_messages(),
                tools=tools,
                api_key_override=api_key,
            )

            if not response.tool_calls:
                # 最终回复：逐 token 模拟流式输出
                content = response.content
                _context.add_assistant(content=content)

                # 以单词为单位模拟流式（更自然的体验）
                words = content.split(" ")
                for i, word in enumerate(words):
                    chunk = word + (" " if i < len(words) - 1 else "")
                    yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
                    await asyncio.sleep(0.02)  # 20ms 间隔

                final_reply = content
                break
            else:
                # 有工具调用
                _context.add_assistant(
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
                    _context.add_tool_result(
                        tool_call_id=tc["id"],
                        name=name,
                        result=result,
                    )
                    yield f"data: {json.dumps({'type': 'tool_result', 'name': name, 'result': result[:200]})}\n\n"

        if not final_reply:
            final_reply = "⚠️ 已达到最大工具调用轮次，请重试。"
            _context.add_assistant(content=final_reply)
            yield f"data: {json.dumps({'type': 'token', 'content': final_reply})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'content': f'❌ {e}', 'model': cfg.current_model.name})}\n\n"

    yield f"data: {json.dumps({'type': 'done'})}\n\n"


# ── Key 管理 API ──────────────────────────────────────────────
@app.post("/api/key/set")
async def key_set(request: Request):
    """保存当前用户的 API Key"""
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "请先登录"}, status_code=401)
    data = await request.json()
    model_name = data.get("model", "")
    api_key = data.get("key", "")
    if not model_name or not api_key:
        return JSONResponse({"error": "模型名和 Key 不能为空"}, status_code=400)
    db.set_user_key(user["id"], model_name, api_key)
    return {"message": f"✅ Key 已保存（仅当前账号可用）", "model": model_name}


@app.post("/api/key/remove")
async def key_remove(request: Request):
    """删除当前用户的 Key"""
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "请先登录"}, status_code=401)
    data = await request.json()
    model_name = data.get("model", "")
    if not model_name:
        return JSONResponse({"error": "模型名不能为空"}, status_code=400)
    if db.delete_user_key(user["id"], model_name):
        return {"message": f"已删除 {model_name} 的 Key"}
    return JSONResponse({"error": f"{model_name} 没有保存的 Key"}, status_code=404)


@app.get("/api/key/list")
async def key_list(request: Request):
    """列出当前用户的 Key 状态（含当前模型信息）"""
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "请先登录"}, status_code=401)
    cfg = get_config()
    user_keys = db.get_user_keys(user["id"])
    models = []
    for name, m in cfg.models.items():
        has_key = name in user_keys
        models.append({
            "name": name,
            "description": m.description,
            "has_key": has_key,
            "current": name == cfg.current_model.name,
        })
    return {"keys": models, "current": cfg.current_model.name}


# ── 对话管理 ──────────────────────────────────────────────────
@app.post("/api/reset")
async def reset_context():
    """重置对话上下文"""
    global _context
    _context = Context()
    return {"message": "对话已重置"}


# ── 用户认证 API ──────────────────────────────────────────────
@app.post("/api/auth/register")
async def auth_register(request: Request):
    """用户注册：发送验证码"""
    data = await request.json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "").strip()

    if not email or "@" not in email:
        return JSONResponse({"error": "请输入有效的邮箱地址"}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"error": "密码至少 6 位"}, status_code=400)

    try:
        # 检查是否已注册
        existing = db.get_user_by_email(email)
        if existing:
            return JSONResponse({"error": "该邮箱已注册"}, status_code=409)

        # 生成验证码
        code = db.generate_code()
        db.save_verification_code(email, code, "register")

        # 发送邮件（未配 SMTP 时打印到日志）
        await email_svc.send_verification_code(email, code, "register")

        # 临时保存用户信息到 Redis（等待验证后写入 DB）
        redis_client.hset(f"pending_user:{email}", mapping={
            "password": db.hash_password(password),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        redis_client.expire(f"pending_user:{email}", 600)

        return {"message": f"验证码已发送到 {email}（开发模式查看服务日志）", "email": email}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except Exception as e:
        return JSONResponse({"error": f"注册失败: {e}"}, status_code=500)


@app.post("/api/auth/verify")
async def auth_verify(request: Request):
    """验证邮箱 + 完成注册"""
    data = await request.json()
    email = data.get("email", "").strip().lower()
    code = data.get("code", "").strip()

    if not email or not code:
        return JSONResponse({"error": "邮箱和验证码不能为空"}, status_code=400)

    # 验证验证码
    if not db.verify_code(email, code, "register"):
        return JSONResponse({"error": "验证码错误或已过期"}, status_code=400)

    # 从 Redis 取出待注册用户
    pending = redis_client.hgetall(f"pending_user:{email}")
    if not pending:
        return JSONResponse({"error": "注册信息已过期，请重新注册"}, status_code=400)

    try:
        password_hash = pending.get(b"password", b"").decode()
        user = db.create_user(email, "")
        # 更新密码哈希
        conn = db.get_pool().getconn()
        cur = conn.cursor()
        cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user["id"]))
        conn.commit()
        db.get_pool().putconn(conn)

        db.verify_user(email)
        redis_client.delete(f"pending_user:{email}")

        return {"message": "注册成功！请登录", "email": email}
    except Exception as e:
        return JSONResponse({"error": f"注册失败: {e}"}, status_code=500)


@app.post("/api/auth/login")
async def auth_login(request: Request):
    """用户登录"""
    data = await request.json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "").strip()

    if not email or not password:
        return JSONResponse({"error": "邮箱和密码不能为空"}, status_code=400)

    try:
        user = db.login_user(email, password)
        if not user:
            return JSONResponse({"error": "邮箱或密码错误"}, status_code=401)
        return {"message": "登录成功", "user": user}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=403)


@app.get("/api/auth/me")
async def auth_me(request: Request):
    """获取当前用户信息（通过 header 中的 token）"""
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return JSONResponse({"error": "未登录"}, status_code=401)
    user = db.get_user_by_token(token)
    if not user:
        return JSONResponse({"error": "登录已过期"}, status_code=401)
    return {"user": user}


# ── 通知 API ──────────────────────────────────────────────────
@app.post("/api/notify/test")
async def notify_test(request: Request):
    """发送测试通知邮件到当前用户的邮箱"""
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "请先登录"}, status_code=401)
    if not email_svc.is_configured():
        return JSONResponse({"error": "SMTP 未配置，请在环境变量中设置 EMAIL_PASSWORD"}, status_code=400)
    ok = await email_svc.send_notification(
        to_addr=user["email"],
        title="✅ Mini Agent 邮件通知测试",
        message="<p>这是一封来自 Mini Agent 的测试邮件。</p><p>如果收到此邮件，说明邮件通知功能已正常工作 🎉</p>",
    )
    if ok:
        return {"message": f"✅ 测试邮件已发送到 {user['email']}"}
    return JSONResponse({"error": "发送失败，请检查 SMTP 配置"}, status_code=500)


@app.post("/api/notify/send")
async def notify_send(request: Request):
    """发送自定义通知（仅管理员可用）"""
    user = _get_user(request)
    if not user or user.get("email") != "admin@mini.com":
        return JSONResponse({"error": "仅管理员可用"}, status_code=403)
    data = await request.json()
    to_addr = data.get("to", user["email"])
    title = data.get("title", "Mini Agent 通知")
    message = data.get("message", "")
    action_text = data.get("action_text", "")
    action_url = data.get("action_url", "")
    ok = await email_svc.send_notification(to_addr, title, message, action_text, action_url)
    if ok:
        return {"message": f"✅ 通知已发送到 {to_addr}"}
    return JSONResponse({"error": "发送失败"}, status_code=500)


# ── 启动 ──────────────────────────────────────────────────────
def main():
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8080
    print(f"🌐 Mini Agent Web UI: http://0.0.0.0:{port}")
    print(f"📡 模型: {get_config().current_model.name}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
