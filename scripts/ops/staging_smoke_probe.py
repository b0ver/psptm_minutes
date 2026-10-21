"""Synthetic CPU smoke against the fixed isolated staging project, never production."""
import argparse
import hashlib
import io
import json
import pathlib
import re
import subprocess
import time
import urllib.request
import uuid
import wave

PROJECT = 'psptm-minutes-staging-smoke'
BASE = 'http://127.0.0.1:18191'


def command(args, data=None):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=90)
    if result.returncode:
        # Never relay command output, which may include paths or credentials.
        raise RuntimeError('staging_command_failed')
    return result.stdout


def inspect(container):
    return json.loads(command(['docker', 'inspect', container]))[0]


def verify_ownership(root):
    api = inspect(PROJECT + '-api-1')
    db = inspect(PROJECT + '-db-1')
    for service, row in [('api', api), ('db', db)]:
        labels = row['Config'].get('Labels', {})
        if (labels.get('com.docker.compose.project') != PROJECT or
                labels.get('com.docker.compose.service') != service or
                not row['State']['Running'] or row['HostConfig'].get('DeviceRequests')):
            raise RuntimeError('staging_ownership_refused')
        volumes = [m['Name'] for m in row['Mounts'] if m['Type'] == 'volume']
        expected = PROJECT + ('_recordings' if service == 'api' else '_database')
        if volumes != [expected]:
            raise RuntimeError('staging_volume_refused')
        expected_networks = {PROJECT + '_isolated'}
        if service == 'api':
            expected_networks.add(PROJECT + '_frontend')
        if set(row['NetworkSettings']['Networks']) != expected_networks:
            raise RuntimeError('staging_network_refused')
        for mount in row['Mounts']:
            if mount['Type'] == 'bind' and (
                    pathlib.Path(mount['Source']).resolve().parent != root / 'secrets' or
                    mount['Destination'] not in ['/run/secrets/db_password', '/run/secrets/calendar_key'] or
                    mount['RW']):
                raise RuntimeError('staging_secret_mount_refused')
    bindings = api['NetworkSettings']['Ports'].get('8080/tcp')
    if bindings != [{'HostIp': '127.0.0.1', 'HostPort': '18191'}]:
        raise RuntimeError('staging_port_refused')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', api['Image']):
        raise RuntimeError('staging_image_refused')
    return api


def main(root):
    root = root.resolve(strict=True)
    if root == pathlib.Path('/home/epopov/projects/psptm_minutes'):
        raise RuntimeError('production_root_refused')
    compose = ['docker', 'compose', '--env-file', str(root / 'staging.env'),
               '-p', PROJECT, '-f', str(root / 'compose.staging-smoke.yaml')]
    # Check actual mounts and port before any HTTP mutation or restart.
    api = verify_ownership(root)
    command(compose + ['config', '--quiet'])

    def execute(service, args, data=None):
        current = verify_ownership(root)
        container = current if service == 'api' else inspect(PROJECT + '-db-1')
        return command(['docker', 'exec', '-i', container['Id']] + args, data)

    def health():
        with urllib.request.urlopen(BASE + '/healthz', timeout=3) as response:
            if response.status != 200:
                raise RuntimeError('staging_health_failed')

    health()
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\0' * 3200)
    payload = audio.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    boundary = 'minutes-synthetic-staging-probe'
    fields = {'title': 'SYNTHETIC staging smoke',
              'import_session': '8ce2aa9f-ec8a-4979-b9f2-2d579480af16'}
    body = b''
    for name, value in fields.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                 f'\r\n\r\n{value}\r\n').encode()
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="recording"; '
             'filename="synthetic.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode()
    body += payload + f'\r\n--{boundary}--\r\n'.encode()
    request = urllib.request.Request(BASE + '/api/meetings', body,
                                    {'Content-Type': 'multipart/form-data; boundary=' + boundary})
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status in (200, 201)
        meeting_id = json.load(response)['id']
    assert isinstance(meeting_id, str) and str(uuid.UUID(meeting_id)) == meeting_id
    verify = f'''
from app.db import Session, Meeting
from pathlib import Path
import hashlib, io, tarfile, tempfile
with Session() as session:
    meeting = session.get(Meeting, '{meeting_id}')
    assert meeting.title == 'SYNTHETIC staging smoke' and meeting.status == 'queued'
    path = Path('/data/recordings') / meeting.recording
    assert path.parent == Path('/data/recordings')
    assert hashlib.sha256(path.read_bytes()).hexdigest() == '{digest}'
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        archive.add(path, arcname='synthetic.wav')
    buffer.seek(0)
    with tempfile.TemporaryDirectory(prefix='staging-smoke-') as target:
        with tarfile.open(fileobj=buffer) as archive:
            archive.extractall(target, filter='data')
        assert hashlib.sha256((Path(target)/'synthetic.wav').read_bytes()).hexdigest() == '{digest}'
print('synthetic_verified')
'''
    assert execute('api', ['python', '-c', verify]).strip() == b'synthetic_verified'
    current = verify_ownership(root)
    command(['docker', 'restart', current['Id']])
    for _ in range(30):
        try:
            health()
            break
        except (OSError, RuntimeError):
            time.sleep(1)
    else:
        raise RuntimeError('staging_restart_timeout')
    assert execute('api', ['python', '-c', verify]).strip() == b'synthetic_verified'
    dump = execute('db', ['pg_dump', '-U', 'minutes', '-d', 'minutes', '-Fc'])
    restored = 'smoke_restored_' + uuid.uuid4().hex[:12]
    execute('db', ['createdb', '-U', 'minutes', restored])
    try:
        execute('db', ['pg_restore', '--exit-on-error', '--no-owner', '-U', 'minutes', '-d', restored], dump)
        result = execute('db', ['psql', '-U', 'minutes', '-d', restored, '-tAc',
                               f"SELECT count(*) FROM meetings WHERE id='{meeting_id}' AND title='SYNTHETIC staging smoke' AND status='queued'"])
        assert result.strip() == b'1'
    finally:
        execute('db', ['dropdb', '-U', 'minutes', restored])
    receipt = dict(project=PROJECT, runtime_image_id=api['Image'],
                   commit=api['Config'].get('Labels', {}).get('org.opencontainers.image.revision'),
                   host_loopback=True, synthetic_upload=True, restart_persistence=True,
                   synthetic_audio_archive_restore=True, synthetic_database_restore=True,
                   gpu_requested=False, real_e2e=False, release_promotion=False, version_rollback=False)
    target = root / 'smoke-acceptance.json'
    temporary = root / ('.smoke-acceptance-' + uuid.uuid4().hex)
    temporary.write_text(json.dumps(receipt, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(target)
    print(json.dumps(receipt))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=pathlib.Path, required=True)
    try:
        main(parser.parse_args().root)
    except Exception as error:
        print(json.dumps({'passed': False, 'error_type': type(error).__name__}))
        raise SystemExit(1) from None
