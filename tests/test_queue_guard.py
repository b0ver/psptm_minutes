"""Exercise the actual deploy guard against isolated PostgreSQL, without Docker mocks."""
import os
import select
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.db import Session, Meeting, MeetingAnalysis, MeetingReport, engine

OPS = Path(__file__).resolve().parents[1] / 'scripts' / 'ops'
TABLES = ('meetings', 'meeting_analysis', 'meeting_reports')


def queues(processing=None):
    with Session.begin() as session:
        meeting = Meeting(title='Synthetic guard fixture', recording='synthetic.wav',
                          status='processing' if processing == 'meetings' else 'queued')
        session.add(meeting)
        session.flush()
        session.add(MeetingAnalysis(meeting_id=meeting.id,
                    status='processing' if processing == 'meeting_analysis' else 'queued'))
        session.add(MeetingReport(meeting_id=meeting.id, template_id='tldr',
                    status='processing' if processing == 'meeting_reports' else 'queued'))


@contextmanager
def guard():
    env = dict(os.environ, PYTHONPATH=str(OPS.parents[1]))
    process = subprocess.Popen([sys.executable, str(OPS / 'queue_guard.py')],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, env=env)
    try:
        yield process
    finally:
        try:
            process.communicate(input='\n', timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


def signal(process):
    assert select.select([process.stdout], [], [], 10)[0], 'Queue guard did not respond'
    return process.stdout.readline().strip()


@pytest.mark.parametrize('table', TABLES)
def test_processing_in_any_queue_refuses_deployment(table):
    queues(processing=table)
    with guard() as process:
        assert signal(process) == 'busy'
        assert process.wait(timeout=10) == 1


def test_guard_blocks_claims_on_all_queues_until_release():
    queues()
    with guard() as process:
        assert signal(process) == 'idle_locked'
        for table in TABLES:
            with pytest.raises(OperationalError, match="lock timeout"):
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout = '150ms'"))
                    # SKIP LOCKED bypasses row locks, never this table-level barrier.
                    connection.execute(text('SELECT * FROM ' + table + " WHERE status='queued' FOR UPDATE SKIP LOCKED"))
        assert process.poll() is None
    assert process.returncode == 0
    for table in TABLES:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '1s'"))
            assert connection.execute(text('SELECT * FROM ' + table + " WHERE status='queued' FOR UPDATE SKIP LOCKED")).first()


def test_inflight_claim_is_observed_before_guard_announces_idle():
    queues()
    # Hold the uncommitted claim before starting the guard, without a timing race.
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("UPDATE meeting_analysis SET status='processing'"))
            with guard() as process:
                assert not select.select([process.stdout], [], [], 0.3)[0]
                transaction.commit()
                assert signal(process) == 'busy'
                assert process.wait(timeout=10) == 1
        finally:
            if transaction.is_active:
                transaction.rollback()
