"""CPU-only rehearsal of consumer restoration after controller death.

This executable rejects production projects. It proves only the restoration
primitive, not admission, queue locking or a production maintenance lease.
"""
import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import time
import uuid
from pathlib import Path

try:
    from . import capacity_watchdog as cw
except ImportError:
    import capacity_watchdog as cw


LABEL = 'org.psptm.pause-probe.run'


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def load(path):
    path = Path(path).absolute()
    if (path.is_symlink() or path.stat().st_mode & 0o077
            or path.parent.stat().st_mode & 0o077):
        raise cw.Refused('private_probe_required')
    m = json.loads(path.read_text())
    if set(m) != {'run_id', 'project', 'worker', 'control', 'image', 'script_sha256'}:
        raise cw.Refused('invalid_probe')
    if not re.fullmatch('[0-9a-f]{16}', m['run_id']):
        raise cw.Refused('invalid_probe')
    if m['project'] != 'psptm-minutes-pause-probe-' + m['run_id']:
        raise cw.Refused('synthetic_project_required')
    if (any(not re.fullmatch('[0-9a-f]{64}', m[k]) for k in ('worker', 'control'))
            or m['worker'] == m['control']
            or not re.fullmatch('sha256:[0-9a-f]{64}', m['image'])):
        raise cw.Refused('immutable_probe_ids_required')
    if m['script_sha256'] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise cw.Refused('probe_code_changed')
    m['_path'] = path
    return m


def owned(m, role):
    if m['project'] != 'psptm-minutes-pause-probe-' + m['run_id']:
        raise cw.Refused('synthetic_project_required')
    # Inspect only bounded identity/state metadata; never env or logs.
    fmt = ('{"id":{{json .Id}},"image":{{json .Image}},"status":{{json .State.Status}},'
           '"running":{{json .State.Running}},"labels":{{json .Config.Labels}}}')
    row = json.loads(cw.command(['docker', 'inspect', '--format', fmt, m[role]]))
    labels = row.get('labels') or {}
    if (row['id'] != m[role] or row['image'] != m['image']
            or labels.get(LABEL) != m['run_id']
            or labels.get('com.docker.compose.project') != m['project']
            or labels.get('com.docker.compose.service') != role):
        raise cw.Refused('synthetic_ownership_mismatch')
    return row


def restore(m):
    journal = m['_path'].parent / 'lease-state.json'
    if not journal.exists():
        return
    state = json.loads(journal.read_text())
    if (state.get('worker') != m['worker'] or state.get('run_id') != m['run_id']
            or state.get('originally_running') is not True):
        raise cw.Refused('restoration_journal_mismatch')
    row = owned(m, 'worker')
    if not row['running']:
        if row['status'] != 'exited':
            raise cw.Refused('unexpected_worker_state')
        cw.command(['docker', 'start', m['worker']])
    if not owned(m, 'worker')['running']:
        raise cw.Refused('restoration_unconfirmed')
    state.update(phase='restored')
    write(journal, state)


def hold(m):
    if not owned(m, 'worker')['running'] or not owned(m, 'control')['running']:
        raise cw.Refused('synthetic_workers_not_running')
    path = m['_path'].parent / 'lease-state.json'
    if path.exists():
        raise cw.Refused('probe_already_consumed')
    state = dict(run_id=m['run_id'], worker=m['worker'], originally_running=True, phase='armed')
    # Journal before mutation, so ExecStopPost recovers even if stop is interrupted.
    write(path, state)
    try:
        cw.command(['docker', 'stop', '--time', '2', m['worker']], timeout=10)
        if owned(m, 'worker')['running']:
            raise cw.Refused('pause_unconfirmed')
        state['phase'] = 'paused'
        write(path, state)
        time.sleep(120)
    finally:
        restore(m)


