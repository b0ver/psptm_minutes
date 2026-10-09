from datetime import timedelta
import json
from uuid import uuid4

import httpx
from sqlalchemy import select,func,text

from app.db import (Session,Meeting,Action,Outbox,TelegramUser,TelegramResource,TelegramDialog,
                    TelegramCommand,TelegramEvent,TelegramCard,BotCursor,now)
from app import telegram_ui as ui
from app.telegram import poll_interface,dispatch_interface,dispatch_owned_action,DeliveryError,telegram_token
class FakeBot:
    def __init__(self, updates=None):self.calls=[];self.updates=updates or []
    def call(self,method,payload):
        self.calls.append((method,payload))
        if method=='getUpdates':return self.updates
        return {'message_id':123}



def update(uid,text='/start',number=1,callback=None):
    message={'message_id':10,'chat':{'id':uid,'type':'private'},'from':{'id':uid},'text':text}
    return {'update_id':number,'callback_query':{'id':'q'+str(number),'from':{'id':uid},'message':message,'data':callback}} if callback else {'update_id':number,'message':message}


def apply(u):
    with Session.begin() as s:return ui.handle(s,u,True,set())


def owned_meeting(uid,status='ready'):
    with Session.begin() as s:
        if not s.get(TelegramUser,str(uid)):s.add(TelegramUser(user_id=str(uid),chat_id=str(uid)));s.flush()
        m=Meeting(title='PRIVATE OWNER TITLE',recording='test.webm',status=status,transcript=[{'start':0,'end':1,'text':'PRIVATE TRANSCRIPT'}],minutes='PRIVATE MINUTES')
        s.add(m);s.flush();s.add(TelegramResource(kind='meeting',resource_id=m.id,user_id=str(uid)))
        return m.id


def test_public_start_and_no_shared_archive(client):
    with Session.begin() as s:s.add(Meeting(title='PRIVATE ARCHIVE',recording='old.webm'))
    bot=FakeBot(updates=[update(101,number=1),update(202,'/meetings',number=2)])
    poll_interface(bot,True,set());poll_interface(bot,True,set())
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(TelegramUser))==2
        payloads=json.dumps([x.payload for x in s.scalars(select(TelegramEvent))])
        assert 'PRIVATE ARCHIVE' not in payloads
        assert s.get(BotCursor,1).offset==3
        assert s.scalar(select(func.count()).select_from(TelegramEvent))==2


def test_user_cannot_read_modify_or_analyze_other_meeting(client):
    mid=owned_meeting(101)
    with Session.begin() as s:
        a=Action(meeting_id=mid,text='PRIVATE TASK',source_seconds=0,evidence='PRIVATE EVIDENCE',status='approved');s.add(a);s.flush();aid=a.id
    apply(update(202,number=1))
    for n,data in enumerate([f'v1:m:{mid}',f'v1:types:{mid}',f'd:{aid}:1'],2):
        answer=apply(update(202,number=n,callback=data))
        assert 'недоступна' in answer[1]
    with Session() as s:
        assert s.get(Action,aid).status=='approved'
        assert 'PRIVATE' not in json.dumps([x.payload for x in s.scalars(select(TelegramEvent).where(TelegramEvent.user_id=='202'))])


def test_control_mode_never_sends_transcript_evidence_or_drafts(client):
    mid=owned_meeting(101)
    with Session.begin() as s:
        s.add(Action(meeting_id=mid,text='PRIVATE DRAFT',source_seconds=0,evidence='PRIVATE EVIDENCE'))
    apply(update(101,number=1,callback=f'v1:m:{mid}'))
    apply(update(101,number=2,text='/tasks'))
    with Session() as s:
        payloads=json.dumps([x.payload for x in s.scalars(select(TelegramEvent))])
        for secret in ('PRIVATE TRANSCRIPT','PRIVATE MINUTES','PRIVATE EVIDENCE','PRIVATE DRAFT'):assert secret not in payloads


def test_link_confirmation_owned_launch_is_durable_and_not_repeatable(client):
    bot=FakeBot(updates=[update(101,'https://telemost.yandex.ru/j/1234567890',1)])
    poll_interface(bot,True,set())
    with Session() as s:
        d=s.get(TelegramDialog,'101');revision=d.revision
        assert s.scalar(select(func.count()).select_from(TelegramCommand))==0
    bot.updates=[update(101,number=2,callback='v1:launch:'+revision)]
    poll_interface(bot,True,set());poll_interface(bot,True,set())
    apply(update(101,number=3,callback='v1:launch:'+revision))
    with Session() as s:
        cmd=s.scalar(select(TelegramCommand));assert cmd.body['recording_authorized']
        assert cmd.body['command_key']==cmd.id
        assert s.scalar(select(func.count()).select_from(TelegramCommand))==1
        assert s.get(TelegramResource,('session',cmd.id)).user_id=='101'


