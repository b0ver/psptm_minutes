"""Restoration invariants with synthetic containers; never production endpoints."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts.ops import capacity_worker_pause_probe as p


@pytest.fixture
def manifest(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / 'probe.json'
    value = dict(run_id='a' * 16, project='psptm-minutes-pause-probe-' + 'a' * 16,
                 worker='1' * 64, control='2' * 64, image='sha256:' + '3' * 64,
                 script_sha256=hashlib.sha256(Path(p.__file__).read_bytes()).hexdigest())
    p.write(path, value)
    return p.load(path)


def state(manifest, role, running=True):
    return dict(id=manifest[role], image=manifest['image'], status='running' if running else 'exited',
                running=running, labels={p.LABEL: manifest['run_id'],
                'com.docker.compose.project': manifest['project'], 'com.docker.compose.service': role})


def journal(m):
    path = m['_path'].parent / 'lease-state.json'
    p.write(path, dict(run_id=m['run_id'], worker=m['worker'], originally_running=True, phase='armed'))
    return path


def test_production_manifest_and_direct_restore_are_rejected_before_docker(manifest, monkeypatch):
    manifest['project'] = 'psptm-minutes'
    path = manifest['_path']; value = {k: v for k, v in manifest.items() if k != '_path'}
    p.write(path, value)
    monkeypatch.setattr(p.cw, 'command', lambda *a, **kw: pytest.fail('No production commands'))
    with pytest.raises(p.cw.Refused, match='synthetic_project_required'):
        p.load(path)
    journal(manifest)
    with pytest.raises(p.cw.Refused, match='synthetic_project_required'):
        p.restore(manifest)


@pytest.mark.parametrize('change', ['public', 'code', 'reused_id'])
def test_private_immutable_manifest_required(manifest, change):
    path = manifest['_path']; value = {k: v for k, v in manifest.items() if k != '_path'}
    if change == 'code': value['script_sha256'] = 'f' * 64
    if change == 'reused_id': value['control'] = value['worker']
    p.write(path, value)
    if change == 'public': path.chmod(0o644)
    with pytest.raises(p.cw.Refused): p.load(path)


def test_restore_never_starts_unarmed_worker(manifest, monkeypatch):
    monkeypatch.setattr(p.cw, 'command', lambda *a, **kw: pytest.fail('No mutation without journal'))
    p.restore(manifest)


def test_restore_checks_image_and_labels_before_start(manifest, monkeypatch):
    journal(manifest)
    row = state(manifest, 'worker', False); row['image'] = 'sha256:' + '9' * 64
    calls = []
    def command(argv, **kw):
        calls.append(argv)
        assert argv[:2] == ['docker', 'inspect']
        return json.dumps(row)
    monkeypatch.setattr(p.cw, 'command', command)
    with pytest.raises(p.cw.Refused, match='synthetic_ownership_mismatch'): p.restore(manifest)
    assert len(calls) == 1


def test_restore_is_idempotent_and_checks_running_after_start(manifest, monkeypatch):
    path = journal(manifest); row = state(manifest, 'worker', False); starts = []
    def command(argv, **kw):
        if argv[:2] == ['docker', 'inspect']: return json.dumps(row)
        assert argv == ['docker', 'start', manifest['worker']]
        starts.append(argv); row.update(running=True, status='running'); return ''
    monkeypatch.setattr(p.cw, 'command', command)
    p.restore(manifest); p.restore(manifest)
    assert len(starts) == 1 and json.loads(path.read_text())['phase'] == 'restored'


def test_failed_start_cannot_claim_restoration(manifest, monkeypatch):
    path = journal(manifest); row = state(manifest, 'worker', False)
    monkeypatch.setattr(p.cw, 'command', lambda argv, **kw: json.dumps(row) if argv[1] == 'inspect' else '')
    with pytest.raises(p.cw.Refused, match='restoration_unconfirmed'): p.restore(manifest)
    assert json.loads(path.read_text())['phase'] == 'armed'


def test_journal_precedes_stop_and_exception_restores_worker(manifest, monkeypatch):
    rows = {r: state(manifest, r) for r in ('worker', 'control')}
    path = manifest['_path'].parent / 'lease-state.json'
    def command(argv, **kw):
        if argv[1] == 'inspect':
            return json.dumps(next(v for v in rows.values() if v['id'] == argv[-1]))
        if argv[1] == 'stop':
            assert json.loads(path.read_text())['phase'] == 'armed'
            rows['worker'].update(running=False, status='exited')
        elif argv[1] == 'start': rows['worker'].update(running=True, status='running')
        else: pytest.fail('Unexpected command')
        return ''
    def interrupted(_): raise p.cw.Refused('synthetic_failure')
    monkeypatch.setattr(p.cw, 'command', command)
    monkeypatch.setattr(p.time, 'sleep', interrupted)
    with pytest.raises(p.cw.Refused, match='synthetic_failure'): p.hold(manifest)
    assert rows['worker']['running'] and rows['control']['running']
    assert json.loads(path.read_text())['phase'] == 'restored'
