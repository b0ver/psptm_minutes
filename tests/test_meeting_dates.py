from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest

from app.db import Session, Meeting, RecordingImport
from app.settings import DATA_DIR


def upload(client, **fields):
    return client.post('/api/meetings', files={'recording': ('test.webm', b'synthetic audio')},
                       data={'title': 'Synthetic meeting', **fields}).json()['id']


def state_file(key, mid, **updates):
    path = DATA_DIR / 'telemost' / key / 'state.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'id': key, 'meeting_id': mid, **updates}))
    return path


def cards(client, mid):
    detail = client.get('/api/meetings/' + mid).json()
    listed = next(m for m in client.get('/api/meetings').json() if m['id'] == mid)
    for field in ('created_at', 'meeting_date', 'recording_started_at'):
        assert detail[field] == listed[field]
    return detail


def test_upload_time_is_not_recording_time(client):
    mid = upload(client)
    data = cards(client, mid)
    assert datetime.fromisoformat(data['created_at']).tzinfo is not None
    assert data['recording_started_at'] is None
    assert data['meeting_date'] is None


def test_user_date_does_not_invent_time(client):
    mid = upload(client, meeting_date='2026-10-30')
    data = cards(client, mid)
    assert data['meeting_date'] == '2026-10-30'
    assert data['recording_started_at'] is None


def test_historical_import_uses_actual_start_and_refreshes_after_agent_write(client):
    key = str(uuid4())
    mid = upload(client, import_session=key)
    started = datetime(2026, 10, 29, 21, 30, tzinfo=timezone.utc)
    path = state_file(key, mid, recording_started_at=started.timestamp())
    assert datetime.fromisoformat(cards(client, mid)['recording_started_at']) == started
    # An import can finish before the agent writes the resulting meeting ID.
    state_file(key, None, recording_started_at=started.timestamp())
    assert cards(client, mid)['recording_started_at'] is None
    state_file(key, mid, recording_started_at=started.timestamp())
    assert datetime.fromisoformat(cards(client, mid)['recording_started_at']) == started
    path.unlink()
    assert cards(client, mid)['recording_started_at'] is None


@pytest.mark.parametrize('value', [None, True, '2026-10-29', -1, float('nan'), float('inf'), 10**100, 9999999999])
def test_invalid_recording_time_does_not_break_archive(client, value):
    key = str(uuid4())
    mid = upload(client, import_session=key)
    state_file(key, mid, recording_started_at=value)
    assert cards(client, mid)['recording_started_at'] is None


@pytest.mark.parametrize('broken', ['missing', 'corrupt', 'other_meeting', 'other_session', 'oversize'])
def test_unavailable_or_unrelated_state_does_not_supply_a_date(client, broken):
    key = str(uuid4())
    mid = upload(client, import_session=key)
    if broken != 'missing':
        path = state_file(key, mid, recording_started_at=1793280600)
        if broken == 'corrupt':
            path.write_text('{broken')
        elif broken == 'other_meeting':
            state_file(key, str(uuid4()), recording_started_at=1793280600)
        elif broken == 'other_session':
            state_file(key, mid, id=str(uuid4()), recording_started_at=1793280600)
        else:
            path.write_bytes(b' ' * (4 * 1024 * 1024 + 1))
    assert cards(client, mid)['recording_started_at'] is None


def test_import_key_cannot_read_a_file_outside_agent_directory(client):
    mid = upload(client)
    with Session.begin() as s:
        s.add(RecordingImport(session_id='../outside', meeting_id=mid,
                              recording_sha256='0' * 64, request_sha256='0' * 64))
    assert cards(client, mid)['recording_started_at'] is None
