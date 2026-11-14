"""CPU-only systemd timeout/SIGKILL drill with real isolated PostgreSQL.

No host data mounts, network or GPU. Existing API/DB image IDs are read only.
Creates and removes three uniquely labelled synthetic containers.
"""
import argparse
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from capacity_watchdog import OWNER_LABEL, command, inspect, load_job


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('lab_lock', type=Path)
    parser.add_argument('--failure', choices=('timeout', 'sigkill'), default='timeout')
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    run_id = uuid.uuid4().hex[:16]
    project = 'psptm-minutes-capacity-' + run_id
    unit = 'minutes-capacity-cpu-probe-' + run_id
    script = Path(__file__).with_name('capacity_watchdog.py').resolve()
    image = command(['docker', 'inspect', '--format', '{{.Image}}', 'psptm-minutes-api-1']).strip()
    db_image = command(['docker', 'inspect', '--format', '{{.Image}}', 'psptm-minutes-db-1']).strip()
    containers = []
    try:
        for service in ('measure', 'capacity-db', 'control'):
            argv = ['docker', 'create', '--runtime=runc', '--network=none',
                    '--security-opt=no-new-privileges',
                    '--label', OWNER_LABEL + '=' + (run_id if service != 'control' else 'control-' + run_id),
                    '--label', 'com.docker.compose.project=' + project,
                    '--label', 'com.docker.compose.service=' + service]
            if service == 'capacity-db':
                argv += ['--tmpfs=/var/lib/postgresql/data', '--env=POSTGRES_USER=minutes',
                         '--env=POSTGRES_DB=minutes', '--env=POSTGRES_PASSWORD=test-only',
                         '--health-cmd=pg_isready -U minutes -d minutes', '--health-interval=1s',
                         db_image]
            else:
                argv += ['--read-only', '--cap-drop=ALL', '--entrypoint=python', image,
                         '-c', 'import time; time.sleep(180)']
            container = command(argv).strip()
            containers.append(container)
            command(['docker', 'start', container])
        until = time.monotonic() + 60
        while inspect(containers[1])['health'] != 'healthy':
            if time.monotonic() >= until:
                raise RuntimeError('synthetic_database_not_ready')
            time.sleep(1)
        path = directory / 'job.json'
        path.write_text(json.dumps(dict(run_id=run_id, project=project, measure=containers[0], database=containers[1],
                                       lab_lock=str(args.lab_lock.resolve()), model_digest='0' * 64)))
        path.chmod(0o600)
        load_job(path)
        command(['systemd-run', '--user', '--unit=' + unit,
                 '--property=RuntimeMaxSec=' + ('3' if args.failure == 'timeout' else '60'),
                 '--property=TimeoutStopSec=60', '--property=UMask=0077',
                 '--property=ExecStopPost=/usr/bin/python3 ' + str(script) + ' cleanup ' + str(path),
                 '/usr/bin/python3', '-c', 'import time; time.sleep(180)'])
        if args.failure == 'sigkill':
            command(['systemctl', '--user', 'kill', '--signal=SIGKILL', '--kill-whom=main', unit])
        until = time.monotonic() + 65
        while time.monotonic() < until:
            state = command(['systemctl', '--user', 'show', unit, '--property=ActiveState', '--value']).strip()
            if state in ('failed', 'inactive'):
                break
            time.sleep(1)
        else:
            raise RuntimeError('synthetic_service_timeout')
        result = command(['systemctl', '--user', 'show', unit, '--property=Result', '--value']).strip()
        with (directory / 'database.dump').open('rb') as backup:
            subprocess.run(['docker', 'run', '--rm', '--runtime=runc', '--network=none',
                            '--entrypoint=pg_restore', '-i', db_image, '--list'],
                           stdin=backup, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=20, check=True)
        report = dict(expected_failure=result == ('timeout' if args.failure == 'timeout' else 'signal'),
                      failure=args.failure, dump_verified=True,
                      owned_stopped=not inspect(containers[0])['running'] and not inspect(containers[1])['running'],
                      control_running=inspect(containers[2])['running'], gpu_requested=False)
        (directory / 'probe.json').write_text(json.dumps(report))
        print(json.dumps(report))
        return 0 if all(report[k] for k in ('expected_failure', 'owned_stopped', 'control_running', 'dump_verified')) else 1
    finally:
        subprocess.run(['systemctl', '--user', 'stop', unit], capture_output=True, timeout=65)
        for container in containers:
            subprocess.run(['docker', 'rm', '-f', container], capture_output=True, timeout=20, check=True)
        subprocess.run(['systemctl', '--user', 'reset-failed', unit], capture_output=True, timeout=10)


if __name__ == '__main__':
    raise SystemExit(main())
