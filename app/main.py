from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import asc, desc
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.config import BOARD_TOKEN, MAX_RENT, TARGET_CITIES
from app.checker import check_source_by_id
from app.database import Base, SessionLocal, engine, get_db, run_startup_migrations
from app.emailer import send_test_email
from app.fit import calculate_fit_label
from app.heartbeat import send_daily_heartbeat
from app.models import DetectedUnit, Property, TrackedSource, UnitChange
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

STATUSES = [
    "New",
    "Interested",
    "Contacted",
    "Tour Scheduled",
    "Applied",
    "Rejected",
    "Unavailable",
]
PROPERTY_TYPES = ["Apartment", "Townhouse", "Other"]
CITIES = ["Menlo Park", "Palo Alto", "Mountain View", "Redwood City", "Other"]
UNIT_FITS = ["Great fit", "Possible fit", "Needs review", "Not a fit"]
UNIT_SORTS = {
    "newest": ("Newest", desc(DetectedUnit.first_seen_at)),
    "rent_asc": ("Rent low to high", asc(DetectedUnit.rent)),
    "rent_desc": ("Rent high to low", desc(DetectedUnit.rent)),
    "available": ("Availability", asc(DetectedUnit.available_date)),
}


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


@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse(url=board_url(BOARD_TOKEN))


@app.get("/board/{token}", response_class=HTMLResponse)
def dashboard(
    token: str,
    request: Request,
    status: str = "",
    fit: str = "",
    unit_fit: str = "",
    unit_sort: str = "newest",
    email_test: str = "",
    email_error: str = "",
    heartbeat: str = "",
    heartbeat_error: str = "",
    db: Session = Depends(get_db),
):
    require_board(token)
    query = db.query(Property)
    if status:
        query = query.filter(Property.status == status)
    if fit:
        query = query.filter(Property.fit_label == fit)
    properties = query.order_by(Property.created_at.desc()).all()
    sources = db.query(TrackedSource).order_by(TrackedSource.created_at.desc()).all()
    unit_query = db.query(DetectedUnit).filter(DetectedUnit.is_available.is_(True))
    if unit_fit:
        unit_query = unit_query.filter(DetectedUnit.fit_label == unit_fit)
    unit_sort_key = unit_sort if unit_sort in UNIT_SORTS else "newest"
    units = unit_query.order_by(UNIT_SORTS[unit_sort_key][1]).all()
    recent_changes = (
        db.query(UnitChange)
        .order_by(UnitChange.detected_at.desc())
        .limit(50)
        .all()
    )

    return templates.TemplateResponse(
        "board.html",
        {
            "request": request,
            "token": token,
            "properties": properties,
            "sources": sources,
            "units": units,
            "recent_changes": recent_changes,
            "statuses": STATUSES,
            "selected_status": status,
            "selected_fit": fit,
            "unit_fits": UNIT_FITS,
            "selected_unit_fit": unit_fit,
            "unit_sorts": UNIT_SORTS,
            "selected_unit_sort": unit_sort_key,
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


@app.get("/board/{token}/properties/new", response_class=HTMLResponse)
def new_property(token: str, request: Request):
    require_board(token)
    return templates.TemplateResponse(
        "property_form.html",
        {
            "request": request,
            "token": token,
            "property": None,
            "statuses": STATUSES,
            "property_types": PROPERTY_TYPES,
            "cities": CITIES,
        },
    )


@app.post("/board/{token}/properties", response_class=HTMLResponse)
def create_property(
    token: str,
    url: str = Form(...),
    property_name: str = Form(""),
    address: str = Form(""),
    city: str = Form(""),
    rent: int | None = Form(None),
    beds: float | None = Form(None),
    baths: float | None = Form(None),
    property_type: str = Form("Apartment"),
    has_amenities: str = Form(""),
    status: str = Form("New"),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    require_board(token)
    prop = Property(
        url=url.strip(),
        property_name=property_name.strip(),
        address=address.strip(),
        city=city.strip(),
        rent=rent,
        beds=beds,
        baths=baths,
        property_type=property_type,
        has_amenities=_parse_bool(has_amenities),
        status=status,
        notes=notes.strip(),
    )
    prop.fit_label = calculate_fit_label(prop)
    db.add(prop)
    db.commit()
    return RedirectResponse(url=board_url(token), status_code=303)


@app.get("/board/{token}/properties/{property_id}/edit", response_class=HTMLResponse)
def edit_property(
    token: str,
    property_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    require_board(token)
    prop = db.get(Property, property_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    return templates.TemplateResponse(
        "property_form.html",
        {
            "request": request,
            "token": token,
            "property": prop,
            "statuses": STATUSES,
            "property_types": PROPERTY_TYPES,
            "cities": CITIES,
        },
    )


@app.post("/board/{token}/properties/{property_id}", response_class=HTMLResponse)
def update_property(
    token: str,
    property_id: int,
    url: str = Form(...),
    property_name: str = Form(""),
    address: str = Form(""),
    city: str = Form(""),
    rent: int | None = Form(None),
    beds: float | None = Form(None),
    baths: float | None = Form(None),
    property_type: str = Form("Apartment"),
    has_amenities: str = Form(""),
    status: str = Form("New"),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    require_board(token)
    prop = db.get(Property, property_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    prop.url = url.strip()
    prop.property_name = property_name.strip()
    prop.address = address.strip()
    prop.city = city.strip()
    prop.rent = rent
    prop.beds = beds
    prop.baths = baths
    prop.property_type = property_type
    prop.has_amenities = _parse_bool(has_amenities)
    prop.status = status
    prop.notes = notes.strip()
    prop.fit_label = calculate_fit_label(prop)

    db.commit()
    return RedirectResponse(url=board_url(token), status_code=303)


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
