import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.calendar_source import CalendarError, YandexCalendar, expand, safe_href, meeting_link
from app import calendar_service as service
from app.db import CalendarSubscription, CalendarOccurrence, Session, now

def mock_provider(monkeypatch, respond):
    from app import calendar_source
    class Response:
        def __init__(self, response):
            self.status_code, self.headers, self.content = response.status_code, response.headers, response.content
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def iter_content(self, chunk_size):
            yield self.content
    class Client:
        def __init__(self):
            self.headers = {}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def request(self, method, url, **kwargs):
            assert self.trust_env is False and kwargs['allow_redirects'] is False
            request = httpx.Request(method, url, headers={**self.headers, **kwargs['headers']}, content=kwargs['data'])
            return Response(respond(request))
    monkeypatch.setattr(calendar_source.requests, 'Session', Client)


URL = 'https://telemost.yandex.ru/j/1234567890'
START = datetime(2026, 10, 12, 9, tzinfo=timezone.utc)


def ics(content):
    return ('BEGIN:VCALENDAR\nVERSION:2.0\n' + content + '\nEND:VCALENDAR').encode()


def event(uid='one', start='20261012T090000Z', extra='', link=URL):
    return f'BEGIN:VEVENT\nUID:{uid}\nDTSTART:{start}\nSUMMARY:Test\nDESCRIPTION:{link}\n{extra}\nEND:VEVENT'


def parsed(content, at=START):
    return expand(ics(content), at - timedelta(minutes=5), at + timedelta(days=14), 'Europe/Moscow', 'owner@yandex.ru')


@pytest.fixture
def key(tmp_path, monkeypatch):
    path = tmp_path / 'key'
    path.write_bytes(Fernet.generate_key())
    monkeypatch.setenv('CALENDAR_KEY_FILE', str(path))
    return path


@pytest.fixture
def subscription(key):
    with Session.begin() as s:
        sub = CalendarSubscription(username='owner@yandex.ru', credential=service.encrypt('private-password'),
            calendar_href='https://caldav.yandex.ru/calendars/owner/events/', title='Test', enabled=True,
            recording_authorized=True, analysis_types=['tldr'], next_sync=now() + timedelta(hours=1))
        s.add(sub)
        s.flush()
        return sub.id


def insert_events(sid, events, at=START):
    with Session.begin() as s:
        sub = s.get(CalendarSubscription, sid)
        service.apply_events(s, sub, events, at, at + timedelta(days=14))
        s.flush()
        return list(s.scalars(select(CalendarOccurrence.id).where(CalendarOccurrence.subscription_id == sid)))[0]


def read(oid):
    with Session() as s:
        return s.get(CalendarOccurrence, oid)


def fake_source(monkeypatch, events):
    class Source:
        async def events(self, *args):
            if isinstance(events, Exception):
                raise events
            return events
    monkeypatch.setattr(service, 'source', lambda sub: Source())


def test_single_event_move_keeps_identity_and_url_does_not_merge_events():
    first = parsed(event())[0]
    moved = parsed(event(start='20261012T110000Z'))[0]
    assert first['event_key'] == moved['event_key'] and moved['recurrence_id'] == 'single'
    assert len(parsed(event('one') + '\n' + event('two'))) == 2
    assert parsed(event('two'))[0]['event_key'] != first['event_key']


def test_repeat_override_exception_and_cancellation():
    series = event('series', extra='RRULE:FREQ=DAILY;COUNT=4\nEXDATE:20261014T090000Z')
    override = event('series', start='20261013T110000Z', extra='RECURRENCE-ID:20261013T090000Z')
    cancellation = event('series', start='20261015T090000Z', extra='RECURRENCE-ID:20261015T090000Z\nSTATUS:CANCELLED')
    items = parsed(series + '\n' + override + '\n' + cancellation)
    assert [x['starts_at'].hour for x in items] == [9, 11, 9]
    assert items[2]['reason'] == 'cancelled'
    assert items[1]['recurrence_id'] == '2026-10-13T09:00:00+00:00'
    baseline = parsed(series)
    assert items[1]['event_key'] == baseline[1]['event_key']


