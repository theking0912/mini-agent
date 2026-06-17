"""
邮件服务模块 — 发送验证码等通知邮件
=====================================
SMTP 配置：
    EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, EMAIL_SMTP_USER
    EMAIL_SMTP_PASSWORD, EMAIL_FROM_ADDR

未配置 SMTP 时，验证码会打印到服务日志方便开发测试。
"""

import os
import logging

logger = logging.getLogger("mini-agent.email")

# SMTP 配置（可从环境变量或 settings 读取）
SMTP_HOST = os.environ.get("EMAIL_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("EMAIL_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("EMAIL_SMTP_PASSWORD", "")
FROM_ADDR = os.environ.get("EMAIL_FROM_ADDR", "noreply@mini-agent.app")


def is_configured() -> bool:
    """SMTP 是否已配置"""
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


async def send_verification_code(email: str, code: str, purpose: str = "register") -> bool:
    """
    发送验证码邮件
    未配置 SMTP 时仅打印日志（开发模式）
    """
    if not is_configured():
        logger.info(f"📧 [DEV] 验证码 for {email} (purpose={purpose}): {code}")
        logger.info(f"   SMTP 未配置，验证码仅打印到日志")
        return True

    subject = {
        "register": "注册验证码 - Mini Agent",
        "reset": "重置密码 - Mini Agent",
        "login": "登录验证码 - Mini Agent",
    }.get(purpose, "验证码 - Mini Agent")

    body = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:24px">
        <h2 style="color:#6c5ce7">Mini Agent</h2>
        <p>您的验证码为：</p>
        <div style="font-size:32px;letter-spacing:8px;text-align:center;
                    padding:16px;margin:16px 0;background:#f0f0f0;
                    border-radius:8px;font-weight:bold">{code}</div>
        <p style="color:#666">验证码有效期为 10 分钟，请尽快使用。</p>
    </div>
    """

    try:
        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(body, "html", "utf-8")
        msg["Subject"] = subject
        msg["From"] = FROM_ADDR
        msg["To"] = email

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)

        logger.info(f"📧 验证码邮件已发送到 {email}")
        return True
    except Exception as e:
        logger.error(f"📧 发送邮件失败: {e}")
        return False
