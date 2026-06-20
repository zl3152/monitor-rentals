from contextlib import asynccontextmanager
from datetime import datetime, timezone
from math import ceil
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import asc, desc, or_
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.config import BOARD_TOKEN, MAX_RENT, TARGET_CITIES
from app.checker import check_source_by_id
from app.database import Base, SessionLocal, engine, get_db, run_startup_migrations
from app.emailer import send_test_email
from app.heartbeat import send_daily_heartbeat
from app.models import DetectedUnit, TrackedSource, UnitChange
from app.scheduler import create_scheduler


Base.metadata.create_all(bind=engine)
run_startup_migrations()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = create_scheduler()
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Monitor Rentals", lifespan=lifespan)
templates = Jinja2Templates(directory="app/templates")
PACIFIC = ZoneInfo("America/Los_Angeles")

CITIES = ["Menlo Park", "Palo Alto", "Mountain View", "Redwood City", "Other"]
UNIT_FITS = ["Great fit", "Possible fit", "Needs review", "Not a fit"]
UNIT_SORTS = {
    "newest": ("Newest", desc(DetectedUnit.first_seen_at)),
    "rent_asc": ("Rent low to high", asc(DetectedUnit.rent)),
    "rent_desc": ("Rent high to low", desc(DetectedUnit.rent)),
    "available": ("Availability", asc(DetectedUnit.available_date)),
}
UNIT_VIEWS = {
    "active": "Active units",
    "dismissed": "Dismissed units",
    "all": "All units",
}
ITEMS_PER_PAGE = 10


def format_pt(value: datetime | None) -> str:
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(PACIFIC).strftime("%Y-%m-%d %H:%M %Z")


templates.env.globals["format_pt"] = format_pt


def require_board(token: str) -> None:
    if token != BOARD_TOKEN:
        raise HTTPException(status_code=404, detail="Board not found")


def board_url(token: str, path: str = "") -> str:
    return f"/board/{token}{path}"


def board_query_url(token: str, params: dict[str, object]) -> str:
    return board_url(token, f"?{urlencode(params)}")


@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse(url=board_url(BOARD_TOKEN))


