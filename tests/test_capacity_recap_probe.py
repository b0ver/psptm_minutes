"""Independent GPU consumer priority. Only synthetic health bodies are used."""
import copy
import io
import json
from urllib.error import URLError

import pytest

from scripts.ops import capacity_watchdog as cw


IDLE = {'db': True, 'ollama': True, 'stt': {'engine': 'synthetic', 'active_leases': 0},
        'queue_depth': 0, 'oldest_pending_sec': 0}


@pytest.fixture
def recap(monkeypatch):
    calls = []
    def inspect(name):
        assert name in [cw.RECAP_BOT, cw.RECAP_STT]
        return {'running': True, 'health': 'healthy', 'labels': {
            'com.docker.compose.project': 'recap',
            'com.docker.compose.service': 'bot' if name == cw.RECAP_BOT else 'stt'}}
    monkeypatch.setattr(cw, 'inspect', inspect)
    monkeypatch.setattr(cw, 'command', lambda argv: json.dumps({
        '8080/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18080'}]}))
    def response(body):
        def get(url, timeout):
            assert url == 'http://127.0.0.1:18080/health' and timeout == 5
            calls.append(url)
            return io.BytesIO(body)
        monkeypatch.setattr(cw.urllib.request, 'urlopen', get)
    response(json.dumps(IDLE).encode())
    return calls, response


def test_idle_recap_performs_only_bounded_health_get(recap):
    calls, _ = recap
    cw.recap_idle()
    assert len(calls) == 1


@pytest.mark.parametrize('key', ['queue_depth', 'oldest_pending_sec', 'active_leases'])
def test_backoff_queue_or_live_stt_blocks_even_without_gpu_compute(recap, key):
    _, response = recap
    value = copy.deepcopy(IDLE)
    (value['stt'] if key == 'active_leases' else value)[key] = 1
    response(json.dumps(value).encode())
    with pytest.raises(cw.Refused, match='recap_busy'): cw.recap_idle()


@pytest.mark.parametrize('value', [True, -1, None, '0', 0.0])
def test_invalid_queue_count_is_not_idle(recap, value):
    _, response = recap
    health = copy.deepcopy(IDLE); health['queue_depth'] = value
    response(json.dumps(health).encode())
    with pytest.raises(cw.Refused, match='recap_probe_unavailable'): cw.recap_idle()


@pytest.mark.parametrize('body', [b'not-json', b'{}', b'[]', b' ' * 65537,
                                    json.dumps(dict(IDLE, db=False)).encode(),
                                    json.dumps(dict(IDLE, ollama=False)).encode()])
def test_unknown_oversize_or_unhealthy_response_refuses(recap, body):
    _, response = recap; response(body)
    with pytest.raises(cw.Refused, match='recap_probe_unavailable'): cw.recap_idle()


def test_unavailable_health_refuses_without_raw_error_details(recap, monkeypatch):
    def broken(*args, **kwargs): raise URLError('synthetic-sensitive-detail')
    monkeypatch.setattr(cw.urllib.request, 'urlopen', broken)
    with pytest.raises(cw.Refused, match='^recap_probe_unavailable$'): cw.recap_idle()


@pytest.mark.parametrize('failure', ['wrong_project', 'unhealthy', 'wrong_port'])
def test_endpoint_identity_is_checked_before_http(recap, monkeypatch, failure):
    calls, _ = recap
    if failure == 'wrong_port':
        monkeypatch.setattr(cw, 'command', lambda argv: '{"8080/tcp":[{"HostIp":"0.0.0.0","HostPort":"18080"}]}')
    else:
        original = cw.inspect
        def inspect(name):
            row = original(name)
            if failure == 'wrong_project': row['labels']['com.docker.compose.project'] = 'other'
            else: row['health'] = 'unhealthy'
            return row
        monkeypatch.setattr(cw, 'inspect', inspect)
    with pytest.raises(cw.Refused, match='recap_probe_unavailable'): cw.recap_idle()
    assert not calls


def test_production_admission_includes_recap_priority(monkeypatch):
    def inspect(name):
        project, service = ('ml-school', 'lab-worker') if name == cw.LAB else (
            'psptm-minutes', 'api' if name == cw.API else 'telemost-agent')
        return dict(running=True, health='healthy', labels={
            'com.docker.compose.project': project, 'com.docker.compose.service': service})
    def command(argv):
        if argv[2] == cw.API:
            return json.dumps(dict(meetings=0, meeting_analysis=0, meeting_reports=0,
                                   queued_meetings=0, queued_analysis=0))
        if argv[2] == cw.AGENT: return '{"blocked":0}'
        return '{"queued":0,"running":0,"unknown":0}'
    def busy(): raise cw.Refused('recap_busy')
    monkeypatch.setattr(cw, 'inspect', inspect)
    monkeypatch.setattr(cw, 'command', command)
    monkeypatch.setattr(cw, 'recap_idle', busy)
    with pytest.raises(cw.Refused, match='recap_busy'): cw.production_idle()
