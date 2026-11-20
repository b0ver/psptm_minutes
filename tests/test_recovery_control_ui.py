from datetime import timedelta
import json
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import select,func
from app.db import (Session,TelegramUser,TelegramResource,TelegramCard,TelegramCommand,
                    TelegramDialog,TelegramEvent,now)
from app import telegram_ui as ui
from app.recovery_signing import verify

KEY=b'test-only-synthetic-control-key-32'

def update(uid=101,number=1,data='v1:repair',chat_type='private'):
    return {'update_id':number,'callback_query':{'id':str(number),'from':{'id':uid},
        'message':{'message_id':10,'chat':{'id':uid,'type':chat_type}},'data':data}}

def apply(data):
    with Session.begin() as s:return ui.handle(s,data,True,set())

@pytest.fixture
def configured(monkeypatch,tmp_path):
    p=tmp_path/'key';p.write_bytes(KEY)
    monkeypatch.setenv('MINUTES_RECOVERY_CONTROL_KEY_FILE',str(p))
    monkeypatch.setenv('MINUTES_RECOVERY_CONTROL_ALLOWED_USERS','101,202')
    sid=str(uuid4())
    with Session.begin() as s:
        s.add(TelegramUser(user_id='101',chat_id='101',active=True));s.flush()
        s.add(TelegramResource(user_id='101',kind='session',resource_id=sid))
        s.add(TelegramCard(user_id='101',session_id=sid,snapshot={'id':sid,'status':'recording'}))
    return sid

def count_commands():
    with Session() as s:return s.scalar(select(func.count()).select_from(TelegramCommand))

def prepare(sid,stale=True):
    def transport(req):
        body=json.loads(req.content)
        verify(KEY,req.method,req.url.path,body,req.headers['X-Minutes-Control-Signature'],{'101','202'})
        assert body['session_id']==sid
        assert req.url.path=='/control/prepare'
        return httpx.Response(200,json={'session_id':sid,'generation':'42','status':'recording','stale':stale,'recovery_pending':False})
    apply(update())
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:assert ui.command_one(client)
    with Session() as s:
        d=s.get(TelegramDialog,'101');return d.revision if d else None

def test_feature_default_closed(monkeypatch):
    monkeypatch.delenv('MINUTES_RECOVERY_CONTROL_ALLOWED_USERS',raising=False)
    assert 'Перезагрузить' not in str(ui.menu('101'))
    apply(update());assert count_commands()==0

def test_menu_is_id_scoped(configured):
    assert 'Перезагрузить' in str(ui.menu('101'))
    assert 'Перезагрузить' not in str(ui.menu('999'))

def test_group_forged_and_other_owner(configured):
    for n,u in enumerate([update(chat_type='group'),update(uid=999),update(uid=202)],1):
        u['update_id']=n;apply(u)
    assert count_commands()==0

def test_healthy_refuses_without_confirmation(configured):
    assert prepare(configured,False) is None
    with Session() as s:
        assert s.scalar(select(TelegramCommand)).status=='done'

def test_confirmation_bound_and_consumed(configured):
    revision=prepare(configured)
    apply(update(uid=202,number=2,data='v1:repair_yes:'+revision))
    assert count_commands()==1
    apply(update(number=3,data='v1:repair_yes:'+revision))
    apply(update(number=4,data='v1:repair_yes:'+revision))
    assert count_commands()==2

def test_expired_confirmation(configured):
    revision=prepare(configured)
    with Session.begin() as s:s.get(TelegramDialog,'101').expires_at=now()-timedelta(seconds=1)
    apply(update(number=2,data='v1:repair_yes:'+revision));assert count_commands()==1

def test_cancel_confirmation(configured):
    revision=prepare(configured)
    apply(update(number=2,data='v1:repair_no:'+revision))
    apply(update(number=3,data='v1:repair_yes:'+revision));assert count_commands()==1

def test_ownership_revoked_before_dispatch(configured):
    revision=prepare(configured);apply(update(number=2,data='v1:repair_yes:'+revision))
    with Session.begin() as s:s.delete(s.get(TelegramResource,('session',configured)))
    def forbidden(req):raise AssertionError('no HTTP allowed')
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as c:assert ui.command_one(c)
    with Session() as s:assert s.scalar(select(TelegramCommand).where(TelegramCommand.kind=='repair')).status=='failed'

