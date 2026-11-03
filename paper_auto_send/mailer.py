from __future__ import annotations

import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime, parseaddr
from zoneinfo import ZoneInfo

from .security import build_email_html

SMTP_HOST = "smtp.126.com"
SMTP_PORT = 465
SHANGHAI = ZoneInfo("Asia/Shanghai")


class MailDeliveryError(RuntimeError):
    pass


def send_email(papers: list[dict[str, str]], now: datetime | None = None, *, test_email: bool = False) -> bool:
    if not papers:
        return False
    sender = os.environ.get("EMAIL_FROM", "").strip()
    recipient = os.environ.get("EMAIL_TO", "").strip()
    password = os.environ.get("EMAIL_SMTP_PWD", "")
    parsed_sender = parseaddr(sender)[1]
    if not sender or parsed_sender != sender or "\r" in sender or "\n" in sender:
        raise MailDeliveryError("EMAIL_FROM is missing or invalid")
    if not recipient or parseaddr(recipient)[1] != recipient or "\r" in recipient or "\n" in recipient:
        raise MailDeliveryError("EMAIL_TO is missing or invalid")
    if "@" not in sender or "@" not in recipient:
        raise MailDeliveryError("Email addresses must contain an address and domain")
    if not password:
        raise MailDeliveryError("EMAIL_SMTP_PWD is missing")

    timestamp = now or datetime.now(SHANGHAI)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Date"] = format_datetime(timestamp)
    if test_email:
        message["Subject"] = f"【试卷邮件排版测试】{timestamp:%Y-%m-%d} {len(papers)}份试卷（不修改发送历史）"
    else:
        message["Subject"] = f"【每日试卷增量推送】{timestamp:%Y-%m-%d} 新增{len(papers)}份试卷"
    message.set_content("试卷分类详见 HTML 邮件正文。" if test_email else "今日新增试卷详见 HTML 邮件正文。")
    message.add_alternative(build_email_html(papers), subtype="html")

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.login(sender, password)
            refused = server.send_message(message, from_addr=sender, to_addrs=[recipient])
            if refused:
                raise MailDeliveryError("SMTP server refused the configured recipient")
    except (OSError, smtplib.SMTPException) as exc:
        raise MailDeliveryError(f"SMTP delivery failed ({exc.__class__.__name__})") from exc
    return True
