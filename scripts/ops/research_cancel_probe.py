import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

import research_window as window


BASE = Path('/home/epopov/projects/psptm_minutes/benchmarks')
TOOLS = BASE / 'quality-controls-20261015/research-schedule-20261023'
RUNTIME = BASE / 'status-boundary-inventory-candidate-delivery-20261018/runtime'
MANIFEST = '0945774bec783ea14a81227f09f25d061136fb7921f2d5e22886122645838cdc'
AUTH = TOOLS / 'bootstrap-authorization.json'
MAX_SECONDS = 480


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def verify_runtime():
    assert sha(RUNTIME / 'manifest.json') == read(RUNTIME / 'FROZEN')['manifest_sha256'] == MANIFEST
    manifest = read(RUNTIME / 'manifest.json')
    assert all(sha(RUNTIME / name) == expected for name, expected in manifest['files'].items())
    return manifest


def authorized(job=None):
    path, value = window.read_private(AUTH)
    if value.get('maximum_probe_jobs') != 1 or value.get('used_probe_jobs') not in (0, 1):
        raise window.resources.Refused('single_probe_authorization_required')
    if job is not None and value.get('bound_probe_run_id') != job['run_id']:
        raise window.resources.Refused('authorization_bound_to_another_job')
    return value


def prepare():
    os.umask(0o077)
    auth = authorized()
    if auth.get('bound_probe_run_id') or auth['used_probe_jobs']:
        raise window.resources.Refused('existing_probe_job_requires_inspection')
    manifest = verify_runtime()
    sys.path.insert(0, str(RUNTIME))
    import stage_policy
    import independent_review_wire

    models = read(RUNTIME / 'models.json')['models']
    reviewers = read(RUNTIME / 'owner-review-policies.json')['independent_source_reviews']
    schema = {'type': 'object', 'properties': {'items': {'type': 'array', 'minItems': 1, 'items': {'type': 'integer'}}}, 'required': ['items'], 'additionalProperties': False}
    system = 'Synthetic transport cancellation check. Produce the full integer sequence from 1 to 100000 in items. Do not summarize or shorten the sequence.'
    user = json.dumps({'source': [{'id': 1, 'text': 'Invented cancellation fixture; no meeting or personal data. ' + 'Synthetic reference paragraph: alpha beta gamma delta are invented labels for this transport test. ' * 256}], 'task': 'Return all 100000 integers in order, following the required schema.'})
    run_id = uuid.uuid4().hex[:16]
    root = TOOLS / ('cancel-probe-' + run_id)
    root.mkdir(mode=0o700)
    job = {'kind': 'cancellation_probe', 'run_id': run_id, 'runtime_manifest_sha256': MANIFEST,
           'image_id': manifest['docker_image'].removeprefix('sha256:'), 'driver_sha256': sha(Path(__file__)), 'cases': []}
    plans = [(model, stage_policy.payload(model, 'map', system, user, schema)) for model in models]
    plans += [(mode['model'], independent_review_wire.payload(mode['model'], system, user, schema)) for mode in reviewers]
    plans.sort(key=lambda item: (0 if item[0]['name'].startswith('gemma') and item[1]['options']['num_ctx'] == 65536 else 2 if item[0]['name'].startswith('gemma') else 1))
    for index, (model, payload) in enumerate(plans):
        case_root = root / ('case-' + str(index))
        case_root.mkdir(mode=0o700)
        case = {'index': index, 'model_digest': model['digest'], 'model': model['name'], 'context': payload['options']['num_ctx'], 'payload': payload}
        case_path = case_root / 'case.json'
        write(case_path, case)
        identifier = window.resources.command(['docker', 'create', '--name', 'psptm-research-cancel-' + run_id + '-' + str(index),
            '--label', window.OWNER_LABEL + '=' + run_id, '--label', window.KIND_LABEL + '=cancellation_probe',
            '--network', 'host', '--read-only', '--user', str(os.getuid()) + ':' + str(os.getgid()),
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--cpus', '1', '--memory', '1g', '--pids-limit', '64',
            '-e', 'PYTHONDONTWRITEBYTECODE=1', '-v', str(RUNTIME) + ':' + str(RUNTIME) + ':ro',
            '-v', str(TOOLS) + ':' + str(TOOLS) + ':ro', '-v', str(case_root) + ':' + str(case_root),
            '--entrypoint', 'python', manifest['docker_image'], '-B', str(Path(__file__).resolve()), 'client', str(case_path)]).strip()
        assert len(identifier) == 64
        job['cases'].append({'container': identifier, 'case_path': str(case_path), 'case_sha256': sha(case_path),
                             'model_digest': model['digest'], 'context': case['context']})
    path = root / 'job.json'
    write(path, job)
    auth['bound_probe_run_id'] = run_id
    write(AUTH, auth)
    print(json.dumps({'status': 'four_transport_cases_prepared_not_started', 'job': str(path), 'maximum_seconds': MAX_SECONDS, 'new_model_calls': 0}))


