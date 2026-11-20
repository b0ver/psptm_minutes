from app.db import Session, TelegramUser, TelegramResource, TelegramEvent
from app.recovery_notifications import queue_recovery_notice
from sqlalchemy import select

EVENT={'event_id':'a'*64,'session_id':'00000000-0000-0000-0000-000000000001','status':'partial_saved'}

def test_calendar_missing_owner_never_guesses():
    with Session.begin() as s:
        s.add(TelegramUser(user_id='101',chat_id='101'));s.flush()
        assert queue_recovery_notice(s, EVENT)=='pending_owner_binding'
        assert not list(s.scalars(select(TelegramEvent)))

def test_private_owner_notification_deduplicates():
    with Session.begin() as s:
        s.add(TelegramUser(user_id='101',chat_id='101'));s.flush()
        s.add(TelegramResource(kind='session',resource_id=EVENT['session_id'],user_id='101'));s.flush()
        assert queue_recovery_notice(s,EVENT)=='queued';s.flush()
        assert queue_recovery_notice(s,EVENT)=='queued';s.flush()
        events=list(s.scalars(select(TelegramEvent)))
        assert len(events)==1 and events[0].payload['chat_id']=='101'

def test_no_group_delivery():
    with Session.begin() as s:
        s.add(TelegramUser(user_id='101',chat_id='-500'));s.flush()
        s.add(TelegramResource(kind='session',resource_id=EVENT['session_id'],user_id='101'));s.flush()
        assert queue_recovery_notice(s,EVENT)=='pending_owner_binding'

def test_no_inactive_delivery():
    with Session.begin() as s:
        s.add(TelegramUser(user_id='101',chat_id='101',active=False));s.flush()
        s.add(TelegramResource(kind='session',resource_id=EVENT['session_id'],user_id='101'));s.flush()
        assert queue_recovery_notice(s,EVENT)=='pending_owner_binding'

def test_calendar_explicit_subscription_mapping(monkeypatch):
    import json
    from app.db import CalendarSubscription,CalendarOccurrence,now
    sub='00000000-0000-0000-0000-000000000022'
    monkeypatch.setenv('MINUTES_CALENDAR_RECOVERY_OWNERS_JSON',json.dumps({sub:'101'}))
    with Session.begin() as s:
        s.add(TelegramUser(user_id='101',chat_id='101'))
        s.add(CalendarSubscription(id=sub,username='synthetic@example.invalid',credential='none',calendar_href='/test',title='Synthetic'));s.flush()
        s.add(CalendarOccurrence(subscription_id=sub,event_key='test',uid='test',recurrence_id='',title='Synthetic',starts_at=now(),session_id=EVENT['session_id']));s.flush()
        assert queue_recovery_notice(s,EVENT)=='queued';s.flush()
        assert len(list(s.scalars(select(TelegramEvent))))==1

def test_unrelated_calendar_mapping_does_not_authorize(monkeypatch):
    import json
    monkeypatch.setenv('MINUTES_CALENDAR_RECOVERY_OWNERS_JSON',json.dumps({'00000000-0000-0000-0000-000000000099':'101'}))
    with Session.begin() as s:
        s.add(TelegramUser(user_id='101',chat_id='101'));s.flush()
        assert queue_recovery_notice(s,EVENT)=='pending_owner_binding'

def test_calendar_misses_cutoff_and_dedup(monkeypatch):
    import json
    from datetime import timedelta
    from app.db import CalendarSubscription,CalendarOccurrence,now
    from app.recovery_notifications import watch_calendar_misses
    sub='00000000-0000-0000-0000-000000000022'
    cutoff=now()-timedelta(minutes=1)
    monkeypatch.setenv('MINUTES_CALENDAR_RECOVERY_OWNERS_JSON',json.dumps({sub:'101'}))
    monkeypatch.setenv('MINUTES_RECOVERY_NOTICES_SINCE',cutoff.isoformat())
    with Session.begin() as s:
        s.add(TelegramUser(user_id='101',chat_id='101'))
        s.add(CalendarSubscription(id=sub,username='synthetic@example.invalid',credential='none',calendar_href='/test',title='Synthetic'));s.flush()
        s.add(CalendarOccurrence(subscription_id=sub,event_key='new',uid='new',recurrence_id='',title='Synthetic',starts_at=now(),status='missed',reason='agent_busy',updated_at=cutoff-timedelta(days=1)))
        s.add(CalendarOccurrence(subscription_id=sub,event_key='old',uid='old',recurrence_id='',title='Synthetic',starts_at=cutoff-timedelta(days=1),status='missed',updated_at=now()))
    watch_calendar_misses();watch_calendar_misses()
    with Session() as s:
        assert len(list(s.scalars(select(TelegramEvent))))==1

def test_notification_conflicting_owners_fail_closed(monkeypatch):
    import json
    from app.db import CalendarSubscription,CalendarOccurrence,now
    sub='00000000-0000-0000-0000-000000000023'
    monkeypatch.setenv('MINUTES_CALENDAR_RECOVERY_OWNERS_JSON',json.dumps({sub:'102'}))
    with Session.begin() as s:
        s.add(TelegramUser(user_id='101',chat_id='101'))
        s.add(TelegramUser(user_id='102',chat_id='102'))
        s.add(CalendarSubscription(id=sub,username='synthetic@example.invalid',credential='none',calendar_href='/test',title='Synthetic'));s.flush()
        s.add(CalendarOccurrence(subscription_id=sub,event_key='test',uid='test',recurrence_id='',title='Synthetic',starts_at=now(),session_id=EVENT['session_id']))
        s.add(TelegramResource(kind='session',resource_id=EVENT['session_id'],user_id='101'));s.flush()
        assert queue_recovery_notice(s,EVENT)=='pending_owner_binding'
        assert not list(s.scalars(select(TelegramEvent)))
