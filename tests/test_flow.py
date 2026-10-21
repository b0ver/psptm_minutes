from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select, func, text

from app.db import Session, Meeting, Action, Audit, Outbox, engine, now
from app.registry import approve, locked_action
from app.worker import process_one
from app.ai import ProcessingError
from app.telegram import dispatch_one, DeliveryError, handle_update, poll


def test_local_access_without_key(client):
    assert 'Authorization' not in client.headers
    r = client.post('/api/meetings', files={'recording': ('test.wav', b'synthetic audio')}, data={'title':'Local upload'})
    assert r.status_code == 201
    assert client.get('/api/meetings').status_code == 200
    assert client.get('/api/meetings/'+r.json()['id']).status_code == 200
    assert client.get('/api/actions').status_code == 200
    assert client.get('/api/outbox').status_code == 200


@pytest.mark.parametrize('value,expected', [('true', 201), ('false', 422), ('invalid', 422)])
def test_explicit_upload_completeness_preserves_api_semantics(client, value, expected):
    response = client.post('/api/meetings', files={'recording': ('test.wav', b'synthetic')},
                           data={'title': 'Synthetic', 'recording_complete': value})
    assert response.status_code == expected


@pytest.mark.parametrize('headers', [
    {'Origin':'https://foreign.example'},
    {'Origin':'null'},
    {'Sec-Fetch-Site':'cross-site'},
])
def test_foreign_browser_cannot_write(client, headers):
    r = client.post('/api/meetings', headers=headers, files={'recording':('test.wav',b'synthetic audio')}, data={'title':'test'})
    assert r.status_code == 403
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(Meeting)) == 0


def test_same_origin_browser_can_upload(client):
    r = client.post('/api/meetings', headers={'Origin':'http://testserver','Sec-Fetch-Site':'same-origin'},
                    files={'recording':('test.wav',b'synthetic audio')}, data={'title':'test'})
    assert r.status_code == 201


def test_upload_and_mocked_local_processing(client):
    r = client.post('/api/meetings', files={'recording': ('../../danger.wav', b'synthetic recording')}, data={'title': 'Встреча'})
    assert r.status_code == 201
    mid = r.json()['id']
    seen = []
    def stt(path):
        seen.append(path)
        assert path.name.endswith('.wav') and '..' not in path.name
        return [{'start': 12, 'end': 18, 'text': 'Подготовить отчёт'}]
    def llm(segments):
        assert segments[0]['text'] == 'Подготовить отчёт'
        return 'Черновик протокола', [{'text': 'Подготовить отчёт', 'owner': None, 'source_seconds': 12, 'evidence': 'Подготовить отчёт'}]
    assert process_one(stt, llm)
    assert not process_one(stt, llm)
    detail = client.get('/api/meetings/'+mid).json()
    assert detail['status'] == 'ready'
    a = detail['actions'][0]
    assert a['status'] == 'draft' and a['due_on'] is None and a['owner'] is None
    assert client.get('/api/outbox').json() == []
    assert len(seen) == 1


@pytest.mark.parametrize('filename,content', [('test.exe', b'x'), ('test.wav', b'')])
def test_bad_upload(client, filename, content):
    assert client.post('/api/meetings', files={'recording': (filename, content)}, data={'title': 'test'}).status_code == 422
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(Meeting)) == 0


def test_upload_larger_than_previous_limit(client, tmp_path):
    from app.settings import DATA_DIR
    # Sparse synthetic input: exercise the real multipart/disk path beyond 512 MiB.
    source = tmp_path / 'large.wav'
    size = 512 * 1024 * 1024 + 1
    with source.open('wb') as f:
        f.seek(size - 1)
        f.write(b'x')
    with source.open('rb') as f:
        r = client.post('/api/meetings', files={'recording':('large.wav', f)}, data={'title':'Large synthetic upload'})
    assert r.status_code == 201
    with Session() as s:
        meeting = s.get(Meeting, r.json()['id'])
        stored = DATA_DIR / 'recordings' / meeting.recording
        assert stored.stat().st_size == size
    stored.unlink()


def test_streaming_upload_without_content_length(client):
    body = (b'--test-boundary\r\nContent-Disposition: form-data; name="title"\r\n\r\nStreaming\r\n'
            b'--test-boundary\r\nContent-Disposition: form-data; name="recording"; filename="test.wav"\r\n'
            b'Content-Type: audio/wav\r\n\r\nsynthetic recording\r\n--test-boundary--\r\n')
    r = client.post('/api/meetings', headers={'Content-Type':'multipart/form-data; boundary=test-boundary'},
                    content=(body[i:i+17] for i in range(0, len(body), 17)))
    assert 'content-length' not in r.request.headers
    assert r.status_code == 201


def test_approval_is_idempotent_and_card_has_no_private_sources(client, draft):
    for _ in range(2):
        assert client.post('/api/actions/'+draft+'/approve', json={'version': 1}).status_code == 200
    with Session() as s:
        rows = list(s.scalars(select(Outbox)))
        assert len(rows) == 1 and rows[0].status == 'pending'
        assert 'Подготовить отчёт' in rows[0].payload
        assert 'PRIVATE' not in rows[0].payload
        assert s.scalar(select(func.count()).select_from(Audit)) == 1


