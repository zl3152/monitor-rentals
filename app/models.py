from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    property_name: Mapped[str] = mapped_column(String(255), default="")
    address: Mapped[str] = mapped_column(String(255), default="")
    city: Mapped[str] = mapped_column(String(120), default="")
    rent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    beds: Mapped[float | None] = mapped_column(Float, nullable=True)
    baths: Mapped[float | None] = mapped_column(Float, nullable=True)
    property_type: Mapped[str] = mapped_column(String(40), default="Apartment")
    has_amenities: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="New")
    fit_label: Mapped[str] = mapped_column(String(40), default="Needs review")
    notes: Mapped[str] = mapped_column(Text, default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class TrackedSource(Base):
    __tablename__ = "tracked_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(255), default="")
    city: Mapped[str] = mapped_column(String(120), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    units: Mapped[list["DetectedUnit"]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
    )


class DetectedUnit(Base):
    __tablename__ = "detected_units"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("tracked_sources.id"))
    external_id: Mapped[str] = mapped_column(String(255), default="")
    floor_plan: Mapped[str] = mapped_column(String(255), default="")
    unit_name: Mapped[str] = mapped_column(String(120), default="")
    rent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    beds: Mapped[float | None] = mapped_column(Float, nullable=True)
    baths: Mapped[float | None] = mapped_column(Float, nullable=True)
    available_date: Mapped[str] = mapped_column(String(80), default="")
    unit_url: Mapped[str] = mapped_column(Text, default="")
    fit_label: Mapped[str] = mapped_column(String(40), default="Needs review")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)

    source: Mapped[TrackedSource] = relationship(back_populates="units")


class UnitChange(Base):
    __tablename__ = "unit_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("detected_units.id"), nullable=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("tracked_sources.id"))
    change_type: Mapped[str] = mapped_column(String(80))
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")
    message: Mapped[str] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    source: Mapped[TrackedSource] = relationship()
    unit: Mapped[DetectedUnit | None] = relationship()
