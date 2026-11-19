"""Only notify a verified private owner. Calendar owner='local' is not an identity."""
from app.db import TelegramResource, TelegramUser

_ALLOWED = {'partial_saved', 'failed', 'upload_uncertain'}

def queue_recovery_notice(s, event):
    if not isinstance(event, dict) or event.get('status') not in _ALLOWED:
        return 'ignored'
    sid, key = event.get('session_id'), event.get('event_id')
    if not isinstance(sid, str) or not isinstance(key, str) or len(key) != 64:
        return 'ignored'
    resource = s.get(TelegramResource, ('session', sid))
    owner = resource.user_id if resource else None
    if owner is None:
        from app.db import CalendarOccurrence
        from sqlalchemy import select
        subscriptions = set(s.scalars(select(CalendarOccurrence.subscription_id).where(CalendarOccurrence.session_id == sid)))
        mappings = configured_calendar_owners()
        owners = {mappings[sub] for sub in subscriptions if sub in mappings}
        if len(subscriptions) != 1 or len(owners) != 1:
            return 'pending_owner_binding'
        owner = owners.pop()
    from app.recovery_control import owns
    if not owns(s, owner, sid):
        return 'pending_owner_binding'
    user = s.get(TelegramUser, owner)
    # Private Telegram chats use the user's own ID. Never broadcast to a group.
    if not user or not user.active or str(user.chat_id) != str(user.user_id):
        return 'pending_owner_binding'
    from app.telegram_ui import emit
    text = {
        'partial_saved': 'После сбоя сохранена запись. Запись может быть неполной. Автоматическая расшифровка не запускалась.',
        'failed': 'После сбоя восстановление записи не подтверждено. Исходные файлы сохранены. Требуется проверка.',
        'upload_uncertain': 'После сбоя результат прежнего импорта записи не подтверждён. Повторный импорт и расшифровка не запускались. Требуется проверка.',
    }[event['status']]
    emit(s, user.user_id, 'recovery:' + key, text)
    return 'queued'


def configured_calendar_owners():
    import json, os
    try:
        value=json.loads(os.getenv('MINUTES_CALENDAR_RECOVERY_OWNERS_JSON','{}'))
        if not isinstance(value,dict):return {}
        from uuid import UUID
        return {str(UUID(k)):str(v) for k,v in value.items() if isinstance(v,str) and v.isdigit()}
    except (ValueError,TypeError):
        return {}


def watch_recovery_notifications(client):
    watch_calendar_misses()
    from app.db import Session
    import httpx, os, time
    from pathlib import Path
    from app.recovery_signing import sign
    users=sorted(v.strip() for v in os.getenv('MINUTES_RECOVERY_CONTROL_ALLOWED_USERS','').split(',') if v.strip())
    try:key=Path(os.environ['MINUTES_RECOVERY_CONTROL_KEY_FILE']).read_bytes()
    except (KeyError,OSError):return
    actor=os.getenv('MINUTES_RECOVERY_CONTROL_ACTOR','')
    if len(key)<32 or actor not in users:return
    endpoint='http://telemost-agent:8090/control/events'
    # Events survive even when their session leaves the recent-30 listing.
    # No ack deletes them; durable Telegram event_key provides deduplication.
    cursor=''
    seen=set()
    while cursor not in seen:
        seen.add(cursor)
        try:
            body={'issued_at':time.time(),'request_id':'recovery-events','user_id':actor,'session_id':cursor or '-', 'generation':'-', 'ownership_verified':True}
            signature=sign(key,'POST','/control/events',body)
            response=client.post(endpoint,json=body,headers={'X-Minutes-Control-Signature':signature})
            if response.status_code!=200:return
            page=response.json()
            if not isinstance(page,dict) or not isinstance(page.get('items'),list):return
        except (httpx.RequestError,ValueError):return
        for event in page['items']:
            with Session.begin() as s:
                queue_recovery_notice(s,event)
        next_cursor=page.get('next_cursor')
        if not isinstance(next_cursor,str) or not next_cursor:return
        cursor=next_cursor


def watch_calendar_misses():
    from datetime import datetime
    import os
    from sqlalchemy import select
    from app.db import Session,CalendarOccurrence
    from app.telegram_ui import emit
    try:
        cutoff=datetime.fromisoformat(os.environ['MINUTES_RECOVERY_NOTICES_SINCE'])
        if cutoff.tzinfo is None:return
    except (KeyError,ValueError):return
    mappings=configured_calendar_owners()
    if not mappings:return
    with Session.begin() as s:
        rows=s.scalars(select(CalendarOccurrence).where(CalendarOccurrence.subscription_id.in_(mappings),
            CalendarOccurrence.status.in_(['missed','failed']),CalendarOccurrence.starts_at>=cutoff))
        for occurrence in rows:
            owner=mappings[occurrence.subscription_id]
            user=s.get(TelegramUser,owner)
            if not user or not user.active or str(user.chat_id)!=str(user.user_id):continue
            reason={'agent_busy':'агент был занят другой записью',
                    'agent_unavailable':'агент был недоступен',
                    'late':'окно подключения истекло'}.get(occurrence.reason,'подключение не подтверждено')
            emit(s,owner,'calendar-missed:'+occurrence.id+':'+occurrence.status,
                 'Ассистент пропустил запланированную встречу: '+reason+'. Проверьте расписание и состояние записи.')