def load(path):
    path, job = window.read_private(path)
    assert job['kind'] == 'cancellation_probe' and len(job['cases']) == 4
    authorized(job)
    assert job['driver_sha256'] == sha(Path(__file__))
    assert job['runtime_manifest_sha256'] == MANIFEST
    job['_path'] = path
    return job


def owned(job, case):
    current = {'container': case['container'], 'run_id': job['run_id'], 'kind': job['kind'], '_image_id': job['image_id']}
    return window.owned(current)


def cleanup(job):
    for case in job['cases']:
        if owned(job, case)['running']:
            window.resources.command(['docker', 'stop', '--time', '3', case['container']], timeout=15)
        if owned(job, case)['running']:
            raise window.resources.Refused('probe_cleanup_unconfirmed')


def client(path):
    case = read(path)
    sys.path.insert(0, str(RUNTIME))
    import runner
    import native_context
    from independent_tokens import native_tokens

    runner.ROOT = RUNTIME
    payload = case['payload']
    assert payload['stream'] is False
    tags = runner.api('/api/tags', timeout=10)['models']
    assert next(model['digest'] for model in tags if model['name'] == case['model']) == case['model_digest']
    counter = native_tokens if case['model'].startswith('gemma') else native_context
    admission = counter.admission(case['model'], payload['messages'][0]['content'], payload['messages'][1]['content'], payload['options']['num_predict'], payload['options']['num_ctx'])
    assert admission['admitted']
    state = {'phase': 'http_submitted', 'response_received': False, 'payload_sha256': runner.digest(payload),
             'model_digest': case['model_digest'], 'context': case['context'], 'admission': admission, 'time': time.time()}
    write(Path(path).parent / 'client-state.json', state)
    try:
        response = runner.api('/api/chat', payload, timeout=360)
    except Exception as error:
        state.update(phase='http_failed_before_cancel', response_received=False,
                     error_type=type(error).__name__, http_status=getattr(error, 'code', None))
        write(Path(path).parent / 'client-state.json', state)
        raise
    state.update(phase='response_received_before_cancel', response_received=True, done=response.get('done'), eval_count=response.get('eval_count'))
    write(Path(path).parent / 'client-state.json', state)


def pending_client(job, case, state_path):
    state = read(state_path)
    if (not owned(job, case)['running'] or state.get('phase') != 'http_submitted'
            or state.get('response_received') is not False):
        raise window.resources.Refused('pending_native_http_not_verified')


def wait_for_admission(current, stop_at):
    until = min(stop_at, time.time() + 45)
    idle_since = None
    while True:
        now = time.time()
        if now >= until:
            raise window.resources.Refused('probe_gpu_settle_timeout')
        # Preserve production priority and reject foreign work even while waiting.
        window.gate(current, starting=False)
        try:
            window.gate(current, starting=True)
        except window.resources.Refused as error:
            if str(error) != 'gpu_busy':
                raise
            idle_since = None
        else:
            idle_since = now if idle_since is None else idle_since
            if now - idle_since >= 10:
                return
        time.sleep(1)


