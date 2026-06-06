from datetime import datetime

from sqlalchemy.orm import Session

from app.config import MAX_RENT, TARGET_CITIES
from app.emailer import send_change_digest
from app.models import DetectedUnit, TrackedSource, UnitChange
from app.parser import ParsedUnit, parse_source_url


def check_source(db: Session, source: TrackedSource) -> int:
    parsed_units = parse_source_url(source.url)
    now = datetime.utcnow()
    seen_external_ids = set()
    changes = []

    for parsed in parsed_units:
        seen_external_ids.add(parsed.external_id)
        unit = (
            db.query(DetectedUnit)
            .filter(
                DetectedUnit.source_id == source.id,
                DetectedUnit.external_id == parsed.external_id,
            )
            .first()
        )

        if unit is None:
            unit = DetectedUnit(source_id=source.id, external_id=parsed.external_id)
            db.add(unit)
            db.flush()
            changes.append(
                _record_change(
                    db,
                    source,
                    unit,
                    "new_unit",
                    "",
                    _unit_summary(parsed),
                    f"New available unit detected: {_unit_summary(parsed)}",
                )
            )
        else:
            changes.extend(_detect_unit_changes(db, source, unit, parsed))

        _apply_parsed_unit(unit, parsed, source, now)

    existing_units = db.query(DetectedUnit).filter(DetectedUnit.source_id == source.id).all()
    for unit in existing_units:
        if unit.external_id not in seen_external_ids and unit.is_available:
            changes.append(
                _record_change(
                    db,
                    source,
                    unit,
                    "unavailable",
                    "available",
                    "unavailable",
                    f"{unit.floor_plan or unit.unit_name} is no longer listed as available.",
                )
            )
            unit.is_available = False

    source.last_checked_at = now
    if changes:
        source.updated_at = now
    db.commit()
    if changes and send_change_digest(changes):
        db.commit()
    return len(parsed_units)


def check_all_active_sources(db: Session) -> int:
    total = 0
    sources = db.query(TrackedSource).filter(TrackedSource.is_active.is_(True)).all()
    for source in sources:
        total += check_source(db, source)
    return total


def _apply_parsed_unit(
    unit: DetectedUnit,
    parsed: ParsedUnit,
    source: TrackedSource,
    checked_at: datetime,
) -> None:
    unit.floor_plan = parsed.floor_plan
    unit.unit_name = parsed.unit_name
    unit.rent = parsed.rent
    unit.beds = parsed.beds
    unit.baths = parsed.baths
    unit.available_date = parsed.available_date
    unit.unit_url = parsed.unit_url
    unit.fit_label = calculate_detected_unit_fit(source, parsed)
    unit.last_seen_at = checked_at
    unit.is_available = True


def calculate_detected_unit_fit(source: TrackedSource, unit: ParsedUnit) -> str:
    city = (source.city or "").strip().lower()
    if city not in TARGET_CITIES:
        return "Needs review"
    if unit.rent is None or unit.beds is None or unit.baths is None:
        return "Needs review"
    if unit.rent > MAX_RENT:
        return "Not a fit"
    if unit.beds >= 2 and unit.baths >= 2:
        return "Great fit"
    if unit.beds >= 1 and unit.baths >= 1:
        return "Possible fit"
    return "Not a fit"


def _detect_unit_changes(
    db: Session,
    source: TrackedSource,
    unit: DetectedUnit,
    parsed: ParsedUnit,
) -> list[UnitChange]:
    changes = []
    if not unit.is_available:
        changes.append(
            _record_change(
                db,
                source,
                unit,
                "available_again",
                "unavailable",
                "available",
                f"{parsed.floor_plan} is available again: {_unit_summary(parsed)}",
            )
        )
    if unit.rent != parsed.rent:
        changes.append(
            _record_change(
                db,
                source,
                unit,
                "rent_changed",
                _format_money(unit.rent),
                _format_money(parsed.rent),
                f"{parsed.floor_plan} rent changed from {_format_money(unit.rent)} to {_format_money(parsed.rent)}.",
            )
        )
    if unit.available_date != parsed.available_date:
        changes.append(
            _record_change(
                db,
                source,
                unit,
                "availability_changed",
                unit.available_date,
                parsed.available_date,
                f"{parsed.floor_plan} availability changed from {unit.available_date or 'unknown'} to {parsed.available_date or 'unknown'}.",
            )
        )

    old_fit = unit.fit_label
    new_fit = calculate_detected_unit_fit(source, parsed)
    if old_fit != new_fit:
        changes.append(
            _record_change(
                db,
                source,
                unit,
                "fit_changed",
                old_fit,
                new_fit,
                f"{parsed.floor_plan} fit changed from {old_fit} to {new_fit}.",
            )
        )
    return changes


def _record_change(
    db: Session,
    source: TrackedSource,
    unit: DetectedUnit | None,
    change_type: str,
    old_value: str,
    new_value: str,
    message: str,
) -> UnitChange:
    change = UnitChange(
        source_id=source.id,
        unit_id=unit.id if unit else None,
        change_type=change_type,
        old_value=str(old_value or ""),
        new_value=str(new_value or ""),
        message=message,
    )
    db.add(change)
    return change


def _unit_summary(unit: ParsedUnit) -> str:
    bed_text = "Studio" if unit.beds == 0 else f"{unit.beds:g} bed" if unit.beds is not None else "unknown beds"
    bath_text = f"{unit.baths:g} bath" if unit.baths is not None else "unknown baths"
    return f"{unit.floor_plan}, {bed_text}/{bath_text}, {_format_money(unit.rent)}, {unit.available_date}"


def _format_money(value: int | None) -> str:
    if value is None:
        return "unknown rent"
    return f"${value:,}"
