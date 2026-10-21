from datetime import datetime, timezone, timedelta
from email.utils import format_datetime

import httpx
import pytest

from app import llm_retry, reports
from app.ai import ProcessingError


@pytest.fixture
def sleeps(monkeypatch):
    values = []
    monkeypatch.setattr(llm_retry.time, 'sleep', values.append)
    monkeypatch.setattr(llm_retry.random, 'uniform', lambda *args: 0)
    return values


def client(monkeypatch, statuses, *, headers=None, incomplete=False):
    posts = []
    monkeypatch.setattr(reports, 'validate_endpoint', lambda *args: None)
    monkeypatch.setattr(reports, 'verify_model', lambda *args: None)
    llm = reports.LocalLLM()
    llm.client.close()
    def respond(request):
        posts.append(request)
        status = statuses[min(len(posts) - 1, len(statuses) - 1)]
        if isinstance(status, Exception):
            raise status
        return httpx.Response(status, headers=headers or {}, json={
            'done': True, 'done_reason': 'length' if incomplete else 'stop',
            'message': {'content': '# Synthetic report'}})
    llm.client = httpx.Client(base_url='http://local', transport=httpx.MockTransport(respond))
    return llm, posts


@pytest.mark.parametrize('failure', [429, 502, 503, 504, httpx.ConnectError('synthetic'), httpx.ReadTimeout('synthetic')])
def test_transient_generation_retried_before_success(monkeypatch, sleeps, failure):
    llm, posts = client(monkeypatch, [failure, 200])
    try:
        assert llm.chat('system', 'input') == '# Synthetic report'
        assert len(posts) == 2 and sleeps == [1]
    finally:
        llm.close()


def test_generation_retries_are_bounded(monkeypatch, sleeps):
    llm, posts = client(monkeypatch, [503])
    try:
        with pytest.raises(httpx.HTTPStatusError):
            llm.chat('system', 'input')
        assert len(posts) == 3 and sleeps == [1, 2]
    finally:
        llm.close()


@pytest.mark.parametrize('status', [400, 401, 403, 404, 500])
def test_permanent_generation_error_not_retried(monkeypatch, sleeps, status):
    llm, posts = client(monkeypatch, [status])
    try:
        with pytest.raises(httpx.HTTPStatusError):
            llm.chat('system', 'input')
        assert len(posts) == 1 and not sleeps
    finally:
        llm.close()


def test_incomplete_answer_is_not_silently_retried_or_accepted(monkeypatch, sleeps):
    llm, posts = client(monkeypatch, [200], incomplete=True)
    try:
        with pytest.raises(ProcessingError, match='analysis_output_incomplete'):
            llm.chat('system', 'input')
        assert len(posts) == 1 and not sleeps
    finally:
        llm.close()


def test_context_guard_sends_no_request(monkeypatch, sleeps):
    llm, posts = client(monkeypatch, [200])
    try:
        with pytest.raises(ProcessingError, match='analysis_context_too_large'):
            llm.chat('system', 'x' * 45001)
        assert not posts and not sleeps
    finally:
        llm.close()


def test_retry_after_honored_within_budget(monkeypatch, sleeps):
    llm, posts = client(monkeypatch, [429, 200], headers={'Retry-After': '12'})
    try:
        llm.chat('system', 'input')
        assert len(posts) == 2 and sleeps == [12]
    finally:
        llm.close()


def test_long_retry_after_fails_instead_of_retrying_early(monkeypatch, sleeps):
    llm, posts = client(monkeypatch, [429], headers={'Retry-After': '120'})
    try:
        with pytest.raises(httpx.HTTPStatusError):
            llm.chat('system', 'input')
        assert len(posts) == 1 and not sleeps
    finally:
        llm.close()


def test_http_date_retry_after_and_malformed_header():
    assert 0 < llm_retry.retry_after_seconds(format_datetime(datetime.now(timezone.utc) + timedelta(seconds=20))) <= 20
    assert llm_retry.retry_after_seconds('invalid') is None
