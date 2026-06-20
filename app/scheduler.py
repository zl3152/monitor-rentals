from threading import Lock
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.checker import check_all_active_sources
from app.database import SessionLocal
from app.heartbeat import send_daily_heartbeat


PACIFIC = ZoneInfo("America/Los_Angeles")
_job_lock = Lock()


def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=PACIFIC)
    scheduler.add_job(
        run_scheduled_checks,
        "cron",
        hour="*/6",
        minute=0,
        id="rental-source-checks",
        replace_existing=True,
    )
    scheduler.add_job(
        run_daily_heartbeat,
        "cron",
        hour=8,
        minute=15,
        id="rental-heartbeat",
        replace_existing=True,
    )
    return scheduler


def run_scheduled_checks() -> int:
    if not _job_lock.acquire(blocking=False):
        return 0

    db = SessionLocal()
    try:
        return check_all_active_sources(db)
    finally:
        db.close()
        _job_lock.release()


def run_daily_heartbeat() -> None:
    db = SessionLocal()
    try:
        send_daily_heartbeat(db)
    finally:
        db.close()