def test_timezone_float_all_day_declined_and_multiple_links():
    assert parsed(event(start='20261012T120000'))[0]['starts_at'] == START
    all_day = event().replace('DTSTART:20261012T090000Z', 'DTSTART;VALUE=DATE:20261013')
    assert parsed(all_day)[0]['reason'] == 'all_day'
    declined = event(extra='ATTENDEE;PARTSTAT=DECLINED:mailto:owner@yandex.ru')
    assert parsed(declined)[0]['reason'] == 'declined'
    assert parsed(event(extra='LOCATION:https://telemost.yandex.ru/j/9876543210'))[0]['reason'] == 'multiple_telemost_links'
    assert parsed(event(link='', extra='X-TELEMOST-CONFERENCE:' + URL))[0]['url'] == URL
    assert parsed(event(link='https://example.org'))[0]['reason'] == 'no_telemost_link'


def test_dst_repeat_follows_local_time():
    contents = event(start='20261024T090000', extra='RRULE:FREQ=DAILY;COUNT=3').replace('DTSTART:', 'DTSTART;TZID=Europe/Berlin:')
    items = parsed(contents, datetime(2026, 10, 24, tzinfo=timezone.utc))
    assert [x['starts_at'].hour for x in items] == [7, 8, 8]


@pytest.mark.parametrize('href', ['http://caldav.yandex.ru/a', 'https://evil.example/a', 'https://caldav.yandex.ru@evil.example/a', 'https://caldav.yandex.ru:443/a', '//127.0.0.1/a', '/a?token=x'])
def test_provider_rejects_foreign_addresses(href):
    with pytest.raises(CalendarError):
        safe_href(href)


@pytest.mark.parametrize('url', ['http://telemost.yandex.ru/j/1234567890', URL + '?x=1', URL + '#x', 'https://evil.example/j/1234567890'])
def test_meeting_url_strict(url):
    with pytest.raises(ValueError):
        meeting_link(url)


def test_invalid_calendar_fails_without_partial_schedule():
    with pytest.raises(CalendarError):
        parsed('BEGIN:VEVENT\nSUMMARY:no id\nEND:VEVENT')
    with pytest.raises(CalendarError):
        parsed(event(extra='RRULE:FREQ=SECONDLY;COUNT=999999'))


def test_xml_incomplete_and_auth_fail_do_not_become_empty_calendar(monkeypatch):
    original = httpx.AsyncClient
    responses = [httpx.Response(401), httpx.Response(207, text='<multistatus xmlns="DAV:"><response><propstat><status>HTTP/1.1 500 Error</status></propstat></response></multistatus>')]
    mock_provider(monkeypatch, lambda r: responses.pop(0))
    provider = YandexCalendar('owner', 'secret')
    with pytest.raises(CalendarError, match='calendar_auth_failed'):
        asyncio.run(provider.discover())
    with pytest.raises(CalendarError, match='calendar_read_incomplete'):
        asyncio.run(provider.events('/calendar/', START, START + timedelta(days=14), 'Europe/Moscow'))


def test_discovery_and_report_only_use_read_operations(monkeypatch):
    original = httpx.AsyncClient
    xmls = [
        '<c:calendar-home-set><d:href>/home/</d:href></c:calendar-home-set>',
        '<d:resourcetype><c:calendar/></d:resourcetype><d:displayname>Test</d:displayname>']
    calls = []
    def respond(request):
        calls.append((request.method, request.url.host))
        return httpx.Response(207, text='<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:href>/calendar/</d:href><d:propstat><d:prop>' + xmls.pop(0) + '</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>')
    mock_provider(monkeypatch, respond)
    assert asyncio.run(YandexCalendar('owner', 'secret').discover()) == [{'href': 'https://caldav.yandex.ru/calendar/', 'title': 'Test'}]
    assert calls == [('PROPFIND', 'caldav.yandex.ru')] * 2


