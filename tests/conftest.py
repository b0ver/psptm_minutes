from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import Base, engine
from app.settings import DATA_DIR
from app.api import app


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE calendar_scheduler_state, calendar_subscriptions, telegram_stats_groups, telegram_usage, telegram_users, meetings, actions, audit, outbox, bot_cursor RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def draft():
    from app.db import Session, Meeting, Action
    with Session.begin() as s:
        m = Meeting(title="Тестовая встреча", recording="test.wav", status="ready", minutes="PRIVATE MINUTES", transcript=[{"start": 12, "end": 20, "text": "PRIVATE TRANSCRIPT"}])
        s.add(m)
        s.flush()
        a = Action(meeting_id=m.id, text="Подготовить отчёт", source_seconds=12, evidence="PRIVATE EVIDENCE")
        s.add(a)
        s.flush()
        return a.id
