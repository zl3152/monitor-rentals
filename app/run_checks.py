from app.checker import check_all_active_sources
from app.database import SessionLocal


def main() -> None:
    db = SessionLocal()
    try:
        count = check_all_active_sources(db)
    finally:
        db.close()
    print(f"Checked active sources and found {count} available units.")


if __name__ == "__main__":
    main()
