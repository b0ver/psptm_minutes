"""Telegram dispatcher adapter for narrowly scoped recorder recovery.
Never invokes /stop, /import, Docker or AI. Feature closed until configured.
"""
import json
import os
from pathlib import Path
import time
from datetime import timedelta
from uuid import UUID
from sqlalchemy import select
from app.db import (TelegramUser, TelegramResource, TelegramCard, TelegramCommand,
                    CalendarOccurrence, TelegramEvent, now)
from app.recovery_signing import sign


# Deliberately longer than TelegramResource.resource_id VARCHAR(36), and not
# a UUID: the deployed legacy dispatcher reads session_id and
# checks TelegramResource ownership before treating an unknown kind as import.
# Keep this sentinel even though only recovery_session_id identifies our target.
LEGACY_RECOVERY_SENTINEL = '__minutes_recovery_only_command_not_a_session_uuid__'


def recovery_session_id(body, require_generation=False):
    if not isinstance(body,dict) or body.get('session_id')!=LEGACY_RECOVERY_SENTINEL:
        raise ValueError('invalid_recovery_command')
    sid=body.get('recovery_session_id')
    if not isinstance(sid,str):raise ValueError('invalid_recovery_session')
    try:canonical=str(UUID(sid))
    except (ValueError,TypeError,AttributeError):raise ValueError('invalid_recovery_session') from None
    if sid!=canonical:raise ValueError('invalid_recovery_session')
    if require_generation:
        generation=body.get('generation')
        if not isinstance(generation,str) or not 1<=len(generation)<=64:
            raise ValueError('invalid_recovery_generation')
    return sid


def recovery_body(sid, generation=None):
    result={'session_id':LEGACY_RECOVERY_SENTINEL,'recovery_session_id':sid}
    if generation is not None:result['generation']=generation
    recovery_session_id(result,require_generation=generation is not None)
    return result


def allowed_users():
    return {x.strip() for x in os.getenv('MINUTES_RECOVERY_CONTROL_ALLOWED_USERS','').split(',') if x.strip().isdigit()}


def enabled(user):
    if str(user) not in allowed_users():return False
    try:
        return len(Path(os.environ['MINUTES_RECOVERY_CONTROL_KEY_FILE']).read_bytes())>=32
    except (KeyError,OSError):
        return False


def calendar_owners():
    from app.recovery_notifications import configured_calendar_owners
    return configured_calendar_owners()


def owns(s,user,sid):
    resource=s.get(TelegramResource,('session',sid))
    subscriptions=set(s.scalars(select(CalendarOccurrence.subscription_id).where(CalendarOccurrence.session_id==sid)))
    if not subscriptions:
        return bool(resource and resource.user_id==user)
    mappings=calendar_owners()
    owners={mappings[sub] for sub in subscriptions if sub in mappings}
    if len(subscriptions)!=1 or len(owners)!=1 or owners!={user}:
        return False
    return resource is None or resource.user_id==user


def candidates(s,user):
    from app.telegram_ui import TERMINAL
    result=set()
    for card in s.scalars(select(TelegramCard).where(TelegramCard.user_id==user)):
        if card.snapshot and card.snapshot.get('status') not in TERMINAL and owns(s,user,card.session_id):
            result.add(card.session_id)
    subscriptions=[sid for sid,uid in calendar_owners().items() if uid==user]
    if subscriptions:
        for item in s.scalars(select(CalendarOccurrence).where(CalendarOccurrence.subscription_id.in_(subscriptions))):
            if item.session_id and item.session_snapshot and item.session_snapshot.get('status') not in TERMINAL and owns(s,user,item.session_id):
                result.add(item.session_id)
    return sorted(result)


def callback(s,user,op,parts,d,update_id,reply):
    from app.telegram_ui import button,keyboard,menu,set_dialog
    if not enabled(user):raise ValueError('access')
    u=s.get(TelegramUser,user)
    if not u or not u.active or u.chat_id!=user:raise ValueError('access')
    if op=='repair':
        active=candidates(s,user)
        if not active:
            reply('У вас нет своей активной записи. Чужую запись я не прерываю.',menu(user));return
        if len(active)!=1:
            reply('Найдено несколько записей. Автоматическое восстановление не выполнялось; нужна проверка.',menu(user));return
        if not s.scalar(select(TelegramCommand.id).where(TelegramCommand.update_id==update_id)):
            s.add(TelegramCommand(update_id=update_id,user_id=user,kind='repair_prepare',body=recovery_body(active[0])))
        reply('Проверяю запись. Здоровую запись не прерываю; файлы и автообработка не затрагиваются.');return
    if not d or d.step!='repair' or len(parts)!=3 or d.revision!=parts[2]:raise ValueError('expired')
    if op=='repair_no':s.delete(d);reply('Восстановление отменено.',menu(user));return
    if op!='repair_yes':raise ValueError('access')
    sid=recovery_session_id(d.data,require_generation=True)
    if not owns(s,user,sid):raise ValueError('access')
    if not s.scalar(select(TelegramCommand.id).where(TelegramCommand.update_id==update_id)):
        s.add(TelegramCommand(update_id=update_id,user_id=user,kind='repair',body=dict(d.data)))
    s.delete(d)
    reply('Запрос принят. Проверю состояние повторно; сохранённые фрагменты останутся, автообработка не запустится.',menu(user))


