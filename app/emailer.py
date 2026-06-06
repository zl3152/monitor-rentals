from datetime import datetime
from email.message import EmailMessage
import smtplib

from app.config import APP_BASE_URL, NOTIFY_EMAILS, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USERNAME
from app.models import UnitChange


def email_is_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD and NOTIFY_EMAILS)


def send_change_digest(changes: list[UnitChange]) -> bool:
    if not changes or not email_is_configured():
        return False

    message = EmailMessage()
    message["From"] = SMTP_USERNAME
    message["To"] = ", ".join(NOTIFY_EMAILS)
    message["Subject"] = f"Rental Tracker: {len(changes)} listing change{'s' if len(changes) != 1 else ''}"
    message.set_content(_build_digest_body(changes))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.send_message(message)

    sent_at = datetime.utcnow()
    for change in changes:
        change.emailed_at = sent_at
    return True


def send_test_email() -> None:
    if not email_is_configured():
        raise RuntimeError("Email is not fully configured.")

    message = EmailMessage()
    message["From"] = SMTP_USERNAME
    message["To"] = ", ".join(NOTIFY_EMAILS)
    message["Subject"] = "Rental Tracker: test email"
    message.set_content(
        "This is a test email from Monitor Rentals.\n\n"
        f"Dashboard: {APP_BASE_URL}\n"
    )

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.send_message(message)


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
