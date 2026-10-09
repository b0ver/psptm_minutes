import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from integrations.telemost import service


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'DATA', tmp_path)
    service.sessions.clear(); service.stops.clear(); service.tasks.clear()
    original = httpx.AsyncClient
    def response(request):
        return httpx.Response(200, json=[{'id': 'tldr'}, {'id': 'meeting_notes'}, {'id': 'action_items'}, {'id': 'key_meanings'}, {'id': 'chapters'}])
    monkeypatch.setattr(service.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs))
    async def fake_run(sid):
        try:
            await service.stops[sid].wait()
        finally:
            service.stops.pop(sid, None); service.tasks.pop(sid, None)
    monkeypatch.setattr(service, 'run', fake_run)
    with TestClient(service.app) as client:
        yield client


@pytest.mark.parametrize('url', ['http://telemost.yandex.ru/j/1234567890', 'https://evil.example/j/1234567890', 'https://telemost.yandex.ru@127.0.0.1/j/1234567890', 'https://telemost.yandex.ru/j/1234567890?redirect=http://localhost', 'https://telemost.yandex.ru:443/j/1234567890', 'https://telemost.yandex.ru/j/../../api'])
def test_agent_rejects_non_meeting_links(url):
    with pytest.raises(ValueError):
        service.valid_link(url)


def test_agent_requires_recording_permission(agent):
    assert agent.post('/sessions', json={'url':'https://telemost.yandex.ru/j/1234567890'}).status_code == 422
    assert not service.sessions


def test_agent_single_session_and_stop(agent):
    body={'url':'https://telemost.yandex.ru/j/1234567890', 'recording_authorized':True}
    result=agent.post('/sessions',json=body)
    assert result.status_code == 202
    sid=result.json()['id']
    assert 'url' not in result.json()
    assert agent.post('/sessions',json=body).status_code == 409
    assert agent.post('/sessions/'+sid+'/stop').json()['status']=='cancelled'
    assert agent.get('/sessions/'+sid+'/preview').status_code==404


def test_unknown_report_type_rejected_before_browser(agent):
    result=agent.post('/sessions',json={'url':'https://telemost.yandex.ru/j/1234567890','recording_authorized':True,'analysis_types':['unknown']})
    assert result.status_code==422 and not service.sessions


def test_proxy_failure_does_not_expose_internal_exception(client, monkeypatch):
    from app import api
    async def unavailable(*args, **kwargs):
        from fastapi import HTTPException
        raise HTTPException(503, 'Агент Телемоста пока недоступен')
    monkeypatch.setattr(api,'telemost_request',unavailable)
    response=client.get('/api/telemost/sessions')
    assert response.status_code==503
    assert 'telemost-agent:' not in response.text
    assert client.post('/api/telemost/sessions/not-a-uuid/stop').status_code==422
    assert client.post('/api/telemost/sessions',json={},headers={'Origin':'https://evil.example'}).status_code==403


def test_recovery_preserves_recording(tmp_path,monkeypatch):
    sid='00000000-0000-0000-0000-000000000001'
    folder=tmp_path/sid;folder.mkdir()
    (folder/'state.json').write_text(json.dumps({'id':sid,'status':'recording','title':'Synthetic','created_at':0}))
    (folder/'recording.webm').write_bytes(b'synthetic')
    monkeypatch.setattr(service,'DATA',tmp_path);service.sessions.clear();service.tasks.clear();service.stops.clear()
    with TestClient(service.app) as c:
        assert c.get('/sessions').json()[0]['error_code']=='agent_interrupted'
    assert (folder/'recording.webm').read_bytes()==b'synthetic'


def test_import_reconciles_lost_http_response(tmp_path, monkeypatch):
    import hashlib
    sid='00000000-0000-0000-0000-000000000002'
    folder=tmp_path/sid;folder.mkdir();file=folder/'recording.webm';file.write_bytes(b'synthetic')
    state={'id':sid,'status':'saved','title':'Synthetic','analysis_types':['tldr'],'created_at':0}
    monkeypatch.setattr(service,'DATA',tmp_path);service.sessions.clear();service.sessions[sid]=state
    original=httpx.AsyncClient; posts=[]
    receipt={'id':'meeting-once','status':'queued','recording_complete':False,
             'recording_sha256':hashlib.sha256(b'synthetic').hexdigest(),
             'request_sha256':hashlib.sha256(json.dumps(['Synthetic',['tldr'],'','',False],ensure_ascii=False).encode()).hexdigest()}
    def respond(request):
        if request.method=='GET': return httpx.Response(200,json=receipt) if posts else httpx.Response(404)
        posts.append(1)
        raise httpx.ReadError('lost acknowledgment',request=request)
    monkeypatch.setattr(service.httpx,'AsyncClient',lambda **kwargs: original(transport=httpx.MockTransport(respond),**kwargs))
    asyncio.run(service.import_recording(sid,file,complete=False))
    assert len(posts)==1 and state['status']=='uploaded' and state['meeting_id']=='meeting-once'
    assert state['recording_file']=='recording.webm' and state['recording_complete'] is False
    assert 'Synthetic' not in (folder/'events.jsonl').read_text()


