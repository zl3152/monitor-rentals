from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATA_DIR, DATABASE_URL


DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def run_startup_migrations() -> None:
    inspector = inspect(engine)
    if "tracked_sources" not in inspector.get_table_names():
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("tracked_sources")
    }
    migrations = {
        "last_check_status": "ALTER TABLE tracked_sources ADD COLUMN last_check_status VARCHAR(40) DEFAULT 'idle'",
        "last_check_error": "ALTER TABLE tracked_sources ADD COLUMN last_check_error TEXT DEFAULT ''",
        "last_check_started_at": "ALTER TABLE tracked_sources ADD COLUMN last_check_started_at DATETIME",
        "last_check_finished_at": "ALTER TABLE tracked_sources ADD COLUMN last_check_finished_at DATETIME",
    }

    with engine.begin() as connection:
        for column_name, statement in migrations.items():
            if column_name not in existing_columns:
                connection.execute(text(statement))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