def test_expired_dialog_and_cancel_do_not_start_recording(client):
    apply(update(101,'https://telemost.yandex.ru/j/1234567890',1))
    with Session.begin() as s:
        d=s.get(TelegramDialog,'101');revision=d.revision;d.expires_at=now()-timedelta(seconds=1)
    assert 'устарела' in apply(update(101,number=2,callback='v1:launch:'+revision))[1]
    with Session() as s:assert s.scalar(select(func.count()).select_from(TelegramCommand))==0


def test_group_start_does_not_grant_access(client):
    u=update(101,number=1);u['message']['chat']['type']='group'
    apply(u)
    with Session() as s:assert s.scalar(select(func.count()).select_from(TelegramUser))==0


def test_uncertain_start_reconciles_same_command_key(client):
    apply(update(101,'https://telemost.yandex.ru/j/1234567890',1))
    with Session() as s:rev=s.get(TelegramDialog,'101').revision
    apply(update(101,number=2,callback='v1:launch:'+rev))
    with Session() as s:cid=s.scalar(select(TelegramCommand)).id
    accepted=[]
    def respond(request):
        if request.method=='GET':return httpx.Response(200,json={'id':cid,'status':'recording'}) if accepted else httpx.Response(404)
        accepted.append(json.loads(request.content)['command_key'])
        raise httpx.ReadError('response lost',request=request)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert ui.command_one(client)
        with Session.begin() as s:s.get(TelegramCommand,cid).next_attempt=now()-timedelta(seconds=1)
        assert ui.command_one(client)
    assert accepted==[cid]
    with Session() as s:assert s.get(TelegramCommand,cid).status=='done'


def test_session_callback_and_cancellation_are_owner_scoped(client):
    apply(update(101,'https://telemost.yandex.ru/j/1234567890',1))
    with Session() as s:rev=s.get(TelegramDialog,'101').revision
    apply(update(101,number=2,callback='v1:launch:'+rev))
    with Session() as s:cid=s.scalar(select(TelegramCommand)).id
    for n,op in enumerate(('session','stop_yes','retry_yes'),3):
        assert 'недоступна' in apply(update(202,number=n,callback=f'v1:{op}:{cid}'))[1]
    with Session() as s:assert s.scalar(select(func.count()).select_from(TelegramCommand))==1


def test_approved_task_delivery_uses_its_owner_chat(client):
    mid=owned_meeting(101)
    apply(update(202,number=1))
    with Session.begin() as s:
        a=Action(meeting_id=mid,text='approved task',source_seconds=0,evidence='PRIVATE EVIDENCE',status='approved');s.add(a);s.flush()
        s.add(Outbox(action_id=a.id,version=1,payload='approved snapshot'))
        legacy=Meeting(title='Legacy',recording='old.webm');s.add(legacy);s.flush()
        old=Action(meeting_id=legacy.id,text='old',source_seconds=0,evidence='PRIVATE');s.add(old);s.flush()
        s.add(Outbox(action_id=old.id,version=1,payload='PRIVATE OLD OUTBOX'))
    bot=FakeBot();assert dispatch_owned_action(bot);assert not dispatch_owned_action(bot)
    assert bot.calls[0][1]['chat_id']=='101'
    assert 'PRIVATE' not in json.dumps(bot.calls)
    with Session() as s:assert s.scalar(select(Outbox).where(Outbox.payload=='PRIVATE OLD OUTBOX')).status=='pending'


def test_notification_429_and_block_are_handled(client):
    apply(update(101,number=1))
    class RateBot:
        def call(self,*args):raise DeliveryError('rate_limited',123)
    assert dispatch_interface(RateBot())
    with Session.begin() as s:
        event=s.scalar(select(TelegramEvent));assert event.attempts==0
        assert (event.next_attempt-now()).total_seconds()>120
        event.next_attempt=now()-timedelta(seconds=1)
    class BlockedBot:
        def call(self,*args):raise DeliveryError('bot_blocked')
    assert dispatch_interface(BlockedBot())
    with Session() as s:assert not s.get(TelegramUser,'101').active


def test_env_token_and_disabled_file(monkeypatch,tmp_path):
    file=tmp_path/'token';file.write_text('DISABLED')
    monkeypatch.setenv('TELEGRAM_TOKEN_FILE',str(file));monkeypatch.setenv('TELEGRAM_TOKEN','synthetic-token')
    assert telegram_token()=='synthetic-token'
    file.write_text('different-synthetic-token')
    import pytest
    with pytest.raises(RuntimeError):telegram_token()


