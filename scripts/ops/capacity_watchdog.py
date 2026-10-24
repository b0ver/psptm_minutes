"""Host-side supervisor for two pre-created, uniquely labelled capacity containers.

No application imports or host dependencies beyond Python, Docker and user systemd.
Container/image preparation stays explicit; this program never builds or deploys.
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo('Europe/Moscow')
OWNER_LABEL = 'org.psptm.capacity.run'
API = 'psptm-minutes-api-1'
AGENT = 'psptm-minutes-telemost-agent-1'
OLLAMA = 'ollama'
INSPECT = ('{"id":{{json .Id}},"labels":{{json .Config.Labels}},'
           '"running":{{json .State.Running}},"status":{{json .State.Status}},'
           '"exit_code":{{json .State.ExitCode}},"health":'
           '{{with index .State "Health"}}{{json .Status}}{{else}}""{{end}}}')
# Read-only, bounded statements. Unrunnable historical analysis rows are counted
# separately; they must not be mistaken for work the consumer could claim.
QUEUE_PROBE = '''
import json
from sqlalchemy import text
from app.db import engine
with engine.connect() as c:
    c.execute(text("SET TRANSACTION READ ONLY"))
    c.execute(text("SET LOCAL statement_timeout = '2s'"))
    counts = {t: c.scalar(text("SELECT count(*) FROM " + t + " WHERE status='processing'"))
              for t in ('meetings', 'meeting_analysis', 'meeting_reports')}
    counts['queued_meetings'] = c.scalar(text("SELECT count(*) FROM meetings WHERE status='queued'"))
    counts['queued_analysis'] = c.scalar(text("""SELECT count(*) FROM meeting_analysis a
        JOIN meetings m ON m.id=a.meeting_id WHERE a.status='queued'
        AND m.status IN ('ready','failed')
        AND CASE WHEN json_typeof(m.transcript)='array'
                 THEN json_array_length(m.transcript)>0 ELSE false END"""))
print(json.dumps(counts))
'''
AGENT_PROBE = '''
import json
from pathlib import Path
from states import TERMINAL
root=Path('/data/telemost')
if not root.is_dir(): raise SystemExit(2)
states=[json.loads(p.read_text()) for p in root.glob('*/state.json')]
# partial_saved is terminal for capacity; deployment has a stricter gate.
print(json.dumps({'blocked':sum(s['status'] not in TERMINAL or s['status']=='upload_uncertain' for s in states)}))
'''


class Refused(Exception):
    pass


def command(argv, timeout=10):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise Refused('command_unavailable')
    return result.stdout


def deadline(now, minimum_minutes=10):
    """Reserve 15 minutes for cancellation/inspection; cap a job at two hours."""
    now = now.astimezone(MOSCOW)
    if now.weekday() < 5:
        if not 7 <= now.hour < 9:
            return None
        end = now.replace(hour=9, minute=0, second=0, microsecond=0)
    else:
        end = (now + timedelta(days=7 - now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    stop = min(end - timedelta(minutes=15), now + timedelta(hours=2))
    return stop if stop - now >= timedelta(minutes=minimum_minutes) else None


def load_job(path):
    path = Path(path).absolute()
    if path.is_symlink() or path.stat().st_mode & 0o077 or path.parent.stat().st_mode & 0o077:
        raise Refused('private_job_required')
    job = json.loads(path.read_text())
    if set(job) != {'run_id', 'project', 'measure', 'database', 'lab_lock', 'model_digest'}:
        raise Refused('invalid_job')
    if not re.fullmatch('[0-9a-f]{16}', job['run_id']):
        raise Refused('invalid_job')
    if job['project'] != 'psptm-minutes-capacity-' + job['run_id']:
        raise Refused('invalid_project')
    if any(not re.fullmatch('[0-9a-f]{64}', job[key]) for key in ('measure', 'database', 'model_digest')):
        raise Refused('immutable_id_required')
    if job['measure'] == job['database']:
        raise Refused('invalid_job')
    lock = Path(job['lab_lock'])
    if not lock.is_absolute() or not lock.is_file() or lock.is_symlink():
        raise Refused('lab_lock_unavailable')
    job['_path'] = path
    return job


def inspect(identifier):
    return json.loads(command(['docker', 'inspect', '--format', INSPECT, identifier]))


def owned(job, role):
    row = inspect(job[role])
    labels = row.get('labels') or {}
    if (row['id'] != job[role] or labels.get(OWNER_LABEL) != job['run_id']
            or labels.get('com.docker.compose.project') != job['project']
            or labels.get('com.docker.compose.service') != ('measure' if role == 'measure' else 'capacity-db')):
        raise Refused('container_ownership_mismatch')
    return row


def production_idle():
    for name, service in ((API, 'api'), (AGENT, 'telemost-agent')):
        row = inspect(name)
        labels = row.get('labels') or {}
        if (not row['running'] or row['health'] != 'healthy'
                or labels.get('com.docker.compose.project') != 'psptm-minutes'
                or labels.get('com.docker.compose.service') != service):
            raise Refused('production_probe_unavailable')
    counts = json.loads(command(['docker', 'exec', API, 'python', '-c', QUEUE_PROBE]))
    sessions = json.loads(command(['docker', 'exec', AGENT, 'python', '-c', AGENT_PROBE]))
    if set(counts) != {'meetings', 'meeting_analysis', 'meeting_reports', 'queued_meetings', 'queued_analysis'}:
        raise Refused('production_probe_unavailable')
    values = list(counts.values()) + [sessions['blocked']]
    if any(type(value) is not int or value < 0 for value in values):
        raise Refused('production_probe_unavailable')
    if any(values):
        raise Refused('production_busy')


def ollama_models():
    with urllib.request.urlopen('http://127.0.0.1:11434/api/ps', timeout=5) as response:
        models = json.load(response)['models']
    if not isinstance(models, list):
        raise Refused('ollama_probe_unavailable')
    return models


def gpu_pids():
    output = command(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader,nounits'])
    return {int(line.strip()) for line in output.splitlines() if line.strip()}


def container_pids(identifier):
    output = command(['docker', 'top', identifier, '-eo', 'pid'])
    return {int(line.strip()) for line in output.splitlines()[1:] if line.strip()}


def graphics_only():
    """Driver classification, never process names; unknown telemetry fails closed."""
    output = command(['nvidia-smi', 'pmon', '-c', '2', '-s', 'um'])
    seen = False
    for line in output.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        fields = line.split()
        if len(fields) < 3 or fields[0] != '0':
            raise Refused('gpu_telemetry_ambiguous')
        if fields[1:3] == ['-', '-']:
            continue
        if not fields[1].isdigit() or fields[2] != 'G':
            raise Refused('foreign_gpu_process')
        seen = True
    return seen


def native_ollama_pids(root=Path('/sys/fs/cgroup')):
    """Bender's ollama container is a socat proxy to this system service."""
    state = dict(line.split('=', 1) for line in command([
        'systemctl', 'show', 'ollama.service', '--property=ActiveState,ControlGroup'
    ]).splitlines() if '=' in line)
    if state.get('ActiveState') == 'inactive':
        return set()
    if state.get('ActiveState') != 'active' or state.get('ControlGroup') != '/system.slice/ollama.service':
        raise Refused('ollama_process_ownership_unavailable')
    folder = root / 'system.slice/ollama.service'
    files = [folder / 'cgroup.procs', *folder.glob('**/cgroup.procs')]
    return {int(pid) for path in files for pid in path.read_text().split()}