def test_apply_move_delete_and_skip_survive_refresh(subscription):
    oid = insert_events(subscription, parsed(event()))
    item = read(oid)
    with Session.begin() as s:
        sub = s.get(CalendarSubscription, subscription)
        service.apply_events(s, sub, parsed(event(start='20261012T100000Z')), START, START + timedelta(days=14))
    assert read(oid).starts_at.hour == 10 and read(oid).version == item.version + 1
    with Session.begin() as s:
        s.get(CalendarOccurrence, oid).skipped = True
    insert_events(subscription, parsed(event(start='20261012T100000Z')))
    assert read(oid).reason == 'user_skip'
    with Session.begin() as s:
        service.apply_events(s, s.get(CalendarSubscription, subscription), [], START, START + timedelta(days=14))
    assert read(oid).status == 'cancelled'


def test_prepare_fresh_and_reconcile_lost_response(subscription, monkeypatch):
    events = parsed(event(extra='DTEND:20261012T100000Z'))
    oid = insert_events(subscription, events)
    fake_source(monkeypatch, events)
    asyncio.run(service.prepare(subscription, oid, START - timedelta(seconds=50)))
    assert read(oid).start_request['command_key'] == oid
    accepted, posts = {}, []
    async def agent(method, path, body=None):
        if method == 'GET':
            return httpx.Response(200, json=accepted) if accepted else httpx.Response(404)
        posts.append(body)
        accepted.update(id=oid, status='recording')
        raise httpx.ReadError('lost response')
    monkeypatch.setattr(service, 'agent_request', agent)
    asyncio.run(service.dispatch(oid, START))
    assert read(oid).status == 'uncertain'
    asyncio.run(service.dispatch(oid, START))
    assert read(oid).status == 'started' and len(posts) == 1
    asyncio.run(service.dispatch(oid, START))
    assert len(posts) == 1


def test_uncertain_404_never_reissues_stale_start(subscription, monkeypatch):
    events = parsed(event())
    oid = insert_events(subscription, events)
    fake_source(monkeypatch, events)
    asyncio.run(service.prepare(subscription, oid, START))
    posts = []
    async def agent(method, path, body=None):
        if method == 'POST':
            posts.append(1)
            raise httpx.ReadError('unknown outcome')
        return httpx.Response(404)
    monkeypatch.setattr(service, 'agent_request', agent)
    asyncio.run(service.dispatch(oid, START))
    fake_source(monkeypatch, [])
    asyncio.run(service.dispatch(oid, START + timedelta(minutes=1)))
    assert len(posts) == 1 and read(oid).status == 'uncertain'


def test_network_failure_keeps_schedule_and_prevents_start(subscription, monkeypatch):
    oid = insert_events(subscription, parsed(event()))
    fake_source(monkeypatch, CalendarError('calendar_unavailable'))
    asyncio.run(service.prepare(subscription, oid, START))
    assert read(oid).status == 'scheduled' and read(oid).start_request is None
    with Session() as s:
        assert s.get(CalendarSubscription, subscription).error_code == 'calendar_unavailable'


def test_cancel_before_start_and_late_job(subscription, monkeypatch):
    oid = insert_events(subscription, parsed(event()))
    fake_source(monkeypatch, [])
    asyncio.run(service.prepare(subscription, oid, START))
    assert read(oid).status == 'cancelled' and read(oid).start_request is None
    insert_events(subscription, parsed(event()))
    asyncio.run(service.prepare(subscription, oid, START + timedelta(minutes=6)))
    assert read(oid).status == 'missed'


