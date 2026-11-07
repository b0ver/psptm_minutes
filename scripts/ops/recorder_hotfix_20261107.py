"""Owner-approved recorder-only rollout. Never builds or replaces other services."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import time
from uuid import uuid4

ROOT = Path('/home/epopov/projects/psptm_minutes-ops/recorder-hotfix-20261107')
PROD = Path('/home/epopov/projects/psptm_minutes')
PREFIX = 'psptm-minutes-'
STAGE = 'psptm-recorder-prod-compat-20261107'
REVISION = 'cdbd17a19c8a487a297c457b6f40d7ff775c0ff4'
IMAGE = 'ghcr.io/b0ver/psptm_minutes-telemost@sha256:84fa3acfb32235cdc055f2964ec89fe9f00a48a76a37bbc3ef3e1ada769834af'
TERMINAL = {'uploaded', 'failed', 'cancelled', 'partial_saved'}


class GateError(RuntimeError):
    pass


def pin_image(text, reference=IMAGE):
    if not re.fullmatch(r'ghcr.io/b0ver/psptm_minutes-telemost@sha256:[0-9a-f]{64}', reference):
        raise GateError('nonimmutable_recorder_image')
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip() == '  telemost-agent:']
    if len(starts) != 1:
        raise GateError('recorder_service_not_unique')
    start = starts[0] + 1
    end = next((i for i in range(start, len(lines)) if lines[i].strip() and not lines[i].startswith('    ')), len(lines))
    images = [i for i in range(start, end) if re.match(r'^    image:\s*', lines[i])]
    if len(images) != 1:
        raise GateError('recorder_image_not_unique')
    i = images[0]
    newline = '\r\n' if lines[i].endswith('\r\n') else '\n' if lines[i].endswith('\n') else ''
    lines[i] = '    image: ' + reference + newline
    return ''.join(lines)


def validate_states(expected, current):
    wanted = {s['id']: (s['status'], s['state_sha256']) for s in expected}
    actual = {s['id']: (s['status'], s['state_sha256']) for s in current}
    if any(s['status'] not in TERMINAL for s in current):
        raise GateError('active_or_uncertain_recording')
    if wanted != actual or len(wanted) != len(expected) or len(actual) != len(current):
        raise GateError('legacy_states_changed')


def validate_identity(baseline, current, recorder_may_change=False):
    if set(baseline) != set(current):
        raise GateError('service_set_changed')
    for service, row in baseline.items():
        if service == 'telemost-agent' and recorder_may_change:
            continue
        if current[service]['id'] != row['id'] or current[service]['image'] != row['image']:
            raise GateError('unrelated_service_changed')


def run(args, data=None, timeout=120):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise GateError('command_failed_' + Path(args[0]).name)
    return result.stdout


def identity(service):
    fmt = '{"id":"{{.Id}}","image":"{{.Image}}","running":{{.State.Running}},"paused":{{.State.Paused}},"project":"{{index .Config.Labels "com.docker.compose.project"}}"}'
    result = json.loads(run(['docker', 'inspect', '--format', fmt, PREFIX + service + '-1']))
    if result['project'] != 'psptm-minutes':
        raise GateError('production_ownership_mismatch')
    return result


def data_read(code):
    image = json.loads((ROOT / 'baseline-images.json').read_text())['api']
    return json.loads(run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cpus', '1', '--memory', '512m',
                          '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--volumes-from', PREFIX + 'api-1:ro',
                          '--entrypoint', 'python', image, '-c', code]))


def states():
    return data_read("from pathlib import Path;import json,hashlib;print(json.dumps([{'id':s['id'],'status':s['status'],'state_sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in Path('/data/telemost').glob('*/state.json') for s in [json.loads(p.read_text())]]))")


def data_manifest():
    return data_read("from pathlib import Path;import os,json;root=Path('/data');rows=[]\nfor folder,dirs,files in os.walk(root,followlinks=False):\n for name in sorted(dirs+files):\n  p=Path(folder)/name;s=p.lstat();rows.append([str(p.relative_to(root)),s.st_mode,s.st_size,s.st_mtime_ns,os.readlink(p) if p.is_symlink() else None])\nprint(json.dumps(sorted(rows)))")


def atomic(path, value):
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, indent=2));temp.chmod(0o600);os.replace(temp, path)


def health(service):
    port = 8090 if service == 'telemost-agent' else 8080
    code = f"import urllib.request,json;assert json.load(urllib.request.urlopen('http://127.0.0.1:{port}/healthz',timeout=3))['status']=='ok'"
    for _ in range(30):
        try:
            run(['docker', 'exec', PREFIX + service + '-1', 'python', '-c', code], timeout=8)
            return
        except GateError:
            time.sleep(1)
    raise GateError('health_failed_' + service)


def compose_agent():
    return run(['docker', 'compose', '--project-directory', str(PROD), '-p', 'psptm-minutes', '-f', str(PROD / 'compose.yaml'),
                '-f', str(PROD / 'compose.telemost.yaml'), '--profile', 'telemost', 'up', '-d', '--no-build', '--no-deps', 'telemost-agent'])


def main(apply=False):
    approval = json.loads((ROOT / 'owner-approval.json').read_text())
    if not (approval.get('recorder_only_exception') is True and approval.get('accepted_62_minutes_two_rotations') is True):
        raise GateError('owner_exception_missing')
    image_info = run(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}}']).decode().strip()
    if image_info != 'amd64 ' + REVISION:
        raise GateError('candidate_binding_mismatch')
    images = json.loads((ROOT / 'baseline-images.json').read_text())
    baseline = {s: identity(s) for s in images}
    if any(baseline[s]['image'] != images[s] or not baseline[s]['running'] or baseline[s]['paused'] for s in baseline):
        raise GateError('baseline_changed_or_not_running')
    expected = json.loads((ROOT / 'durable-reconciliation.json').read_text())
    validate_states(expected, states())
    if (PROD / 'compose.telemost.yaml').is_symlink():raise GateError('symlinked_production_overlay')
    original = (PROD / 'compose.telemost.yaml').read_bytes()
    replacement = pin_image(original.decode()).encode()
    if not apply:
        print(json.dumps({'dry_run_passed': True, 'only_image_line_changes': True, 'legacy_states': len(expected)}), flush=True)
        return
    offhost = json.loads((ROOT / 'offhost-data-verified.json').read_text())
    backup = json.loads((ROOT / 'online-data-safe.json').read_text())
    restore = json.loads((ROOT / 'restore-online-safe.json').read_text())
    if not (offhost.get('decryption_hash_verified') and offhost.get('plaintext_sha256') == backup['sha256'] and restore.get('full_recordings_restore_completed')):
        raise GateError('backup_gate_incomplete')
    if data_manifest() != json.loads((ROOT / 'online-data-manifest.json').read_text()):
        raise GateError('data_changed_after_online_backup')
    attempt = ROOT / ('cutover-' + uuid4().hex[:12]);attempt.mkdir(mode=0o700)
    atomic(ROOT / 'current-attempt.json', {'directory': str(attempt)})
    paused = [];guard = None;old_stopped = False;changed = False;committed = False
    def phase(name, **fields):
        atomic(attempt / 'state.json', {'phase': name, 'paused_by_us': paused, 'committed': committed, **fields})
        print(json.dumps({'phase': name, **fields}), flush=True)
    def pause(service):
        row = identity(service)
        if row['id'] != baseline[service]['id'] or row['paused'] or not row['running']:
            raise GateError('container_changed_before_pause')
        run(['docker', 'pause', row['id']]);paused.append(service)
    def resume(service):
        if service not in paused:return
        row = identity(service)
        if row['id'] != baseline[service]['id']:
            raise GateError('container_changed_before_resume')
        if row['paused']:run(['docker', 'unpause', row['id']])
        paused.remove(service)
    def close_guard():
        nonlocal guard
        if guard:
            try:
                guard.stdin.write('\n');guard.stdin.flush()
            except (BrokenPipeError, ValueError):pass
            resume('api')
            try:guard.communicate(timeout=15)
            except subprocess.TimeoutExpired:guard.kill();guard.wait()
            guard = None
    try:
        phase('closing_admission')
        pause('telegram');pause('calendar-scheduler')
        code = (ROOT / 'queue_guard.py').read_text().replace("connection.execute(text(\"SET LOCAL lock_timeout = '5s'\"))", "connection.execute(text(\"SET LOCAL lock_timeout = '5s'\"))\n            connection.execute(text(\"SET LOCAL idle_in_transaction_session_timeout = '10min'\"))")
        guard = subprocess.Popen(['docker', 'exec', '-i', baseline['api']['id'], 'python', '-u', '-c', code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ready, _, _ = select.select([guard.stdout], [], [], 15)
        if not ready or guard.stdout.readline().strip() != 'idle_locked':raise GateError('processing_or_lock_unavailable')
        pause('worker');pause('analysis-worker');pause('api')
        validate_states(expected, states())
        run(['docker', 'stop', '--time', '60', baseline['telemost-agent']['id']]);old_stopped = True
        validate_states(expected, states())
        if data_manifest() != json.loads((ROOT / 'online-data-manifest.json').read_text()):raise GateError('data_changed_before_cutover')
        phase('quiescent')
        metadata = attempt / 'metadata';metadata.mkdir(mode=0o700)
        with (metadata / 'database.dump').open('xb') as out:
            r = subprocess.run(['docker', 'exec', baseline['db']['id'], 'pg_dump', '-U', 'minutes', '-d', 'minutes', '-Fc'], stdout=out, stderr=subprocess.PIPE, timeout=120)
            if r.returncode:raise GateError('database_backup_failed')
        run(['docker', 'cp', baseline['api']['id'] + ':/run/secrets', str(metadata / 'secrets')])
        for name in ('db_password', 'calendar_key'):(metadata / 'secrets' / name).chmod(0o444)
        shutil.copy2(PROD / '.env', metadata / 'environment.env')
        for name in ('compose.yaml', 'compose.gpu.yaml', 'compose.telemost.yaml'):shutil.copy2(PROD / name, metadata / name)
        atomic(metadata / 'bindings.json', {'baseline': baseline, 'recordings_sha256': backup['sha256'], 'candidate': IMAGE, 'source_commit': REVISION})
        dbname = 'restore_final_' + uuid4().hex[:12]
        run(['docker', 'exec', STAGE + '-db-1', 'createdb', '-U', 'minutes', dbname])
        with (metadata / 'database.dump').open('rb') as src:
            r = subprocess.run(['docker', 'exec', '-i', STAGE + '-db-1', 'pg_restore', '--exit-on-error', '--no-owner', '-U', 'minutes', '-d', dbname], stdin=src, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            if r.returncode:raise GateError('final_database_restore_failed')
        verify = (ROOT / 'restore-verify.py').read_text().replace('production_restore_online', dbname)
        result = run(['docker', 'run', '--rm', '--network', STAGE + '_isolated', '--read-only', '--cpus', '1', '--memory', '512m', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--volumes-from', STAGE + '-api-1:ro', '--mount', 'type=volume,source=psptm-recorder-restore-data-20261107,target=/restore,readonly', '--mount', 'type=bind,source=' + str(metadata / 'secrets/calendar_key') + ',target=/backup-calendar-key,readonly', '-e', 'DB_HOST=db', '-e', 'DB_PASSWORD_FILE=/run/secrets/db_password', '--entrypoint', 'python', images['api'], '-c', verify], timeout=180)
        atomic(attempt / 'restore-safe.json', json.loads(result))
        import tarfile
        archive = attempt / 'metadata.tar'
        with tarfile.open(archive, 'w') as tar:tar.add(metadata, arcname='metadata')
        digest = hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest()
        atomic(ROOT / 'final-metadata-ready.json', {'archive': str(archive), 'sha256': digest, 'attempt': str(attempt)})
        phase('waiting_for_offhost_metadata')
        deadline = time.monotonic() + 180
        while True:
            gate = attempt / 'offhost-confirmed.json'
            if gate.exists() and json.loads(gate.read_text()).get('sha256') == digest:break
            if guard.poll() is not None or time.monotonic() >= deadline:raise GateError('offhost_metadata_gate_timeout')
            time.sleep(2)
        if (PROD / 'compose.telemost.yaml').read_bytes() != original:raise GateError('overlay_changed_concurrently')
        phase('switching_recorder')
        changed = True
        temp = PROD / 'compose.telemost.yaml.hotfix-tmp';temp.write_bytes(replacement);temp.chmod((PROD / 'compose.telemost.yaml').stat().st_mode & 0o777);os.replace(temp, PROD / 'compose.telemost.yaml')
        compose_agent();health('telemost-agent')
        expected_code = json.loads((ROOT / 'candidate-code-sha256.json').read_text())
        code_check = "from pathlib import Path;import sys,json,hashlib;expected=json.load(sys.stdin);assert all('..' not in Path(name).parts and not Path(name).is_absolute() for name in expected);assert all(hashlib.sha256((Path('/agent')/name).read_bytes()).hexdigest()==digest for name,digest in expected.items());print('code_verified')"
        run(['docker', 'exec', '-i', PREFIX + 'telemost-agent-1', 'python', '-c', code_check], data=json.dumps(expected_code).encode())
        for field in ('Cmd', 'Entrypoint'):
            wanted = run(['docker', 'image', 'inspect', IMAGE, '--format', '{{json .Config.' + field + '}}'])
            actual = run(['docker', 'inspect', PREFIX + 'telemost-agent-1', '--format', '{{json .Config.' + field + '}}'])
            if wanted != actual:raise GateError('recorder_command_override')
        loaded_check = "import sys,json,urllib.request;rows=json.load(sys.stdin);assert all(json.load(urllib.request.urlopen('http://127.0.0.1:8090/sessions/'+r['id'],timeout=3))['status']==r['status'] for r in rows);print('legacy_runtime_verified')"
        run(['docker', 'exec', '-i', PREFIX + 'telemost-agent-1', 'python', '-c', loaded_check], data=json.dumps(expected).encode())
        validate_states(expected, states())
        current = {s: identity(s) for s in baseline};validate_identity(baseline, current, recorder_may_change=True)
        expected_image = run(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}']).decode().strip()
        if current['telemost-agent']['image'] != expected_image:raise GateError('deployed_image_mismatch')
        committed = True
        close_guard()
        health('api')
        proxy_check = "import json,urllib.request;rows=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/telemost/sessions',timeout=12));assert isinstance(rows,list);print('production_api_proxy_verified')"
        run(['docker', 'exec', PREFIX + 'api-1', 'python', '-c', proxy_check], timeout=20)
        for service in ('analysis-worker', 'worker', 'calendar-scheduler', 'telegram'):resume(service)
        health('api')
        phase('complete', legacy_states_preserved=len(expected), unrelated_containers_unchanged=True, candidate=IMAGE)
    except BaseException:
        phase('recovering')
        if not committed:
            if changed:
                current_states = states()
                if any(s['status'] not in TERMINAL for s in current_states):
                    phase('rollback_deferred_active_recording')
                else:
                    (PROD / 'compose.telemost.yaml').write_bytes(original);compose_agent();health('telemost-agent')
            elif old_stopped:
                run(['docker', 'start', baseline['telemost-agent']['id']]);health('telemost-agent')
        raise
    finally:
        try:close_guard()
        except BaseException:print(json.dumps({'manual_check_required':'queue_guard_or_api_resume'}),flush=True)
        for service in ('api', 'analysis-worker', 'worker', 'calendar-scheduler', 'telegram'):
            try:resume(service)
            except GateError:print(json.dumps({'manual_resume_required':service}),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser();parser.add_argument('--apply', action='store_true');args = parser.parse_args()
    os.umask(0o077)
    lock = (ROOT / 'deployment.lock').open('a');fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    def interrupt(*_):raise GateError('deployment_interrupted')
    for name in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):signal.signal(name, interrupt)
    try:main(args.apply)
    except BaseException as error:
        print(json.dumps({'deployment_failed': type(error).__name__, 'code': str(error) if isinstance(error, GateError) else 'private_diagnostics_omitted'}), flush=True)
        raise SystemExit(1)