def native_gpu_owned(pid, root=Path('/proc')):
    """Check late-arriving driver PIDs directly against the live service cgroup."""
    state = dict(line.split('=', 1) for line in command([
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


def worker_snapshot():
    fmt = ('{"id":{{json .Id}},"image":{{json .Image}},"started_at":{{json .State.StartedAt}},'
           '"running":{{json .State.Running}},"labels":{{json .Config.Labels}}}')
    row = json.loads(command(['docker', 'inspect', '--format', fmt, 'psptm-minutes-worker-1']))
    labels = row.get('labels') or {}
    if (not row['running'] or labels.get('com.docker.compose.project') != 'psptm-minutes'
            or labels.get('com.docker.compose.service') != 'worker'
            or not re.fullmatch('[0-9a-f]{64}', row['id'])
            or not re.fullmatch('sha256:[0-9a-f]{64}', row['image'])):
        raise Refused('idle_worker_identity_unavailable')
    return {k: row[k] for k in ('id', 'image', 'started_at')}


def idle_worker(job):
    """Explicit per-job identity binding; absence preserves the strict GPU gate."""
    if '_path' not in job:
        return None
    path = job['_path'].parent / 'idle-worker.json'
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise Refused('private_idle_worker_binding_required')
    bound = json.loads(path.read_text())
    if (set(bound) != {'job_sha256', 'worker'}
            or bound['job_sha256'] != hashlib.sha256(job['_path'].read_bytes()).hexdigest()
            or bound['worker'] != worker_snapshot()):
        raise Refused('idle_worker_binding_changed')
    return dict(bound['worker'], pids=container_pids(bound['worker']['id']))


def idle_context_gate(job, initial):
    """Whole-device zero over 10 seconds, with fresh production/identity probes."""
    started = time.monotonic()
    since = None
    previous = None
    while True:
        production_idle()
        worker = idle_worker(job)
        values = command(['nvidia-smi', '--query-gpu=utilization.gpu,memory.free',
                          '--format=csv,noheader,nounits']).strip().split(',')
        if (worker is None or len(values) != 2 or float(values[0]) != 0
                or not math.isfinite(float(values[1])) or float(values[1]) < 20000
                or gpu_pids() != initial or initial - worker['pids'] or ollama_models()):
            raise Refused('gpu_busy')
        now = time.monotonic()
        if now - started >= 30:
            raise Refused('idle_worker_observation_timeout')
        if previous is not None and now - previous > 3:
            raise Refused('idle_worker_observation_gap')
        previous = now
        since = now if since is None else since
        if now - since >= 10:
            production_idle()
            return
        time.sleep(1)


def bind_idle_worker(job):
    for role in ('measure', 'database'):
        if owned(job, role)['status'] != 'created':
            raise Refused('fresh_containers_required')
    production_idle()
    value = dict(job_sha256=hashlib.sha256(job['_path'].read_bytes()).hexdigest(), worker=worker_snapshot())
    path = job['_path'].parent / 'idle-worker.json'
    if path.exists() or path.is_symlink():
        raise Refused('idle_worker_binding_already_exists')
    with path.open('x') as output:
        output.write(json.dumps(value))
    path.chmod(0o600)
    resource_gate(job, starting=True)
    return value


def resource_gate(job, starting):
    production_idle()
    models = ollama_models()
    worker = idle_worker(job)
    if starting:
        values = command(['nvidia-smi', '--query-gpu=utilization.gpu,memory.free', '--format=csv,noheader,nounits']).strip().split(',')
        if len(values) != 2 or not math.isfinite(float(values[1])) or float(values[1]) < 20000 or models:
            raise Refused('gpu_busy')
        pids = gpu_pids()
        if pids:
            if worker is None or pids - worker['pids']:
                raise Refused('gpu_busy')
            idle_context_gate(job, pids)
            return
        utilization = float(values[0])
        if not math.isfinite(utilization) or not 0 <= utilization <= 20:
            raise Refused('gpu_busy')
        if utilization > 5 and (not graphics_only() or gpu_pids() or ollama_models()):
            raise Refused('gpu_busy')
    else:
        if any(model['digest'].removeprefix('sha256:') != job['model_digest'] for model in models):
            raise Refused('foreign_model_loaded')
        allowed = container_pids(job['measure']) | container_pids(OLLAMA) | native_ollama_pids()
        if worker is not None:
            allowed |= worker['pids']
        observed = gpu_pids()
        unknown = {pid for pid in observed - allowed if not native_gpu_owned(pid)}
        if unknown:
            if '_path' in job:
                details = {'time': datetime.now(MOSCOW).isoformat(), 'gpu_pids': sorted(observed),
                           'allowed_snapshot': sorted(allowed), 'unclassified_pids': sorted(unknown)}
                owners = {}
                for pid in unknown:
                    try:
                        owners[str(pid)] = (Path('/proc') / str(pid) / 'cgroup').read_text().strip()
                    except OSError:
                        owners[str(pid)] = 'pid_unavailable'
                details['unclassified_cgroups'] = owners
                try:
                    (job['_path'].parent / 'gpu-refusal.json').write_text(json.dumps(details))
                except OSError:
                    pass  # A failed audit write must not prevent stopping this workload.
            raise Refused('foreign_gpu_process')


def save(job, status, **fields):
    target = job['_path'].parent / 'watchdog.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps({'run_id': job['run_id'], 'status': status,
                                    'time': datetime.now(MOSCOW).isoformat(), **fields}))
    temporary.replace(target)


