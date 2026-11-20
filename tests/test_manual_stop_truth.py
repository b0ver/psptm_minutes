"""Manual stop contracts. Synthetic data, isolated PostgreSQL and HTTP transport."""
from datetime import timedelta
from uuid import uuid4
import json

import httpx
import pytest
from sqlalchemy import select, func

from app import telegram_ui as ui
from app.db import (Session, TelegramUser, TelegramResource, TelegramCard,
                    TelegramCommand, TelegramEvent, Meeting, now)
from app.telegram import poll_interface, dispatch_interface


def update(number, sid, op='stop', user=101):
    return {'update_id': number, 'callback_query': {
        'id': 'q'+str(number), 'from': {'id': user}, 'data': f'v1:{op}:{sid}',
        'message': {'message_id': 10, 'chat': {'id': user, 'type': 'private'}}}}


class Bot:
    def __init__(self, updates=()): self.updates=updates; self.calls=[]
    def call(self, method, payload):
        self.calls.append(method)
        return self.updates if method=='getUpdates' else {'message_id': 10}


def seed(status='recording', **fields):
    sid=str(uuid4()); at=now()
    state={'id': sid, 'title': 'Synthetic selected meeting', 'status': status,
           'created_at': at.timestamp()-600, 'recording_started_at': at.timestamp()-300,
           '_agent_confirmed_at': at.timestamp()-60, **fields}
    with Session.begin() as s:
        s.add(TelegramUser(user_id='101', chat_id='101', active=True)); s.flush()
        s.add(TelegramResource(kind='session', resource_id=sid, user_id='101'))
        s.add(TelegramCard(session_id=sid, user_id='101', snapshot=state))
    return sid, state


def click(sid, op='stop_yes', number=1):
    with Session.begin() as s: return ui.handle(s, update(number,sid,op),False,{'101'})


def due():
    with Session.begin() as s:
        for cmd in s.scalars(select(TelegramCommand).where(TelegramCommand.status=='pending')):
            cmd.next_attempt=now()-timedelta(seconds=1)


def snapshot(sid):
    with Session() as s: return s.get(TelegramCard,sid).snapshot


def events():
    with Session() as s: return [e.payload['text'] for e in s.scalars(select(TelegramEvent))]


def test_confirmation_names_selected_meeting_status_age_and_exact_target():
    sid,state=seed(); click(sid,'stop')
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(TelegramCommand))==0
        payload=s.scalar(select(TelegramEvent)).payload
    assert state['title'] in payload['text'] and 'Идёт запись' in payload['text']
    assert 'Длительность: 5 мин.' in payload['text']
    assert 'встреча закончилась' in payload['text']
    assert 'обработать сохраненное' in payload['text']
    assert payload['reply_markup']['inline_keyboard'][0][0]['callback_data']=='v1:stop_yes:'+sid


def test_404_keeps_unknown_outcome_and_retries_only_the_same_stop():
    sid,_=seed(); click(sid); paths=[]
    def respond(request):
        paths.append(request.url.path); return httpx.Response(404)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        for _ in range(10):
            due(); assert ui.command_one(client)
        assert not ui.command_one(client)
    assert paths==['/api/telemost/sessions/'+sid+'/stop']*10
    with Session() as s: assert s.scalar(select(TelegramCommand)).status=='failed'
    state=snapshot(sid)
    assert state['status']=='recording' and state['_stop_uncertain']
    assert state['_agent_issue']=='session_missing'
    with Session() as s: text,_=ui.card_view(s,'101',state)
    assert 'Результат остановки не подтвержден' in text
    assert 'Последнее известное состояние: Идёт запись' in text
    assert 'Отменено' not in text
    assert any('Ее состояние остается неизвестным' in text for text in events())


@pytest.mark.parametrize('response', ['timeout','503','wrong_id','bad_json','bad_status'])
def test_uncertain_stop_does_not_claim_done_or_cancelled(response):
    sid,_=seed(); click(sid)
    def respond(request):
        if response=='timeout': raise httpx.ReadTimeout('synthetic',request=request)
        if response=='503': return httpx.Response(503)
        if response=='bad_json': return httpx.Response(202,text='not json')
        if response=='bad_status': return httpx.Response(202,json={'id':sid,'status':'unrecognized'})
        return httpx.Response(202,json={'id':str(uuid4()),'status':'stopping'})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client: assert ui.command_one(client)
    with Session() as s: assert s.scalar(select(TelegramCommand)).status=='pending'
    state=snapshot(sid); assert state['_stop_uncertain'] and state['status']=='recording'


def test_later_stop_ack_clears_uncertainty_without_claiming_transcript_ready():
    sid,_=seed(); click(sid)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(404))) as client: ui.command_one(client)
    due()
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(202,json={'id':sid,'status':'stopping'}))) as client:
        ui.command_one(client)
    state=snapshot(sid); assert state['status']=='stopping'
    assert '_stop_uncertain' not in state and '_agent_issue' not in state
    with Session() as s: text,_=ui.card_view(s,'101',state)
    assert 'Останавливаем запись' in text and 'Транскрипт готов' not in text


