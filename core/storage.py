"""
MinIO 存储层 — AWS S3 兼容的签名 V4 上传/删除
===============================================
从 server.py 抽出，统一管理对象存储操作。
"""
import hashlib
import hmac
import urllib.request as _urllib
from datetime import datetime as dt

# ── 默认配置 ──────────────────────────────────────────────────
MINIO_ENDPOINT = "http://172.18.0.1:9000"
MINIO_ACCESS_KEY = "leroy"
MINIO_SECRET_KEY = "Leroy.Lee_09.12.24"
AVATAR_BUCKET = "avatars"


def _aws4_sign(
    method: str,
    path: str,
    data: bytes | None,
    content_type: str | None = None,
) -> dict:
    """构造 AWS Signature V4 请求头

    参数：
        method      — HTTP 方法（PUT / DELETE / GET）
        path        — "bucket/object" 格式
        data        — 请求体（GET/DELETE 传 None）
        content_type — Content-Type（仅 PUT 需要）

    返回：可直接传给 urllib.Request 的 headers dict
    """
    bucket, obj = path.split("/", 1)
    host = MINIO_ENDPOINT.replace("http://", "").replace("https://", "")
    date_str = dt.utcnow().strftime("%Y%m%d")
    amz_date = dt.utcnow().strftime("%Y%m%dT%H%M%SZ")
    payload_hash = hashlib.sha256(data or b"").hexdigest()

    service = "s3"
    region = "us-east-1"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"

    canonical_req = (
        f"{method}\n/{bucket}/{obj}\n\n"
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

    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    date_key = _sign(("AWS4" + MINIO_SECRET_KEY).encode(), date_str)
    region_key = _sign(date_key, region)
    service_key = _sign(region_key, service)
    signing_key = _sign(service_key, "aws4_request")
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    auth_header = (
        f"{algo} Credential={MINIO_ACCESS_KEY}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    headers = {
        "Host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Authorization": auth_header,
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def minio_put(path: str, data: bytes, content_type: str) -> bool:
    """上传文件到 MinIO"""
    headers = _aws4_sign("PUT", path, data, content_type)
    try:
        req = _urllib.Request(
            f"{MINIO_ENDPOINT}/{path}",
            data=data,
            method="PUT",
            headers=headers,
        )
        with _urllib.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204)
    except Exception as e:
        print(f"MinIO PUT 失败: {e}")
        return False


def minio_delete(path: str) -> bool:
    """删除 MinIO 对象（忽略 404）"""
    headers = _aws4_sign("DELETE", path, None)
    try:
        req = _urllib.Request(
            f"{MINIO_ENDPOINT}/{path}",
            method="DELETE",
            headers=headers,
        )
        with _urllib.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204, 404)
    except Exception as e:
        print(f"MinIO DELETE 失败: {e}")
        return False


def minio_get(path: str) -> tuple[bytes, str] | None:
    """从 MinIO 获取对象内容，返回 (data, content_type) 或 None"""
    bucket, obj = path.split("/", 1)
    url = f"{MINIO_ENDPOINT}/{bucket}/{obj}"
    try:
        req = _urllib.Request(url)
        req.add_header("User-Agent", "MiniAgent/1.0")
        with _urllib.urlopen(req, timeout=3) as resp:
            data = resp.read()
        ct = resp.headers.get("Content-Type", "image/png")
        return data, ct
    except Exception:
        return None
