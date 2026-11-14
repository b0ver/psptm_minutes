"""Read-only lab admission. All database data in these tests is synthetic."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from scripts.ops import capacity_watchdog as cw


@pytest.mark.parametrize('active', ['queued', 'running', 'unknown'])
def test_lab_work_blocks_admission(monkeypatch, active):
    def inspect(name):
        project, service = ('ml-school', 'lab-worker') if name == cw.LAB else ('psptm-minutes', 'api' if name == cw.API else 'telemost-agent')
        return dict(running=True, health='healthy', labels={'com.docker.compose.project': project, 'com.docker.compose.service': service})
    def command(argv):
        name = argv[2]
        if name == cw.API:
            return json.dumps(dict(meetings=0, meeting_analysis=0, meeting_reports=0, queued_meetings=0, queued_analysis=0))
        if name == cw.AGENT: return '{"blocked":0}'
        assert name == cw.LAB and '-B' in argv
        return json.dumps({key: int(key == active) for key in ['queued', 'running', 'unknown']})
    monkeypatch.setattr(cw, 'inspect', inspect)
    monkeypatch.setattr(cw, 'command', command)
    with pytest.raises(cw.Refused, match='production_busy'): cw.production_idle()


def test_lab_sql_is_read_only_and_reports_unknown_states(tmp_path):
    path = tmp_path / 'lab database.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE lab_jobs (state TEXT NOT NULL)')
        db.executemany('INSERT INTO lab_jobs VALUES (?)', [(s,) for s in ['queued', 'running', 'done', 'failed', 'new-state']])
    before = hashlib.sha256(path.read_bytes()).hexdigest(); path.chmod(0o444)
    prefix = 'import types,sys;from pathlib import Path;m=types.ModuleType("app.config");m.DB_PATH=Path('+repr(str(path))+');sys.modules["app.config"]=m;'
    result = subprocess.run([sys.executable, '-B', '-c', prefix + 'exec('+repr(cw.LAB_QUEUE_PROBE)+')'],
                            capture_output=True, text=True, check=True, env=dict(os.environ, PYTHONPATH='/app'))
    assert json.loads(result.stdout) == dict(queued=1, running=1, unknown=1)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
