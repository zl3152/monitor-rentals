# monitor-rentals

A lightweight rental tracker for Bay Area apartment listings.

## MVP scope

- Add apartment community/property manager source URLs
- Track source pages that should be checked for new units
- Prepare a detected-units table for future automated parsing
- Add properties manually by URL
- Track rent, beds, baths, city, status, amenities, and notes
- Automatically label each listing as `Great fit`, `Possible fit`, `Needs review`, or `Not a fit`
- Use a shared secret board URL for simple roommate access

Current fit rules:

- Budget under `$5,500`
- Cities: Menlo Park, Palo Alto, Mountain View, Redwood City
- Apartments only
- Amenities required
- Prefer `2b2b`, accept `1b1b`

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/board/<your-board-token>
```

The SQLite database is created at `data/rental_tracker.db` on first run.

## Scheduled checks

When the FastAPI app is running, active source pages are checked automatically:

- Hourly from 7 AM to 11 PM Pacific time
- At midnight and 6 AM overnight

Use **Check now** on the dashboard when you want an immediate manual check.

You can also run all active checks from the terminal:

```bash
python -m app.run_checks
```

## Gmail SMTP

The checker records changes even when email is not configured. To send email alerts,
create a `.env` file from `.env.example` and provide a Gmail app password.

## Deploy on Render

This repo includes `render.yaml` for a simple Render web service.

Recommended Render settings:

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Persistent disk mount path: `/opt/render/project/src/data`
- Environment variable: `DATA_DIR=/opt/render/project/src/data`

Set these Render environment variables manually:

```text
PYTHON_VERSION=3.13
BOARD_TOKEN=<your-board-token>
APP_BASE_URL=https://<your-render-service>.onrender.com/board/<your-board-token>
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=<your-gmail-address>
SMTP_PASSWORD=<your-gmail-app-password>
NOTIFY_EMAILS=<you@example.com>,<roommate@example.com>
```

Do not upload `.env` to GitHub. Render stores production secrets as environment variables.