def test_busy_then_moved_does_not_start_old_link(subscription, monkeypatch):
    events = parsed(event())
    oid = insert_events(subscription, events)
    fake_source(monkeypatch, events)
    asyncio.run(service.prepare(subscription, oid, START))
    posts = []
    async def agent(method, path, body=None):
        if method == 'POST':
            posts.append(body)
            return httpx.Response(409)
        return httpx.Response(404)
    monkeypatch.setattr(service, 'agent_request', agent)
    asyncio.run(service.dispatch(oid, START))
    assert read(oid).reason == 'agent_busy'
    fake_source(monkeypatch, parsed(event(start='20261012T100000Z')))
    asyncio.run(service.dispatch(oid, START + timedelta(minutes=1)))
    assert len(posts) == 1 and read(oid).status == 'scheduled' and read(oid).starts_at.hour == 10


def test_concurrent_dispatch_accepts_one_session(subscription, monkeypatch):
    events = parsed(event())
    oid = insert_events(subscription, events)
    fake_source(monkeypatch, events)
    asyncio.run(service.prepare(subscription, oid, START))
    accepted, posts = {}, []
    async def agent(method, path, body=None):
        if method == 'GET':
            return httpx.Response(200, json=accepted) if accepted else httpx.Response(404)
        posts.append(body)
        accepted.update(id=oid, status='recording')
        return httpx.Response(202, json=accepted)
    monkeypatch.setattr(service, 'agent_request', agent)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: asyncio.run(service.dispatch(oid, START)), range(2)))
    assert len(posts) == 1 and read(oid).status == 'started'


def test_api_secret_redaction_consent_owner_pause_and_skip(client, subscription):
    assert client.post('/api/calendars/discover', json={'username': '', 'password': 'private-password'}).status_code == 422
    assert 'private-password' not in client.post('/api/calendars/discover', json={'username': '', 'password': 'private-password'}).text
    listing = client.get('/api/calendars')
    assert listing.status_code == 200 and 'credential' not in listing.text and 'private-password' not in listing.text
    assert client.patch('/api/calendars/' + subscription, json={'enabled': True}).status_code == 422
    oid = insert_events(subscription, parsed(event()))
    assert client.post('/api/calendars/occurrences/' + oid + '/skip', json={'version': 99}).status_code == 409
    assert client.post('/api/calendars/occurrences/' + oid + '/skip', json={'version': 1}).status_code == 200
    assert read(oid).skipped
    assert client.patch('/api/calendars/' + subscription, json={'enabled': False}).status_code == 200
    with Session.begin() as s:
        s.get(CalendarSubscription, subscription).owner = 'another_owner'
    assert client.get('/api/calendars').json()['subscriptions'] == []
    assert client.delete('/api/calendars/' + subscription).status_code == 404


def test_pause_between_intent_and_contact_prevents_post(client, subscription, monkeypatch):
    events = parsed(event())
    oid = insert_events(subscription, events)
    fake_source(monkeypatch, events)
    asyncio.run(service.prepare(subscription, oid, START))
    assert client.patch('/api/calendars/' + subscription, json={'enabled': False}).status_code == 200
    async def forbidden(*args):
        raise AssertionError('No agent contact after pause')
    monkeypatch.setattr(service, 'agent_request', forbidden)
    asyncio.run(service.dispatch(oid, START))
    assert read(oid).status == 'paused' and read(oid).start_request is None


def test_encrypted_password_and_disconnect(client, subscription):
    with Session() as s:
        encrypted = s.get(CalendarSubscription, subscription).credential
    assert 'private-password' not in encrypted
    assert service.cipher().decrypt(encrypted.encode()).decode() == 'private-password'
    assert client.delete('/api/calendars/' + subscription).status_code == 200
    with Session() as s:
        sub = s.get(CalendarSubscription, subscription)
        assert not sub.connected and not sub.enabled and sub.credential == ''


