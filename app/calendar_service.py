"""Personal local calendar subscriptions, durable intents and scheduler."""
import asyncio
import math
import time
from datetime import timedelta

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, text

from app.db import CalendarSubscription, CalendarOccurrence, CalendarSchedulerState, Session, engine, now
from app.settings import secret
from app.calendar_source import CalendarError, YandexCalendar

OWNER = 'local'  # Same trusted personal web boundary as the existing web archive.
REASONS = {
    'no_telemost_link': 'Нет ссылки Телемоста', 'multiple_telemost_links': 'Нужно выбрать одну ссылку',
    'all_day': 'Событие на весь день', 'cancelled': 'Событие отменено', 'declined': 'Приглашение отклонено',
    'removed': 'Событие удалено или перенесено за пределы расписания', 'paused': 'Автоподключение выключено',
    'user_skip': 'Пропущено владельцем', 'late': 'Время допустимого опоздания истекло',
    'agent_busy': 'Агент занят другой встречей', 'agent_unavailable': 'Агент недоступен',
    'start_uncertain': 'Проверяем результат подключения', 'start_rejected': 'Агент отклонил запуск',
    'calendar_auth_failed': 'Не удалось войти в календарь. Проверьте пароль приложения; новый пароль может заработать через 2–3 часа',
    'calendar_not_found': 'Календарь недоступен', 'calendar_unavailable': 'Не удалось обновить календарь',
    'calendar_parse_failed': 'Не удалось прочитать события или правила повторов',
    'calendar_read_incomplete': 'Получены неполные данные календаря',
    'calendar_response_too_large': 'Ответ календаря превышает допустимый размер',
    'calendar_key_unavailable': 'Ключ подключения недоступен',
    'calendar_discovery_failed': 'Не удалось получить список календарей',
    'calendar_address_invalid': 'Некорректный адрес календаря',
    'calendar_rate_limited': 'Яндекс временно ограничил запросы календаря. Повторите позже; это не означает неверный пароль',
}
MUTABLE = {'scheduled', 'paused', 'blocked', 'cancelled'}
TERMINAL = {'uploaded', 'failed', 'cancelled', 'partial_saved', 'upload_uncertain'}


def cipher():
    try:
        return Fernet(secret('CALENDAR_KEY').encode())
    except Exception:
        raise CalendarError('calendar_key_unavailable') from None


def encrypt(password):
    return cipher().encrypt(password.encode()).decode()


def source(sub):
    try:
        password = cipher().decrypt(sub.credential.encode()).decode()
    except InvalidToken:
        raise CalendarError('calendar_key_unavailable') from None
    return YandexCalendar(sub.username, password)


def subscription_dict(sub):
    return {k: getattr(sub, k) for k in ('id', 'title', 'username', 'timezone', 'enabled', 'connected',
        'recording_authorized', 'analysis_types', 'lead_seconds', 'late_seconds', 'default_duration', 'last_sync', 'error_code')}


def occurrence_dict(item):
    return {k: getattr(item, k) for k in ('id', 'subscription_id', 'title', 'url', 'starts_at', 'ends_at',
        'status', 'reason', 'skipped', 'version', 'session_id', 'session_snapshot')}


def apply_events(s, sub, events, at, stop):
    previous = {o.event_key: o for o in s.scalars(select(CalendarOccurrence).where(
        CalendarOccurrence.subscription_id == sub.id).with_for_update())}
    seen = set()
    for event in events:
        key = event['event_key']
        seen.add(key)
        item = previous.get(key)
        if item is None:
            item = CalendarOccurrence(subscription_id=sub.id, **event)
            s.add(item)
            item.skipped = False
            item.version = 1
        elif item.start_request or item.status not in MUTABLE:
            # An intent is frozen before contacting the agent; never alter a repeat request.
            continue
        else:
            changed = any(getattr(item, k) != v for k, v in event.items())
            if changed:
                item.version += 1
            for k, v in event.items():
                setattr(item, k, v)
        reason = 'user_skip' if item.skipped else event['reason']
        item.status = 'blocked' if reason else ('scheduled' if sub.enabled else 'paused')
        item.reason = reason or (None if sub.enabled else 'paused')
        item.updated_at = at
    for key, item in previous.items():
        if key not in seen and item.status in MUTABLE and not item.start_request and at - timedelta(minutes=5) <= item.starts_at <= stop:
            item.status, item.reason = 'cancelled', 'removed'
            item.version += 1
            item.updated_at = at
    sub.last_sync, sub.next_sync, sub.error_code = at, at + timedelta(minutes=5), None


