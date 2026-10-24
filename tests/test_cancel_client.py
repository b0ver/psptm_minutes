"""Cancellation client only: no real inference or production endpoints."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from app.ai import ProcessingError
from scripts.ops import ollama_cancel_client as probe

DIGEST = 'a' * 64


def transport(request):
    if request.url.path == '/api/tags':
        return httpx.Response(200, json={'models': [{'name': probe.OLLAMA_MODEL, 'digest': DIGEST}]})
    return httpx.Response(200, text=json.dumps({'message': {'content': 'SYNTHETIC RESPONSE MUST NOT BE PERSISTED'}, 'done': True})+'\n')


def test_generated_content_never_enters_progress_file_or_stdout(tmp_path, capsys):
    progress = probe.Progress(tmp_path / 'state.json')
    with httpx.Client(base_url='http://127.0.0.1', transport=httpx.MockTransport(transport)) as client:
        probe.stream_probe(client, {probe.OLLAMA_MODEL: DIGEST}, DIGEST, progress)
    saved = progress.path.read_text()
    assert 'SYNTHETIC RESPONSE' not in saved + capsys.readouterr().out
    state = json.loads(saved)
    assert state['done'] and state['phase'] == 'completed_before_cancel' and state['characters'] > 0


def test_wrong_digest_never_sends_http_request(tmp_path):
    with httpx.Client(base_url='http://127.0.0.1', transport=httpx.MockTransport(lambda r: pytest.fail('HTTP forbidden'))) as client:
        with pytest.raises(ProcessingError, match='cancel_probe_digest_mismatch'):
            probe.stream_probe(client, {probe.OLLAMA_MODEL: DIGEST}, 'b' * 64, probe.Progress(tmp_path / 'state.json'))


def test_cli_requires_ack_without_network(monkeypatch, capsys):
    monkeypatch.delenv('CAPACITY_CANCEL_ACK', raising=False)
    monkeypatch.setattr(probe, 'validate_endpoint', lambda url: pytest.fail('No network before ACK'))
    assert probe.main() == 1
    assert 'cancel_probe_ack_required' in capsys.readouterr().out


def test_closed_window_refuses_before_network(monkeypatch, capsys):
    monkeypatch.setenv('CAPACITY_CANCEL_ACK', 'synthetic-only')
    monkeypatch.setattr(probe, 'deadline', lambda now: None)
    monkeypatch.setattr(probe, 'validate_endpoint', lambda url: pytest.fail('No network outside window'))
    assert probe.main() == 1
    assert 'cancel_probe_outside_window' in capsys.readouterr().out


def test_sigterm_closes_actual_stream_and_reports_only_client_cancellation(tmp_path):
    disconnected = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            body = json.dumps({'models': [{'name': 'qwen3.5:9b', 'digest': DIGEST}]}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200); self.end_headers()
            try:
                for _ in range(400):
                    self.wfile.write((json.dumps({'message': {'content': 'SYNTHETIC TCP RESPONSE'}, 'done': False})+'\n').encode())
                    self.wfile.flush(); time.sleep(.02)
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    policy = tmp_path / 'policy.json'; policy.write_text(json.dumps({'qwen3.5:9b': DIGEST}))
    env = dict(os.environ, CAPACITY_CANCEL_ACK='synthetic-only', CANCEL_PROBE_EXPECTED_DIGEST=DIGEST,
               OLLAMA_BASE_URL=f'http://127.0.0.1:{server.server_port}', OLLAMA_MODEL='qwen3.5:9b',
               MODEL_POLICY_PATH=str(policy), DATA_DIR=str(tmp_path), PYTHONPATH='/app')
    # Only the offline test overrides the clock; the executable CLI has no bypass flag.
    code = "from datetime import datetime,timedelta; from scripts.ops import ollama_cancel_client as p; p.deadline=lambda now: now+timedelta(minutes=20); raise SystemExit(p.main())"
    process = subprocess.Popen([sys.executable, '-c', code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    path = tmp_path / 'cancel-state.json'
    try:
        until = time.monotonic() + 10
        while time.monotonic() < until:
            if path.exists() and json.loads(path.read_text()).get('chunks', 0) >= 2:
                break
            if process.poll() is not None:
                pytest.fail('Synthetic client exited before streaming')
            time.sleep(.05)
        else:
            pytest.fail('Synthetic stream never started')
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 130
        assert disconnected.wait(5)
        assert json.loads(path.read_text())['phase'] == 'client_cancelled'
        result = json.loads(stdout)
        assert not result['server_cancellation_verified']
        assert 'SYNTHETIC TCP RESPONSE' not in stdout + stderr + path.read_text()
    finally:
        if process.poll() is None:
            process.kill(); process.communicate(timeout=5)
        server.shutdown(); server.server_close(); thread.join(timeout=2)