def test_confirm_pending_status_result_same_operation(configured):
    revision=prepare(configured);apply(update(number=2,data='v1:repair_yes:'+revision))
    seen=[]
    def transport(req):
        body=json.loads(req.content);seen.append((req.url.path,body['request_id']))
        verify(KEY,req.method,req.url.path,body,req.headers['X-Minutes-Control-Signature'],{'101'})
        assert body['generation']=='42'
        if req.url.path=='/control/confirm':
            return httpx.Response(202,json={'session_id':configured,'status':'recording','result':'accepted','operation_id':body['request_id']})
        assert req.url.path=='/control/status'
        return httpx.Response(200,json={'session_id':configured,'status':'partial_saved','operation_id':body['request_id']})
    with httpx.Client(transport=httpx.MockTransport(transport)) as c:
        assert ui.command_one(c)
        with Session.begin() as s:
            cmd=s.scalar(select(TelegramCommand).where(TelegramCommand.kind=='repair'));cmd.next_attempt=now()-timedelta(seconds=1)
        assert ui.command_one(c)
    assert seen[0][1]==seen[1][1]
    with Session() as s:
        cmd=s.scalar(select(TelegramCommand).where(TelegramCommand.kind=='repair'));assert cmd.status=='done'
        text=' '.join(e.payload['text'] for e in s.scalars(select(TelegramEvent)))
        assert 'Автообработка не запускалась' in text

def test_endpoint_rejects_changed_or_healthy_session(configured):
    revision=prepare(configured);apply(update(number=2,data='v1:repair_yes:'+revision))
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(409,json={'detail':'active_recording_protected'}))) as c:
        assert ui.command_one(c)
    with Session() as s:assert s.scalar(select(TelegramCommand).where(TelegramCommand.kind=='repair')).status=='failed'

def test_exact_calendar_subscription_owner_only(configured,monkeypatch):
    from app.db import CalendarSubscription,CalendarOccurrence
    from app.recovery_control import owns,candidates
    subs=[str(uuid4()),str(uuid4())];sessions=[str(uuid4()),str(uuid4())]
    monkeypatch.setenv('MINUTES_CALENDAR_RECOVERY_OWNERS_JSON',json.dumps({subs[0]:'101'}))
    with Session.begin() as s:
        for n in range(2):
            s.add(CalendarSubscription(id=subs[n],owner='local',username='synthetic',credential='not-a-secret',calendar_href='/cal/'+str(n),title='Test'))
        s.flush()
        for n in range(2):
            s.add(CalendarOccurrence(subscription_id=subs[n],event_key=str(n),uid=str(n),recurrence_id='',title='Test',starts_at=now(),session_id=sessions[n],session_snapshot={'id':sessions[n],'status':'recording'}))
    with Session() as s:
        assert owns(s,'101',sessions[0])
        assert not owns(s,'101',sessions[1])
        assert not owns(s,'202',sessions[0])
        assert sessions[0] in candidates(s,'101')
        assert sessions[1] not in candidates(s,'101')

def test_calendar_binding_conflicting_with_telegram_resource_denied(configured,monkeypatch):
    from app.db import CalendarSubscription,CalendarOccurrence
    from app.recovery_control import owns,candidates
    subscription=str(uuid4())
    monkeypatch.setenv('MINUTES_CALENDAR_RECOVERY_OWNERS_JSON',json.dumps({subscription:'202'}))
    with Session.begin() as s:
        s.add(CalendarSubscription(id=subscription,owner='local',username='synthetic',credential='not-a-secret',calendar_href='/cal/conflict',title='Test'));s.flush()
        s.add(CalendarOccurrence(subscription_id=subscription,event_key='conflict',uid='conflict',recurrence_id='',title='Test',starts_at=now(),session_id=configured,session_snapshot={'id':configured,'status':'recording'}))
    with Session() as s:
        assert not owns(s,'101',configured)
        assert not owns(s,'202',configured)
        assert configured not in candidates(s,'101')
        assert configured not in candidates(s,'202')

