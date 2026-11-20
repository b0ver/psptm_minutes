"""Proposed Recap health entrypoint. Deployment requires owner approval.

A busy shared GPU lease defers model readiness and checks liveness only.
A free lease runs the original GPU smoke, keeping the lock across exec.
"""
import argparse
import fcntl
import json
import os
import stat
import sys
import urllib.request


def liveness():
    os.kill(1, 0)
    with urllib.request.urlopen('http://bot:8080/health', timeout=3) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError('health_response_oversize')
    value = json.loads(raw)
    if value['db'] is not True or value['ollama'] is not True:
        raise ValueError('health_unavailable')


def run(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('regular_lock_required')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            liveness()
            print(json.dumps({'ok': True, 'gpu_readiness': 'deferred_shared_lock',
                              'liveness': 'passed'}))
            return 0
        # exec preserves the open file description and its flock until the
        # original smoke exits. Do not release it in a parent process first.
        os.set_inheritable(fd, True)
        os.execv(sys.executable, [sys.executable, '-m', 'recap', '--smoke', '--only', 'stt'])
        raise RuntimeError('exec_returned')
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('lock')
    args = parser.parse_args()
    try:
        return run(args.lock)
    except Exception:
        print(json.dumps({'ok': False, 'reason': 'health_probe_unavailable'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
