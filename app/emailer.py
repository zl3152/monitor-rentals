from datetime import datetime
from email.message import EmailMessage
import smtplib

import httpx

from app.config import (
    APP_BASE_URL,
    EMAIL_FROM,
    NOTIFY_EMAILS,
    RESEND_API_KEY,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USERNAME,
)
from app.models import UnitChange


def email_is_configured() -> bool:
    return bool((RESEND_API_KEY and EMAIL_FROM and NOTIFY_EMAILS) or _smtp_is_configured())


def send_change_digest(changes: list[UnitChange]) -> bool:
    if not changes or not email_is_configured():
        return False

    subject = f"Rental Tracker: {len(changes)} listing change{'s' if len(changes) != 1 else ''}"
    body = _build_digest_body(changes)
    _send_email(subject, body)

    sent_at = datetime.utcnow()
    for change in changes:
        change.emailed_at = sent_at
    return True


def send_test_email() -> None:
    if not email_is_configured():
        raise RuntimeError("Email is not fully configured.")

    _send_email(
        "Rental Tracker: test email",
        "This is a test email from Monitor Rentals.\n\n"
        f"Dashboard: {APP_BASE_URL}\n",
    )


def _send_email(subject: str, body: str) -> None:
    if RESEND_API_KEY:
        _send_resend_email(subject, body)
        return
    _send_smtp_email(subject, body)


def _send_resend_email(subject: str, body: str) -> None:
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json={
            "from": EMAIL_FROM,
            "to": NOTIFY_EMAILS,
            "subject": subject,
            "text": body,
        },
        timeout=20,
    )
    response.raise_for_status()


def _send_smtp_email(subject: str, body: str) -> None:
    if not _smtp_is_configured():
        raise RuntimeError("SMTP email is not fully configured.")

    message = EmailMessage()
    message["From"] = SMTP_USERNAME
    message["To"] = ", ".join(NOTIFY_EMAILS)
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.send_message(message)


def _smtp_is_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD and NOTIFY_EMAILS)


def _build_digest_body(changes: list[UnitChange]) -> str:
    lines = ["Rental Tracker detected changes:", ""]
    for change in changes:
        unit_name = ""
        if change.unit:
            unit_name = change.unit.floor_plan or change.unit.unit_name
        source_name = change.source.name or "Tracked source"
        lines.append(f"- {source_name}{f' / {unit_name}' if unit_name else ''}: {change.message}")
        if change.unit and change.unit.unit_url:
            lines.append(f"  {change.unit.unit_url}")
    lines.extend(["", f"Dashboard: {APP_BASE_URL}"])
    return "\n".join(lines)
