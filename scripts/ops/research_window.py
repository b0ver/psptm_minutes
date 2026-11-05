import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import time
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import capacity_watchdog as resources


MOSCOW = ZoneInfo('Europe/Moscow')
OWNER_LABEL = 'psptm.research.owner'
KIND_LABEL = 'psptm.research.kind'
LOCKS = ('/home/epopov/projects/psptm_minutes/benchmarks/qwen_lab/lab.lock',
         '/tmp/psptm-benchmark-gpu.lock')


def window_end(now):
    if now.tzinfo is None:
        raise ValueError('timezone_required')
    local = now.astimezone(MOSCOW)
    if local.weekday() >= 5:
        return (local + timedelta(days=7 - local.weekday())).replace(hour=6, minute=0, second=0, microsecond=0)
    if local.hour < 6:
        return local.replace(hour=6, minute=0, second=0, microsecond=0)
    if local.hour >= 22:
        tomorrow = local + timedelta(days=1)
        if tomorrow.weekday() >= 5:
            return window_end(tomorrow.replace(hour=0, minute=0, second=0, microsecond=0))
        return tomorrow.replace(hour=6, minute=0, second=0, microsecond=0)
    return None


def next_open(now):
    local = now.astimezone(MOSCOW)
    return local if window_end(now) is not None else local.replace(hour=22, minute=0, second=0, microsecond=0)


def deadline(now, minimum_minutes=10):
    end = window_end(now)
    if end is None:
        return None
    stop = min(end - timedelta(minutes=15), now.astimezone(MOSCOW) + timedelta(hours=2))
    return stop if stop - now >= timedelta(minutes=minimum_minutes) else None


def read_private(path):
    path = Path(path).absolute()
    if path.resolve() != path or not path.is_file() or path.is_symlink():
        raise resources.Refused('canonical_private_file_required')
    if any(item.stat().st_uid != os.getuid() or item.stat().st_mode & 0o077 for item in (path, path.parent)):
        raise resources.Refused('canonical_private_file_required')
    return path, json.loads(path.read_text())


def load_job(path):
    path, job = read_private(path)
    if set(job) != {'run_id', 'container', 'kind', 'model_digests', 'cancellation_receipt', 'cancellation_receipt_sha256',
                    'runtime_root', 'runtime_manifest_sha256', 'client_protocol'}:
        raise resources.Refused('invalid_research_job')
    if not re.fullmatch('[0-9a-f]{16}', job['run_id']) or not re.fullmatch('[0-9a-f]{64}', job['container']):
        raise resources.Refused('immutable_job_required')
    if job['kind'] not in {'inference', 'training'}:
        raise resources.Refused('invalid_research_kind')
    if not isinstance(job['model_digests'], list) or not job['model_digests'] or any(not re.fullmatch('[0-9a-f]{64}', item) for item in job['model_digests']):
        raise resources.Refused('model_allowlist_required')
    protocol = {'inference': 'frozen_ollama_nonstream', 'training': 'local_training'}[job['kind']]
    if job['client_protocol'] != protocol:
        raise resources.Refused('client_protocol_mismatch')
    runtime = Path(job['runtime_root'])
    if not runtime.is_absolute() or runtime.resolve() != runtime or not runtime.is_dir():
        raise resources.Refused('canonical_runtime_required')
    manifest_path = runtime / 'manifest.json'
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != job['runtime_manifest_sha256']:
        raise resources.Refused('cancellation_runtime_changed')
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest.get('files'), dict) or not manifest['files']:
        raise resources.Refused('runtime_files_required')
    for name, expected in manifest['files'].items():
        artifact = runtime / name
        if not artifact.resolve().is_relative_to(runtime) or artifact.is_symlink() or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected:
            raise resources.Refused('cancellation_runtime_changed')
    receipt_path, receipt = read_private(job['cancellation_receipt'])
    if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != job['cancellation_receipt_sha256']:
        raise resources.Refused('cancellation_receipt_changed')
    if (receipt.get('server_cancellation_verified') is not True
            or receipt.get('kind') != job['kind']
            or receipt.get('model_digests') != job['model_digests']
            or receipt.get('client_protocol') != job['client_protocol']
            or receipt.get('runtime_manifest_sha256') != job['runtime_manifest_sha256']
            or not re.fullmatch('[0-9a-f]{64}', receipt.get('image_id', ''))):
        raise resources.Refused('verified_cancellation_required')
    job['_image_id'] = receipt['image_id']
    job['_path'] = path
    return job


