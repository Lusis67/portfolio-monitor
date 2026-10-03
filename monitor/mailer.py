"""Send the email through Gmail SMTP with stdlib smtplib.

Credentials come from the environment and nowhere else:
  GMAIL_ADDRESS       the account, and the primary recipient
  GMAIL_APP_PASSWORD  a Google app password, not the account password

--dry-run never reaches this module, which is the point: the owner can see
his first email without configuring any of this.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


class MailError(Exception):
    pass


def credentials() -> tuple[str, str]:
    address = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    missing = [
        name
        for name, value in (("GMAIL_ADDRESS", address), ("GMAIL_APP_PASSWORD", password))
        if not value
    ]
    if missing:
        raise MailError(
            f"{' and '.join(missing)} not set. Set them as GitHub Secrets, or use "
            "--dry-run, which needs no credentials at all."
        )
    return address, password


def build_message(subject: str, text_body: str, html_body: str, sender: str,
                  recipients: list[str]) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    # Plain text first, HTML second: clients show the last part they can render.
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    return message


def send(subject: str, text_body: str, html_body: str, also_to=None) -> list[str]:
    address, password = credentials()
    recipients = [address] + [a for a in (also_to or []) if a and a != address]
    message = build_message(subject, text_body, html_body, address, recipients)
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=60) as smtp:
            smtp.login(address, password)
            smtp.send_message(message, from_addr=address, to_addrs=recipients)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(
            f"Gmail rejected the login ({exc.smtp_code}). An app password is required; "
            "the ordinary account password will not work."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"{type(exc).__name__}: {exc}") from exc
    return recipients