def dispatch(s,cmd,client):
    from app.telegram_ui import emit,menu,set_dialog,keyboard,button,command_retry
    import httpx
    try:
        sid=recovery_session_id(cmd.body,require_generation=cmd.kind=='repair')
    except ValueError:
        cmd.status='failed';return True
    user=s.get(TelegramUser,cmd.user_id)
    if not enabled(cmd.user_id) or not user or not user.active or user.chat_id!=cmd.user_id or not owns(s,cmd.user_id,sid):
        cmd.status='failed';return True
    cmd.attempts+=1
    path='/control/prepare' if cmd.kind=='repair_prepare' else '/control/confirm'
    if cmd.body.get('operation_pending'):path='/control/status'
    body={'issued_at':time.time(),'request_id':cmd.id,'user_id':cmd.user_id,'session_id':sid,
          'generation':cmd.body.get('generation','-'),'ownership_verified':True}
    try:
        key=Path(os.environ['MINUTES_RECOVERY_CONTROL_KEY_FILE']).read_bytes()
        signature=sign(key,'POST',path,body)
        r=client.post('http://telemost-agent:8090'+path,json=body,headers={'X-Minutes-Control-Signature':signature})
        r.raise_for_status();state=r.json()
        if state.get('session_id')!=sid:raise ValueError('session_mismatch')
        if cmd.kind=='repair' and state.get('operation_id')!=cmd.id:raise ValueError('operation_mismatch')
        if cmd.kind=='repair_prepare':
            cmd.status='done'
            if state.get('status')!='recording' or not state.get('stale') or state.get('recovery_pending'):
                emit(s,cmd.user_id,'repair-ready:'+cmd.id,'Здоровая запись защищена. Если восстановление уже идёт, дождитесь его окончания. Новых действий не выполнено.',menu(cmd.user_id));return True
            d=set_dialog(s,cmd.user_id,'repair',recovery_body(sid,state['generation']))
            d.expires_at=now()+timedelta(seconds=120)
            emit(s,cmd.user_id,'repair-confirm:'+cmd.id,
                 'Восстановить зависшую запись? Сохранённые фрагменты останутся. Автообработка не запустится. Здоровую запись не прерву.',
                 keyboard([[button('Восстановить',f'v1:repair_yes:{d.revision}'),button('Отмена',f'v1:repair_no:{d.revision}')]]))
        elif r.status_code==202 or state.get('status') in {'recovery_pending','recording','finishing'}:
            cmd.body={**cmd.body,'operation_pending':True}
            cmd.next_attempt=now()+timedelta(seconds=5)
            if not s.scalar(select(TelegramEvent.id).where(TelegramEvent.event_key=='repair-pending:'+cmd.id)):
                emit(s,cmd.user_id,'repair-pending:'+cmd.id,'Восстановление началось. Проверяю сохранённые фрагменты; это еще не завершение.',menu(cmd.user_id))
        else:
            cmd.status='done'
            good=state.get('status')=='partial_saved'
            emit(s,cmd.user_id,'repair-result:'+cmd.id,'Сохранённые фрагменты восстановлены. Автообработка не запускалась.' if good else 'Восстановление завершилось с ошибкой. Исходные фрагменты сохранены; нужна проверка.',menu(cmd.user_id))
    except httpx.HTTPStatusError as error:
        if 400<=error.response.status_code<500 and error.response.status_code!=429:
            cmd.status='failed';emit(s,cmd.user_id,'repair-rejected:'+cmd.id,'Восстановление не выполнено: запись активна, изменилась или доступ не подтверждён. Файлы не удалялись.',menu(cmd.user_id))
        else:command_retry(s,cmd)
    except (OSError,ValueError,KeyError,httpx.RequestError):command_retry(s,cmd)
    return True