def supervise(job, stop_at):
    auth = authorized(job)
    if auth['used_probe_jobs'] or (job['_path'].parent / 'probe-started.json').exists():
        raise window.resources.Refused('single_probe_already_consumed')
    verify_runtime()
    end = window.deadline(datetime.now(window.MOSCOW))
    if end is None or not time.time() < stop_at <= min(end.timestamp(), time.time() + MAX_SECONDS + 1):
        raise window.resources.Refused('outside_probe_window')
    with ExitStack() as stack:
        for name in window.LOCKS:
            handle = stack.enter_context(open(name, 'a+'))
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for case in job['cases']:
            assert owned(job, case)['status'] == 'created'
            assert sha(case['case_path']) == case['case_sha256']
        first = {'container': job['cases'][0]['container'], 'model_digests': list({case['model_digest'] for case in job['cases']})}
        window.gate(first, starting=True)
        auth['used_probe_jobs'] = 1
        auth['status'] = 'single_probe_consumed'
        write(AUTH, auth)
        write(job['_path'].parent / 'probe-started.json', {'time': time.time(), 'stop_at': stop_at})
        proofs = []
        try:
            for case in job['cases']:
                current = {'container': case['container'], 'model_digests': first['model_digests']}
                while window.resources.ollama_models():
                    if time.time() >= stop_at:
                        raise window.resources.Refused('probe_time_budget_reached')
                    window.gate(current, starting=False)
                    time.sleep(1)
                wait_for_admission(current, stop_at)
                window.gate(current, starting=True)
                window.resources.command(['docker', 'start', case['container']])
                state_path = Path(case['case_path']).parent / 'client-state.json'
                active = []
                until = min(stop_at, time.time() + 180)
                observed = []
                while len(active) < 4:
                    if time.time() >= until or not owned(job, case)['running']:
                        raise window.resources.Refused('inflight_work_not_verified')
                    window.gate(current, starting=False)
                    if state_path.exists():
                        state = read(state_path)
                        if state.get('phase') != 'http_submitted' or state['response_received']:
                            raise window.resources.Refused('probe_completed_before_cancel_inconclusive')
                        activity = window.process_gpu_activity()
                        own = window.ollama_server_pids()
                        busy = max((usage or 0 for pid, usage in activity.items() if pid in own), default=0)
                        observed.append({'time': time.time(), 'own_gpu_sm_percent': busy})
                        write(state_path.parent / 'activity-observations.json', {'samples': observed, 'threshold_percent': 10, 'required_consecutive_samples': 4})
                        active = active + [{'time': time.time(), 'own_gpu_sm_percent': busy}] if busy >= 10 else []
                pending_client(job, case, state_path)
                cancelled_at = time.time()
                window.resources.command(['docker', 'stop', '--time', '3', case['container']], timeout=15)
                assert not owned(job, case)['running'] and read(state_path)['response_received'] is False
                assert read(state_path)['phase'] == 'http_submitted'
                idle_since = None
                samples = []
                until = min(stop_at, time.time() + 45)
                while idle_since is None or time.time() - idle_since < 10:
                    if time.time() >= until:
                        raise window.resources.Refused('server_compute_cessation_not_verified')
                    window.gate(current, starting=False)
                    utilization = float(window.resources.command(['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits']).strip())
                    now = time.time()
                    samples.append({'time': now, 'gpu_utilization': utilization})
                    idle_since = (idle_since or now) if utilization <= 5 else None
                proofs.append({'model_digest': case['model_digest'], 'context': case['context'], 'container': case['container'],
                               'case_sha256': case['case_sha256'], 'client_state_sha256': sha(state_path),
                               'active_samples': active, 'idle_samples': samples, 'cancelled_at': cancelled_at,
                               'http_pending_when_stopped': True, 'own_container_stopped': True,
                               'server_compute_cessation_observed_seconds': time.time() - idle_since})
                write(job['_path'].parent / 'probe-progress.json', {'completed_cases': len(proofs), 'proofs': proofs, 'server_cancellation_verified': False})
            verify_runtime()
            receipt = {'server_cancellation_verified': True, 'kind': 'inference', 'model_digests': sorted(first['model_digests']),
                       'runtime_manifest_sha256': MANIFEST, 'client_protocol': 'frozen_ollama_nonstream', 'image_id': job['image_id'],
                       'proofs': proofs, 'driver_sha256': sha(Path(__file__)), 'job_sha256': sha(job['_path']),
                       'method': 'stop_own_nonstream_client_while_own_server_gpu_active_then_observe_ten_seconds_idle',
                       'no_response_received_before_or_after_stop': True, 'quality_accepted': False}
            write(job['_path'].parent / 'cancellation-receipt.json', receipt)
        finally:
            cleanup(job)


def dispatch(mode, path, stop_at):
    job = load(path)
    if mode == 'cleanup':
        cleanup(job)
    elif mode == 'start':
        end = window.deadline(datetime.now(window.MOSCOW))
        if end is None:
            raise window.resources.Refused('outside_probe_window')
        deadline = min(end.timestamp(), time.time() + MAX_SECONDS)
        script = Path(window.__file__).resolve()
        window.resources.command(['systemd-run', '--user', '--collect', '--unit=minutes-research-' + job['run_id'],
            '--property=RuntimeMaxSec=' + str(MAX_SECONDS), '--property=TimeoutStopSec=45', '--property=KillMode=control-group', '--property=UMask=0077',
            '--property=ExecStopPost=/usr/bin/python3 ' + str(script) + ' cleanup ' + str(path),
            '/usr/bin/python3', str(script), 'run', str(path), '--stop-at', str(deadline)])
        print(json.dumps({'status': 'single_probe_watchdog_dispatched', 'unit': 'minutes-research-' + job['run_id'], 'maximum_seconds': MAX_SECONDS}))
    elif mode == 'run':
        supervise(job, stop_at)
    else:
        raise window.resources.Refused('invalid_probe_mode')
    return 0


if __name__ == '__main__':
    os.umask(0o077)
    if sys.argv[1] == 'prepare':
        prepare()
    elif sys.argv[1] == 'client':
        client(Path(sys.argv[2]))