def test_disconnect_card_preserves_metadata_and_freezes_known_duration():
    sid,original=seed(capture_health='rotating')
    def unavailable(request): raise httpx.ConnectError('synthetic',request=request)
    with httpx.Client(transport=httpx.MockTransport(unavailable)) as client: ui.watch_cards(client)
    state=snapshot(sid); assert state['id']==sid and state['title']==original['title']
    assert state['_agent_confirmed_at']==original['_agent_confirmed_at']
    text=events()[-1]
    assert 'Связь с агентом потеряна' in text
    assert 'Последнее известное состояние: Идёт запись' in text
    assert 'Последняя известная длительность: 4 мин.' in text
    assert 'Запись звука продолжается' not in text
    with Session() as s: _,markup=ui.card_view(s,'101',state)
    assert markup['inline_keyboard'][0][0]['callback_data']=='v1:stop:'+sid


def test_loss_and_reconnection_publish_without_waiting_one_minute():
    sid,_=seed(); bot=Bot()
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'id':sid,'status':'recording','title':'Synthetic selected meeting'}))) as client:
        ui.watch_cards(client)
    while dispatch_interface(bot): pass
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(503))) as client: ui.watch_cards(client)
    assert any('Связь с агентом потеряна' in t for t in events())
    while dispatch_interface(bot): pass
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'id':sid,'status':'recording','title':'Synthetic selected meeting'}))) as client:
        ui.watch_cards(client)
    assert '_agent_issue' not in snapshot(sid)
    assert 'Связь с агентом потеряна' not in events()[-1]


def test_reconnected_recording_does_not_clear_unconfirmed_stop():
    sid,_=seed(_stop_uncertain=True,_agent_issue='session_missing')
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'id':sid,'status':'recording'}))) as client:
        ui.watch_cards(client)
    state=snapshot(sid); assert state['_stop_uncertain'] and '_agent_issue' not in state
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'id':sid,'status':'finishing'}))) as client:
        ui.watch_cards(client)
    assert '_stop_uncertain' not in snapshot(sid)


def test_late_poll_does_not_overwrite_newer_stop_response():
    sid,_=seed()
    def respond(request):
        with Session.begin() as s:
            s.get(TelegramCard,sid).snapshot={'id':sid,'status':'stopping','_agent_confirmed_at':now().timestamp()+1}
        return httpx.Response(200,json={'id':sid,'status':'recording'})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client: ui.watch_cards(client)
    assert snapshot(sid)['status']=='stopping'


def test_duplicate_update_filtered_and_distinct_clicks_keep_exact_target():
    sid,_=seed(); bot=Bot([update(1,sid,'stop_yes')])
    poll_interface(bot,False,{'101'}); poll_interface(bot,False,{'101'})
    with Session() as s: assert s.scalar(select(func.count()).select_from(TelegramCommand))==1
    bot.updates=[update(2,sid,'stop_yes')]; poll_interface(bot,False,{'101'}); paths=[]
    def respond(request):
        paths.append(request.url.path); return httpx.Response(202,json={'id':sid,'status':'stopping'})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert ui.command_one(client); assert ui.command_one(client); assert not ui.command_one(client)
    assert paths==['/api/telemost/sessions/'+sid+'/stop']*2


def test_foreign_or_disallowed_confirmation_never_enqueues_stop():
    sid,_=seed()
    with Session.begin() as s:
        answer=ui.handle(s,update(1,sid,'stop',202),True,set())
        assert 'недоступна' in answer[1]
        answer=ui.handle(s,update(2,sid,'stop_yes'),False,set())
        assert 'Нет доступа' in answer[1]
    with Session() as s: assert s.scalar(select(func.count()).select_from(TelegramCommand))==0


def test_partial_unknown_queued_and_ready_remain_distinct():
    sid,_=seed()
    with Session.begin() as s:
        m=Meeting(title='Synthetic',recording='fixture.webm',status='queued');s.add(m);s.flush();mid=m.id
        s.add(TelegramResource(kind='meeting',resource_id=mid,user_id='101'))
    with Session() as s:
        queued,_=ui.card_view(s,'101',{'id':sid,'status':'uploaded','meeting_id':mid})
        partial,markup=ui.card_view(s,'101',{'id':sid,'status':'partial_saved','recording_complete':False})
        unknown,_=ui.card_view(s,'101',{'id':sid,'status':'upload_uncertain'})
    assert 'В очереди на транскрибацию' in queued and 'Транскрипт готов' not in queued
    assert 'Неполная запись' in partial and ':retry:' in json.dumps(markup)
    assert 'передачу нужно повторить' in unknown
    with Session.begin() as s: s.get(Meeting,mid).status='ready'
    with Session() as s: ready,_=ui.card_view(s,'101',{'id':sid,'status':'uploaded','meeting_id':mid})
    assert 'Транскрипт готов' in ready


@pytest.mark.parametrize('fields', [
    {'presence':'unconfirmed', 'capture_health':'healthy'},
    {'capture_health':'ui_unavailable'},
])
def test_alive_agent_without_call_presence_does_not_claim_recording_or_attendance(fields):
    sid,state=seed(recording_issue='connection_unconfirmed', recording_complete=False, **fields)
    with Session() as session:
        text,markup=ui.card_view(session,'101',state)
    assert 'Связь со встречей не подтверждена' in text
    assert 'Идёт запись' not in text
    assert 'Ассистент во встрече' not in text
    assert 'Ассистент остается во встрече' not in text
    assert 'Полнота записи не подтверждена' in text
    assert any(button['callback_data']=='v1:stop:'+sid
               for row in markup['inline_keyboard'] for button in row)