def test_connect_verifies_selected_calendar_and_stays_paused(client, key, monkeypatch):
    from app import calendar_api
    class Source:
        async def discover(self):
            return [{'href': 'https://caldav.yandex.ru/calendar/', 'title': 'Test'}]
    monkeypatch.setattr(calendar_api, 'YandexCalendar', lambda *args: Source())
    fake_source(monkeypatch, [])
    body = {'username': 'owner@yandex.ru', 'password': 'private-password', 'calendar_href': 'https://caldav.yandex.ru/calendar/'}
    response = client.post('/api/calendars', json=body)
    assert response.status_code == 201 and response.json()['enabled'] is False
    assert 'password' not in response.text and 'credential' not in response.text
    assert client.post('/api/calendars', json={**body, 'calendar_href': 'https://caldav.yandex.ru/other/'}).status_code == 422
    assert client.post('/api/calendars', json={**body, 'timezone': 'unknown/timezone'}).status_code == 422
    assert client.post('/api/calendars', json=body, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/calendars', json=body).json()['id'] == response.json()['id']


def test_scheduler_singleton_lock_and_heartbeat(monkeypatch):
    from app.db import engine, CalendarSchedulerState
    from sqlalchemy import text
    with engine.connect() as blocker:
        assert blocker.scalar(text('SELECT pg_try_advisory_lock(78124691)'))
        asyncio.run(service.tick())
        with Session() as s:
            assert s.get(CalendarSchedulerState, 1) is None
        blocker.execute(text('SELECT pg_advisory_unlock(78124691)'))
        blocker.commit()
    asyncio.run(service.tick())
    with Session() as s:
        assert s.get(CalendarSchedulerState, 1).last_tick >= now() - timedelta(seconds=5)


def test_unknown_get_does_not_freeze_a_start_that_was_never_sent(subscription, monkeypatch):
    events = parsed(event())
    oid = insert_events(subscription, events)
    fake_source(monkeypatch, events)
    asyncio.run(service.prepare(subscription, oid, START))
    async def unavailable(*args, **kwargs):
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(service, 'agent_request', unavailable)
    asyncio.run(service.dispatch(oid, START))
    assert read(oid).status == 'dispatching' and read(oid).reason == 'agent_unavailable'


def test_no_restart_after_accepted_agent_state_disappears(subscription, monkeypatch):
    events = parsed(event())
    oid = insert_events(subscription, events)
    fake_source(monkeypatch, events)
    asyncio.run(service.prepare(subscription, oid, START))
    with Session.begin() as s:
        s.get(CalendarOccurrence, oid).status = 'started'
    async def agent(method, path, body=None):
        assert method == 'GET'
        return httpx.Response(404)
    monkeypatch.setattr(service, 'agent_request', agent)
    asyncio.run(service.dispatch(oid, START))
    assert read(oid).status == 'uncertain'


def test_skip_serializes_with_sending_request(client, subscription, monkeypatch):
    import threading
    events = parsed(event())
    oid = insert_events(subscription, events)
    fake_source(monkeypatch, events)
    asyncio.run(service.prepare(subscription, oid, START))
    contacted, finish = threading.Event(), threading.Event()
    async def agent(method, path, body=None):
        if method == 'GET':
            return httpx.Response(404)
        contacted.set()
        assert await asyncio.to_thread(finish.wait, 3)
        return httpx.Response(202, json={'id': oid, 'status': 'starting'})
    monkeypatch.setattr(service, 'agent_request', agent)
    with ThreadPoolExecutor(max_workers=2) as pool:
        sent = pool.submit(lambda: asyncio.run(service.dispatch(oid, START)))
        assert contacted.wait(3)
        skipped = pool.submit(lambda: client.post('/api/calendars/occurrences/' + oid + '/skip', json={'version': 1}))
        finish.set()
        sent.result(timeout=5)
        assert skipped.result(timeout=5).status_code == 409
    assert read(oid).status == 'started'


def test_provider_identifies_itself_and_reports_retry_after(monkeypatch):
    original = httpx.AsyncClient
    def respond(request):
        assert request.headers['User-Agent'] == 'psptm-minutes-calendar/1.0'
        return httpx.Response(429, headers={'Retry-After': '600'}, text='limited')
    mock_provider(monkeypatch, respond)
    with pytest.raises(CalendarError, match='calendar_rate_limited') as error:
        asyncio.run(YandexCalendar('owner', 'private-password').discover())
    assert error.value.retry_after == 600 and 'private-password' not in str(error.value)


def test_rate_limit_delays_scheduler_and_manual_refresh(subscription, monkeypatch):
    oid = insert_events(subscription, parsed(event()))
    fake_source(monkeypatch, CalendarError('calendar_rate_limited', 600))
    asyncio.run(service.prepare(subscription, oid, START))
    with Session() as s:
        sub = s.get(CalendarSubscription, subscription)
        assert sub.next_sync == START + timedelta(seconds=600)
    def forbidden(sub):
        raise AssertionError('Rate limit backoff must prevent network requests')
    monkeypatch.setattr(service, 'source', forbidden)
    monkeypatch.setattr(service, 'now', lambda: START + timedelta(seconds=10))
    asyncio.run(service.prepare(subscription, oid, START + timedelta(seconds=10)))
    asyncio.run(service.refresh_subscription(subscription))
    assert read(oid).start_request is None


def test_rate_limit_api_is_distinct_from_wrong_password(client, monkeypatch):
    from app import calendar_api
    class Limited:
        async def discover(self):
            raise CalendarError('calendar_rate_limited', 600)
    monkeypatch.setattr(calendar_api, 'YandexCalendar', lambda *args: Limited())
    r = client.post('/api/calendars/discover', json={'username': 'test@yandex.ru', 'password': 'private-password'})
    assert r.status_code == 429 and r.headers['Retry-After'] == '600'
    assert 'не означает неверный пароль' in r.json()['detail']
    assert 'private-password' not in r.text


def test_retry_after_invalid_and_http_date_are_bounded():
    from app.calendar_source import retry_delay
    from email.utils import format_datetime
    assert retry_delay(None) == 300
    assert retry_delay('nonsense') == 300
    assert retry_delay('1') == 60
    assert retry_delay('99999999') == 86400
    assert 590 <= retry_delay(format_datetime(now() + timedelta(seconds=600))) <= 600


def test_discovery_cache_avoids_reauthentication_and_changes_with_password(client, key, monkeypatch):
    from app import calendar_api
    calendar_api._discoveries.clear()
    calls = []
    class Source:
        async def discover(self):
            calls.append(1)
            return [{'href':'https://caldav.yandex.ru/calendar/', 'title':'Test'}]
    monkeypatch.setattr(calendar_api, 'YandexCalendar', lambda *args: Source())
    fake_source(monkeypatch, [])
    body = {'username':'cached@yandex.ru', 'password':'private-password'}
    assert client.post('/api/calendars/discover', json=body).status_code == 200
    assert client.post('/api/calendars', json={**body,'calendar_href':'https://caldav.yandex.ru/calendar/'}).status_code == 201
    assert len(calls) == 1
    assert client.post('/api/calendars/discover', json={**body,'password':'changed-password'}).status_code == 200
    assert len(calls) == 2
    calendar_api._discoveries.clear()


def test_prepare_does_not_poll_provider_before_join_window(subscription, monkeypatch):
    oid = insert_events(subscription, parsed(event()))
    def forbidden(sub):
        raise AssertionError('No extra provider reads before the configured lead time')
    monkeypatch.setattr(service, 'source', forbidden)
    for seconds in range(300, 60, -5):
        asyncio.run(service.prepare(subscription, oid, START - timedelta(seconds=seconds)))
    assert read(oid).status == 'scheduled' and read(oid).start_request is None
    # At the boundary, fresh cancellation must still prevent entry.
    fake_source(monkeypatch, parsed(event(extra='STATUS:CANCELLED')))
    asyncio.run(service.prepare(subscription, oid, START - timedelta(seconds=60)))
    assert read(oid).status == 'blocked' and read(oid).start_request is None


def test_regular_calendar_sync_leaves_five_minutes_between_reads(subscription):
    insert_events(subscription, parsed(event()), at=START)
    with Session() as s:
        sub = s.get(CalendarSubscription, subscription)
        assert sub.next_sync == START + timedelta(minutes=5)