def capture_evidence(job):
    # Keep private outputs available for human review after tmpfs PostgreSQL stops.
    owned(job, 'measure')
    log = job['_path'].parent / 'measurement.log'
    with log.open('w') as output:
        subprocess.run(['docker', 'logs', job['measure']], stdout=output, stderr=subprocess.STDOUT,
                       timeout=15, check=True)
    database = owned(job, 'database')
    target = job['_path'].parent / 'database.dump'
    if database['running'] and database['health'] == 'healthy' and not target.exists():
        temporary = target.with_suffix('.tmp')
        try:
            with temporary.open('wb') as output:
                subprocess.run(['docker', 'exec', job['database'], 'pg_dump', '-U', 'minutes', '-d', 'minutes', '-Fc'],
                               stdout=output, stderr=subprocess.DEVNULL, timeout=30, check=True)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def cleanup(job):
    """Stop only owned IDs; preserve evidence before stopping the tmpfs database."""
    measure = owned(job, 'measure')
    if measure['running']:
        command(['docker', 'stop', '--time', '10', job['measure']], timeout=20)
    if owned(job, 'measure')['running']:
        raise Refused('cleanup_unconfirmed')
    capture_failed = False
    try:
        capture_evidence(job)
    except Exception:
        capture_failed = True
    database = owned(job, 'database')
    if database['running']:
        command(['docker', 'stop', '--time', '10', job['database']], timeout=20)
    if owned(job, 'database')['running']:
        raise Refused('cleanup_unconfirmed')
    if capture_failed:
        raise Refused('evidence_capture_failed')