def test_import_rejects_different_metadata_receipt(tmp_path, monkeypatch):
    import hashlib
    sid='00000000-0000-0000-0000-000000000003'
    folder=tmp_path/sid;folder.mkdir();file=folder/'recording.webm';file.write_bytes(b'synthetic')
    state={'id':sid,'status':'saved','title':'Synthetic','analysis_types':['tldr'],'created_at':0}
    monkeypatch.setattr(service,'DATA',tmp_path);service.sessions.clear();service.sessions[sid]=state
    original=httpx.AsyncClient
    def respond(request):
        assert request.method=='GET'
        return httpx.Response(200,json={'recording_sha256':hashlib.sha256(b'synthetic').hexdigest(),
          'recording_complete':True,'request_sha256':'changed'})
    monkeypatch.setattr(service.httpx,'AsyncClient',lambda **kwargs: original(transport=httpx.MockTransport(respond),**kwargs))
    asyncio.run(service.import_recording(sid,file))
    assert state['status']=='failed' and state['error_code']=='import_key_conflict'


def test_restart_preserves_known_native_download_for_retry(tmp_path, monkeypatch):
    sid='00000000-0000-0000-0000-000000000004'
    folder=tmp_path/sid;folder.mkdir();(folder/'recording.webm').write_bytes(b'synthetic')
    monkeypatch.setattr(service,'DATA',tmp_path);service.sessions.clear()
    service.sessions[sid]={'id':sid,'status':'uploading','recording_complete':False,'recording_file':'recording.webm'}
    service.recover_partial(sid,'agent_interrupted')
    assert service.sessions[sid]['status']=='upload_uncertain'
    assert service.sessions[sid]['recording_complete'] is False


def test_start_key_reconciles_response_and_rejects_changed_parameters(agent):
    key='00000000-0000-0000-0000-000000000099'
    body={'url':'https://telemost.yandex.ru/j/1234567890','recording_authorized':True,'command_key':key}
    first=agent.post('/sessions',json=body)
    repeat=agent.post('/sessions',json=body)
    assert first.status_code==202 and repeat.status_code==202
    assert first.json()['id']==repeat.json()['id']==key
    assert agent.get('/sessions/'+key).json()['id']==key
    assert agent.post('/sessions',json={**body,'duration_minutes':30}).status_code==409
    assert agent.post('/sessions',json={**body,'command_key':'invalid'}).status_code==422


def test_start_key_still_matches_when_observed_title_changes(agent):
    key='00000000-0000-0000-0000-000000000088'
    body={'url':'https://telemost.yandex.ru/j/1234567890','recording_authorized':True,'command_key':key}
    assert agent.post('/sessions',json=body).status_code==202
    service.sessions[key]['title']='Название из Телемоста'
    repeated=agent.post('/sessions',json=body)
    assert repeated.status_code==202 and repeated.json()['title']=='Название из Телемоста'


def test_saved_recording_imports_even_when_browser_cleanup_fails(tmp_path, monkeypatch):
    sid='00000000-0000-0000-0000-000000000009'
    monkeypatch.setattr(service,'DATA',tmp_path)
    monkeypatch.setattr(service,'sessions',{sid:{'status':'saved'}})
    calls=[]
    class Browser:
        async def close(self):raise RuntimeError('browser disconnected')
    async def imported(sid, file, complete):calls.append((sid,file,complete))
    monkeypatch.setattr(service,'import_recording',imported)
    file=tmp_path/'recording.webm'
    asyncio.run(service.cleanup_and_import(sid,file,False,Browser()))
    assert calls==[(sid,file,False)]
    assert service.sessions[sid]['cleanup_error_code']=='browser_close_after_save_failed'


@pytest.mark.parametrize('reason', ['meeting_ended_or_removed', 'native_recording_stopped'])
def test_removed_assistant_never_touches_meeting_controls(reason):
    class Forbidden:
        def __getattr__(self, name):raise AssertionError('No UI access after removal')
    asyncio.run(service.stop_native_recording(Forbidden(),Forbidden(),reason))
