from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.config import BOARD_TOKEN, MAX_RENT, TARGET_CITIES
from app.checker import check_source_by_id
from app.database import Base, SessionLocal, engine, get_db
from app.fit import calculate_fit_label
from app.models import DetectedUnit, Property, TrackedSource, UnitChange
from app.scheduler import create_scheduler


Base.metadata.create_all(bind=engine)


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
    units = (
        db.query(DetectedUnit)
        .filter(DetectedUnit.is_available.is_(True))
        .order_by(DetectedUnit.first_seen_at.desc())
        .all()
    )
    recent_changes = (
        db.query(UnitChange)
        .order_by(UnitChange.detected_at.desc())
        .limit(12)
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
            "max_rent": MAX_RENT,
            "target_cities": sorted(city.title() for city in TARGET_CITIES),
        },
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
