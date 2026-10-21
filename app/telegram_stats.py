"""Aggregate statistics for one explicitly configured Telegram group.
Only counters are stored for incoming requests; message text and sender IDs are not retained.
"""
from datetime import datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo
import os

from sqlalchemy import select, func
from app.db import (Session, now, Meeting, MeetingAnalysis, MeetingReport, Action,
                    TelegramUser, TelegramUsage, TelegramStatsGroup, TelegramStatsReply)


def group_id():
    return os.getenv('TELEGRAM_STATS_CHAT_ID', '').strip()


def observe(s, update):
    callback=update.get('callback_query')
    message=callback.get('message',{}) if callback else update.get('message',{})
    member=update.get('my_chat_member',{})
    chat=message.get('chat',{}) or member.get('chat',{})
    if chat.get('type') not in {'group','supergroup'}:return False,None
    sender=(callback or message or member).get('from',{})
    if sender.get('is_bot'):return True,None
    raw=(message.get('text') or '').strip().split()
    command=raw[0].split('@')[0].lower() if raw else ''
    data=callback.get('data','') if callback else ''
    is_stats=command=='/stats' and not callback
    if callback:is_stats=data in {'stats:all','stats:today'}
    if not is_stats and not member:return True,None
    cid=str(chat['id'])
    group=s.get(TelegramStatsGroup,cid)
    if not group:group=TelegramStatsGroup(chat_id=cid,title=str(chat.get('title',''))[:200]);s.add(group);s.flush()
    else:group.title=str(chat.get('title',group.title))[:200];group.observed_at=now()
    if not is_stats:return True,None
    if not s.get(TelegramUsage,update['update_id']):s.add(TelegramUsage(update_id=update['update_id'],kind='group_stats'))
    period='today' if (callback and data=='stats:today') or (not callback and len(raw)>1 and raw[1].lower() in {'today','сегодня'}) else 'all'
    if not s.get(TelegramStatsReply,update['update_id']):
        s.add(TelegramStatsReply(update_id=update['update_id'],chat_id=cid,period=period))
    answer=(callback['id'],'Статистика запрошена' if cid==group_id() else 'Группа пока не подключена') if callback else None
    return True,answer


def record_private_usage(s,update):
    callback=update.get('callback_query')
    message=callback.get('message',{}) if callback else update.get('message',{})
    source=(callback or message).get('from',{})
    if message.get('chat',{}).get('type')!='private' or source.get('is_bot'):return
    if not s.get(TelegramUsage,update['update_id']):
        s.add(TelegramUsage(update_id=update['update_id'],kind='callback' if callback else 'message'))


def grouped(s,model,since=None,date_column=None):
    query=select(model.status,func.count()).group_by(model.status)
    if since is not None:query=query.where(date_column>=since)
    return dict(s.execute(query).all())


def render(s,period):
    local=now().astimezone(ZoneInfo('Europe/Moscow'))
    since=datetime.combine(local.date(),time.min,tzinfo=ZoneInfo('Europe/Moscow')).astimezone(timezone.utc) if period=='today' else None
    meetings=grouped(s,Meeting,since,Meeting.created_at)
    analyses=grouped(s,MeetingAnalysis,since,MeetingAnalysis.updated_at)
    reports=grouped(s,MeetingReport,since,MeetingReport.updated_at)
    actions=grouped(s,Action)  # Tasks have no creation timestamp: always the current total.
    users=s.scalar(select(func.count()).select_from(TelegramUser))
    usage=select(func.count()).select_from(TelegramUsage)
    if since is not None:usage=usage.where(TelegramUsage.created_at>=since)
    requests=s.scalar(usage)
    started=s.scalar(select(func.min(TelegramUsage.created_at)))
    start_label=started.astimezone(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M') if started else local.strftime('%d.%m.%Y %H:%M')
    lines=['PSPTM · статистика приложения', 'Сегодня (МСК)' if since else 'За всё время',local.strftime('На %d.%m.%Y %H:%M МСК'),'']
    lines += [f'Обращения к боту: {requests}',f'Пользователи бота, всего: {users}',
              f'Встречи: {sum(meetings.values())}',
              f'  Транскрипт готов: {meetings.get("ready",0)}',
              f'  В очереди: {meetings.get("queued",0)} · в обработке: {meetings.get("processing",0)} · ошибки: {meetings.get("failed",0)}',
              f'Разборы встреч: готово {analyses.get("ready",0)} · в очереди {analyses.get("queued",0)} · в обработке {analyses.get("processing",0)} · ошибки {analyses.get("failed",0)}',
              f'Отчёты: готово {reports.get("ready",0)} из {sum(reports.values())}',
              f'  В очереди: {reports.get("queued",0)} · в обработке: {reports.get("processing",0)} · ошибки: {reports.get("failed",0)}','',
              f'Поручения, текущий итог: {sum(actions.values())}',
              f'  Черновики: {actions.get("draft",0)} · утверждены: {actions.get("approved",0)}',
              f'  В работе: {actions.get("in_progress",0)} · выполнены: {actions.get("done",0)}','',
              f'Учёт обращений к боту с {start_label} МСК; это входящие сообщения и нажатия, не вызовы AI.',
              'Счётчики охватывают всё приложение, включая веб-загрузки.']
    if since:lines.append('Сегодня: встречи по дате загрузки; разборы и отчёты по последнему обновлению. Пользователи и поручения — текущие итоги.')
    markup={'inline_keyboard':[[{'text':'Обновить','callback_data':'stats:'+period},{'text':'Сегодня' if period=='all' else 'За всё время','callback_data':'stats:today' if period=='all' else 'stats:all'}]]}
    return '\n'.join(lines),markup


def dispatch(bot):
    from app.telegram_errors import DeliveryError
    cid=group_id()
    if not cid:return False
    with Session.begin() as s:
        row=s.scalar(select(TelegramStatsReply).where(TelegramStatsReply.chat_id==cid,TelegramStatsReply.status=='pending',TelegramStatsReply.next_attempt<=now()).order_by(TelegramStatsReply.update_id).with_for_update(skip_locked=True).limit(1))
        if not row:return False
        recent=s.scalar(select(func.max(TelegramStatsReply.next_attempt)).where(TelegramStatsReply.chat_id==cid,TelegramStatsReply.status=='sent'))
        if recent and (now()-recent).total_seconds()<5:
            row.next_attempt=recent+timedelta(seconds=5);return False
        body,markup=render(s,row.period)
        row.attempts+=1
        try:
            bot.call('sendMessage',{'chat_id':cid,'text':body,'reply_markup':markup})
            row.status='sent';row.next_attempt=now()
        except DeliveryError as error:
            if error.code=='rate_limited':row.attempts-=1;row.next_attempt=now()+timedelta(seconds=error.retry_after)
            else:
                row.status='failed' if row.attempts>=5 or error.code=='bot_blocked' else 'pending'
                row.next_attempt=now()+timedelta(seconds=min(300,10*2**row.attempts))
        return True