def test_card_watcher_creates_only_owner_notifications(client):
    mid=owned_meeting(101);sid=str(uuid4())
    with Session.begin() as s:
        s.add(TelegramResource(kind='session',resource_id=sid,user_id='101'))
        s.add(TelegramCard(session_id=sid,user_id='101',snapshot={'id':sid,'status':'uploaded','meeting_id':mid,'chunks':2}))
    with httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError('No network for terminal session')))) as http:
        ui.watch_cards(http);ui.watch_cards(http)
    with Session() as s:
        events=list(s.scalars(select(TelegramEvent)));assert len(events)==2
        assert all(e.user_id=='101' and e.payload['chat_id']=='101' for e in events)
        assert 'PRIVATE TRANSCRIPT' not in json.dumps([e.payload for e in events])


def test_stop_before_start_cancels_pending_launch(client):
    apply(update(101,'https://telemost.yandex.ru/j/1234567890',1))
    with Session() as s:rev=s.get(TelegramDialog,'101').revision
    apply(update(101,number=2,callback='v1:launch:'+rev))
    with Session() as s:cid=s.scalar(select(TelegramCommand)).id
    apply(update(101,number=3,callback=f'v1:stop_yes:{cid}'))
    requests=[]
    def respond(request):
        requests.append(request.url.path)
        return httpx.Response(404)
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        assert ui.command_one(http)
        assert not ui.command_one(http)
    assert requests==[f'/api/telemost/sessions/{cid}/stop']
    with Session() as s:
        assert s.get(TelegramCommand,cid).status=='cancelled'
        assert s.get(TelegramCard,cid).snapshot['status']=='cancelled'


def test_overdue_uses_task_timezone(client,monkeypatch):
    from datetime import datetime,timezone,date
    monkeypatch.setattr(ui,'now',lambda:datetime(2026,10,8,22,0,tzinfo=timezone.utc))
    mid=owned_meeting(101)
    with Session.begin() as s:
        s.add(Action(meeting_id=mid,text='Moscow overdue',source_seconds=0,evidence='',status='approved',due_on=date(2026,10,8),timezone='Europe/Moscow'))
        s.add(Action(meeting_id=mid,text='UTC still today',source_seconds=0,evidence='',status='approved',due_on=date(2026,10,8),timezone='UTC'))
    with Session() as s:text,_=ui.tasks_view(s,'101','overdue',0)
    assert 'Moscow overdue' in text
    assert 'UTC still today' not in text


def test_terminal_card_stops_polling_after_delivery(client):
    apply(update(101,number=1));sid=str(uuid4())
    with Session.begin() as s:
        s.add(TelegramResource(kind='session',resource_id=sid,user_id='101'))
        s.add(TelegramCard(session_id=sid,user_id='101',snapshot={'id':sid,'status':'cancelled'}))
    with httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError('No network')))) as http:
        ui.watch_cards(http)
        while dispatch_interface(FakeBot()):pass
        ui.watch_cards(http)
    with Session() as s:assert s.get(TelegramCard,sid).settled


def test_dispatcher_lock_coexists_with_workers_but_excludes_second_bot(client):
    from app.db import engine
    from app.telegram import DISPATCHER_LOCK
    with engine.connect() as workers,engine.connect() as bot,engine.connect() as second:
        try:
            for key in (90601008,90601009):
                assert workers.scalar(text('SELECT pg_try_advisory_lock(:key)'),{'key':key})
            assert bot.scalar(text('SELECT pg_try_advisory_lock(:key)'),{'key':DISPATCHER_LOCK})
            assert not second.scalar(text('SELECT pg_try_advisory_lock(:key)'),{'key':DISPATCHER_LOCK})
        finally:
            workers.execute(text('SELECT pg_advisory_unlock_all()'))
            bot.execute(text('SELECT pg_advisory_unlock_all()'))
            second.execute(text('SELECT pg_advisory_unlock_all()'))


def test_telegram_poll_conflict_has_safe_error_code():
    import pytest
    from app.telegram import Telegram
    bot=Telegram('synthetic-token')
    bot.client.close()
    bot.client=httpx.Client(base_url='https://api.telegram.org/botsynthetic-token/',
        transport=httpx.MockTransport(lambda request:httpx.Response(409,json={'ok':False,'error_code':409,'description':'Conflict: terminated by other getUpdates request'})))
    try:
        with pytest.raises(DeliveryError) as error:bot.call('getUpdates',{'timeout':0})
        assert error.value.code=='telegram_poll_conflict'
        assert 'synthetic-token' not in str(error.value)
    finally:bot.client.close()
