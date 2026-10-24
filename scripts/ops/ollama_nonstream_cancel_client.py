"""Synthetic nonstream cancellation client. Generated text is never persisted."""
import hashlib
import json
import os
import re
import signal
from datetime import datetime
from pathlib import Path

import httpx

from app.ai import ProcessingError, validate_endpoint, verify_model
from app.settings import OLLAMA_URL, OLLAMA_MODEL, model_policy

if __package__:
    from .capacity_watchdog import MOSCOW, deadline
    from .ollama_cancel_client import Progress
else:
    from capacity_watchdog import MOSCOW, deadline
    from ollama_cancel_client import Progress


PROFILES = {'extract-json': (8192, 2500, True),
            'analysis-json': (32768, 12000, True),
            'analysis-text': (32768, 6000, False)}


class Cancelled(Exception):
    pass


def payload(profile):
    if profile not in PROFILES:
        raise ProcessingError('cancel_probe_profile_invalid')
    context, predict, structured = PROFILES[profile]
    body = dict(model=OLLAMA_MODEL, stream=False, think=False,
                options=dict(num_ctx=context, num_predict=predict, temperature=0),
                messages=[dict(role='user', content='Синтетическая проверка отмены. Перечисляй все целые числа от 1 до 100000 по порядку. Не сокращай список. Не добавляй пояснения.')])
    if structured:
        body['messages'][0]['content'] += ' Верни числа в массиве items.'
        body['format'] = dict(type='object', properties={'items': dict(type='array', items={'type': 'integer'})},
                              required=['items'], additionalProperties=False)
    return body


def nonstream_probe(client, policy, expected, profile, progress):
    if not re.fullmatch('[0-9a-f]{64}', expected) or policy.get(OLLAMA_MODEL) != expected:
        raise ProcessingError('cancel_probe_digest_mismatch')
    body = payload(profile)
    verify_model(client, OLLAMA_MODEL, policy)
    progress.state.update(transport='nonstream', profile=profile, response_received=False,
                          num_ctx=body['options']['num_ctx'], num_predict=body['options']['num_predict'],
                          payload_sha256=hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest())
    progress.save('request_prepared', force=True)
    def trace(name, info):
        # Ignore trace values. They may contain request/response objects.
        if name == 'http11.send_request_body.complete':
            progress.save('awaiting_response', force=True)
    response = client.post('/api/chat', json=body, extensions={'trace': trace})
    progress.state['response_received'] = True
    progress.save('response_received_before_cancel', force=True)
    response.raise_for_status()
    progress.state['done'] = response.json().get('done') is True
    progress.save(force=True)


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
        profile = os.environ.get('CANCEL_PROBE_PROFILE', '')
        payload(profile)  # Reject an unknown profile before any network request.
        validate_endpoint(OLLAMA_URL)
        progress = Progress(Path(os.environ.get('DATA_DIR', '/data')) / 'cancel-state.json')
        progress.state.update(transport='nonstream', profile=profile, response_received=False)
        progress.save(force=True)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
            signal.signal(sig, cancel)
        signal.setitimer(signal.ITIMER_REAL, min(120, end.timestamp() - datetime.now(MOSCOW).timestamp()))
        with httpx.Client(base_url=OLLAMA_URL, timeout=125, trust_env=False, follow_redirects=False) as client:
            nonstream_probe(client, model_policy(), os.environ.get('CANCEL_PROBE_EXPECTED_DIGEST', ''), profile, progress)
        print(json.dumps({'phase': 'response_received_before_cancel', 'server_cancellation_verified': False}))
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
