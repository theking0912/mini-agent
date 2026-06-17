"""
Mini Agent Web UI — FastAPI 服务
=================================
"""
import sys
import os
import json
import asyncio
from pathlib import Path
from typing import AsyncGenerator
from datetime import datetime, timezone

from fastapi import FastAPI, Request, File, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, Response
import uvicorn

# Mini Agent 核心
from core.config import get_config, reload_config
from core.context import Context
from core import user, email as email_svc, verify
from core.db import init_db, get_redis
from tools import registry
import urllib.request as _urllib

# Redis 客户端
redis_client = get_redis()

# ── 应用 ──────────────────────────────────────────────────────
app = FastAPI(title="Mini Agent Web UI")

# 每个会话独立上下文（简单起见用单个，后续可扩展为多会话）
_context = Context()

# ── MinIO 配置 ────────────────────────────────────────────────
MINIO_ENDPOINT = "http://172.18.0.1:9000"
MINIO_ACCESS_KEY = "leroy"
MINIO_SECRET_KEY = "Leroy.Lee_09.12.24"
AVATAR_BUCKET = "avatars"


# ── 认证辅助 ──────────────────────────────────────────────────
def _get_user(request: Request) -> dict | None:
    """从请求头提取当前登录用户"""
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return None
    return user.get_user_by_token(token)


def _minio_put(path: str, data: bytes, content_type: str) -> bool:
    """上传文件到 MinIO（兼容 AWS S3 API）"""
    import base64, hashlib, hmac
    from datetime import datetime as dt

    bucket, obj = path.split("/", 1)
    host = MINIO_ENDPOINT.replace("http://", "")
    date_str = dt.utcnow().strftime("%Y%m%d")
    amz_date = dt.utcnow().strftime("%Y%m%dT%H%M%SZ")

    # 构造签名
    service = "s3"
    region = "us-east-1"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    payload_hash = hashlib.sha256(data).hexdigest()

    canonical_req = (
        f"PUT\n/{bucket}/{obj}\n\n"
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n\n"
        f"{signed_headers}\n{payload_hash}"
    )
    algo = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_str}/{region}/{service}/aws4_request"
    string_to_sign = (
        f"{algo}\n{amz_date}\n{credential_scope}\n"
        f"{hashlib.sha256(canonical_req.encode()).hexdigest()}"
    )

    def sign(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    date_key = sign(("AWS4" + MINIO_SECRET_KEY).encode(), date_str)
    region_key = sign(date_key, region)
    service_key = sign(region_key, service)
    signing_key = sign(service_key, "aws4_request")
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    auth_header = (
        f"{algo} Credential={MINIO_ACCESS_KEY}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    req = _urllib.Request(
        f"{MINIO_ENDPOINT}/{bucket}/{obj}",
        data=data,
        method="PUT",
        headers={
            "Host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "Authorization": auth_header,
            "Content-Type": content_type,
        },
    )
    try:
        with _urllib.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204)
    except Exception as e:
        print(f"MinIO PUT 失败: {e}")
        return False


def _minio_delete(path: str) -> bool:
    """删除 MinIO 对象（忽略 404）"""
    import hashlib, hmac
    from datetime import datetime as dt

    bucket, obj = path.split("/", 1)
    host = MINIO_ENDPOINT.replace("http://", "")
    date_str = dt.utcnow().strftime("%Y%m%d")
    amz_date = dt.utcnow().strftime("%Y%m%dT%H%M%SZ")

    service = "s3"
    region = "us-east-1"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    payload_hash = hashlib.sha256(b"").hexdigest()

    canonical_req = (
        f"DELETE\n/{bucket}/{obj}\n\n"
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n\n"
        f"{signed_headers}\n{payload_hash}"
    )
    algo = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_str}/{region}/{service}/aws4_request"
    string_to_sign = (
        f"{algo}\n{amz_date}\n{credential_scope}\n"
        f"{hashlib.sha256(canonical_req.encode()).hexdigest()}"
    )

    def sign(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    date_key = sign(("AWS4" + MINIO_SECRET_KEY).encode(), date_str)
    region_key = sign(date_key, region)
    service_key = sign(region_key, service)
    signing_key = sign(service_key, "aws4_request")
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    auth_header = (
        f"{algo} Credential={MINIO_ACCESS_KEY}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    req = _urllib.Request(
        f"{MINIO_ENDPOINT}/{bucket}/{obj}",
        method="DELETE",
        headers={
            "Host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "Authorization": auth_header,
        },
    )
    try:
        with _urllib.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204, 404)
    except Exception as e:
        print(f"MinIO DELETE 失败: {e}")
        return False