@pytest.mark.parametrize('kind',['repair_prepare','repair'])
def test_exact_deployed_legacy_handler_rejects_pending_recovery_without_http(configured,kind):
    """Actual deployed source, not a rewrite/simulation of the unsafe branch."""
    import hashlib,importlib.util
    from pathlib import Path
    fixture=Path(__file__).parent/'fixtures'/'telegram_ui_legacy_9d8b05ee.py'
    assert hashlib.sha256(fixture.read_bytes()).hexdigest()=='9d8b05ee5425adc25ed0a54539c9e8aaeed7b6c142639c4b37975495af73e0df'
    spec=importlib.util.spec_from_file_location('legacy_recovery_rollback_probe',fixture)
    legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
    if kind=='repair_prepare':apply(update())
    else:
        revision=prepare(configured)
        apply(update(number=2,data='v1:repair_yes:'+revision))
    with Session() as s:
        cmd=s.scalar(select(TelegramCommand).where(TelegramCommand.kind==kind))
        cid=cmd.id
        assert cmd.body['session_id']=='__minutes_recovery_only_command_not_a_session_uuid__'
        assert len(cmd.body['session_id'])>TelegramResource.__table__.c.resource_id.type.length==36
        assert cmd.body['recovery_session_id']==configured
        assert legacy.owned(s,'101','session',configured)  # not an ownership-negative test
        assert not legacy.owned(s,'101','session','__minutes_recovery_only_command_not_a_session_uuid__')
    calls=[]
    def transport(request):
        calls.append(request.url.path)
        raise AssertionError('Legacy rollback must not import or make any HTTP request')
    with httpx.Client(transport=httpx.MockTransport(transport)) as c:
        assert legacy.command_one(c)
    assert calls==[]
    with Session() as s:assert s.get(TelegramCommand,cid).status=='failed'

@pytest.mark.parametrize('kind',['repair_prepare','repair'])
@pytest.mark.parametrize('body',[{},[],{'session_id':'__minutes_recovery_only_command_not_a_session_uuid__'},
    {'session_id':'__minutes_recovery_only_command_not_a_session_uuid__','recovery_session_id':'not-a-uuid'}])
def test_invalid_recovery_body_fails_closed_without_retry_loop(configured,kind,body):
    with Session.begin() as s:
        cmd=TelegramCommand(update_id=901,user_id='101',kind=kind,body=body)
        s.add(cmd);s.flush();cid=cmd.id
    def forbidden(request):raise AssertionError('no HTTP for malformed recovery body')
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as c:
        assert ui.command_one(c)
        assert ui.command_one(c) is False
    with Session() as s:assert s.get(TelegramCommand,cid).status=='failed'

@pytest.mark.parametrize('kind',['repair_prepare','repair'])
@pytest.mark.parametrize('with_new_field',[False,True])
def test_old_real_session_id_command_shape_not_accepted_by_new_dispatch(configured,kind,with_new_field):
    with Session.begin() as s:
        body={'session_id':configured,'generation':'42'}
        if with_new_field:body['recovery_session_id']=configured
        cmd=TelegramCommand(update_id=902,user_id='101',kind=kind,body=body)
        s.add(cmd);s.flush();cid=cmd.id
    def forbidden(request):raise AssertionError('no HTTP for pre-hardening shape')
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as c:assert ui.command_one(c)
    with Session() as s:assert s.get(TelegramCommand,cid).status=='failed'


def test_invalid_recovery_dialog_fails_closed(configured):
    from app.telegram_ui import set_dialog
    with Session.begin() as s:
        d=set_dialog(s,'101','repair',{'session_id':configured,'generation':'42'});revision=d.revision
    apply(update(number=9,data='v1:repair_yes:'+revision))
    assert count_commands()==0


@pytest.mark.parametrize('generation',[None,123,''])
def test_repair_command_invalid_generation_fails_closed(configured,generation):
    from app.recovery_control import LEGACY_RECOVERY_SENTINEL
    with Session.begin() as s:
        body={'session_id':LEGACY_RECOVERY_SENTINEL,'recovery_session_id':configured,'generation':generation}
        cmd=TelegramCommand(update_id=903,user_id='101',kind='repair',body=body)
        s.add(cmd);s.flush();cid=cmd.id
    def forbidden(request):raise AssertionError('no HTTP for invalid generation')
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as c:assert ui.command_one(c)
    with Session() as s:assert s.get(TelegramCommand,cid).status=='failed'
