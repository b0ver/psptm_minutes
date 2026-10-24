"""Window boundaries, immutable ownership and cleanup before releasing GPU lease."""
import copy
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from app.db import Session, Meeting, MeetingAnalysis
from scripts.ops import capacity_watchdog as cw


@pytest.mark.parametrize('date,expected', [
    ('2026-10-21T06:59:00', None),
    ('2026-10-21T07:00:00', '2026-10-21T08:45:00+03:00'),
    ('2026-10-21T08:34:00', '2026-10-21T08:45:00+03:00'),
    ('2026-10-21T08:36:00', None),
    ('2026-10-21T09:00:00', None),
    ('2026-10-24T23:00:00', '2026-10-25T01:00:00+03:00'),
    ('2026-10-25T23:30:00', '2026-10-25T23:45:00+03:00'),
    ('2026-10-25T23:40:00', None),
    ('2026-10-26T00:00:00', None),
])
def test_moscow_window_and_reserved_shutdown_time(date, expected):
    result = cw.deadline(datetime.fromisoformat(date).replace(tzinfo=cw.MOSCOW))
    assert (result.isoformat() if result else None) == expected


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(cw, 'capture_evidence', lambda job: None)
    tmp_path.chmod(0o700)
    lock = tmp_path / 'lab.lock'
    lock.touch(mode=0o600)
    path = tmp_path / 'job.json'
    data = dict(run_id='a' * 16, project='psptm-minutes-capacity-' + 'a' * 16,
                measure='1' * 64, database='2' * 64, model_digest='3' * 64,
                lab_lock=str(lock))
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    return cw.load_job(path)


def row(job, role):
    return dict(id=job[role], labels={cw.OWNER_LABEL: job['run_id'],
                'com.docker.compose.project': job['project'],
                'com.docker.compose.service': 'measure' if role == 'measure' else 'capacity-db'},
                status='created', running=False, exit_code=0, health='healthy')


@pytest.mark.parametrize('field,value', [
    ('id', '9' * 64), (cw.OWNER_LABEL, 'different'),
    ('com.docker.compose.project', 'psptm-minutes'),
    ('com.docker.compose.service', 'worker'),
])
def test_cleanup_refuses_reused_or_foreign_container(job, monkeypatch, field, value):
    data = row(job, 'measure')
    if field == 'id':
        data[field] = value
    else:
        data['labels'][field] = value
    monkeypatch.setattr(cw, 'inspect', lambda identifier: data)
    monkeypatch.setattr(cw, 'command', lambda *a, **k: pytest.fail('Must not issue stop'))
    with pytest.raises(cw.Refused, match='container_ownership_mismatch'):
        cw.cleanup(job)


def test_job_rejects_production_project_or_public_config(job):
    data = json.loads(job['_path'].read_text())
    data['project'] = 'psptm-minutes'
    job['_path'].write_text(json.dumps(data))
    with pytest.raises(cw.Refused, match='invalid_project'):
        cw.load_job(job['_path'])
    job['_path'].chmod(0o644)
    with pytest.raises(cw.Refused, match='private_job_required'):
        cw.load_job(job['_path'])


def test_database_not_stopped_if_measure_cleanup_is_unconfirmed(job, monkeypatch):
    rows = {job[role]: row(job, role) for role in ('measure', 'database')}
    for data in rows.values():
        data.update(status='running', running=True)
    monkeypatch.setattr(cw, 'inspect', lambda identifier: rows[identifier])
    stops = []
    monkeypatch.setattr(cw, 'command', lambda argv, **kwargs: stops.append(argv[-1]) or '')
    with pytest.raises(cw.Refused, match='cleanup_unconfirmed'):
        cw.cleanup(job)
    assert stops == [job['measure']]


def clock(monkeypatch):
    now = datetime(2026, 10, 21, 7, 0, tzinfo=cw.MOSCOW)
    class Frozen:
        @classmethod
        def now(cls, zone):
            return now
    monkeypatch.setattr(cw, 'datetime', Frozen)
    monkeypatch.setattr(cw.time, 'time', lambda: now.timestamp())
    monkeypatch.setattr(cw.time, 'sleep', lambda seconds: None)
    return now


