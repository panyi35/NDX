from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate

log = logging.getLogger(__name__)


@dataclass
class SmtpSettings:
    host: str
    port: int
    user: str
    password: str
    sender: str
    recipients: list[str]

    @classmethod
    def from_env(cls, fallback_recipients: list[str] | None = None) -> "SmtpSettings":
        missing = [k for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS") if not os.getenv(k)]
        if missing:
            raise RuntimeError(f"缺少环境变量: {', '.join(missing)}（参考 .env.example）")

        raw_to = os.getenv("MAIL_TO", "")
        recipients = [x.strip() for x in raw_to.split(",") if x.strip()]
        recipients = recipients or list(fallback_recipients or [])
        if not recipients:
            raise RuntimeError("未配置收件人：请设置 MAIL_TO 或 config.yaml 的 email.to")

        user = os.environ["SMTP_USER"]
        return cls(
            host=os.environ["SMTP_HOST"],
            port=int(os.getenv("SMTP_PORT", "465")),
            user=user,
            password=os.environ["SMTP_PASS"],
            sender=os.getenv("MAIL_FROM") or user,
            recipients=recipients,
        )


def send(settings: SmtpSettings, subject: str, html: str, text: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    # 部分服务商（尤其是 QQ/163）要求 From 与登录账号一致，否则退信
    msg["From"] = formataddr(("美股指数日报", settings.sender))
    msg["To"] = ", ".join(settings.recipients)
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    if settings.port == 465:
        smtp = smtplib.SMTP_SSL(settings.host, settings.port, timeout=30)
    else:
        smtp = smtplib.SMTP(settings.host, settings.port, timeout=30)
    with smtp:
        if settings.port != 465:
            smtp.starttls()
        smtp.login(settings.user, settings.password)
        smtp.send_message(msg)
    log.info("邮件已发送至 %s", ", ".join(settings.recipients))
