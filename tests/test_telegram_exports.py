from uuid import uuid4
import httpx
from sqlalchemy import select,func
from app.db import Session,TelegramUser,TelegramResource,TelegramEvent,Meeting,MeetingReport,RecordingImport
from app.telegram import dispatch_interface,Telegram
from app.telegram_ui import handle
from app.telegram_exports import document
import pytest


def setup_meeting():
    with Session.begin() as s:
        s.add(TelegramUser(user_id='101',chat_id='101'));s.add(TelegramUser(user_id='202',chat_id='202'));s.flush()
        m=Meeting(title='Встреча',recording='audio.webm',status='ready',transcript=[{'start':65,'end':70,'text':'Русский транскрипт'}]);s.add(m);s.flush()
        s.add(TelegramResource(kind='meeting',resource_id=m.id,user_id='101'))
        r=MeetingReport(meeting_id=m.id,template_id='tldr',status='ready',content='Короткий итог');s.add(r);s.flush()
        s.add(RecordingImport(session_id=str(uuid4()),meeting_id=m.id,recording_sha256='a'*64,request_sha256='b'*64,complete=False))
        return m.id,r.id


def callback(user,data,number=1,kind='private'):
    return {'update_id':number,'callback_query':{'id':'cb','from':{'id':int(user)},'message':{'message_id':1,'chat':{'id':int(user),'type':kind}},'data':data}}


def test_export_sections_unicode_and_partial_warning(client):
    mid,rid=setup_meeting()
    with Session() as s:
        filename,raw=document(s,'101',mid,'transcript');text=raw.decode()
        assert filename.endswith('-transcript.md') and '[00:01:05] Русский транскрипт' in text
        assert 'Неполная запись' in text and 'Короткий итог' not in text
        _,raw=document(s,'101',mid,rid);assert 'Короткий итог' in raw.decode() and 'Русский транскрипт' not in raw.decode()
        _,raw=document(s,'101',mid,'all');assert 'Короткий итог' in raw.decode() and 'Русский транскрипт' in raw.decode()
        with pytest.raises(ValueError):document(s,'202',mid,'all')


def test_export_callback_requires_owner_and_private_chat(client):
    mid,rid=setup_meeting()
    with Session.begin() as s:
        handle(s,callback('202','v1:mdr:'+rid),True,set())
        handle(s,callback('101','v1:mdt:'+mid,2,'supergroup'),True,set())
    with Session() as s:assert not s.scalar(select(func.count()).select_from(TelegramEvent))
    with Session.begin() as s:handle(s,callback('101','v1:mdr:'+rid,3),True,set())
    with Session() as s:
        e=s.scalar(select(TelegramEvent));assert e.method=='sendDocument'
        assert 'Короткий итог' not in str(e.payload)


def test_delivery_rechecks_owner_after_queued_request(client):
    mid,rid=setup_meeting()
    with Session.begin() as s:
        handle(s,callback('101','v1:mdr:'+rid),True,set())
        s.get(TelegramResource,('meeting',mid)).user_id='202'
    class NoSend:
        def call(self,*args):raise AssertionError('No private document must be sent')
    assert dispatch_interface(NoSend())
    with Session() as s:assert s.scalar(select(TelegramEvent)).status=='failed'


def test_document_uses_multipart_transport_without_parse_mode():
    seen=[]
    def respond(request):
        seen.append(request)
        return httpx.Response(200,json={'ok':True,'result':{'message_id':1}})
    bot=Telegram('synthetic-token');bot.client.close()
    bot.client=httpx.Client(base_url='https://api.telegram.org/botsynthetic-token/',transport=httpx.MockTransport(respond))
    try:bot.call('sendDocument',{'chat_id':'101','_document':('meeting.md','Русский текст'.encode())})
    finally:bot.client.close()
    assert 'multipart/form-data' in seen[0].headers['content-type']
    assert 'Русский текст'.encode() in seen[0].content and b'meeting.md' in seen[0].content
    assert b'parse_mode' not in seen[0].content
