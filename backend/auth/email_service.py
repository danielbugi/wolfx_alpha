# File: backend/auth/email_service.py
"""
Sends the 2FA login code by plain SMTP (stdlib smtplib - no vendor API dependency, works with any mailbox
the user points it at: their own domain's mail, Gmail, etc.). Configured via .env: SMTP_HOST, SMTP_PORT,
SMTP_USER, SMTP_PASSWORD, SMTP_FROM.

If SMTP_HOST is unset, the code is logged instead of emailed - a dev-only fallback, never a silent
"pretend it sent" (the log line says exactly that so it's never mistaken for real delivery).
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


class EmailSendError(RuntimeError):
    pass


def send_login_code(to_email: str, code: str, ttl_minutes: int) -> None:
    host = os.environ.get("SMTP_HOST")
    if not host:
        logger.warning("SMTP_HOST is not set - DEV FALLBACK: login code for %s is %s (NOT emailed)", to_email, code)
        return

    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM", user or "")

    message = EmailMessage()
    message["Subject"] = "Your Donchian Screener sign-in code"
    message["From"] = sender
    message["To"] = to_email
    message.set_content(
        f"Your sign-in code is {code}\n\nIt expires in {ttl_minutes} minutes. "
        f"If you did not try to sign in, ignore this email."
    )

    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls()
            if user and password:
                smtp.login(user, password)
            # send_message() raises on a fully-refused send, but a PARTIALLY refused one (e.g. the
            # server accepts the connection then rejects just this recipient) returns a non-empty dict
            # instead of raising — that would otherwise look identical to a real success in the logs.
            refused = smtp.send_message(message)
            if refused:
                logger.error("SMTP server refused %s for the 2FA email: %s", to_email, refused)
                raise EmailSendError(f"Server refused the recipient: {refused}")
    except (smtplib.SMTPException, OSError) as e:
        logger.error("Failed to send 2FA email to %s: %s", to_email, e)
        raise EmailSendError(str(e)) from e

    logger.info("2FA code emailed to %s via %s:%s", to_email, host, port)
