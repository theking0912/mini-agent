"""
邮件服务模块 — 发送验证码和通知邮件
=====================================
SMTP 配置来源：config/db.json（和环境变量完全脱钩）

未配置 SMTP 时，邮件会打印到日志方便开发测试。
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("mini-agent.email")

# ── SMTP 配置（从 config/db.json 加载）─────────────────────────
_MAIL_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "db.json"
try:
    with open(_MAIL_CONFIG_PATH, encoding="utf-8") as _f:
        _mail_cfg = json.load(_f)["email"]
    SMTP_HOST = _mail_cfg["host"]
    SMTP_PORT = _mail_cfg["port"]
    SMTP_USER = _mail_cfg["user"]
    SMTP_PASSWORD = _mail_cfg["password"]
    FROM_ADDR = _mail_cfg.get("from_addr", SMTP_USER)
except Exception as _e:
    logger.warning(f"无法加载 {_MAIL_CONFIG_PATH}: {_e}")
    SMTP_HOST = ""
    SMTP_PORT = 465
    SMTP_USER = ""
    SMTP_PASSWORD = ""
    FROM_ADDR = ""

_IS_SSL = SMTP_PORT == 465


def is_configured() -> bool:
    """SMTP 是否已配置（有密码才算配好）"""
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


def _send_raw(to_addr: str, subject: str, html_body: str) -> bool:
    """底层发送逻辑，自动识别 SSL / STARTTLS"""
    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = FROM_ADDR
    msg["To"] = to_addr

    try:
        if _IS_SSL:
            # 端口 465：SSL 直连
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            # 端口 587 / 25：STARTTLS 升级
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)

        logger.info(f"📧 邮件已发送到 {to_addr} — {subject}")
        return True
    except Exception as e:
        logger.error(f"📧 发送邮件失败 ({to_addr}): {e}")
        return False


# ── 发送验证码 ──────────────────────────────────────────────

async def send_verification_code(email: str, code: str, purpose: str = "register") -> bool:
    """
    发送验证码邮件
    未配置 SMTP 时仅打印日志（开发模式）
    """
    if not is_configured():
        logger.info(f"📧 [DEV] 验证码 for {email} (purpose={purpose}): {code}")
        logger.info("   SMTP 未配置，验证码仅打印到日志")
        return True

    subject = {
        "register": "注册验证码 - Mini Agent",
        "reset": "重置密码 - Mini Agent",
        "login": "登录验证码 - Mini Agent",
    }.get(purpose, "验证码 - Mini Agent")

    body = f"""\
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:24px">
        <h2 style="color:#6c5ce7">Mini Agent</h2>
        <p>您的验证码为：</p>
        <div style="font-size:32px;letter-spacing:8px;text-align:center;
                    padding:16px;margin:16px 0;background:#f0f0f0;
                    border-radius:8px;font-weight:bold">{code}</div>
        <p style="color:#666">验证码有效期为 2 分钟，请尽快使用。</p>
    </div>"""

    return _send_raw(email, subject, body)


# ── 发送通知 ────────────────────────────────────────────────

async def send_notification(
    to_addr: str,
    title: str,
    message: str,
    action_text: str = "",
    action_url: str = "",
) -> bool:
    """
    发送通用通知邮件

    参数：
        to_addr     收件人邮箱
        title       通知标题（邮件主题）
        message     通知正文（纯文本或 HTML）
        action_text 操作按钮文字（如"查看详情"）
        action_url  操作按钮链接
    """
    if not is_configured():
        logger.info(f"📧 [DEV] 通知 to {to_addr}: {title}")
        logger.info(f"   {message}")
        return True

    action_html = ""
    if action_text and action_url:
        action_html = f"""
        <div style="text-align:center;margin:24px 0">
            <a href="{action_url}" target="_blank"
               style="display:inline-block;background:#6c5ce7;color:white;
                      padding:12px 28px;border-radius:6px;text-decoration:none;
                      font-size:15px">{action_text}</a>
        </div>"""

    body = f"""\
    <div style="font-family:sans-serif;max-width:520px;margin:0 auto;padding:24px">
        <h2 style="color:#6c5ce7;margin-bottom:12px">{title}</h2>
        <div style="color:#333;line-height:1.7;font-size:14px">
            {message}
        </div>
        {action_html}
        <hr style="border:none;border-top:1px solid #eee;margin:20px 0">
        <p style="font-size:12px;color:#999">
            来自 Mini Agent · notification@shunfuai.com
        </p>
    </div>"""

    return _send_raw(to_addr, title, body)
