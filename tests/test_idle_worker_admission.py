"""Explicit, identity-bound idle resident CUDA context; no production mutations."""
import hashlib
import json

import pytest

from scripts.ops import capacity_watchdog as cw


@pytest.fixture
def bound(tmp_path, monkeypatch):
    path = tmp_path / 'job.json'; path.write_text('synthetic')
    row = dict(id='1' * 64, image='sha256:' + '2' * 64, started_at='fixed', running=True,
               labels={'com.docker.compose.project': 'psptm-minutes', 'com.docker.compose.service': 'worker'})
    identity = {k: row[k] for k in ('id', 'image', 'started_at')}
    proof = tmp_path / 'idle-worker.json'
    proof.write_text(json.dumps(dict(job_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), worker=identity)))
    proof.chmod(0o600)
    monkeypatch.setattr(cw, 'command', lambda argv: json.dumps(row))
    monkeypatch.setattr(cw, 'container_pids', lambda cid: {42})
    return {'_path': path}, row, proof


def test_binding_matches_actual_worker_and_job_bytes(bound):
    job, row, proof = bound
    assert cw.idle_worker(job)['pids'] == {42}
    job['_path'].write_text('changed')
    with pytest.raises(cw.Refused, match='idle_worker_binding_changed'): cw.idle_worker(job)


@pytest.mark.parametrize('key,value', [('id', '3' * 64), ('image', 'sha256:' + '4' * 64),
                                      ('started_at', 'restarted'), ('running', False)])
def test_replaced_or_restarted_worker_refused(bound, key, value):
    job, row, _ = bound; row[key] = value
    with pytest.raises(cw.Refused): cw.idle_worker(job)


def test_public_or_symlinked_proof_refused(bound):
    job, _, proof = bound
    proof.chmod(0o644)
    with pytest.raises(cw.Refused): cw.idle_worker(job)
    target = proof.with_name('target'); proof.rename(target); proof.symlink_to(target)
    with pytest.raises(cw.Refused): cw.idle_worker(job)


def gate_setup(monkeypatch):
    now = [0]
    checks = []
    monkeypatch.setattr(cw.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(cw.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(cw, 'production_idle', lambda: checks.append(now[0]))
    monkeypatch.setattr(cw, 'ollama_models', lambda: [])
    monkeypatch.setattr(cw, 'gpu_pids', lambda: {42})
    monkeypatch.setattr(cw, 'idle_worker', lambda job: {'pids': {42}})
    monkeypatch.setattr(cw, 'command', lambda argv: '0, 23494')
    return now, checks


def test_ten_seconds_zero_and_repeated_production_checks(monkeypatch):
    now, checks = gate_setup(monkeypatch)
    cw.resource_gate({}, starting=True)
    assert now[0] == 10 and len(checks) >= 12


@pytest.mark.parametrize('metric', ['1, 23494', '10, 23494', 'NaN, 23494', '0, NaN', '0, 100'])
def test_resident_context_needs_measured_device_zero_and_free_vram(monkeypatch, metric):
    gate_setup(monkeypatch)
    monkeypatch.setattr(cw, 'command', lambda argv: metric)
    with pytest.raises(cw.Refused): cw.resource_gate({}, starting=True)


def test_no_implicit_admission_without_binding(monkeypatch):
    gate_setup(monkeypatch)
    monkeypatch.setattr(cw, 'idle_worker', lambda job: None)
    with pytest.raises(cw.Refused, match='gpu_busy'): cw.resource_gate({}, starting=True)


def test_foreign_compute_not_allowed_by_worker_binding(monkeypatch):
    gate_setup(monkeypatch)
    monkeypatch.setattr(cw, 'gpu_pids', lambda: {42, 99})
    with pytest.raises(cw.Refused, match='gpu_busy'): cw.resource_gate({}, starting=True)


def test_new_queue_during_idle_observation_refuses(monkeypatch):
    now, _ = gate_setup(monkeypatch)
    def production():
        if now[0] >= 3: raise cw.Refused('production_busy')
    monkeypatch.setattr(cw, 'production_idle', production)
    with pytest.raises(cw.Refused, match='production_busy'): cw.resource_gate({}, starting=True)


def test_long_gap_refuses_instead_of_counting_unobserved_time(monkeypatch):
    now, _ = gate_setup(monkeypatch)
    monkeypatch.setattr(cw.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + 4))
    with pytest.raises(cw.Refused, match='idle_worker_observation_gap'): cw.resource_gate({}, starting=True)


def test_native_driver_pid_can_be_verified_after_membership_snapshot(tmp_path, monkeypatch):
    main = tmp_path / '10'; main.mkdir()
    (main / 'cgroup').write_text('0::/system.slice/ollama.service\n')
    gpu = tmp_path / '20'; gpu.mkdir()
    (gpu / 'stat').write_text('20 (runner) ' + ' '.join(['S'] + ['0'] * 18 + ['123']))
    (gpu / 'cgroup').write_text('0::/system.slice/ollama.service\n')
    monkeypatch.setattr(cw, 'command', lambda argv: 'ActiveState=active\nControlGroup=/system.slice/ollama.service\nMainPID=10')
    assert cw.native_gpu_owned(20, tmp_path)
    (gpu / 'cgroup').write_text('0::/system.slice/foreign.service\n')
    assert not cw.native_gpu_owned(20, tmp_path)
    assert not cw.native_gpu_owned(99, tmp_path)


@pytest.mark.parametrize('group', ['/system.slice/foreign.service', '/'])
def test_native_fallback_rejects_unexpected_service_group(monkeypatch, group):
    monkeypatch.setattr(cw, 'command', lambda argv: 'ActiveState=active\nControlGroup=' + group + '\nMainPID=10')
    assert not cw.native_gpu_owned(20)


def test_runtime_fallback_never_allows_other_compute(monkeypatch):
    gate_setup(monkeypatch)
    monkeypatch.setattr(cw, 'container_pids', lambda cid: set())
    monkeypatch.setattr(cw, 'native_ollama_pids', lambda: set())
    monkeypatch.setattr(cw, 'gpu_pids', lambda: {42, 99})
    seen = []
    monkeypatch.setattr(cw, 'native_gpu_owned', lambda pid: seen.append(pid) or False)
    with pytest.raises(cw.Refused, match='foreign_gpu_process'):
        cw.resource_gate({'measure': 'synthetic', 'model_digest': 'a' * 64}, starting=False)
    assert seen == [99]