def supervise(job, stop_at):
    end = deadline(datetime.now(MOSCOW))
    if end is None or not math.isfinite(stop_at) or not time.time() < stop_at <= end.timestamp() + 1:
        raise Refused('outside_window')
    for role in ('measure', 'database'):
        if owned(job, role)['status'] != 'created':
            raise Refused('fresh_containers_required')
    with Path(job['lab_lock']).open('r+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('lab_busy') from None
        try:
            resource_gate(job, starting=True)
            save(job, 'starting', stop_at=stop_at)
            command(['docker', 'start', job['database']])
            health_until = min(stop_at, time.time() + 90)
            while owned(job, 'database')['health'] != 'healthy':
                if time.time() >= health_until:
                    raise Refused('database_not_ready')
                time.sleep(1)
            if time.time() >= stop_at:
                raise Refused('window_closed')
            resource_gate(job, starting=True)
            command(['docker', 'start', job['measure']])
            save(job, 'running', stop_at=stop_at)
            while True:
                row = owned(job, 'measure')
                if not row['running']:
                    if row['status'] != 'exited' or row['exit_code'] != 0:
                        raise Refused('measurement_failed')
                    return
                if time.time() >= stop_at or deadline(datetime.now(MOSCOW), minimum_minutes=0) is None:
                    raise Refused('window_closed')
                resource_gate(job, starting=False)
                time.sleep(3)
        finally:
            # Locks remain held until both own containers have stopped.
            cleanup(job)


def start_service(job):
    end = deadline(datetime.now(MOSCOW))
    if end is None:
        raise Refused('outside_window')
    for role in ('measure', 'database'):
        if owned(job, role)['status'] != 'created':
            raise Refused('fresh_containers_required')
    script = Path(__file__).resolve()
    if any(not re.fullmatch(r'/[A-Za-z0-9/_.-]+', str(p)) for p in (script, job['_path'])):
        raise Refused('systemd_path_unsupported')
    unit = 'minutes-capacity-' + job['run_id']
    command(['systemd-run', '--user', '--collect', '--unit=' + unit,
             '--property=RuntimeMaxSec=' + str(max(1, int(end.timestamp() - time.time()))),
             '--property=TimeoutStopSec=60', '--property=UMask=0077', '--property=KillMode=control-group',
             '--property=ExecStopPost=/usr/bin/python3 ' + str(script) + ' cleanup ' + str(job['_path']),
             '/usr/bin/python3', str(script), 'run', str(job['_path']), '--stop-at', str(end.timestamp())])
    print(json.dumps({'unit': unit, 'stop_at': end.isoformat()}))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('start', 'run', 'cleanup', 'check', 'bind-idle-worker'))
    parser.add_argument('job', type=Path)
    parser.add_argument('--stop-at', type=float)
    args = parser.parse_args()
    job = None
    def interrupted(signum, frame):
        raise Refused('controller_interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        job = load_job(args.job)
        if args.mode == 'start':
            start_service(job)
        elif args.mode == 'cleanup':
            cleanup(job)
        elif args.mode == 'bind-idle-worker':
            bind_idle_worker(job)
            print(json.dumps({'idle_worker_bound': True, 'ready': True}))
        elif args.mode == 'check':
            owned(job, 'measure'); owned(job, 'database')
            resource_gate(job, starting=True)
            print(json.dumps({'ready': True, 'window_open': deadline(datetime.now(MOSCOW)) is not None}))
        else:
            if args.stop_at is None:
                raise Refused('deadline_required')
            supervise(job, args.stop_at)
            save(job, 'finished', result='success', stopped_verified=True)
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, Refused) else 'watchdog_unavailable'
        if job is not None and args.mode == 'run':
            save(job, 'failed', reason=code)
        print(json.dumps({'ready': False, 'reason': code}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
