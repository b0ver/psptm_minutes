"""Synthetic-only streaming client for a guarded, real Ollama cancellation drill.

Never consumes meeting inputs or prints generated text. This client does not
certify server cancellation: an operator must observe the GPU after stopping it.
"""
import json
import os
import re
import signal
import time
from datetime import datetime
from pathlib import Path

import httpx

from app.ai import ProcessingError, validate_endpoint, verify_model
from app.settings import OLLAMA_URL, OLLAMA_MODEL, model_policy

if __package__:
    from .capacity_watchdog import MOSCOW, deadline
else:
    from capacity_watchdog import MOSCOW, deadline


class Cancelled(Exception):
    pass


class Progress:
    def __init__(self, path):
        self.path = Path(path)
        self.state = dict(phase='prepared', chunks=0, characters=0, done=False)
        self.last_write = 0

    def save(self, phase=None, force=False):
        if phase:
            self.state['phase'] = phase
        now = time.monotonic()
        if not force and now - self.last_write < .25:
            return
        self.state['updated_at'] = datetime.now(MOSCOW).isoformat()
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state))
        temporary.replace(self.path)
        self.last_write = now


def stream_probe(client, policy, expected_digest, progress):
    if (not re.fullmatch('[0-9a-f]{64}', expected_digest)
            or policy.get(OLLAMA_MODEL) != expected_digest):
        raise ProcessingError('cancel_probe_digest_mismatch')
    verify_model(client, OLLAMA_MODEL, policy)
    progress.save('requesting', force=True)
    payload = dict(model=OLLAMA_MODEL, stream=True, think=False,
                   options=dict(num_ctx=32768, num_predict=6000, temperature=0),
                   messages=[dict(role='user', content='Синтетическая проверка потока: перечисляй целые числа от 1 до 100000 по порядку. Не сокращай список и не добавляй пояснений.')])
    with client.stream('POST', '/api/chat', json=payload) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            row = json.loads(line)
            if row.get('error'):
                raise ProcessingError('cancel_probe_protocol_error')
            message = row.get('message') or {}
            length = len(message.get('content') or '') + len(message.get('thinking') or '')
            if length:
                progress.state['chunks'] += 1
                progress.state['characters'] += length
                progress.save('streaming', force=progress.state['chunks'] == 1)
            if row.get('done') is True:
                progress.state['done'] = True
                progress.save('completed_before_cancel', force=True)
                return
    raise ProcessingError('cancel_probe_incomplete_stream')


def main():
    os.umask(0o077)
    progress = None
    def cancel(signum, frame):
        raise Cancelled()
    try:
        if os.environ.get('CAPACITY_CANCEL_ACK') != 'synthetic-only':
            raise ProcessingError('cancel_probe_ack_required')
        end = deadline(datetime.now(MOSCOW))
        if end is None:
            raise ProcessingError('cancel_probe_outside_window')
        validate_endpoint(OLLAMA_URL)
        progress = Progress(Path(os.environ.get('DATA_DIR', '/data')) / 'cancel-state.json')
        progress.save(force=True)  # Fail before request if the private volume is not writable.
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
            signal.signal(sig, cancel)
        signal.setitimer(signal.ITIMER_REAL, min(120, end.timestamp() - time.time()))
        with httpx.Client(base_url=OLLAMA_URL, timeout=125, trust_env=False, follow_redirects=False) as client:
            stream_probe(client, model_policy(), os.environ.get('CANCEL_PROBE_EXPECTED_DIGEST', ''), progress)
        # A normally completed generation cannot prove cancellation.
        print(json.dumps({'phase': 'completed_before_cancel', 'server_cancellation_verified': False}))
        return 2
    except Cancelled:
        if progress is not None:
            progress.save('client_cancelled', force=True)
        print(json.dumps({'phase': 'client_cancelled', 'server_cancellation_verified': False}))
        return 130
    except Exception as error:
        code = error.code if isinstance(error, ProcessingError) else 'cancel_probe_failed'
        if progress is not None:
            progress.state['error_code'] = code
            progress.save('failed', force=True)
        print(json.dumps({'phase': 'failed', 'error_code': code, 'server_cancellation_verified': False}))
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == '__main__':
    raise SystemExit(main())