def test_production_work_interrupts_only_own_containers_before_unlock(job, monkeypatch):
    now = clock(monkeypatch)
    rows = {job[role]: row(job, role) for role in ('measure', 'database')}
    monkeypatch.setattr(cw, 'inspect', lambda identifier: copy.deepcopy(rows[identifier]))
    monkeypatch.setattr(cw, 'production_idle', lambda: None)
    stops = []
    def command(argv, **kwargs):
        data = rows[argv[-1]]
        if argv[1] == 'start':
            data.update(running=True, status='running')
        else:
            assert argv[1:4] == ['stop', '--time', '10']
            with Path(job['lab_lock']).open('r+') as other:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            stops.append(argv[-1])
            data.update(running=False, status='exited')
        return ''
    monkeypatch.setattr(cw, 'command', command)
    def resource_gate(job, starting):
        if not starting:
            raise cw.Refused('production_busy')
    monkeypatch.setattr(cw, 'resource_gate', resource_gate)
    with pytest.raises(cw.Refused, match='production_busy'):
        cw.supervise(job, cw.deadline(now).timestamp())
    assert stops == [job['measure'], job['database']]
    with Path(job['lab_lock']).open('r+') as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_busy_lab_never_starts_container(job, monkeypatch):
    now = clock(monkeypatch)
    monkeypatch.setattr(cw, 'inspect', lambda identifier: row(job, 'measure' if identifier == job['measure'] else 'database'))
    monkeypatch.setattr(cw, 'command', lambda *a, **k: pytest.fail('Must not start or stop'))
    with Path(job['lab_lock']).open('r+') as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(cw.Refused, match='lab_busy'):
            cw.supervise(job, cw.deadline(now).timestamp())


def test_gpu_refusal_after_database_health_never_starts_client(job, monkeypatch):
    now = clock(monkeypatch)
    rows = {job[role]: row(job, role) for role in ('measure', 'database')}
    monkeypatch.setattr(cw, 'inspect', lambda identifier: copy.deepcopy(rows[identifier]))
    monkeypatch.setattr(cw, 'production_idle', lambda: None)
    starts = []
    def command(argv, **kw):
        data = rows[argv[-1]]
        if argv[1] == 'start':
            starts.append(argv[-1]); data.update(running=True, status='running')
        else:
            data.update(running=False, status='exited')
        return ''
    def gate(job, starting):
        assert rows[job['database']]['running'] and rows[job['database']]['health'] == 'healthy'
        raise cw.Refused('gpu_busy')
    monkeypatch.setattr(cw, 'command', command)
    monkeypatch.setattr(cw, 'resource_gate', gate)
    with pytest.raises(cw.Refused, match='gpu_busy'): cw.supervise(job, cw.deadline(now).timestamp())
    assert starts == [job['database']] and rows[job['measure']]['status'] == 'created'
    assert not rows[job['database']]['running']


def test_closed_window_never_starts_container(job, monkeypatch):
    class Closed:
        @classmethod
        def now(cls, zone):
            return datetime(2026, 10, 21, 13, tzinfo=cw.MOSCOW)
    monkeypatch.setattr(cw, 'datetime', Closed)
    monkeypatch.setattr(cw, 'command', lambda *a, **k: pytest.fail('Must not call Docker'))
    with pytest.raises(cw.Refused, match='outside_window'):
        cw.supervise(job, 1)
    with pytest.raises(cw.Refused, match='outside_window'):
        cw.start_service(job)


def test_production_probe_counts_real_runnable_and_processing_work():
    with Session.begin() as session:
        failed = Meeting(title='Synthetic', recording='synthetic.wav', status='failed')
        ready = Meeting(title='Synthetic', recording='synthetic.wav', status='ready',
                        transcript=[{'start': 0, 'end': 1, 'text': 'Synthetic'}])
        session.add_all([failed, ready]); session.flush()
        session.add_all([MeetingAnalysis(meeting_id=failed.id, status='queued'),
                        MeetingAnalysis(meeting_id=ready.id, status='queued')])
    output = subprocess.run([sys.executable, '-c', cw.QUEUE_PROBE],
                            env=dict(os.environ, PYTHONPATH='/app'), capture_output=True, text=True, check=True)
    counts = json.loads(output.stdout)
    assert counts == dict(meetings=0, meeting_analysis=0, meeting_reports=0,
                          queued_meetings=0, queued_analysis=1)


