"""Actual Docker metadata regression, no network/GPU/mounts or application startup."""
import json
import sys
import uuid

from capacity_watchdog import OWNER_LABEL, Refused, command, owned


def main(image):
    run_id = uuid.uuid4().hex[:16]
    job = dict(run_id=run_id, project='psptm-minutes-capacity-' + run_id)
    container = command(['docker', 'create', '--runtime=runc', '--network=none', '--read-only',
                         '--cap-drop=ALL', '--security-opt=no-new-privileges',
                         '--label', OWNER_LABEL + '=' + run_id,
                         '--label', 'com.docker.compose.project=' + job['project'],
                         '--label', 'com.docker.compose.service=measure',
                         '--entrypoint=python', image, '-c', 'pass']).strip()
    job['measure'] = container
    try:
        assert owned(job, 'measure')['status'] == 'created'
        command(['docker', 'start', '--attach', container])
        row = owned(job, 'measure')
        assert row['status'] == 'exited' and row['exit_code'] == 0 and not row['running']
        try:
            owned(dict(job, project='psptm-minutes'), 'measure')
        except Refused:
            pass
        else:
            raise AssertionError('Foreign project accepted')
        print(json.dumps({'docker_metadata': 'passed', 'ownership_refusal': 'passed', 'gpu_requested': False}))
    finally:
        command(['docker', 'rm', '-f', container])


if __name__ == '__main__':
    main(sys.argv[1])
