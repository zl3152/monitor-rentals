from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.emailer import send_heartbeat_email
from app.models import DetectedUnit, TrackedSource, UnitChange


def build_heartbeat_summary(db: Session) -> dict[str, object]:
    since = datetime.utcnow() - timedelta(hours=24)
    last_successful_source = (
        db.query(TrackedSource)
        .filter(TrackedSource.last_check_status == "succeeded")
        .order_by(TrackedSource.last_check_finished_at.desc())
        .first()
    )

    return {
        "active_sources": db.query(TrackedSource)
        .filter(TrackedSource.is_active.is_(True))
        .count(),
        "available_units": db.query(DetectedUnit)
        .filter(DetectedUnit.is_available.is_(True))
        .count(),
        "great_fit_units": db.query(DetectedUnit)
        .filter(
            DetectedUnit.is_available.is_(True),
            DetectedUnit.fit_label == "Great fit",
        )
        .count(),
        "possible_fit_units": db.query(DetectedUnit)
        .filter(
            DetectedUnit.is_available.is_(True),
            DetectedUnit.fit_label == "Possible fit",
        )
        .count(),
        "changes_last_24h": db.query(UnitChange)
        .filter(UnitChange.detected_at >= since)
        .count(),
        "last_successful_check": (
            last_successful_source.last_check_finished_at.strftime("%Y-%m-%d %H:%M UTC")
            if last_successful_source and last_successful_source.last_check_finished_at
            else None
        ),
    }


def send_daily_heartbeat(db: Session) -> None:
    send_heartbeat_email(build_heartbeat_summary(db))
