"""Read-only post-stop telemetry. Never starts requests or certifies cancellation.

Run immediately after stopping the owned unit. The separate acceptance protocol
must link this evidence to an active stream and the actual stop time.
"""
import argparse
import fcntl
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import capacity_watchdog as cw
except ImportError:
    import capacity_watchdog as cw


def pmon_compute(output):
    result = {}
    seen = False
    for line in output.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        fields = line.split()
        if len(fields) < 4 or fields[0] != '0':
            raise cw.Refused('gpu_telemetry_ambiguous')
        seen = True
        if fields[1:3] == ['-', '-']:
            continue
        if not fields[1].isdigit() or fields[2] not in ('G', 'C', 'C+G'):
            raise cw.Refused('gpu_telemetry_ambiguous')
        if fields[2] != 'G':
            pid = int(fields[1])
            if pid in result:
                raise cw.Refused('gpu_telemetry_ambiguous')
            sm = fields[3]
            if sm != '-' and (not sm.isdigit() or not 0 <= int(sm) <= 100):
                raise cw.Refused('gpu_telemetry_ambiguous')
            result[pid] = None if sm == '-' else int(sm)
    if not seen:
        raise cw.Refused('gpu_telemetry_ambiguous')
    return result


def process_identity(pid, cgroup, root=Path('/proc')):
    """Read the actual driver's PID, not two non-atomic membership lists."""
    folder = root / str(pid)
    try:
        before = (folder / 'stat').read_text().rsplit(')', 1)[1].split()[19]
        groups = (folder / 'cgroup').read_text().splitlines()
        after = (folder / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        raise cw.Refused('process_identity_unavailable') from None
    if before != after:
        raise cw.Refused('process_identity_changed')
    if groups != ['0::' + cgroup]:
        raise cw.Refused('foreign_gpu_process')
    return before


def ollama_identity(pid, root=Path('/proc')):
    return process_identity(pid, '/system.slice/ollama.service', root)


def identities(job, pids):
    worker = cw.idle_worker(job)
    result = {}
    for pid in pids:
        if worker is not None and pid in worker['pids']:
            result[pid] = ('worker', process_identity(pid, '/system.slice/docker-' + worker['id'] + '.scope'))
        else:
            result[pid] = ('ollama', ollama_identity(pid))
    return result


def snapshot(job):
    for role in ('measure', 'database'):
        if cw.owned(job, role)['status'] != 'exited':
            raise cw.Refused('owned_containers_not_stopped')
    cw.production_idle()
    if any(m['digest'].removeprefix('sha256:') != job['model_digest'] for m in cw.ollama_models()):
        raise cw.Refused('foreign_model_loaded')
    initial = cw.gpu_pids()
    before = identities(job, initial)
    compute = pmon_compute(cw.command(['nvidia-smi', 'pmon', '-c', '1', '-s', 'um']))
    utilization = float(cw.command(['nvidia-smi', '--query-gpu=utilization.gpu',
                                    '--format=csv,noheader,nounits']).strip())
    gpu_observed_monotonic = time.monotonic()
    gpu_observed_epoch = time.time()
    final = cw.gpu_pids()
    if initial != final or set(compute) != final:
        raise cw.Refused('gpu_process_snapshot_changed')
    if identities(job, final) != before:
        raise cw.Refused('process_identity_changed')
    if not math.isfinite(utilization) or not 0 <= utilization <= 20:
        raise cw.Refused('gpu_busy')
    cw.production_idle()
    # '-' stays unknown. A separate measured whole-device zero is independent
    # evidence; positive process SM contradicts it and resets the interval.
    positive = any(sm is not None and sm > 0 for sm in compute.values())
    basis = ('no_compute' if not compute else 'whole_device_zero' if utilization == 0
             else 'per_process_zero' if all(sm == 0 for sm in compute.values()) else 'unknown')
    return {'gpu_utilization_percent': utilization, 'compute_pid_count': len(final),
            'gpu_observed_monotonic': gpu_observed_monotonic,
            'gpu_observed_epoch': gpu_observed_epoch,
            'compute_sm_percent': sorted(compute.values(), key=lambda x: -1 if x is None else x),
            'quiescence_basis': basis, 'quiescent': not positive and basis != 'unknown'}


def observe(job, sample=snapshot, clock=time.monotonic, sleep=time.sleep, rows=None):
    started = clock()
    since = previous = None
    rows = [] if rows is None else rows
    while clock() - started < 30:
        row = sample(job)
        now = clock()
        measured = row.get('gpu_observed_monotonic', now)
        if (type(measured) not in (int, float) or not math.isfinite(measured)
                or not started <= measured <= now or now - measured > 3):
            raise cw.Refused('gpu_telemetry_stale')
        row['elapsed_seconds'] = round(now - started, 3)
        row['gpu_elapsed_seconds'] = round(measured - started, 3)
        rows.append(row)
        if not row['quiescent'] or (previous is not None and measured - previous > 3):
            since = None
        if row['quiescent']:
            since = measured if since is None else since
            if measured - since >= 10 and now - started < 30:
                return rows, True
        previous = measured
        sleep(1)
    return rows, False


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job', type=Path)
    args = parser.parse_args()
    report = {'observed_at': datetime.now(timezone.utc).isoformat(),
              'server_cancellation_verified': False, 'quiescence_verified': False}
    target = None
    try:
        job = cw.load_job(args.job)
        target = job['_path'].parent / 'post-stop-observation.json'
        # Never overwrite a completed observation with a later idle interval.
        with target.open('x') as output:
            output.write(json.dumps(report))
        report['run_id'] = job['run_id']
        with open(job['lab_lock'], 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            report['samples'] = []
            report['observation_started_epoch'] = time.time()
            try:
                _, report['quiescence_verified'] = observe(job, rows=report['samples'])
            finally:
                report['observation_finished_epoch'] = time.time()
        report['result'] = 'quiescent' if report['quiescence_verified'] else 'quiescence_not_verified'
    except FileExistsError:
        target = None
        report['result'] = 'observation_already_exists'
    except Exception as error:
        report['result'] = str(error) if isinstance(error, cw.Refused) else 'observation_unavailable'
    if target is not None:
        target.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    return 0 if report['quiescence_verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