async def refresh_locked(s, sub, at):
    stop = at + timedelta(days=14)
    try:
        events = await source(sub).events(sub.calendar_href, at - timedelta(minutes=5), stop, sub.timezone)
        apply_events(s, sub, events, at, stop)
        return True
    except CalendarError as exc:
        sub.error_code = str(exc)
        sub.next_sync = at + timedelta(seconds=exc.retry_after)
        return False


async def refresh_subscription(sid):
    with Session.begin() as s:
        sub = s.scalar(select(CalendarSubscription).where(CalendarSubscription.id == sid,
            CalendarSubscription.owner == OWNER, CalendarSubscription.connected.is_(True)).with_for_update())
        if not sub:
            return None
        if sub.error_code == 'calendar_rate_limited' and now() < sub.next_sync:
            return subscription_dict(sub)
        await refresh_locked(s, sub, now())
        return subscription_dict(sub)


async def agent_request(method, path, body=None):
    async with httpx.AsyncClient(base_url='http://telemost-agent:8090', timeout=10,
                                 trust_env=False, follow_redirects=False) as client:
        return await client.request(method, path, json=body)


def start_body(sub, item, at):
    deadline = min((item.ends_at + timedelta(minutes=10)) if item.ends_at else at + timedelta(minutes=sub.default_duration),
                   at + timedelta(minutes=120))
    return {'command_key': item.id, 'url': item.url, 'title': item.title, 'title_from_telemost': False,
        'recording_authorized': True, 'analysis_types': sub.analysis_types,
        'duration_minutes': max(1, min(120, math.ceil((deadline - at).total_seconds() / 60))),
        'admission_deadline': (item.starts_at + timedelta(minutes=10)).timestamp(),
        'recording_deadline': deadline.timestamp()}


async def prepare(sid, oid, at):
    began = time.monotonic()
    # Keep the subscription row locked through fresh reading, serializing pause/cancel with the intent.
    with Session.begin() as s:
        sub = s.scalar(select(CalendarSubscription).where(CalendarSubscription.id == sid).with_for_update())
        if not sub or not sub.connected or not sub.enabled or not sub.recording_authorized:
            return
        if sub.error_code == 'calendar_rate_limited' and at < sub.next_sync:
            return
        item = s.scalar(select(CalendarOccurrence).where(CalendarOccurrence.id == oid).with_for_update())
        if item.status != 'scheduled' or item.skipped:
            return
        # tick considers the next five minutes, but provider revalidation is
        # needed only when the configured admission window actually opens.
        if at < item.starts_at - timedelta(seconds=sub.lead_seconds):
            return
        if at > item.starts_at + timedelta(seconds=sub.late_seconds):
            item.status, item.reason = 'missed', item.reason or 'late'
            return
        if not await refresh_locked(s, sub, at):
            item.reason = sub.error_code
            return
        s.flush()
        verified_at = at + timedelta(seconds=time.monotonic() - began)
        if verified_at > item.starts_at + timedelta(seconds=sub.late_seconds):
            item.status, item.reason = 'missed', 'late'
            return
        if item.status != 'scheduled' or item.reason or at < item.starts_at - timedelta(seconds=sub.lead_seconds):
            return
        # Commit immutable intent before any POST so a killed scheduler can reconcile it.
        item.start_request = start_body(sub, item, at)
        item.session_id = item.id
        item.status, item.reason = 'dispatching', None