def probe(directory, image, failure):
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    run_id = uuid.uuid4().hex[:16]
    m = dict(run_id=run_id, project='psptm-minutes-pause-probe-' + run_id, image=image,
             script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    script = Path(__file__).resolve()
    path = directory.resolve() / 'probe.json'
    if any(not re.fullmatch(r'/[A-Za-z0-9/_.-]+', str(p)) for p in (script, path)):
        raise cw.Refused('systemd_path_unsupported')
    if not re.fullmatch('sha256:[0-9a-f]{64}', image):
        raise cw.Refused('immutable_image_required')
    containers = []
    unit = 'minutes-pause-probe-' + run_id
    try:
        for role in ('worker', 'control'):
            identifier = cw.command([
                'docker', 'create', '--runtime=runc', '--network=none', '--read-only',
                '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=0.5', '--memory=64m',
                '--pids-limit=32', '--label', LABEL + '=' + run_id,
                '--label', 'com.docker.compose.project=' + m['project'],
                '--label', 'com.docker.compose.service=' + role,
                '--entrypoint=python', image, '-B', '-c', 'import time; time.sleep(180)']).strip()
            m[role] = identifier
            containers.append(identifier)
            cw.command(['docker', 'start', identifier])
        write(path, m)
        m = load(path)
        cw.command(['systemd-run', '--user', '--unit=' + unit,
                    '--property=RuntimeMaxSec=' + ('5' if failure == 'timeout' else '60'),
                    '--property=TimeoutStopSec=20', '--property=KillMode=control-group',
                    '--property=UMask=0077',
                    '--property=ExecStopPost=/usr/bin/python3 -B ' + str(script) + ' restore ' + str(path),
                    '/usr/bin/python3', '-B', str(script), 'hold', str(path)])
        until = time.monotonic() + 15
        journal = directory / 'lease-state.json'
        while not journal.exists() or json.loads(journal.read_text())['phase'] != 'paused':
            if time.monotonic() >= until:
                raise cw.Refused('synthetic_pause_timeout')
            time.sleep(.1)
        if failure == 'sigkill':
            cw.command(['systemctl', '--user', 'kill', '--signal=SIGKILL', '--kill-whom=main', unit])
        until = time.monotonic() + 30
        while cw.command(['systemctl', '--user', 'show', unit, '--property=ActiveState', '--value']).strip() not in ('inactive', 'failed'):
            if time.monotonic() >= until:
                raise cw.Refused('synthetic_unit_timeout')
            time.sleep(.2)
        result = cw.command(['systemctl', '--user', 'show', unit, '--property=Result', '--value']).strip()
        report = dict(failure=failure, expected_unit_result=result == ('timeout' if failure == 'timeout' else 'signal'),
                      journal_restored=json.loads(journal.read_text())['phase'] == 'restored',
                      worker_running=owned(m, 'worker')['running'], control_running=owned(m, 'control')['running'],
                      production_touched=False, gpu_requested=False)
        write(directory / 'receipt.json', report)
        print(json.dumps(report))
        return 0 if all(report[k] for k in ('expected_unit_result', 'journal_restored', 'worker_running', 'control_running')) else 1
    finally:
        subprocess.run(['systemctl', '--user', 'stop', unit], capture_output=True, timeout=30)
        for cid in containers:
            role = 'worker' if cid == m.get('worker') else 'control'
            owned(m, role)
            cw.command(['docker', 'rm', '-f', cid])
        subprocess.run(['systemctl', '--user', 'reset-failed', unit], capture_output=True, timeout=10)


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=('probe', 'hold', 'restore'))
    p.add_argument('path', type=Path)
    p.add_argument('--image')
    p.add_argument('--failure', choices=('timeout', 'sigkill'), default='timeout')
    a = p.parse_args()
    def interrupted(signum, frame):
        raise cw.Refused('probe_interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        if a.mode == 'probe':
            return probe(a.path, a.image or '', a.failure)
        m = load(a.path)
        hold(m) if a.mode == 'hold' else restore(m)
        return 0
    except Exception as error:
        print(json.dumps({'result': str(error) if isinstance(error, cw.Refused) else 'probe_unavailable'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