def owned(job):
    row = resources.inspect(job['container'])
    labels = row.get('labels') or {}
    if row['id'] != job['container'] or labels.get(OWNER_LABEL) != job['run_id'] or labels.get(KIND_LABEL) != job['kind']:
        raise resources.Refused('research_ownership_mismatch')
    image = resources.command(['docker', 'inspect', '--format', '{{.Image}}', job['container']]).strip()
    if image.removeprefix('sha256:') != job['_image_id']:
        raise resources.Refused('cancellation_image_mismatch')
    return row


def process_gpu_activity():
    output = resources.command(['nvidia-smi', 'pmon', '-c', '1', '-s', 'u'])
    values = {}
    for line in output.splitlines():
        fields = line.split()
        if not fields or fields[0].startswith('#'):
            continue
        if len(fields) < 5 or not fields[1].isdigit():
            raise resources.Refused('gpu_activity_unavailable')
        if 'C' in fields[2]:
            values[int(fields[1])] = float(fields[3]) if fields[3].isdigit() else None
    return values


def ollama_server_pids():
    output = resources.command(['systemctl', 'show', 'ollama.service', '-p', 'ActiveState', '-p', 'MainPID', '-p', 'ControlGroup'])
    properties = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    if properties.get('ActiveState') != 'active' or properties.get('ControlGroup') != '/system.slice/ollama.service':
        raise resources.Refused('native_ollama_membership_unverified')
    main_pid = properties.get('MainPID', '')
    if not main_pid.isdigit() or int(main_pid) <= 0:
        raise resources.Refused('native_ollama_membership_unverified')
    root = Path('/sys/fs/cgroup/system.slice/ollama.service')
    members = set()
    for path in (root / 'cgroup.procs', *root.rglob('*/cgroup.procs')):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise resources.Refused('native_ollama_membership_unverified')
        members.update(int(value) for value in path.read_text().split())
    if int(main_pid) not in members:
        raise resources.Refused('native_ollama_membership_unverified')
    return members | resources.container_pids(resources.OLLAMA)


def native_gpu_owned(pid, root=Path('/proc')):
    """Check late-arriving driver PIDs directly against the live service cgroup."""
    state = dict(line.split('=', 1) for line in resources.command([
        'systemctl', 'show', 'ollama.service', '--property=ActiveState,ControlGroup,MainPID'
    ]).splitlines() if '=' in line)
    group = '/system.slice/ollama.service'
    if (state.get('ActiveState') != 'active' or state.get('ControlGroup') != group
            or not state.get('MainPID', '').isdigit() or int(state['MainPID']) < 1):
        return False
    try:
        if (root / state['MainPID'] / 'cgroup').read_text().splitlines() != ['0::' + group]:
            return False
        folder = root / str(pid)
        before = (folder / 'stat').read_text().rsplit(')', 1)[1].split()[19]
        groups = (folder / 'cgroup').read_text().splitlines()
        after = (folder / 'stat').read_text().rsplit(')', 1)[1].split()[19]
        return (before == after and len(groups) == 1
                and (groups[0] == '0::' + group or groups[0].startswith('0::' + group + '/')))
    except (OSError, IndexError):
        return False


def gate(job, starting):
    resources.production_idle()
    models = resources.ollama_models()
    worker = resources.inspect('psptm-minutes-worker-1')
    labels = worker.get('labels') or {}
    if (not worker['running'] or labels.get('com.docker.compose.project') != 'psptm-minutes'
            or labels.get('com.docker.compose.service') != 'worker'):
        raise resources.Refused('production_worker_unavailable')
    idle_worker_pids = resources.container_pids('psptm-minutes-worker-1')
    if any((usage or 0) > 0 for pid, usage in process_gpu_activity().items() if pid in idle_worker_pids):
        raise resources.Refused('production_gpu_busy')
    if starting:
        usage = resources.command(['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits']).strip()
        utilization = float(usage)
        unexpected_pids = resources.gpu_pids() - idle_worker_pids
        if utilization > 5 or unexpected_pids or models:
            print(json.dumps({'status': 'admission_refused_diagnostics', 'reason': 'gpu_busy',
                              'time': datetime.now(MOSCOW).isoformat(),
                              'gpu_utilization_percent': utilization,
                              'unexpected_compute_process_count': len(unexpected_pids),
                              'loaded_model_count': len(models)}), flush=True)
            raise resources.Refused('gpu_busy')
    else:
        if any(model['digest'].removeprefix('sha256:') not in job['model_digests'] for model in models):
            raise resources.Refused('foreign_model_loaded')
        client_pids = resources.container_pids(job['container']) if resources.inspect(job['container'])['running'] else set()
        allowed = client_pids | ollama_server_pids() | idle_worker_pids
        observed = resources.gpu_pids()
        late = observed - allowed
        if late:
            # A runner can start between the cgroup snapshot and nvidia-smi.
            # Require live service membership and stable process identity.
            verified = {pid for pid in late if native_gpu_owned(pid)}
            if '_path' in job:
                diagnostic = {'time': datetime.now(MOSCOW).isoformat(),
                              'observed_pids': sorted(observed),
                              'snapshot_pids': sorted(allowed),
                              'late_service_owned_pids': sorted(verified),
                              'unverified_pids': sorted(late - verified)}
                try:
                    (job['_path'].parent / 'gpu-ownership-observation.json').write_text(json.dumps(diagnostic))
                except OSError:
                    pass  # Diagnostics must never prevent refusal.
            if late - verified:
                raise resources.Refused('foreign_gpu_process')
            # Ownership alone does not authorize other work on the shared server.
            resources.production_idle()
            if any(model['digest'].removeprefix('sha256:') not in job['model_digests']
                   for model in resources.ollama_models()):
                raise resources.Refused('foreign_model_loaded')


