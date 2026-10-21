"""Read-only Yandex CalDAV and bounded iCalendar expansion. No arbitrary hosts."""
import hashlib
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import urljoin, urlsplit, quote
from zoneinfo import ZoneInfo
from xml.etree.ElementTree import Element, SubElement, tostring

import requests
from defusedxml.ElementTree import fromstring
from icalendar import Calendar
import recurring_ical_events
from email.utils import parsedate_to_datetime
from contextlib import nullcontext

BASE = 'https://caldav.yandex.ru'
DAV = 'DAV:'
CAL = 'urn:ietf:params:xml:ns:caldav'
MAX_RESPONSE = 8 * 1024 * 1024
MAX_OCCURRENCES = 2000


class CalendarError(Exception):
    """Only fixed codes are allowed across the API/log boundary."""
    def __init__(self, code, retry_after=60):
        super().__init__(code)
        self.retry_after = retry_after


def retry_delay(value):
    try:
        delay = int(value)
    except (TypeError, ValueError):
        try:
            delay = int((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            delay = 300
    return max(60, min(86400, delay))


def safe_href(href):
    absolute = urljoin(BASE + '/', href)
    u = urlsplit(absolute)
    if u.scheme != 'https' or u.netloc != 'caldav.yandex.ru' or u.query or u.fragment or '\\' in href or any(ord(c) < 32 for c in href):
        raise CalendarError('calendar_address_invalid')
    return absolute


def meeting_link(value):
    u = urlsplit(value.strip())
    if (u.scheme != 'https' or u.netloc not in {'telemost.yandex.ru', 'telemost.yandex.com'}
            or not re.fullmatch(r'/j/[A-Za-z0-9_-]{8,128}', u.path) or u.query or u.fragment):
        raise ValueError('invalid_meeting_link')
    return u.geturl()


def extract_link(event):
    links = set()
    for field in ('X-TELEMOST-CONFERENCE', 'LOCATION', 'URL', 'DESCRIPTION'):
        values = event.get(field, [])
        if not isinstance(values, list):
            values = [values]
        for value in values:
            for match in re.findall(r'https?://[^\s<>"\']+', unescape(str(value))):
                try:
                    links.add(meeting_link(match.rstrip('.,;!)]}')))
                except ValueError:
                    pass
    if not links:
        return None, 'no_telemost_link'
    if len(links) > 1:
        return None, 'multiple_telemost_links'
    return links.pop(), None


def utc(value, tz):
    if not isinstance(value, datetime):
        value = datetime.combine(value, datetime.min.time())
    return (value if value.tzinfo else value.replace(tzinfo=tz)).astimezone(timezone.utc)


def expand(data, start, stop, fallback_timezone, username):
    try:
        calendar = Calendar.from_ical(data)
        if calendar.name != 'VCALENDAR':
            raise ValueError('not_a_calendar')
        tz = ZoneInfo(str(calendar.get('X-WR-TIMEZONE') or fallback_timezone))
        originals = calendar.walk('VEVENT')
        if len(originals) > MAX_OCCURRENCES or any(e.errors for e in originals):
            raise ValueError('invalid_or_large_calendar')
        series = {str(e.get('UID')) for e in originals if e.get('RRULE') or e.get('RDATE') or e.get('RECURRENCE-ID')}
        # Materialize floating times before expansion so range filtering uses the calendar zone.
        for event in originals:
            if not event.get('UID') or not event.get('DTSTART'):
                raise ValueError('missing_event_identity')
            for name in ('DTSTART', 'DTEND', 'RECURRENCE-ID'):
                prop = event.get(name)
                if prop and isinstance(prop.dt, datetime) and prop.dt.tzinfo is None:
                    if prop.params.get('TZID'):
                        raise ValueError('unknown_timezone')
                    prop.dt = prop.dt.replace(tzinfo=tz)
            for name in ('RDATE', 'EXDATE'):
                properties = event.get(name, [])
                if not isinstance(properties, list):
                    properties = [properties]
                for prop in properties:
                    for item in prop.dts:
                        if isinstance(item.dt, datetime) and item.dt.tzinfo is None:
                            item.dt = item.dt.replace(tzinfo=tz)
        results = []
        # after() is lazy, unlike between(); stop before dense recurrences exhaust memory.
        for event in recurring_ical_events.of(calendar).after(start):
            begins = utc(event.decoded('DTSTART'), tz)
            if begins > stop:
                break
            if begins < start:
                continue
            if len(results) >= MAX_OCCURRENCES:
                raise ValueError('too_many_occurrences')
            uid = str(event['UID'])
            rid = event.get('RECURRENCE-ID') if uid in series else None
            recurrence = utc(rid.dt, tz).isoformat() if rid else 'single'
            ends = utc(event.decoded('DTEND'), tz) if event.get('DTEND') else None
            if ends and ends <= begins:
                ends = None
            url, reason = extract_link(event)
            if not isinstance(event.decoded('DTSTART'), datetime):
                reason = 'all_day'
            if str(event.get('STATUS', '')).upper() == 'CANCELLED':
                reason = 'cancelled'
            attendees = event.get('ATTENDEE', [])
            if not isinstance(attendees, list):
                attendees = [attendees]
            if any(str(a).removeprefix('mailto:').casefold() == username.casefold()
                   and str(a.params.get('PARTSTAT', '')).upper() == 'DECLINED' for a in attendees):
                reason = 'declined'
            results.append(dict(event_key=hashlib.sha256(json.dumps([uid, recurrence]).encode()).hexdigest(),
                uid=uid, recurrence_id=recurrence, title=str(event.get('SUMMARY') or 'Встреча в Телемосте')[:200],
                url=url, starts_at=begins, ends_at=ends, reason=reason))
        return results
    except CalendarError:
        raise
    except Exception:
        raise CalendarError('calendar_parse_failed') from None


class YandexCalendar:
    def __init__(self, username, password):
        self.username, self.password = username, password
        self._client = None

    async def request(self, method, href, body=None, depth='0'):
        return await asyncio.to_thread(self.request_sync, method, href, body, depth)

    def request_sync(self, method, href, body=None, depth='0'):
        try:
            # CalDAV requests use the same established transport as Python CalDAV clients.
            # Run blocking I/O off the event loop; never use ambient proxies or redirects.
            with (nullcontext(self._client) if self._client is not None else requests.Session()) as client:
                client.trust_env = False
                client.headers.update({'User-Agent': 'psptm-minutes-calendar/1.0'})
                with client.request(method, safe_href(href), data=body, auth=(self.username, self.password),
                        timeout=20, stream=True, allow_redirects=False,
                        headers={'Depth': depth, 'Content-Type': 'application/xml; charset=utf-8'}) as response:
                    if response.status_code in {401, 403}:
                        raise CalendarError('calendar_auth_failed')
                    if response.status_code == 404:
                        raise CalendarError('calendar_not_found')
                    if response.status_code == 429:
                        raise CalendarError('calendar_rate_limited', retry_delay(response.headers.get('Retry-After')))
                    if response.status_code not in {200, 207}:
                        raise CalendarError('calendar_unavailable')
                    chunks, size = [], 0
                    for chunk in response.iter_content(chunk_size=65536):
                        size += len(chunk)
                        if size > MAX_RESPONSE:
                            raise CalendarError('calendar_response_too_large')
                        chunks.append(chunk)
            return fromstring(b''.join(chunks))
        except CalendarError:
            raise
        except Exception:
            raise CalendarError('calendar_unavailable') from None

    async def properties(self, href, names, depth='0'):
        root = Element('{DAV:}propfind')
        prop = SubElement(root, '{DAV:}prop')
        for name in names:
            SubElement(prop, name)
        return await self.request('PROPFIND', href, tostring(root), depth)

    @staticmethod
    def successful_props(response):
        for status in response.findall('{DAV:}propstat'):
            if ' 200 ' in (status.findtext('{DAV:}status') or ''):
                yield status.find('{DAV:}prop')

    async def discover(self):
        with requests.Session() as client:
            self._client = client
            try:
                return await self._discover()
            finally:
                self._client = None

    async def _discover(self):
        principal = '/principals/users/' + quote(self.username, safe='@') + '/'
        root = await self.properties(principal, ['{' + CAL + '}calendar-home-set'])
        home = next((p.findtext('{' + CAL + '}calendar-home-set/{DAV:}href')
                     for r in root.findall('{DAV:}response') for p in self.successful_props(r)
                     if p.findtext('{' + CAL + '}calendar-home-set/{DAV:}href')), None)
        if not home:
            raise CalendarError('calendar_discovery_failed')
        root = await self.properties(home, ['{DAV:}resourcetype', '{DAV:}displayname'], '1')
        found = []
        for response in root.findall('{DAV:}response'):
            for prop in self.successful_props(response):
                if prop.find('{DAV:}resourcetype/{' + CAL + '}calendar') is not None:
                    href = safe_href(response.findtext('{DAV:}href') or '')
                    found.append({'href': href, 'title': (prop.findtext('{DAV:}displayname') or 'Календарь')[:200]})
        if not found:
            raise CalendarError('calendar_discovery_failed')
        return found

    async def events(self, href, start, stop, tz):
        root = Element('{' + CAL + '}calendar-query')
        prop = SubElement(root, '{DAV:}prop')
        SubElement(prop, '{' + CAL + '}calendar-data')
        filt = SubElement(root, '{' + CAL + '}filter')
        comp = SubElement(filt, '{' + CAL + '}comp-filter', name='VCALENDAR')
        comp = SubElement(comp, '{' + CAL + '}comp-filter', name='VEVENT')
        SubElement(comp, '{' + CAL + '}time-range', start=start.strftime('%Y%m%dT%H%M%SZ'), end=stop.strftime('%Y%m%dT%H%M%SZ'))
        result = await self.request('REPORT', href, tostring(root), '1')
        combined = Calendar()
        seen = set()
        for response in result.findall('{DAV:}response'):
            props = list(self.successful_props(response))
            if not props:
                raise CalendarError('calendar_read_incomplete')
            data = next((p.findtext('{' + CAL + '}calendar-data') for p in props
                         if p.findtext('{' + CAL + '}calendar-data')), None)
            if not data:
                raise CalendarError('calendar_read_incomplete')
            try:
                source = Calendar.from_ical(data)
                if source.name != 'VCALENDAR':
                    raise ValueError('not_a_calendar')
                for child in source.subcomponents:
                    encoded = child.to_ical()
                    if encoded not in seen:
                        combined.add_component(child)
                        seen.add(encoded)
                if source.get('X-WR-TIMEZONE'):
                    combined['X-WR-TIMEZONE'] = source['X-WR-TIMEZONE']
            except Exception:
                raise CalendarError('calendar_parse_failed') from None
        if result.tag != '{DAV:}multistatus':
            raise CalendarError('calendar_read_incomplete')
        return expand(combined.to_ical(), start, stop, tz, self.username)