@ app.on_event("startup")
async def startup():
    """初始化数据库"""
    try:
        init_db()
        print("🗄️  PostgreSQL 数据库就绪")
    except Exception as e:
        print(f"⚠️  PostgreSQL 初始化失败: {e}")
        print("   请确保 PostgreSQL 和 Redis 已启动")


# ── 静态页面 ──────────────────────────────────────────────────
@ app.get("/")
async def index():
    html_path = Path(__file__).resolve().parent / "web" / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return "<h1>web/index.html not found</h1>"


@ app.get("/auth")
async def auth_page():
    html_path = Path(__file__).resolve().parent / "web" / "auth.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return "<h1>web/auth.html not found</h1>"


# ── 模型 API ──────────────────────────────────────────────────
@ app.get("/api/models")
async def list_models(request: Request):
    cfg = get_config()
    u = _get_user(request)
    user_id = u["id"] if u else None
    models = []
    for name, m in cfg.models.items():
        has_key = bool(m.api_key)
        if not has_key and user_id:
            has_key = user.has_user_key(user_id, name)
        models.append({
            "name": name,
            "description": m.description,
            "model": m.model,
            "base_url": m.base_url,
            "has_key": has_key,
            "current": name == cfg.current_model.name,
        })
    return {"models": models, "current": cfg.current_model.name}


@ app.post("/api/switch")
async def switch_model(request: Request):
    data = await request.json()
    name = data.get("model", "")
    try:
        msg = get_config().switch(name)
        return {"message": msg}
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ── Key 管理 ──────────────────────────────────────────────────
@ app.post("/api/key/set")
async def key_set(request: Request):
    u = _get_user(request)
    if not u:
        return JSONResponse({"error": "请先登录"}, status_code=401)
    data = await request.json()
    model_name = data.get("model", "")
    api_key = data.get("key", "").strip()
    if not model_name or not api_key:
        return JSONResponse({"error": "模型和 Key 不能为空"}, status_code=400)
    user.set_user_key(u["id"], model_name, api_key)
    return {"message": f"✅ {model_name} 的 API Key 已保存"}


@ app.post("/api/key/remove")
async def key_remove(request: Request):
    u = _get_user(request)
    if not u:
        return JSONResponse({"error": "请先登录"}, status_code=401)
    data = await request.json()
    model_name = data.get("model", "")
    if user.delete_user_key(u["id"], model_name):
        return {"message": f"已删除 {model_name} 的 Key"}
    return JSONResponse({"error": f"{model_name} 没有保存的 Key"}, status_code=404)


@ app.get("/api/key/list")
async def key_list(request: Request):
    u = _get_user(request)
    if not u:
        return JSONResponse({"error": "请先登录"}, status_code=401)
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


# ── 对话管理 ──────────────────────────────────────────────────
@ app.post("/api/reset")
async def reset_context():
    global _context
    _context = Context()
    return {"message": "对话已重置"}