@app.get("/board/{token}", response_class=HTMLResponse)
def dashboard(
    token: str,
    request: Request,
    unit_fit: str = "",
    unit_sort: str = "newest",
    unit_view: str = "active",
    unit_page: int = 1,
    recent_page: int = 1,
    apartment_name: str = "",
    email_test: str = "",
    email_error: str = "",
    heartbeat: str = "",
    heartbeat_error: str = "",
    db: Session = Depends(get_db),
):
    require_board(token)
    apartment_name = apartment_name.strip()
    sources = db.query(TrackedSource).order_by(TrackedSource.created_at.desc()).all()
    unit_view_key = unit_view if unit_view in UNIT_VIEWS else "active"
    unit_query = db.query(DetectedUnit).filter(DetectedUnit.is_available.is_(True))
    if apartment_name:
        unit_query = unit_query.join(TrackedSource).filter(
            TrackedSource.name.ilike(f"%{apartment_name}%")
        )
    if unit_view_key == "dismissed":
        unit_query = unit_query.filter(DetectedUnit.dismissed_at.is_not(None))
    elif unit_view_key == "active":
        unit_query = unit_query.filter(DetectedUnit.dismissed_at.is_(None))
    if unit_fit:
        unit_query = unit_query.filter(DetectedUnit.fit_label == unit_fit)
    unit_sort_key = unit_sort if unit_sort in UNIT_SORTS else "newest"
    total_units = unit_query.count()
    total_unit_pages = max(1, ceil(total_units / ITEMS_PER_PAGE))
    unit_page = min(max(unit_page, 1), total_unit_pages)
    unit_offset = (unit_page - 1) * ITEMS_PER_PAGE
    units = (
        unit_query.order_by(UNIT_SORTS[unit_sort_key][1])
        .offset(unit_offset)
        .limit(ITEMS_PER_PAGE)
        .all()
    )
    unit_page_base_params = {
        "unit_view": unit_view_key,
        "unit_sort": unit_sort_key,
        "recent_page": recent_page,
    }
    if unit_fit:
        unit_page_base_params["unit_fit"] = unit_fit
    if apartment_name:
        unit_page_base_params["apartment_name"] = apartment_name
    unit_pagination = {
        "page": unit_page,
        "per_page": ITEMS_PER_PAGE,
        "total": total_units,
        "total_pages": total_unit_pages,
        "start": unit_offset + 1 if total_units else 0,
        "end": min(unit_offset + len(units), total_units),
        "prev_url": board_query_url(
            token, {**unit_page_base_params, "unit_page": unit_page - 1}
        )
        if unit_page > 1
        else "",
        "next_url": board_query_url(
            token, {**unit_page_base_params, "unit_page": unit_page + 1}
        )
        if unit_page < total_unit_pages
        else "",
    }
    recent_query = (
        db.query(UnitChange)
        .join(TrackedSource, UnitChange.source_id == TrackedSource.id)
        .outerjoin(DetectedUnit, UnitChange.unit_id == DetectedUnit.id)
        .filter(or_(UnitChange.unit_id.is_(None), DetectedUnit.dismissed_at.is_(None)))
    )
    if apartment_name:
        recent_query = recent_query.filter(TrackedSource.name.ilike(f"%{apartment_name}%"))
    total_recent_changes = recent_query.count()
    total_recent_pages = max(1, ceil(total_recent_changes / ITEMS_PER_PAGE))
    recent_page = min(max(recent_page, 1), total_recent_pages)
    recent_offset = (recent_page - 1) * ITEMS_PER_PAGE
    recent_changes = (
        recent_query.order_by(UnitChange.detected_at.desc())
        .offset(recent_offset)
        .limit(ITEMS_PER_PAGE)
        .all()
    )
    recent_page_base_params = {
        "unit_view": unit_view_key,
        "unit_sort": unit_sort_key,
        "unit_page": unit_page,
    }
    if unit_fit:
        recent_page_base_params["unit_fit"] = unit_fit
    if apartment_name:
        recent_page_base_params["apartment_name"] = apartment_name
    recent_pagination = {
        "page": recent_page,
        "per_page": ITEMS_PER_PAGE,
        "total": total_recent_changes,
        "total_pages": total_recent_pages,
        "start": recent_offset + 1 if total_recent_changes else 0,
        "end": min(recent_offset + len(recent_changes), total_recent_changes),
        "prev_url": board_query_url(
            token, {**recent_page_base_params, "recent_page": recent_page - 1}
        )
        if recent_page > 1
        else "",
        "next_url": board_query_url(
            token, {**recent_page_base_params, "recent_page": recent_page + 1}
        )
        if recent_page < total_recent_pages
        else "",
    }

    return templates.TemplateResponse(
        "board.html",
        {
            "request": request,
            "token": token,
            "sources": sources,
            "units": units,
            "unit_pagination": unit_pagination,
            "recent_changes": recent_changes,
            "recent_pagination": recent_pagination,
            "unit_fits": UNIT_FITS,
            "selected_unit_fit": unit_fit,
            "unit_sorts": UNIT_SORTS,
            "selected_unit_sort": unit_sort_key,
            "unit_views": UNIT_VIEWS,
            "selected_unit_view": unit_view_key,
            "selected_apartment_name": apartment_name,
            "email_test": email_test,
            "email_error": email_error,
            "heartbeat": heartbeat,
            "heartbeat_error": heartbeat_error,
            "max_rent": MAX_RENT,
            "target_cities": sorted(city.title() for city in TARGET_CITIES),
        },
    )


@app.post("/board/{token}/test-email", response_class=HTMLResponse)
def test_email(token: str):
    require_board(token)
    try:
        send_test_email()
        return RedirectResponse(url=board_url(token, "?email_test=sent"), status_code=303)
    except Exception as exc:
        error = quote(str(exc)[:240])
        return RedirectResponse(
            url=board_url(token, f"?email_test=failed&email_error={error}"),
            status_code=303,
        )


@app.post("/board/{token}/heartbeat", response_class=HTMLResponse)
def heartbeat_email(token: str, db: Session = Depends(get_db)):
    require_board(token)
    try:
        send_daily_heartbeat(db)
        return RedirectResponse(url=board_url(token, "?heartbeat=sent"), status_code=303)
    except Exception as exc:
        error = quote(str(exc)[:240])
        return RedirectResponse(
            url=board_url(token, f"?heartbeat=failed&heartbeat_error={error}"),
            status_code=303,
        )


