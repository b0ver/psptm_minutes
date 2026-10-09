import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / 'private'
API = 'http://127.0.0.1:11434'


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def api(path, payload=None):
    request = urllib.request.Request(API + path, data=None if payload is None else json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def sql(query):
    result = subprocess.run(['docker', 'exec', 'psptm-minutes-db-1', 'psql', '-U', 'minutes', '-d', 'minutes', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-c', 'BEGIN READ ONLY; ' + query + '; COMMIT;'], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('readonly_database_query_failed')
    return result.stdout.strip()


def freeze():
    os.umask(0o077)
    PRIVATE.mkdir(exist_ok=True, mode=0o700)
    if (ROOT / 'manifest.json').exists():
        raise RuntimeError('already_frozen')
    assets = ROOT / 'assets'
    assets.mkdir(exist_ok=True)
    shutil.copytree(ROOT.parent / 'config' / 'meeting_kit', assets / 'meeting_kit', dirs_exist_ok=True)
    shutil.copyfile(ROOT.parent / 'app' / 'reports.py', assets / 'reports.py')
    models, excluded, seen = [], [], set()
    tags = api('/api/tags')['models']
    for model in tags:
        if model['details'].get('format') != 'gguf':
            excluded.append({'name': model['name'], 'reason': 'nonlocal_or_non_gguf', 'digest': model['digest']})
            continue
        show = api('/api/show', {'model': model['name']})
        reason = None
        if model['details'].get('format') != 'gguf':
            reason = 'nonlocal_or_non_gguf'
        elif 'embedding' in show.get('capabilities', []) and 'completion' not in show.get('capabilities', []):
            reason = 'embedding'
        elif model['digest'] in seen:
            reason = 'duplicate_digest'
        if reason:
            excluded.append({'name': model['name'], 'reason': reason, 'digest': model['digest']})
            continue
        seen.add(model['digest'])
        contexts = [value for key, value in show.get('model_info', {}).items() if key.endswith('.context_length') and isinstance(value, int)]
        context = min(32768, min(contexts) if contexts else 32768)
        models.append({**model, 'show': show, 'options': {'num_ctx': context, 'num_predict_json': min(12000, context // 4 if context < 32768 else 12000), 'num_predict_markdown': min(6000, context // 4 if context < 32768 else 6000), 'temperature': 0, 'seed': 42}, 'think': 'low' if model['details'].get('family') == 'gptoss' else False, 'profile': 'compact' if context < 32768 else 'production', 'production_profile_metadata_compatible': context >= 32768})
    atomic(ROOT / 'models.json', {'models': models, 'excluded': excluded})
    records = json.loads(sql("SELECT coalesce(json_agg(row_to_json(source)), '[]'::json) FROM (SELECT id,transcript FROM meetings WHERE json_typeof(transcript)='array' AND json_array_length(transcript)>0 ORDER BY length(transcript::text),id) source"))
    if len(records) < 5:
        raise RuntimeError('fewer_than_five_transcripts')
    def task_markers(record):
        text = ' '.join(segment['text'] for segment in record['transcript']).lower()
        return sum(text.count(marker) for marker in ('сделаю', 'нужно', 'поруч', 'подготов', 'до пятницы', 'беру'))
    def ambiguous_markers(record):
        text = ' '.join(segment['text'] for segment in record['transcript']).lower()
        return sum(text.count(marker) for marker in ('не знаю', 'может', 'наверное', 'непонят', 'предлага', 'отмен'))
    chosen = [records[0], records[len(records) // 2], records[-1]]
    for scoring in (task_markers, ambiguous_markers):
        chosen.append(max((record for record in records if record not in chosen), key=scoring))
    private_records, selection = [], []
    for index, record in enumerate(chosen):
        identifier = f'R{index + 1:02d}'
        encoded = json.dumps(record['transcript'], ensure_ascii=False, sort_keys=True).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        private_records.append({'id': identifier, 'source_id': record['id'], 'segments': record['transcript'], 'input_hash': digest})
        selection.append({'id': identifier, 'sha256': digest, 'segments': len(record['transcript']), 'characters': sum(len(segment['text']) for segment in record['transcript'])})
    atomic(PRIVATE / 'real-inputs.json', private_records)
    atomic(ROOT / 'selection.json', selection)
    atomic(ROOT / 'runtime.json', {'ollama': api('/api/version'), 'gpu': subprocess.run(['nvidia-smi', '--query-gpu=name,driver_version,memory.total', '--format=csv,noheader'], capture_output=True, text=True).stdout.strip(), 'image': subprocess.run(['docker', 'image', 'inspect', 'psptm-minutes:local', '--format', '{{.Id}}'], capture_output=True, text=True).stdout.strip()})
    files = [path for path in ROOT.rglob('*') if path.is_file() and 'private' not in path.parts and '__pycache__' not in path.parts and path.name not in ('gate.json', 'manifest.json')]
    atomic(ROOT / 'manifest.json', {'frozen_at': time.time(), 'models': len(models), 'synthetic_planned': len(models) * 160, 'real_planned': len(models) * 80, 'files': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(files)}})
    print(json.dumps({'models': len(models), 'synthetic': len(models) * 160, 'real': len(models) * 80, 'snapshot_count': len(chosen)}))


def gate():
    os.umask(0o077)
    while True:
        try:
            queues = json.loads(sql("SELECT json_build_object('stt', (SELECT count(*) FROM meetings WHERE status IN ('queued','processing')), 'analysis', (SELECT count(*) FROM meeting_analysis a JOIN meetings m ON m.id=a.meeting_id WHERE a.status='processing' OR (a.status='queued' AND m.status IN ('ready','failed') AND json_typeof(m.transcript)='array' AND json_array_length(m.transcript)>0)))"))
            telemetry = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,utilization.gpu', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=10)
            memory, utilization = [int(value.strip()) for value in telemetry.stdout.strip().split(',')]
            gate_value = {'time': time.time(), 'ready': not any(queues.values()), 'queues': queues, 'memory_mib': memory, 'gpu_utilization': utilization}
            atomic(ROOT / 'gate.json', gate_value)
            with (ROOT / 'resource-samples.jsonl').open('a') as output:
                output.write(json.dumps(gate_value) + '\n')
        except Exception:
            atomic(ROOT / 'gate.json', {'time': time.time(), 'ready': False, 'error': 'gate_unavailable'})
        time.sleep(5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['freeze', 'gate'])
    arguments = parser.parse_args()
    {'freeze': freeze, 'gate': gate}[arguments.command]()