def publish_runtime_gate(job):
    # Called only after the host guard has passed all live resource checks.
    # gate.json is mutable telemetry, never part of the frozen manifest.
    root = Path(job['runtime_root'])
    manifest = json.loads((root / 'manifest.json').read_text())
    if 'gate.json' in manifest['files']:
        raise resources.Refused('frozen_gate_telemetry_forbidden')
    target = root / 'gate.json'
    if target.is_symlink():
        raise resources.Refused('canonical_gate_telemetry_required')
    usage = float(resources.command(['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits']).strip())
    temporary = root / 'research-gate.tmp'
    with temporary.open('x') as handle:
        json.dump({'time': time.time(), 'ready': True, 'gpu_utilization': usage,
                   'source': 'research_watchdog_after_live_gate'}, handle)
    temporary.replace(target)


def save(job, status, **fields):
    target = job['_path'].parent / 'schedule-watchdog.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps({'run_id': job['run_id'], 'status': status,
                                    'time': datetime.now(MOSCOW).isoformat(), **fields}) + '\n')
    temporary.replace(target)


def cleanup(job):
    if owned(job)['running']:
        resources.command(['docker', 'stop', '--time', '10', job['container']], timeout=20)
    if owned(job)['running']:
        raise resources.Refused('research_stop_unconfirmed')


def supervise(job, stop_at):
    now = datetime.now(MOSCOW)
    end = deadline(now)
    if end is None or not math.isfinite(stop_at) or not time.time() < stop_at <= end.timestamp() + 1:
        raise resources.Refused('outside_research_window')
    with ExitStack() as stack:
        for filename in LOCKS:
            lock = stack.enter_context(open(filename, 'a+'))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise resources.Refused('lab_busy') from None
        if owned(job)['status'] != 'created':
            raise resources.Refused('fresh_container_required')
        try:
            gate(job, starting=True)
            publish_runtime_gate(job)
            if deadline(datetime.now(MOSCOW)) is None or time.time() >= stop_at:
                raise resources.Refused('outside_research_window')
            save(job, 'starting', stop_at=stop_at)
            resources.command(['docker', 'start', job['container']])
            while owned(job)['running']:
                if time.time() >= stop_at or window_end(datetime.now(MOSCOW)) is None:
                    raise resources.Refused('research_window_closed')
                gate(job, starting=False)
                publish_runtime_gate(job)
                time.sleep(5)
            row = owned(job)
            save(job, 'terminal_requires_result_and_checkpoint_audit', exit_code=row['exit_code'], quality_accepted=False)
        except BaseException:
            save(job, 'interrupted_requires_request_audit_no_blind_retry', quality_accepted=False)
            raise
        finally:
            cleanup(job)


