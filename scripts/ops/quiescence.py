"""Run via the current API container's stdin; print counts, never meeting content."""
import json
from sqlalchemy import text
from app.db import engine

with engine.connect() as connection:
    counts = {
        table: connection.scalar(text(f"SELECT count(*) FROM {table} WHERE status='processing'"))
        for table in ("meetings", "meeting_analysis", "meeting_reports")
    }
print(json.dumps(counts))
raise SystemExit(1 if any(counts.values()) else 0)
