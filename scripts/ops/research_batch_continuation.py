"""Bounded CPU coordinator. All model starts go through research_window.py start.

It never resumes an interrupted request and never accepts overall benchmark quality.
"""
import argparse
import fcntl
import hashlib
import importlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


class Stopped(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Stopped(code)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    temporary = path.with_suffix('.coordinator.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def terminal_unit(properties):
    require(properties.get('ActiveState') in {'inactive', 'failed'}, 'unit_not_terminal')
    require(properties.get('MainPID') == '0', 'unit_process_still_live')


def native_value(cache, raw, key):
    metrics, response = cache['metrics'], raw['response']
    require(metrics == raw['metrics'] and metrics['key'] == key, 'native_metrics_changed')
    require(response['done'] and response.get('done_reason') != 'length', 'native_response_incomplete')
    text = re.sub(r'<think>.*?</think>', '', response['message']['content'], flags=re.S).strip()
    value = re.sub(r'^```(?:markdown|md)?\s*|\s*```$', '', text) if isinstance(cache['value'], str) else json.loads(text)
    require(value == cache['value'], 'native_cache_value_changed')
    budget = metrics['native_budget']
    require(budget['admitted'] and not budget['truncation_enabled'], 'native_admission_changed')
    require(response['prompt_eval_count'] <= budget['native_content_token_envelope'] + budget['chat_envelope_allowance'], 'native_prompt_envelope_changed')


def synthetic_summary(summary, phase, count):
    require(phase in {'original_screen_prerequisite', 'full_synthetic'}, 'unsupported_phase')
    planned = 4 if phase == 'original_screen_prerequisite' else 24
    require(summary['planned'] == planned and summary['observed'] == count, 'synthetic_scope_changed')
    rows = summary['rows']
    require(len(rows) == (count if planned == 4 else 24), 'synthetic_row_count_changed')
    require(all(row['status'] in {'completed', 'not_started'} for row in rows), 'synthetic_failed_or_paused')
    complete = [row for row in rows if row['status'] == 'completed']
    require(len(complete) == count and all(row['passed'] is True for row in complete), 'synthetic_gold_failed')
    require(not summary.get('budget_checkpoint_pending', False), 'budget_checkpoint_requires_manual_audit')
    require(count < planned or summary['passed'] is True, 'complete_scope_not_passed')
    return 'full_synthetic' if phase == 'full_synthetic' or count == 4 else 'original_screen_prerequisite'


def audit(root, phase):
    requests = sorted((root / 'private/requests').glob('*.json'))
    calls = {f.stem: f for f in (root / 'private/calls').glob('*.json')}
    raws = {f.stem: f for f in (root / 'private/raw').glob('*.json')}
    keys = set()
    for path in requests:
        record = read(path)
        require(record['status'] == 'completed', 'interrupted_or_failed_request_requires_audit')
        keys.update(call['key'] for call in record['calls'])
    require(keys == set(calls) == set(raws), 'native_inventory_mismatch')
    for key in keys:
        native_value(read(calls[key]), read(raws[key]), key)
    cells = sorted((root / 'private/synthetic').glob('*.json'))
    require(all(read(path)['status'] == 'completed' for path in cells), 'synthetic_not_completed')
    count = len(cells)
    summary_path = root / ('compare-smoke-summary.json' if phase == 'original_screen_prerequisite' else 'full-regression-summary.json')
    next_phase = synthetic_summary(read(summary_path), phase, count)
    inventory = {str(path.relative_to(root)): sha(path) for path in requests + list(calls.values()) + list(raws.values()) + cells + [summary_path]}
    return {'status': 'passed', 'requests': len(requests), 'calls': len(calls), 'raw': len(raws),
            'synthetic_completed': count, 'next_phase': next_phase,
            'inventory_sha256': hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest(),
            'completed_synthetic_gold_passed': True, 'quality_accepted': False}


def create_job(window, state, job, phase, adapter):
    root = Path(job['runtime_root'])
    run = secrets.token_hex(8)
    batch = root.parent / ('batch-' + run)
    batch.mkdir(mode=0o700)
    argv = ['docker', 'create', '--name', 'psptm-research-coordinated-' + run,
            '--label', window.OWNER_LABEL + '=' + run, '--label', window.KIND_LABEL + '=inference',
            '--network', 'host', '--read-only', '--user', str(os.getuid()) + ':' + str(os.getgid()),
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--cpus', '2', '--memory', '4g',
            '--pids-limit', '128', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m,mode=1777',
            '-e', 'PYTHONDONTWRITEBYTECODE=1', '-v', str(root) + ':/bench',
            '-v', str(root / 'fixtures') + ':/generation:ro', '-w', '/bench']
    if phase == 'original_screen_prerequisite':
        require(sha(adapter) == '8ad03803d99f15e68764dda937b62691a7d6320d4355d08c7fa7d88aa4395314', 'screen_adapter_changed')
        argv += ['-v', str(adapter) + ':/run/research_screen_batch.py:ro']
    argv += ['--entrypoint', 'python', 'sha256:' + job['_image_id'], '-B']
    argv += ['/run/research_screen_batch.py'] if phase == 'original_screen_prerequisite' else ['full_regression.py', '--maximum-cells', '1']
    container = window.resources.command(argv).strip()
    require(re.fullmatch('[0-9a-f]{64}', container), 'created_container_identity_invalid')
    new = {key: value for key, value in job.items() if not key.startswith('_')}
    new.update(run_id=run, container=container)
    path = batch / 'job.json'
    write(path, new)
    window.owned(window.load_job(path))
    write(root.parent / 'current-batch.json', {'batchdir': str(batch), 'run_id': run,
          'container': container, 'started': False, 'phase': phase})
    return path, run


def tick(window, state_path, adapter, remaining_seconds):
    state = read(state_path)
    require(Path(state['controller']).resolve() == Path(window.__file__).resolve().parent, 'controller_pointer_changed')
    job = window.load_job(state['current_job'])
    require(Path(job['runtime_root']).parent == Path(state['execution_root']), 'execution_pointer_changed')
    require(state['current_run_id'] == job['run_id'], 'job_pointer_changed')
    row = window.owned(job)
    unit = window.resources.command(['systemctl', '--user', 'show', 'minutes-research-' + job['run_id'] + '.service',
                                     '-p', 'ActiveState', '-p', 'MainPID'])
    properties = dict(line.split('=', 1) for line in unit.splitlines() if '=' in line)
    if row['running']:
        require(properties.get('ActiveState') == 'active' and int(properties.get('MainPID', 0)) > 0, 'running_container_without_live_watchdog')
        current = Path(job['runtime_root']).parent / 'current-batch.json'
        if current.exists():
            batch = read(current)
            require(batch['run_id'] == job['run_id'], 'batch_pointer_changed')
            if not batch.get('started'):
                batch.update(started=True, unit='minutes-research-' + job['run_id'] + '.service')
                write(current, batch)
        return {'status': 'verified_live_wait', 'run_id': job['run_id']}
    phase = state['current_phase']
    require(phase in {'original_screen_prerequisite', 'full_synthetic'}, 'phase_requires_agent_review')
    if row['status'] != 'created':
        if properties.get('ActiveState') in {'active', 'activating', 'deactivating'}:
            # Container exit precedes the supervisor's final cleanup/ExecStopPost.
            # Keep the same handle and wait; do not create a replacement job.
            return {'status': 'awaiting_supervisor_terminal', 'run_id': job['run_id']}
        terminal_unit(properties)
        require(row['status'] == 'exited' and row['exit_code'] == 0, 'interrupted_container_requires_agent_audit')
        receipt = audit(Path(job['runtime_root']), phase)
        write(Path(state['current_job']).parent / 'coordinator-terminal-native-audit.json', receipt)
        require(receipt['synthetic_completed'] > state.get('synthetic_completed', 0), 'batch_made_no_new_cell')
        if receipt['synthetic_completed'] == 24:
            state.update(synthetic_completed=24, status='synthetic_24_passed_real_phase_requires_agent')
            write(state_path, state)
            return {'status': 'synthetic_24_passed_real_phase_requires_agent', 'run_id': job['run_id']}
        phase = receipt['next_phase']
    else:
        require(properties.get('ActiveState') in {'inactive', 'failed', None}, 'created_job_has_live_unit')
        receipt = None
    end = window.deadline(datetime.now(window.MOSCOW))
    if end is None or remaining_seconds < 600:
        return {'status': 'no_new_batch_insufficient_observation_or_window_time'}
    try:
        window.gate(job, starting=True)
    except window.resources.Refused as error:
        return {'status': 'verified_resource_deferral', 'reason': str(error), 'run_id': job['run_id']}
    # Explicit preflight of both shared locks; the actual supervisor reacquires them.
    for filename in window.LOCKS:
        try:
            with open(filename, 'a+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'verified_resource_deferral', 'reason': 'lab_busy', 'run_id': job['run_id']}
    require(read(state_path)['current_job'] == state['current_job'], 'concurrent_pointer_change')
    if receipt is not None:
        path, run = create_job(window, state, job, phase, adapter)
        state.update(current_job=str(path), current_run_id=run, current_phase=phase,
                     synthetic_completed=receipt['synthetic_completed'], status='coordinator_batch_prepared')
        write(state_path, state)
    else:
        path, run = Path(state['current_job']), job['run_id']
    # No Docker start/run or direct model call is allowed here.
    controller = Path(window.__file__).resolve()
    result = subprocess.run([sys.executable, str(controller), 'check'], capture_output=True, text=True, timeout=15)
    require(result.returncode == 0 and json.loads(result.stdout)['launch_allowed'], 'window_check_refused')
    result = subprocess.run([sys.executable, str(controller), 'start', str(path)], capture_output=True, text=True, timeout=30)
    require(result.returncode == 0, 'guard_start_refused')
    state.update(status='coordinator_guard_dispatched', blocker=None)
    write(state_path, state)
    return {'status': 'guard_dispatched_not_quality_acceptance', 'run_id': run}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--controller', required=True, type=Path)
    parser.add_argument('--maximum-seconds', type=int, default=7200)
    args = parser.parse_args()
    require(600 <= args.maximum_seconds <= 7200, 'invalid_coordinator_budget')
    sys.path.insert(0, str(args.controller))
    window = importlib.import_module('research_window')
    base = args.state.parent
    status_path = base / 'batch-continuation-status.json'
    def stop(signum, frame):
        raise Stopped('coordinator_stopped_guarded_job_may_remain_live')
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with (base / 'batch-continuation.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        end = time.monotonic() + args.maximum_seconds
        try:
            while time.monotonic() < end:
                if window.deadline(datetime.now(window.MOSCOW)) is None:
                    write(status_path, {'status': 'outside_window_no_new_load', 'quality_accepted': False})
                    return
                result = tick(window, args.state, base / 'research_screen_batch.py', end - time.monotonic())
                write(status_path, {**result, 'time': time.time(), 'quality_accepted': False})
                if result['status'] in {'synthetic_24_passed_real_phase_requires_agent', 'no_new_batch_insufficient_observation_or_window_time'}:
                    return
                time.sleep(15)
            write(status_path, {'status': 'coordinator_budget_ended_guarded_job_may_remain_live', 'quality_accepted': False})
        except Exception as error:
            write(status_path, {'status': 'stopped_requires_agent_audit',
                  'reason': str(error) if isinstance(error, Stopped) else type(error).__name__,
                  'quality_accepted': False})
            raise SystemExit(1)


if __name__ == '__main__':
    os.umask(0o077)
    main()
