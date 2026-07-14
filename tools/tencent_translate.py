"""
腾讯翻译 API 客户端（TMT）
==========================
使用 Tencent Cloud API v3（TC3-HMAC-SHA256）签名。

优先级：腾讯翻译优先 → 失败/超长时返回 None 触发 LLM 兜底

用法：
    from tools.tencent_translate import translate_text
    result = await translate_text("Hello world", secret_id, secret_key)
    # → "你好世界"
"""
import hashlib
import hmac
import json
import time
import urllib.request

# ── 常量 ──────────────────────────────────────────────────────
TMT_ENDPOINT = "https://tmt.tencentcloudapi.com"
TMT_SERVICE = "tmt"
TMT_VERSION = "2018-03-21"
TMT_REGION = "ap-guangzhou"
TMT_ACTION = "TextTranslate"

# 单次翻译最大字节（Tencent TMT 限制约 2000 bytes，留余量）
MAX_BYTES = 1800

# ── TC3-HMAC-SHA256 签名 ──────────────────────────────────────


def _sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sign_headers(secret_id: str, secret_key: str, payload: dict) -> dict:
    """生成 TC3-HMAC-SHA256 认证头"""
    algorithm = "TC3-HMAC-SHA256"
    timestamp = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))

    # 1. Canonical Request
    payload_str = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    ct = "application/json; charset=utf-8"
    canonical_headers = (
        f"content-type:{ct}\n"
        f"host:tmt.tencentcloudapi.com\n"
        f"x-tc-action:{TMT_ACTION.lower()}\n"
    )
    signed_headers = "content-type;host;x-tc-action"

    canonical_request = (
        "POST\n"
        "/\n"
        "\n"
        f"{canonical_headers}\n"
        f"{signed_headers}\n"
        f"{_sha256_hex(payload_str)}"
    )

    # 2. String to Sign
    credential_scope = f"{date}/{TMT_SERVICE}/tc3_request"
    string_to_sign = (
        f"{algorithm}\n"
        f"{timestamp}\n"
        f"{credential_scope}\n"
        f"{_sha256_hex(canonical_request)}"
    )

    # 3. Signing Key
    secret_date = _hmac_sha256(f"TC3{secret_key}".encode("utf-8"), date)
    secret_service = _hmac_sha256(secret_date, TMT_SERVICE)
    secret_signing = _hmac_sha256(secret_service, "tc3_request")
    signature = _hmac_sha256(secret_signing, string_to_sign).hex()

    # 4. Authorization
    authorization = (
        f"{algorithm} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    return {
        "Authorization": authorization,
        "Content-Type": ct,
        "Host": "tmt.tencentcloudapi.com",
        "X-TC-Action": TMT_ACTION,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": TMT_VERSION,
        "X-TC-Region": TMT_REGION,
    }


# ── 翻译 ──────────────────────────────────────────────────────


async def translate_text(
    text: str,
    secret_id: str,
    secret_key: str,
    source: str = "auto",
    target: str = "zh",
) -> str | None:
    """
    调用腾讯翻译 API 翻译文本。

    参数：
        text:      要翻译的文本
        secret_id: 腾讯云 SecretId
        secret_key: 腾讯云 SecretKey
        source:    源语言（'auto'=自动检测，'en', 'zh'...）
        target:    目标语言（'zh', 'en'...）

    返回：
        翻译后的文本，失败返回 None（由调用方触发 LLM 兜底）
    """
    text = text.strip()
    if not text:
        return ""

    # 超过字节限制 → 返回 None 触发 LLM 兜底
    if len(text.encode("utf-8")) > MAX_BYTES:
        return None

    payload = {
        "SourceText": text,
        "Source": source,
        "Target": target,
        "ProjectId": 0,
    }

    headers = _sign_headers(secret_id, secret_key, payload)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        TMT_ENDPOINT,
        data=data,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            resp_data = result.get("Response", {})
            if "TargetText" in resp_data:
                return resp_data["TargetText"]
            # 有错误信息
            err_msg = resp_data.get("Error", {}).get("Message", "unknown")
            print(f"[TencentTMT] API error: {err_msg}")
            return None
    except Exception as e:
        print(f"[TencentTMT] Request failed: {e}")
        return None


# ── 测试 ──────────────────────────────────────────────────────


async def test():
    """快速测试（需要真实密钥）"""
    import os
    sid = os.environ.get("TENCENT_SECRET_ID", "")
    sk = os.environ.get("TENCENT_SECRET_KEY", "")
    if not sid or not sk:
        print("请设置 TENCENT_SECRET_ID 和 TENCENT_SECRET_KEY 环境变量")
        return

    texts = [
        "Hello, how are you?",
        "The quick brown fox jumps over the lazy dog.",
        "Technical documentation for API integration.",
        "短文本测试。",
    ]
    for t in texts:
        r = await translate_text(t, sid, sk)
        status = "✅" if r else "❌"
        print(f"{status} 原文: {t[:40]:40s} → {r or 'None'}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test())
