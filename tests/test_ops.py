"""Deployment gates must refuse missing services and schema drift."""
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy

OPS = Path(__file__).resolve().parents[1] / 'scripts' / 'ops'


def check_services(tmp_path, rows):
    fixture = tmp_path / 'services.json'
    fixture.write_text('\n'.join(json.dumps(row) for row in rows))
    return subprocess.run([sys.executable, str(OPS / 'check_services.py'), str(fixture)],
                          capture_output=True, text=True)


def service_rows():
    return [{'Service': name, 'State': 'running', 'Health': 'healthy' if name in
             {'db', 'api', 'telemost-agent'} else ''} for name in
            ('db', 'api', 'telemost-agent', 'worker', 'analysis-worker', 'telegram', 'calendar-scheduler')]


def test_service_gate_accepts_current_compose_shape(tmp_path):
    assert check_services(tmp_path, service_rows()).returncode == 0


@pytest.mark.parametrize('problem', ['missing', 'unhealthy', 'restarting', 'starting'])
def test_service_gate_refuses_broken_release(tmp_path, problem):
    rows = service_rows()
    if problem == 'missing':
        rows.pop()
    elif problem == 'restarting':
        rows[0]['State'] = 'restarting'
    else:
        rows[0]['Health'] = problem
    assert check_services(tmp_path, rows).returncode == 1


def test_schema_gate_accepts_current_schema():
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(OPS / 'schema_check.py'))
    assert error.value.code == 0


def test_schema_gate_refuses_missing_column(monkeypatch):
    original = sqlalchemy.inspect
    def inspect(connection):
        inspector = original(connection)
        get_columns = inspector.get_columns
        def filtered(name):
            columns = get_columns(name)
            return [column for column in columns if column['name'] != 'recording'] if name == 'meetings' else columns
        inspector.get_columns = filtered
        return inspector
    monkeypatch.setattr(sqlalchemy, 'inspect', inspect)
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(OPS / 'schema_check.py'))
    assert error.value.code == 1


def test_quiescence_gate_refuses_active_job():
    from app.db import Session, Meeting
    with Session.begin() as session:
        session.add(Meeting(title='fixture', recording='fixture.wav', status='processing'))
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(OPS / 'quiescence.py'))
    assert error.value.code == 1