async def dispatch(oid, at):
    began = time.monotonic()
    with Session.begin() as s:
        sid = s.scalar(select(CalendarOccurrence.subscription_id).where(CalendarOccurrence.id == oid))
        sub = s.scalar(select(CalendarSubscription).where(CalendarSubscription.id == sid).with_for_update())
        item = s.scalar(select(CalendarOccurrence).where(CalendarOccurrence.id == oid).with_for_update())
        if not item.start_request or item.status not in {'dispatching', 'uncertain', 'started'}:
            return
        posted = False
        try:
            response = await agent_request('GET', '/sessions/' + item.session_id)
            if response.status_code == 200:
                snapshot = response.json()
                if snapshot.get('id') != item.session_id:
                    raise ValueError('unexpected_session')
                item.session_snapshot = snapshot
                item.status, item.reason = ('finished' if snapshot.get('status') in TERMINAL else 'started'), None
                return
            if response.status_code != 404:
                item.reason = 'agent_unavailable'
                return
            if item.status == 'started':
                item.status, item.reason = 'uncertain', 'start_uncertain'
                return  # An accepted session must never be recreated if its state disappeared.
            if item.status == 'uncertain':
                item.reason = 'start_uncertain'
                return  # A timed-out POST may still finish later; never use stale data to resend it.
            if not sub.connected or not sub.enabled or item.skipped:
                item.status, item.reason = 'paused', 'paused'
                item.start_request, item.session_id = None, None
                return
            if at > item.starts_at + timedelta(seconds=sub.late_seconds):
                if item.status != 'uncertain':
                    item.status, item.reason = 'missed', item.reason or 'late'
                return
            # Intent may have waited while the agent was busy; refresh again before retrying.
            # Uncertain POSTs reconcile/retry only the same immutable request.
            if item.status != 'uncertain':
                if sub.error_code == 'calendar_rate_limited' and at < sub.next_sync:
                    item.reason = sub.error_code
                    return
                events = await source(sub).events(sub.calendar_href, at - timedelta(minutes=5), at + timedelta(days=14), sub.timezone)
                current = next((e for e in events if e['event_key'] == item.event_key), None)
                if not current or current['reason'] or any(current[k] != getattr(item, k) for k in ('url', 'starts_at', 'ends_at', 'title')):
                    item.start_request, item.session_id = None, None
                    item.status, item.reason = 'scheduled', None
                    apply_events(s, sub, events, at, at + timedelta(days=14))
                    return
            posted = True
            if at + timedelta(seconds=time.monotonic() - began) > item.starts_at + timedelta(seconds=sub.late_seconds):
                item.status, item.reason = 'missed', 'late'
                return
            response = await agent_request('POST', '/sessions', item.start_request)
            if response.status_code == 202:
                snapshot = response.json()
                if snapshot.get('id') != item.session_id:
                    raise ValueError('unexpected_session')
                item.status, item.reason, item.session_snapshot = 'started', None, snapshot
            elif response.status_code == 409:
                item.status, item.reason = 'dispatching', 'agent_busy'
            elif response.status_code == 503:
                item.reason = 'agent_unavailable'
            elif 400 <= response.status_code < 500:
                item.status, item.reason = 'failed', 'start_rejected'
            else:
                item.status, item.reason = 'uncertain', 'start_uncertain'
        except CalendarError as exc:
            sub.error_code = str(exc)
            sub.next_sync = at + timedelta(seconds=exc.retry_after)
            item.reason = str(exc)
        except (httpx.RequestError, ValueError):
            if posted:
                item.status, item.reason = 'uncertain', 'start_uncertain'
            else:
                item.reason = 'agent_unavailable'
        item.updated_at = at


async def tick():
    # Session-level lock uses this dedicated connection, never a pooled Session connection.
    with engine.connect() as lock:
        if not lock.scalar(text('SELECT pg_try_advisory_lock(78124691)')):
            return
        try:
            at = now()
            with Session() as s:
                ids = list(s.scalars(select(CalendarSubscription.id).where(
                    CalendarSubscription.owner == OWNER, CalendarSubscription.connected.is_(True), CalendarSubscription.next_sync <= at)))
            for sid in ids:
                await refresh_subscription(sid)
            with Session() as s:
                due = list(s.execute(select(CalendarOccurrence.subscription_id, CalendarOccurrence.id).join(CalendarSubscription).where(
                    CalendarSubscription.enabled.is_(True), CalendarSubscription.connected.is_(True),
                    CalendarOccurrence.status == 'scheduled', CalendarOccurrence.starts_at <= at + timedelta(minutes=5)
                ).order_by(CalendarOccurrence.starts_at, CalendarOccurrence.id)))
            for sid, oid in due:
                await prepare(sid, oid, now())
            with Session() as s:
                running = list(s.scalars(select(CalendarOccurrence.id).where(CalendarOccurrence.status.in_(
                    ['dispatching', 'uncertain', 'started'])).order_by(CalendarOccurrence.starts_at, CalendarOccurrence.id)))
            for oid in running:
                await dispatch(oid, now())
            with Session.begin() as s:
                state = s.get(CalendarSchedulerState, 1)
                if state is None:
                    s.add(CalendarSchedulerState(id=1, last_tick=now()))
                else:
                    state.last_tick = now()
        finally:
            lock.execute(text('SELECT pg_advisory_unlock(78124691)'))
            lock.commit()


async def main():
    while True:
        try:
            await tick()
        except Exception:
            # Never emit exception text: provider errors may include private calendar data.
            print('calendar_scheduler_tick_failed', flush=True)
        await asyncio.sleep(5)


if __name__ == '__main__':
    asyncio.run(main())