def start(job):
    end = deadline(datetime.now(MOSCOW))
    if end is None:
        raise resources.Refused('outside_research_window')
    if owned(job)['status'] != 'created':
        raise resources.Refused('fresh_container_required')
    script, path = Path(__file__).resolve(), job['_path']
    if any(not re.fullmatch(r'/[A-Za-z0-9/_.-]+', str(item)) for item in (script, path)):
        raise resources.Refused('systemd_path_unsupported')
    resources.command(['systemd-run', '--user', '--collect', '--unit=minutes-research-' + job['run_id'],
                       '--property=RuntimeMaxSec=' + str(max(1, int(end.timestamp() - time.time()))),
                       '--property=TimeoutStopSec=60', '--property=KillMode=control-group', '--property=UMask=0077',
                       '--property=ExecStopPost=/usr/bin/python3 ' + str(script) + ' cleanup ' + str(path),
                       '/usr/bin/python3', str(script), 'run', str(path), '--stop-at', str(end.timestamp())])
    return {'status': 'supervisor_dispatched_not_model_acceptance', 'stop_at': end.isoformat()}


def readonly_cpu(mode):
    base = Path('/home/epopov/projects/psptm_minutes/benchmarks')
    runtime = base / 'status-boundary-inventory-candidate-delivery-20261018/runtime'
    tools = Path(__file__).resolve().parent
    manifest_path = runtime / 'manifest.json'
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != '0945774bec783ea14a81227f09f25d061136fb7921f2d5e22886122645838cdc':
        raise resources.Refused('cpu_audit_runtime_changed')
    manifest = json.loads(manifest_path.read_text())
    image = manifest['docker_image']
    if not re.fullmatch('sha256:[0-9a-f]{64}', image):
        raise resources.Refused('immutable_cpu_image_required')
    argv = ['docker', 'run', '--rm', '--network', 'none', '--read-only',
            '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m,mode=1777',
            '--user', str(os.getuid()) + ':' + str(os.getgid()), '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--cpus', '1', '--memory', '1g',
            '--pids-limit', '64', '-e', 'PYTHONDONTWRITEBYTECODE=1',
            '-v', str(tools) + ':' + str(tools) + ':ro', '-w', str(tools)]
    if mode in {'audit-interruption', 'audit-completed-screen'}:
        output = tools / ('interruption-audit' if mode == 'audit-interruption' else 'completed-screen-audit')
        if output.exists():
            raise resources.Refused('existing_audit_requires_inspection')
        output.mkdir(mode=0o700)
        argv += ['-v', str(runtime) + ':' + str(runtime) + ':ro', '-v', str(output) + ':' + str(output)]
        script = 'audit_v39_interrupted_screen.py' if mode == 'audit-interruption' else 'audit_v39_completed_screen.py'
        command = ['-B', str(tools / script)]
    elif mode == 'cpu-tests':
        command = ['-B', '-m', 'unittest', '-v', 'test_research_window']
    else:
        raise resources.Refused('cpu_mode_not_allowed')
    result = subprocess.run(argv + ['--entrypoint', 'python', image] + command,
                            capture_output=True, text=True, timeout=60)
    (tools / (mode + '.log')).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise resources.Refused('readonly_cpu_audit_failed')
    print(json.dumps({'status': 'readonly_cpu_task_completed', 'mode': mode, 'new_model_calls': 0}))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('check', 'start', 'run', 'cleanup', 'cpu-tests', 'audit-interruption', 'audit-completed-screen'))
    parser.add_argument('job', nargs='?', type=Path)
    parser.add_argument('--stop-at', type=float)
    args = parser.parse_args()

    def interrupted(signum, frame):
        raise resources.Refused('supervisor_interrupted')

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        now = datetime.now(MOSCOW)
        if args.mode in {'cpu-tests', 'audit-interruption', 'audit-completed-screen'}:
            readonly_cpu(args.mode)
            return 0
        if args.mode == 'check':
            end = deadline(now)
            print(json.dumps({'timezone': 'Europe/Moscow', 'window_open': window_end(now) is not None,
                              'launch_allowed': end is not None, 'next_open': next_open(now).isoformat(),
                              'stop_by': end.isoformat() if end else None, 'new_model_calls': 0}))
            return 0
        if args.job is None:
            raise resources.Refused('job_required')
        header_path, header = read_private(args.job)
        if header.get('kind') == 'cancellation_probe':
            import research_cancel_probe

            return research_cancel_probe.dispatch(args.mode, header_path, args.stop_at)
        job = load_job(args.job)
        if args.mode == 'start':
            print(json.dumps(start(job)))
        elif args.mode == 'cleanup':
            cleanup(job)
        else:
            if args.stop_at is None:
                raise resources.Refused('deadline_required')
            supervise(job, args.stop_at)
        return 0
    except Exception as error:
        print(json.dumps({'status': 'deferred_or_stopped', 'reason': str(error) if isinstance(error, resources.Refused) else 'schedule_guard_unavailable'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
