"""Calendar controls for the existing trusted, personal web interface."""
from datetime import timedelta
import hashlib
import json
import time
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import select

from app import reports
from app.db import CalendarSubscription, CalendarOccurrence, CalendarSchedulerState, Session, now
from app.calendar_source import CalendarError, YandexCalendar, safe_href
from app.calendar_service import (OWNER, MUTABLE, REASONS, encrypt, subscription_dict,
                                  occurrence_dict, refresh_subscription)

router = APIRouter(prefix='/api/calendars')
# Five-minute, process-local cache of successful discovery only. The digest ties
# the selection to the exact credentials; passwords and failures are not stored.
_discoveries = {}


async def discovered_calendars(body):
    key = hashlib.sha256(json.dumps([body.username, body.password.get_secret_value()]).encode()).digest()
    at = time.monotonic()
    for old_key, (expires, _) in list(_discoveries.items()):
        if expires <= at:
            _discoveries.pop(old_key, None)
    if key in _discoveries:
        return _discoveries[key][1]
    found = await YandexCalendar(body.username, body.password.get_secret_value()).discover()
    if len(_discoveries) >= 128:
        _discoveries.pop(next(iter(_discoveries)))
    _discoveries[key] = (time.monotonic() + 300, found)
    return found



class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: SecretStr

    @field_validator('username')
    @classmethod
    def username_clean(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 33 for c in value):
            raise ValueError('invalid_username')
        return value

    @field_validator('password')
    @classmethod
    def password_size(cls, value):
        if not 1 <= len(value.get_secret_value()) <= 512:
            raise ValueError('invalid_password')
        return value


class Connect(Credentials):
    calendar_href: str = Field(max_length=2000)
    timezone: str = Field(default='Europe/Moscow', max_length=64)

    @field_validator('timezone')
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except (KeyError, ValueError):
            raise ValueError('invalid_timezone') from None
        return value


class Options(BaseModel):
    enabled: bool
    recording_authorized: bool = False
    analysis_types: list[str] = Field(default_factory=lambda: list(reports.DEFAULT_TYPES), max_length=16)
    lead_seconds: int = Field(default=60, ge=0, le=300)
    late_seconds: int = Field(default=300, ge=0, le=300)
    default_duration: int = Field(default=60, ge=1, le=120)


class Skip(BaseModel):
    version: int = Field(ge=1)
    skipped: bool = True


def uuid_value(value):
    try:
        return str(UUID(value))
    except ValueError:
        raise HTTPException(422, 'Некорректный идентификатор') from None


def locked_sub(s, sid):
    sub = s.scalar(select(CalendarSubscription).where(CalendarSubscription.id == uuid_value(sid),
        CalendarSubscription.owner == OWNER).with_for_update())
    if not sub:
        raise HTTPException(404, 'Подписка не найдена')
    return sub


def provider_error(exc):
    return HTTPException(429 if str(exc) == 'calendar_rate_limited' else 422,
        REASONS.get(str(exc), 'Не удалось подключить календарь'),
        headers={'Retry-After': str(exc.retry_after)} if str(exc) == 'calendar_rate_limited' else None)


@router.get('')
def listing():
    with Session() as s:
        subs = list(s.scalars(select(CalendarSubscription).where(CalendarSubscription.owner == OWNER,
            CalendarSubscription.connected.is_(True)).order_by(CalendarSubscription.created_at)))
        items = list(s.scalars(select(CalendarOccurrence).join(CalendarSubscription).where(
            CalendarSubscription.owner == OWNER, CalendarSubscription.connected.is_(True),
            CalendarOccurrence.starts_at >= now() - timedelta(days=1)
        ).order_by(CalendarOccurrence.starts_at).limit(500)))
        state = s.get(CalendarSchedulerState, 1)
        scheduler = {'available': bool(state and state.last_tick >= now() - timedelta(seconds=90)),
                     'last_tick': state.last_tick if state else None}
        by_id = {sub.id: sub for sub in subs}
        overlap, until = set(), None
        for item in items:
            if item.skipped or not item.url or item.status not in {'scheduled', 'paused', 'dispatching', 'started'}:
                continue
            sub = by_id[item.subscription_id]
            begins = item.starts_at - timedelta(seconds=sub.lead_seconds)
            ends = min(item.ends_at + timedelta(minutes=10) if item.ends_at else item.starts_at + timedelta(minutes=sub.default_duration),
                       item.starts_at + timedelta(minutes=120))
            if until and begins < until:
                overlap.add(item.id)
            until = max(until, ends) if until else ends
        return {'subscriptions': [subscription_dict(sub) for sub in subs], 'scheduler': scheduler,
                'occurrences': [{**occurrence_dict(item), 'may_overlap': item.id in overlap} for item in items], 'reasons': REASONS}


@router.post('/discover')
async def discover(body: Credentials):
    try:
        return await discovered_calendars(body)
    except CalendarError as exc:
        raise provider_error(exc) from None