@pytest.mark.parametrize('stop_at', [float('nan'), float('inf'), 0])
def test_invalid_deadline_never_starts_container(job, monkeypatch, stop_at):
    clock(monkeypatch)
    monkeypatch.setattr(cw, 'command', lambda *a, **k: pytest.fail('Must not call Docker'))
    with pytest.raises(cw.Refused, match='outside_window'):
        cw.supervise(job, stop_at)


def test_runtime_keeps_reserved_deadline_without_admission_headroom():
    now = datetime(2026, 10, 21, 8, 40, tzinfo=cw.MOSCOW)
    assert cw.deadline(now) is None
    assert cw.deadline(now, minimum_minutes=0).isoformat() == '2026-10-21T08:45:00+03:00'


def test_failed_evidence_capture_still_stops_own_database(job, monkeypatch):
    rows = {job[role]: row(job, role) for role in ('measure', 'database')}
    for data in rows.values():
        data.update(running=True, status='running')
    monkeypatch.setattr(cw, 'inspect', lambda identifier: rows[identifier])
    def stop(argv, **kwargs):
        rows[argv[-1]].update(running=False, status='exited')
        return ''
    monkeypatch.setattr(cw, 'command', stop)
    def failed(job):
        raise OSError('Synthetic disk failure')
    monkeypatch.setattr(cw, 'capture_evidence', failed)
    with pytest.raises(cw.Refused, match='evidence_capture_failed'):
        cw.cleanup(job)
    assert not any(data['running'] for data in rows.values())


@pytest.mark.parametrize('telemetry,accepted', [
    ('# header\n0 2177 G - - - - - - 114 0 Xorg\n0 2501 G - - - - - - 10 0 gnome-shell', True),
    ('0 99 C - - - - - - 100 0 python', False),
    ('0 99 C+G - - - - - - 100 0 Xorg', False),
    ('0 - - - - - - - - - - -', False),
    ('unsupported', False),
    ('1 99 G - - - - - - 10 0 Xorg', False),
])
def test_graphics_classification_uses_driver_type(monkeypatch, telemetry, accepted):
    monkeypatch.setattr(cw, 'command', lambda *a, **k: telemetry)
    if accepted:
        assert cw.graphics_only()
    else:
        try:
            assert not cw.graphics_only()
        except cw.Refused:
            pass


@pytest.mark.parametrize('utilization,compute,models,graphics,accepted', [
    (10, set(), [], True, True),
    (21, set(), [], True, False),
    (10, {99}, [], True, False),
    (10, set(), [{'digest': 'foreign'}], True, False),
    (10, set(), [], False, False),
    (float('nan'), set(), [], True, False),
])
def test_desktop_exception_preserves_admission_gates(monkeypatch, utilization, compute, models, graphics, accepted):
    monkeypatch.setattr(cw, 'production_idle', lambda: None)
    monkeypatch.setattr(cw, 'ollama_models', lambda: models)
    monkeypatch.setattr(cw, 'gpu_pids', lambda: compute)
    monkeypatch.setattr(cw, 'graphics_only', lambda: graphics)
    monkeypatch.setattr(cw, 'command', lambda *a, **k: f'{utilization},23000')
    if accepted:
        cw.resource_gate({}, starting=True)
    else:
        with pytest.raises(cw.Refused):
            cw.resource_gate({}, starting=True)


def test_native_ollama_membership_uses_systemd_cgroup(tmp_path, monkeypatch):
    monkeypatch.setattr(cw, 'command', lambda *a, **k: 'ActiveState=active\nControlGroup=/system.slice/ollama.service\n')
    folder = tmp_path / 'system.slice/ollama.service'
    (folder / 'runner').mkdir(parents=True)
    (folder / 'cgroup.procs').write_text('3396\n')
    (folder / 'runner/cgroup.procs').write_text('4400\n')
    assert cw.native_ollama_pids(tmp_path) == {3396, 4400}


@pytest.mark.parametrize('group', ['/system.slice/other.service', '/', ''])
def test_native_ollama_refuses_unexpected_cgroup(monkeypatch, group):
    monkeypatch.setattr(cw, 'command', lambda *a, **k: 'ActiveState=active\nControlGroup=' + group)
    with pytest.raises(cw.Refused):
        cw.native_ollama_pids()