@app.post("/board/{token}/units/{unit_id}/dismiss", response_class=HTMLResponse)
def dismiss_unit(
    token: str,
    unit_id: int,
    db: Session = Depends(get_db),
):
    require_board(token)
    unit = db.get(DetectedUnit, unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    unit.dismissed_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(url=board_url(token), status_code=303)


@app.post("/board/{token}/units/{unit_id}/restore", response_class=HTMLResponse)
def restore_unit(
    token: str,
    unit_id: int,
    db: Session = Depends(get_db),
):
    require_board(token)
    unit = db.get(DetectedUnit, unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    unit.dismissed_at = None
    db.commit()
    return RedirectResponse(url=board_url(token, "?unit_view=dismissed"), status_code=303)


@app.get("/board/{token}/sources/new", response_class=HTMLResponse)
def new_source(token: str, request: Request):
    require_board(token)
    return templates.TemplateResponse(
        "source_form.html",
        {
            "request": request,
            "token": token,
            "source": None,
            "cities": CITIES,
        },
    )


@app.post("/board/{token}/sources", response_class=HTMLResponse)
def create_source(
    token: str,
    url: str = Form(...),
    name: str = Form(""),
    city: str = Form(""),
    notes: str = Form(""),
    is_active: str = Form("yes"),
    db: Session = Depends(get_db),
):
    require_board(token)
    source = TrackedSource(
        url=url.strip(),
        name=name.strip(),
        city=city.strip(),
        notes=notes.strip(),
        is_active=_parse_bool(is_active) is not False,
    )
    db.add(source)
    db.commit()
    return RedirectResponse(url=board_url(token), status_code=303)


@app.get("/board/{token}/sources/{source_id}/edit", response_class=HTMLResponse)
def edit_source(
    token: str,
    source_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    require_board(token)
    source = db.get(TrackedSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return templates.TemplateResponse(
        "source_form.html",
        {
            "request": request,
            "token": token,
            "source": source,
            "cities": CITIES,
        },
    )


@app.post("/board/{token}/sources/{source_id}", response_class=HTMLResponse)
def update_source(
    token: str,
    source_id: int,
    url: str = Form(...),
    name: str = Form(""),
    city: str = Form(""),
    notes: str = Form(""),
    is_active: str = Form("yes"),
    db: Session = Depends(get_db),
):
    require_board(token)
    source = db.get(TrackedSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    source.url = url.strip()
    source.name = name.strip()
    source.city = city.strip()
    source.notes = notes.strip()
    source.is_active = _parse_bool(is_active) is not False
    db.commit()
    return RedirectResponse(url=board_url(token), status_code=303)


@app.post("/board/{token}/sources/{source_id}/delete", response_class=HTMLResponse)
def delete_source(
    token: str,
    source_id: int,
    db: Session = Depends(get_db),
):
    require_board(token)
    source = db.get(TrackedSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    db.query(UnitChange).filter(UnitChange.source_id == source.id).delete(
        synchronize_session=False
    )
    db.query(DetectedUnit).filter(DetectedUnit.source_id == source.id).delete(
        synchronize_session=False
    )
    db.delete(source)
    db.commit()
    return RedirectResponse(url=board_url(token), status_code=303)


@app.post("/board/{token}/sources/{source_id}/check", response_class=HTMLResponse)
def check_source_now(
    token: str,
    source_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    require_board(token)
    source = db.get(TrackedSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    source.last_check_status = "queued"
    source.last_check_error = ""
    source.last_check_started_at = datetime.utcnow()
    db.commit()
    background_tasks.add_task(_check_source_in_background, source_id)
    return RedirectResponse(url=board_url(token), status_code=303)


def _check_source_in_background(source_id: int) -> None:
    db = SessionLocal()
    try:
        check_source_by_id(db, source_id)
    finally:
        db.close()


def _parse_bool(value: str) -> bool | None:
    if value == "yes":
        return True
    if value == "no":
        return False
    return None