@router.post('', status_code=201)
async def connect(body: Connect):
    try:
        # Do not accept an arbitrary provider href even if it uses the same host.
        address = safe_href(body.calendar_href)
        found = await discovered_calendars(body)
        selected = next((item for item in found if item['href'] == address), None)
        if not selected:
            raise CalendarError('calendar_not_found')
        credential = encrypt(body.password.get_secret_value())
    except CalendarError as exc:
        raise provider_error(exc) from None
    with Session.begin() as s:
        # Same account/calendar reconnect updates the secret and never enables recording.
        from sqlalchemy import text
        import hashlib
        key = int.from_bytes(hashlib.sha256((OWNER + body.username + address).encode()).digest()[:8], 'big', signed=True)
        s.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})
        sub = s.scalar(select(CalendarSubscription).where(CalendarSubscription.owner == OWNER,
            CalendarSubscription.username == body.username, CalendarSubscription.calendar_href == address).with_for_update())
        if sub is None:
            sub = CalendarSubscription(owner=OWNER, username=body.username, calendar_href=address,
                title=selected['title'], analysis_types=list(reports.DEFAULT_TYPES))
            s.add(sub)
        sub.credential, sub.timezone = credential, body.timezone
        sub.connected, sub.enabled, sub.recording_authorized = True, False, False
        sub.next_sync, sub.error_code = now(), None
        s.flush()
        for item in s.scalars(select(CalendarOccurrence).where(CalendarOccurrence.subscription_id == sub.id).with_for_update()):
            if item.status == 'dispatching':
                item.start_request, item.session_id = None, None
                item.status, item.reason = 'paused', 'paused'
            elif item.status == 'scheduled':
                item.status, item.reason = 'paused', 'paused'
        result = subscription_dict(sub)
    await refresh_subscription(result['id'])
    return result


@router.patch('/{sid}')
def options(sid: str, body: Options):
    if body.enabled and not body.recording_authorized:
        raise HTTPException(422, 'Подтвердите разрешение на автоматическую запись')
    try:
        types = reports.selected_types(body.analysis_types)
    except ValueError:
        raise HTTPException(422, 'Выберите известные типы разборов') from None
    if not body.analysis_types:
        raise HTTPException(422, 'Выберите хотя бы один тип разбора')
    with Session.begin() as s:
        sub = locked_sub(s, sid)
        if not sub.connected:
            raise HTTPException(409, 'Сначала подключите календарь')
        sub.enabled, sub.recording_authorized = body.enabled, body.recording_authorized
        sub.analysis_types = types
        sub.lead_seconds, sub.late_seconds, sub.default_duration = body.lead_seconds, body.late_seconds, body.default_duration
        sub.next_sync = now()
        for item in s.scalars(select(CalendarOccurrence).where(CalendarOccurrence.subscription_id == sub.id).with_for_update()):
            if item.status == 'dispatching':  # Agent definitively did not accept this request yet.
                item.start_request, item.session_id = None, None
                item.status, item.reason = 'scheduled', None
            if item.status in MUTABLE and not item.start_request and not item.skipped and item.reason in {None, 'paused'}:
                item.status, item.reason = ('scheduled', None) if sub.enabled else ('paused', 'paused')
                item.version += 1
        return subscription_dict(sub)


@router.post('/{sid}/sync')
async def sync(sid: str):
    result = await refresh_subscription(uuid_value(sid))
    if result is None:
        raise HTTPException(404, 'Подписка не найдена')
    return result


@router.delete('/{sid}')
def disconnect(sid: str):
    with Session.begin() as s:
        sub = locked_sub(s, sid)
        sub.connected, sub.enabled, sub.recording_authorized, sub.credential = False, False, False, ''
        for item in s.scalars(select(CalendarOccurrence).where(CalendarOccurrence.subscription_id == sub.id).with_for_update()):
            if item.status in MUTABLE or item.status == 'dispatching':
                item.status, item.reason, item.start_request, item.session_id = 'cancelled', 'paused', None, None
                item.version += 1
    return {'disconnected': True}


@router.post('/occurrences/{oid}/skip')
def skip(oid: str, body: Skip):
    with Session.begin() as s:
        sid = s.scalar(select(CalendarOccurrence.subscription_id).where(CalendarOccurrence.id == uuid_value(oid)))
        if not sid:
            raise HTTPException(404, 'Событие не найдено')
        sub = locked_sub(s, sid)
        item = s.scalar(select(CalendarOccurrence).where(CalendarOccurrence.id == uuid_value(oid)).with_for_update())
        if item.version != body.version:
            raise HTTPException(409, 'Событие изменилось. Обновите расписание')
        if item.status not in MUTABLE | {'dispatching'}:
            raise HTTPException(409, 'Запуск уже состоялся или его результат проверяется. Используйте управление записью')
        item.skipped = body.skipped
        item.version += 1
        item.start_request, item.session_id = None, None
        item.status, item.reason = ('blocked', 'user_skip') if item.skipped else ('paused', 'paused')
        sub.next_sync = now()
        return occurrence_dict(item)
