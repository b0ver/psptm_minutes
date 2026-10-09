import json
from datetime import datetime,timezone
from sqlalchemy import select,func
from app import telegram_stats as stats
from app.db import Session,Meeting,MeetingAnalysis,MeetingReport,Action,TelegramUsage,TelegramStatsGroup,TelegramStatsReply
from app.telegram import poll_interface,DeliveryError

class Bot:
    def __init__(self,updates=()):self.updates=updates;self.calls=[]
    def call(self,method,payload):
        self.calls.append((method,payload))
        return self.updates if method=='getUpdates' else {'message_id':5}


def update(i=1,chat=-100123,text='/stats@bender_svetlanovna_bot'):
    return {'update_id':i,'message':{'chat':{'id':chat,'type':'supergroup','title':'PSPTM_AIForce'},'from':{'id':101},'text':text}}


def test_stats_requires_exact_configured_chat_and_replay_is_deduped(client,monkeypatch):
    monkeypatch.delenv('TELEGRAM_STATS_CHAT_ID',raising=False)
    bot=Bot([update()]);poll_interface(bot,True,set());poll_interface(bot,True,set())
    assert not stats.dispatch(bot)
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(TelegramStatsReply))==1
        assert s.scalar(select(func.count()).select_from(TelegramUsage))==1
    monkeypatch.setenv('TELEGRAM_STATS_CHAT_ID','-100999');assert not stats.dispatch(bot)
    monkeypatch.setenv('TELEGRAM_STATS_CHAT_ID','-100123');assert stats.dispatch(bot)
    sends=[p for m,p in bot.calls if m=='sendMessage']
    assert len(sends)==1 and sends[0]['chat_id']=='-100123'
    assert not stats.dispatch(bot)


def test_render_uses_counts_without_private_materials(client):
    with Session.begin() as s:
        m=Meeting(title='SECRET TITLE',recording='secret.mp4',status='ready',transcript=[{'text':'SECRET TRANSCRIPT'}],minutes='SECRET MINUTES');s.add(m);s.flush()
        s.add(MeetingAnalysis(meeting_id=m.id,status='ready',facts={'secret':'SECRET FACTS'}))
        s.add(MeetingReport(meeting_id=m.id,template_id='tldr',status='ready',content='SECRET REPORT'))
        s.add(Action(meeting_id=m.id,text='SECRET TASK',evidence='SECRET EVIDENCE',owner='SECRET NAME',source_seconds=0,status='draft'))
        s.add(TelegramUsage(update_id=1,kind='message'))
    with Session() as s:text,markup=stats.render(s,'all')
    assert 'SECRET' not in text
    assert 'Встречи: 1' in text and 'Отчёты: готово 1 из 1' in text
    assert 'Обращения к боту: 1' in text and 'Черновики: 1' in text
    assert all(len(b['callback_data'].encode())<=64 for row in markup['inline_keyboard'] for b in row)


def test_today_uses_moscow_midnight_and_report_update_date(client,monkeypatch):
    monkeypatch.setattr(stats,'now',lambda:datetime(2026,10,9,1,tzinfo=timezone.utc))
    with Session.begin() as s:
        old=Meeting(title='old',recording='old',created_at=datetime(2026,10,8,20,tzinfo=timezone.utc));s.add(old);s.flush()
        s.add(Meeting(title='new',recording='new',created_at=datetime(2026,10,8,22,tzinfo=timezone.utc)))
        s.add(MeetingReport(meeting_id=old.id,template_id='tldr',status='ready',updated_at=datetime(2026,10,8,23,tzinfo=timezone.utc)))
    with Session() as s:text,_=stats.render(s,'today')
    assert 'Встречи: 1' in text and 'Отчёты: готово 1 из 1' in text
    assert 'по последнему обновлению' in text


def test_other_group_commands_and_private_stats_callbacks_do_not_send_global_stats(client,monkeypatch):
    monkeypatch.setenv('TELEGRAM_STATS_CHAT_ID','-100123')
    u=update(text='PRIVATE GROUP CONVERSATION')
    poll_interface(Bot([u]),True,set())
    with Session() as s:
        assert not s.scalar(select(func.count()).select_from(TelegramStatsReply))
        assert not s.scalar(select(func.count()).select_from(TelegramUsage))
    u=update(i=2);u['message']['chat']={'id':101,'type':'private'}
    u['callback_query']={'id':'cb','from':{'id':101},'message':u.pop('message'),'data':'stats:all'}
    poll_interface(Bot([u]),True,set())
    assert not stats.dispatch(Bot())


def test_stats_rate_limit_keeps_request_pending(client,monkeypatch):
    monkeypatch.setenv('TELEGRAM_STATS_CHAT_ID','-100123')
    poll_interface(Bot([update()]),True,set())
    class RateBot:
        def call(self,*args):raise DeliveryError('rate_limited',120)
    assert stats.dispatch(RateBot())
    with Session() as s:
        r=s.get(TelegramStatsReply,1)
        assert r.attempts==0 and r.status=='pending'


def test_group_callback_is_scoped_to_its_chat(client,monkeypatch):
    monkeypatch.setenv('TELEGRAM_STATS_CHAT_ID','-100123')
    u=update(chat=-100999);u['callback_query']={'id':'cb','from':{'id':101},'message':u.pop('message'),'data':'stats:today'}
    bot=Bot([u]);poll_interface(bot,True,set());assert not stats.dispatch(bot)
    assert 'Группа пока не подключена' in bot.calls[-1][1]['text']
