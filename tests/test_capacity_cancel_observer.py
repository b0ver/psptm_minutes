"""Synthetic telemetry only; no NVIDIA, systemd, Ollama or remote calls."""
import json
import sys

import pytest

from scripts.ops import capacity_cancel_observer as obs


@pytest.mark.parametrize('body,expected', [
    ('# header\n0 12 C - -\n0 13 G 5 1', {12: None}),
    ('0 12 C 0 0', {12: 0}),
    ('0 12 C+G 89 1', {12: 89}),
    ('0 - - - -', {}),
])
def test_driver_sm_parser_preserves_unknown(body, expected):
    assert obs.pmon_compute(body) == expected


@pytest.mark.parametrize('body', ['', '# header only', '1 12 C 0', '0 12 X 0', '0 12 C NaN',
                                  '0 12 C 101', '0 12 C 0\n0 12 C 1'])
def test_ambiguous_driver_output_refused(body):
    with pytest.raises(obs.cw.Refused):
        obs.pmon_compute(body)


def test_exact_pid_cgroup_and_start_time(tmp_path):
    folder = tmp_path / '123'; folder.mkdir()
    (folder / 'stat').write_text('123 (runner with spaces) ' + ' '.join(['S'] + ['0'] * 18 + ['987']))
    (folder / 'cgroup').write_text('0::/system.slice/ollama.service\n')
    assert obs.ollama_identity(123, tmp_path) == '987'
    (folder / 'cgroup').write_text('0::/system.slice/other.service\n')
    with pytest.raises(obs.cw.Refused, match='foreign_gpu_process'):
        obs.ollama_identity(123, tmp_path)
    with pytest.raises(obs.cw.Refused, match='process_identity_unavailable'):
        obs.ollama_identity(999, tmp_path)


def snapshot_stubs(monkeypatch, sm='0', pids=None):
    pids = {12} if pids is None else pids
    monkeypatch.setattr(obs.cw, 'owned', lambda *a: {'status': 'exited'})
    monkeypatch.setattr(obs.cw, 'production_idle', lambda: None)
    monkeypatch.setattr(obs.cw, 'ollama_models', lambda: [])
    monkeypatch.setattr(obs.cw, 'gpu_pids', lambda: pids)
    monkeypatch.setattr(obs, 'ollama_identity', lambda pid: '987')
    monkeypatch.setattr(obs.cw, 'command', lambda argv: f'0 12 C {sm} 0' if 'pmon' in argv else '0')


def test_device_zero_is_independent_evidence_without_converting_unknown_sm(monkeypatch):
    snapshot_stubs(monkeypatch, '-')
    row = obs.snapshot({})
    assert row['compute_sm_percent'] == [None] and row['quiescent']
    assert row['quiescence_basis'] == 'whole_device_zero'


def test_unknown_sm_with_positive_device_util_is_not_quiescent(monkeypatch):
    snapshot_stubs(monkeypatch, '-')
    monkeypatch.setattr(obs.cw, 'command', lambda argv: '0 12 C - 0' if 'pmon' in argv else '1')
    assert not obs.snapshot({})['quiescent']


def test_positive_compute_sm_contradicts_device_zero(monkeypatch):
    snapshot_stubs(monkeypatch, '89')
    assert not obs.snapshot({})['quiescent']


def test_bound_worker_pid_uses_exact_container_cgroup(monkeypatch):
    snapshot_stubs(monkeypatch, '-')
    monkeypatch.setattr(obs.cw, 'idle_worker', lambda job: {'id': 'a' * 64, 'pids': {12}})
    groups = []
    def identity(pid, group):
        groups.append(group)
        return '987'
    monkeypatch.setattr(obs, 'process_identity', identity)
    row = obs.snapshot({})
    assert row['quiescent'] and row['compute_sm_percent'] == [None]
    assert groups == ['/system.slice/docker-' + 'a' * 64 + '.scope'] * 2


def test_worker_change_between_snapshots_cannot_certify_quiescence(monkeypatch):
    snapshot_stubs(monkeypatch)
    workers = iter(['a', 'b'])
    monkeypatch.setattr(obs.cw, 'idle_worker', lambda job: {'id': next(workers) * 64, 'pids': {12}})
    monkeypatch.setattr(obs, 'process_identity', lambda pid, group: group)
    with pytest.raises(obs.cw.Refused, match='process_identity_changed'): obs.snapshot({})


def test_pid_disappearing_between_sources_is_explicit_refusal(monkeypatch):
    snapshot_stubs(monkeypatch, pids=set())
    with pytest.raises(obs.cw.Refused, match='gpu_process_snapshot_changed'):
        obs.snapshot({})


def test_live_owned_container_refused_before_telemetry(monkeypatch):
    snapshot_stubs(monkeypatch)
    monkeypatch.setattr(obs.cw, 'owned', lambda *a: {'status': 'running'})
    with pytest.raises(obs.cw.Refused, match='owned_containers_not_stopped'):
        obs.snapshot({})


def run_samples(values, gaps=None):
    now = [0]
    count = [0]
    def sample(job):
        i = count[0]; count[0] += 1
        return {'quiescent': values[min(i, len(values) - 1)]}
    def sleep(seconds):
        now[0] += gaps.get(count[0], seconds) if gaps else seconds
    return obs.observe({}, sample=sample, clock=lambda: now[0], sleep=sleep)


def test_requires_ten_continuous_seconds_and_resets_after_activity():
    rows, okay = run_samples([True] * 7 + [False] + [True] * 11)
    assert okay and rows[-1]['elapsed_seconds'] == 18


def test_sampling_gap_resets_continuity():
    rows, okay = run_samples([True], gaps={5: 4})
    assert okay and rows[-1]['elapsed_seconds'] == 18


def test_non_quiescent_times_out():
    rows, okay = run_samples([False])
    assert not okay and len(rows) == 30


def test_partial_evidence_survives_failure_and_existing_receipt_not_overwritten(tmp_path, monkeypatch, capsys):
    lock = tmp_path / 'lock'; lock.touch()
    monkeypatch.setattr(sys, 'argv', ['observer', str(tmp_path / 'job.json')])
    monkeypatch.setattr(obs.cw, 'load_job', lambda p: {'_path': p, 'run_id': 'synthetic', 'lab_lock': str(lock)})
    def fail(job, rows):
        rows.append({'quiescent': False})
        raise obs.cw.Refused('gpu_process_snapshot_changed')
    monkeypatch.setattr(obs, 'observe', fail)
    assert obs.main() == 1
    target = tmp_path / 'post-stop-observation.json'
    saved = target.read_text(); report = json.loads(saved)
    assert report['samples'] == [{'quiescent': False}]
    assert report['result'] == 'gpu_process_snapshot_changed'
    assert not report['server_cancellation_verified']
    assert obs.main() == 1
    assert target.read_text() == saved
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['result'] == 'observation_already_exists'
