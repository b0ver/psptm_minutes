"""Synthetic HTTP only. No real model or production database is used."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.ai import ProcessingError
from scripts.ops import ollama_nonstream_cancel_client as p

DIGEST = 'a' * 64


@pytest.mark.parametrize('profile,context,predict,structured', [
    ('extract-json', 8192, 2500, True), ('analysis-json', 32768, 12000, True),
    ('analysis-text', 32768, 6000, False),
])
def test_profiles_match_application_transport_and_budget(profile, context, predict, structured):
    body = p.payload(profile)
    assert body['stream'] is False and body['think'] is False
    assert body['options'] == dict(num_ctx=context, num_predict=predict, temperature=0)
    assert ('format' in body) is structured


def test_response_text_never_enters_progress_or_stdout(tmp_path, capsys):
    requests = []
    def transport(request):
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': p.OLLAMA_MODEL, 'digest': DIGEST}]})
        requests.append(request)
        request.extensions['trace']('http11.send_request_body.complete', {'opaque': 'NEVER_PERSIST_TRACE_DATA'})
        return httpx.Response(200, json={'done': True, 'message': {'content': 'NEVER_PERSIST_RESPONSE_TEXT'}})
    progress = p.Progress(tmp_path / 'state.json')
    with httpx.Client(base_url='http://127.0.0.1', transport=httpx.MockTransport(transport)) as client:
        p.nonstream_probe(client, {p.OLLAMA_MODEL: DIGEST}, DIGEST, 'analysis-json', progress)
    saved = progress.path.read_text()
    assert 'NEVER_PERSIST' not in saved + capsys.readouterr().out
    assert len(requests) == 1
    assert progress.state['response_received'] is True and progress.state['done'] is True
    assert progress.state['phase'] == 'response_received_before_cancel'
    assert progress.state['chunks'] == 0 and len(progress.state['payload_sha256']) == 64


def test_digest_and_profile_refuse_before_http(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('HTTP forbidden'))) as client:
        with pytest.raises(ProcessingError, match='cancel_probe_digest_mismatch'):
            p.nonstream_probe(client, {p.OLLAMA_MODEL: DIGEST}, 'b' * 64, 'extract-json', p.Progress(tmp_path / 'x'))
    with pytest.raises(ProcessingError, match='cancel_probe_profile_invalid'): p.payload('unknown')


def test_cli_ack_and_window_refuse_before_network(monkeypatch, capsys):
    monkeypatch.delenv('CAPACITY_CANCEL_ACK', raising=False)
    monkeypatch.setattr(p.httpx, 'Client', lambda *a, **kw: pytest.fail('No network'))
    assert p.main() == 1 and 'cancel_probe_ack_required' in capsys.readouterr().out
    monkeypatch.setenv('CAPACITY_CANCEL_ACK', 'synthetic-only')
    monkeypatch.setattr(p, 'deadline', lambda now: None)
    assert p.main() == 1 and 'cancel_probe_outside_window' in capsys.readouterr().out
    monkeypatch.setattr(p, 'deadline', lambda now: now + timedelta(minutes=20))
    monkeypatch.setenv('CANCEL_PROBE_PROFILE', 'unknown')
    assert p.main() == 1 and 'cancel_probe_profile_invalid' in capsys.readouterr().out


def test_sigterm_disconnects_actual_nonstream_request_before_response(tmp_path):
    body_sent = threading.Event(); disconnected = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            body = json.dumps({'models': [{'name': 'qwen3.5:9b', 'digest': DIGEST}]}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert body['stream'] is False
            body_sent.set()
            self.connection.settimeout(5)
            try:
                if self.connection.recv(1) == b'': disconnected.set()
            except OSError: pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    policy = tmp_path / 'policy.json'; policy.write_text(json.dumps({'qwen3.5:9b': DIGEST}))
    env = dict(os.environ, CAPACITY_CANCEL_ACK='synthetic-only', CANCEL_PROBE_PROFILE='analysis-json',
               CANCEL_PROBE_EXPECTED_DIGEST=DIGEST, OLLAMA_MODEL='qwen3.5:9b',
               MODEL_POLICY_PATH=str(policy), DATA_DIR=str(tmp_path), PYTHONPATH='/app',
               OLLAMA_BASE_URL=f'http://127.0.0.1:{server.server_port}')
    code = 'from datetime import timedelta;from scripts.ops import ollama_nonstream_cancel_client as p;p.deadline=lambda now:now+timedelta(minutes=20);raise SystemExit(p.main())'
    process = subprocess.Popen([sys.executable, '-c', code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert body_sent.wait(8)
        until = time.monotonic() + 5; state = tmp_path / 'cancel-state.json'
        while time.monotonic() < until:
            if state.exists() and json.loads(state.read_text())['phase'] == 'awaiting_response': break
            time.sleep(.02)
        assert json.loads(state.read_text())['phase'] == 'awaiting_response'
        assert not json.loads(state.read_text())['response_received']
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 130 and disconnected.wait(3)
        final = json.loads(state.read_text())
        assert final['phase'] == 'client_cancelled' and not final['response_received']
        assert not json.loads(stdout)['server_cancellation_verified']
    finally:
        if process.poll() is None: process.kill(); process.wait()
        server.shutdown(); server.server_close(); thread.join(timeout=3)
