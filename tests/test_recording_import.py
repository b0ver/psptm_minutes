from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import app
from app.db import Session, Meeting, RecordingImport


def send(client, key, audio=b'synthetic audio', **kwargs):
    return client.post('/api/meetings', files={'recording': ('test.webm', audio)},
                       data={'title': 'Synthetic import', 'import_session': key, **kwargs})


def test_repeat_and_conflict(client):
    key = str(uuid4())
    first = send(client, key)
    repeat = send(client, key)
    assert first.status_code == 201 and repeat.status_code == 200
    assert first.json()['id'] == repeat.json()['id']
    assert send(client, key, b'different bytes').status_code == 409
    assert send(client, key, recording_complete='false').status_code == 409
    assert client.get('/api/recording-imports/'+key).json()['id'] == first.json()['id']
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(Meeting)) == 1
        assert s.scalar(select(func.count()).select_from(RecordingImport)) == 1


def test_concurrent_imports_create_one_meeting(client):
    key = str(uuid4())
    def submit(_):
        with TestClient(app) as c:
            return send(c, key)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(submit, range(4)))
    assert sorted(r.status_code for r in responses) == [200, 200, 200, 201]
    assert len({r.json()['id'] for r in responses}) == 1


def test_partial_label_and_validation(client):
    key = str(uuid4())
    response = send(client, key, recording_complete='false')
    assert response.status_code == 201
    assert client.get('/api/meetings/'+response.json()['id']).json()['recording_complete'] is False
    assert send(client, 'invalid').status_code == 422
    assert send(client, '', recording_complete='false').status_code == 422
    assert client.get('/api/recording-imports/'+str(uuid4())).status_code == 404