# ── 用户认证 API ──────────────────────────────────────────────
@ app.post("/api/auth/register")
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

        # 临时保存用户信息到 Redis（等待验证后写入 DB）
        # 注意：这里存原始密码，create_user 内部会做哈希
        redis_client.hset(f"pending_user:{email}", mapping={
            "password": password,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        redis_client.expire(f"pending_user:{email}", 600)

        return {"message": f"验证码已发送到 {email}（开发模式查看服务日志）", "email": email}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except Exception as e:
        return JSONResponse({"error": f"注册失败: {e}"}, status_code=500)


@ app.post("/api/auth/verify")
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


@ app.post("/api/auth/login")
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


@ app.get("/api/auth/me")
async def auth_me(request: Request):
    u = _get_user(request)
    if not u:
        return JSONResponse({"error": "未登录"}, status_code=401)
    return {"user": u}


@ app.post("/api/auth/logout")
async def auth_logout(request: Request):
    u = _get_user(request)
    if u:
        user.logout_user(u["id"])
    return {"message": "已退出登录"}


# ── 头像 ──────────────────────────────────────────────────────
@ app.get("/api/avatar/{user_id}")
async def get_avatar(user_id: int):
    """从 MinIO 获取用户上传的头像，没有则返回 404"""
    avatar_path = user.get_user_avatar(user_id)
    if avatar_path:
        bucket, obj = avatar_path.split("/", 1)
        url = f"{MINIO_ENDPOINT}/{bucket}/{obj}"
        try:
            req = _urllib.Request(url)
            req.add_header("User-Agent", "MiniAgent/1.0")
            with _urllib.urlopen(req, timeout=3) as resp:
                data = resp.read()
            ct = resp.headers.get("Content-Type", "image/png")
            return Response(content=data, media_type=ct)
        except Exception:
            pass
    return Response(status_code=404)


@ app.post("/api/avatar/upload")
async def upload_avatar(request: Request, file: UploadFile = File(...)):
    """上传用户头像到 MinIO（自动清理旧扩展名）"""
    u = _get_user(request)
    if not u:
        return JSONResponse({"error": "请先登录"}, status_code=401)

    # 只支持常见图片格式
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

    # 先清理旧扩展名的头像文件，防止 get_avatar 找到旧文件
    for old_ext in ["png", "jpg", "jpeg", "gif", "webp", "svg"]:
        if old_ext == ext:
            continue
        _minio_delete(f"{AVATAR_BUCKET}/{u['id']}.{old_ext}")

    if not _minio_put(obj_path, data, file.content_type):
        return JSONResponse({"error": "上传到 MinIO 失败"}, status_code=500)

    # 记录头像路径到用户表
    user.set_user_avatar(u["id"], obj_path)

    return {"message": "头像已更新", "url": f"{MINIO_ENDPOINT}/{obj_path}"}


# ── 聊天 API ──────────────────────────────────────────────────
@ app.post("/api/chat")
async def chat(request: Request):
    u = _get_user(request)
    if not u:
        return JSONResponse({"error": "请先登录"}, status_code=401)

    data = await request.json()
    user_message = data.get("message", "").strip()
    if not user_message:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    # 检查 Key（优先用户 Key，其次全局 Key）
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
        _stream_chat(user_message, api_key),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_no_key(model_name: str) -> AsyncGenerator[str, None]:
    """SSE: 告知前端未配置 API Key"""
    yield f"data: {json.dumps({'type': 'start', 'model': model_name})}\n\n"
    yield f"data: {json.dumps({'type': 'no_key', 'model': model_name})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


async def _stream_chat(user_message: str, api_key: str) -> AsyncGenerator[str, None]:
    """流式生成聊天回复"""
    global _context
    cfg = get_config()
    model_name = cfg.current_model.name
    tools = registry.get_schemas()

    from core import llm

    yield f"data: {json.dumps({'type': 'start', 'model': model_name})}\n\n"

    try:
        _context.add_user(user_message)
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
                content = response.content
                _context.add_assistant(content=content)

                # 以单词为单位模拟流式输出
                words = content.split(" ")
                for i, word in enumerate(words):
                    chunk = word + (" " if i < len(words) - 1 else "")
                    yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
                    await asyncio.sleep(0.02)

                final_reply = content
                break
            else:
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
                    yield f"data: {json.dumps({'type': 'tool_result', 'name': name, 'result': str(result)[:200]})}\n\n"

        if not final_reply:
            final_reply = "⚠️ 已达到最大工具调用轮次，请重试。"
            _context.add_assistant(content=final_reply)
            yield f"data: {json.dumps({'type': 'token', 'content': final_reply})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'content': f'❌ {e}', 'model': cfg.current_model.name})}\n\n"

    yield f"data: {json.dumps({'type': 'done'})}\n\n"


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8080
    uvicorn.run(app, host="0.0.0.0", port=port)