def test_stale_draft_and_immutable_approval(client, draft):
    r = client.patch('/api/actions/'+draft, json={'version':1, 'text':'Проверить отчёт', 'owner': None, 'due_on':'2026-10-09'})
    assert r.status_code == 200 and r.json()['version'] == 2
    assert client.post('/api/actions/'+draft+'/approve', json={'version':1}).status_code == 409
    assert client.post('/api/actions/'+draft+'/approve', json={'version':2}).status_code == 200
    assert client.patch('/api/actions/'+draft, json={'version':2, 'text':'изменить'}).status_code == 409
    assert client.post('/api/actions/'+draft+'/status', json={'version':2, 'status':'done'}).status_code == 200
    assert client.post('/api/actions/'+draft+'/status', json={'version':2, 'status':'in_progress'}).status_code == 409


def test_concurrent_approval(draft):
    def submit(_):
        with Session.begin() as s:
            approve(s, locked_action(s, draft), 1)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(submit, range(2)))
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(Outbox)) == 1


def test_rejected_model_fails_job_without_outbox():
    with Session.begin() as s:
        m=Meeting(title='test', recording='test.wav', transcript=[{'start':0,'text':'test'}])
        s.add(m); s.flush(); mid=m.id
    def fail(_):
        raise ProcessingError('cloud_model_forbidden')
    process_one(extractor=fail)
    with Session() as s:
        assert s.get(Meeting, mid).status == 'failed'
        assert s.get(Meeting, mid).error_code == 'cloud_model_forbidden'
        assert s.scalar(select(func.count()).select_from(Action)) == 0


def test_single_worker_lock():
    with engine.connect() as one, engine.connect() as two:
        assert one.scalar(text('SELECT pg_try_advisory_lock(90601008)'))
        assert not two.scalar(text('SELECT pg_try_advisory_lock(90601008)'))
        one.execute(text('SELECT pg_advisory_unlock(90601008)'))


class FakeBot:
    def __init__(self, fail=False, updates=None):
        self.calls=[]; self.fail=fail; self.updates=updates or []
    def call(self, method, payload):
        self.calls.append((method,payload))
        if self.fail: raise DeliveryError('telegram_unavailable')
        if method=='getUpdates': return self.updates
        return {'message_id':123}


def test_delivery_only_after_approval(client, draft):
    bot=FakeBot()
    assert not dispatch_one(bot,'123')
    assert bot.calls==[]
    client.post('/api/actions/'+draft+'/approve',json={'version':1})
    assert dispatch_one(bot,'123')
    assert not dispatch_one(bot,'123')
    method,payload=bot.calls[0]
    assert method=='sendMessage' and payload['chat_id']=='123'
    assert 'PRIVATE' not in payload['text']
    assert len(payload['reply_markup']['inline_keyboard'][0][0]['callback_data'].encode()) <= 64
    with Session() as s:
        assert s.scalar(select(Outbox)).status == 'sent'


def test_delivery_retry_is_bounded(client,draft):
    client.post('/api/actions/'+draft+'/approve',json={'version':1})
    bot=FakeBot(fail=True)
    for i in range(5):
        with Session.begin() as s:
            s.scalar(select(Outbox)).next_attempt=now()-timedelta(seconds=1)
        assert dispatch_one(bot,'123')
    assert not dispatch_one(bot,'123')
    with Session() as s:
        assert s.scalar(select(Outbox)).status=='failed'


@pytest.mark.parametrize('chat,user',[(999,7),(123,999)])
def test_callback_acl(client,draft,chat,user):
    client.post('/api/actions/'+draft+'/approve',json={'version':1})
    update={'callback_query':{'id':'q','from':{'id':user},'message':{'chat':{'id':chat}},'data':'d:'+draft+':1'}}
    with Session.begin() as s:
        assert handle_update(s,update,'123',{'7'})[1]=='Нет доступа'
    with Session() as s:
        assert s.get(Action,draft).status=='approved'


def test_callback_and_cursor_are_durable_and_idempotent(client,draft):
    client.post('/api/actions/'+draft+'/approve',json={'version':1})
    update={'update_id':42,'callback_query':{'id':'q','from':{'id':7},'message':{'chat':{'id':123}},'data':'d:'+draft+':1'}}
    bot=FakeBot(updates=[update])
    poll(bot,'123',{'7'}); poll(bot,'123',{'7'})
    with Session() as s:
        assert s.get(Action,draft).status=='done'
        assert s.scalar(select(func.count()).select_from(Audit))==2
    assert [p['offset'] for m,p in bot.calls if m=='getUpdates']==[0,43]


def test_ready_meeting_retains_unverified_action_warning(client):
    from app.ai import VerifiedActions
    r=client.post('/api/meetings',files={'recording':('test.wav',b'synthetic')},data={'title':'Warning test'})
    actions=VerifiedActions();actions.unverified_count=1
    assert process_one(lambda path:[{'text':'Synthetic test','start':0,'end':1}],lambda segments:('Протокол',actions))
    m=client.get('/api/meetings/'+r.json()['id']).json()
    assert m['status']=='ready' and m['minutes']=='Протокол'
    assert m['error_code']=='unverified_action_candidates' and m['actions']==[]
