"""Shared Jinja2 templates and display filters.

Every database timestamp is stored as naive UTC. Operators work in IST, so the
`ist` filter converts for display only. The database and the JSON APIs stay UTC.
"""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from fastapi.templating import Jinja2Templates

IST = timezone(timedelta(hours=5, minutes=30), "IST")

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def ist(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Render a stored timestamp in IST, with the zone labelled.

    A naive value is treated as UTC (that is how the database writes it).
    None renders as an em dash, matching the rest of the UI.
    """
    if value is None:
        return "—"
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return f"{aware.astimezone(IST).strftime(fmt)} IST"


templates.env.filters["ist"] = ist
