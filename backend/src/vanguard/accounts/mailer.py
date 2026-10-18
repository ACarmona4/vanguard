"""Password-reset email delivery configured by deployment secrets."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from urllib.parse import quote


def send_password_reset(email: str, token: str) -> None:
    host = os.getenv("VANGUARD_SMTP_HOST")
    sender = os.getenv("VANGUARD_SMTP_FROM")
    if not host or not sender:
        return
    public_url = os.getenv("VANGUARD_PUBLIC_URL", "http://127.0.0.1:5173").rstrip("/")
    reset_url = f"{public_url}/#reset/{quote(token, safe='')}"
    message = EmailMessage()
    message["Subject"] = "Reset your Vanguard password"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        "A password reset was requested for your Vanguard account.\n\n"
        f"Open this link within 30 minutes:\n{reset_url}\n\n"
        "If you did not request it, ignore this message."
    )
    port = int(os.getenv("VANGUARD_SMTP_PORT", "587"))
    username = os.getenv("VANGUARD_SMTP_USERNAME")
    password = os.getenv("VANGUARD_SMTP_PASSWORD")
    with smtplib.SMTP(host, port, timeout=15) as client:
        client.starttls()
        if username and password:
            client.login(username, password)
        client.send_message(message)
