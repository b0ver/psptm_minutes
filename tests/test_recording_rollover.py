import asyncio
import time

import pytest

from integrations.telemost import rollover


class Scope:
    def __init__(self, armed=True):
        self.armed = armed
        self.stopping = False

    async def evaluate(self, script):
        if '__agentBeginRollover' in script:
            return self.armed
        self.stopping = True


class Page:
    def __init__(self, armed=True):
        self.frames = [Scope(False), Scope(armed)]


class Leave:
    visible = True

    async def is_visible(self):
        return self.visible


@pytest.mark.parametrize('blocker', ['stop', 'deadline', 'removed'])
def test_rotation_does_not_restart_after_stop_deadline_or_removal(blocker):
    async def run():
        stop, leave, page = asyncio.Event(), Leave(), Page()
        if blocker == 'stop':
            stop.set()
        if blocker == 'removed':
            leave.visible = False

        async def forbidden(*args):
            raise AssertionError('No recording controls after stop')

        assert not await rollover.rotate(page, object(), leave, stop,
            time.time() + (-1 if blocker == 'deadline' else 60), forbidden, forbidden)
    asyncio.run(run())


def test_rotation_requires_a_running_continuous_copy():
    async def run():
        async def forbidden(*args):
            raise AssertionError('Do not interrupt the only usable native copy')

        with pytest.raises(RuntimeError, match='continuous_capture_unavailable'):
            await rollover.rotate(Page(False), object(), Leave(), asyncio.Event(),
                                  time.time() + 60, forbidden, forbidden)
    asyncio.run(run())


@pytest.mark.parametrize('blocker', ['stop', 'deadline', 'removed'])
def test_rotation_rechecks_permissions_before_starting_next_file(blocker, monkeypatch):
    async def run():
        stop, leave, page = asyncio.Event(), Leave(), Page()
        clock = [10]
        monkeypatch.setattr(rollover.time, 'time', lambda: clock[0])

        async def stop_native(*args):
            if blocker == 'stop':
                stop.set()
            elif blocker == 'deadline':
                clock[0] = 21
            else:
                leave.visible = False

        async def forbidden(*args):
            raise AssertionError('Do not start another recording')

        assert not await rollover.rotate(page, object(), leave, stop, 20, forbidden, stop_native)
        assert all(scope.stopping for scope in page.frames)
    asyncio.run(run())


def test_rotation_timeout_is_bounded(monkeypatch):
    async def run():
        monkeypatch.setattr(rollover, 'ROTATION_TIMEOUT_SECONDS', 0.01)
        cancelled = asyncio.Event()

        async def hanging(*args):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with pytest.raises(asyncio.TimeoutError):
            await rollover.rotate(Page(), object(), Leave(), asyncio.Event(),
                                  time.time() + 60, hanging, hanging)
        assert cancelled.is_set()
    asyncio.run(run())


def test_rotation_does_not_report_an_unstarted_next_file():
    async def run():
        async def stop_native(*args):
            pass
        async def start_native(*args):
            return False
        assert not await rollover.rotate(Page(), object(), Leave(), asyncio.Event(),
                                         time.time()+60, start_native, stop_native)
    asyncio.run(run())


@pytest.mark.parametrize('command', ['/stop', 'останови запись', 'стоп запись'])
def test_private_chat_stop_controls_only_the_owners_active_session(command):
    from uuid import uuid4
    from sqlalchemy import select
    from app.db import Session, TelegramUser, TelegramResource, TelegramCard, TelegramCommand
    from app.telegram_ui import handle
    own, foreign = str(uuid4()), str(uuid4())
    with Session.begin() as s:
        for user, sid in (('101', own), ('202', foreign)):
            s.add(TelegramUser(user_id=user, chat_id=user));s.flush()
            s.add(TelegramResource(kind='session', resource_id=sid, user_id=user))
            s.add(TelegramCard(session_id=sid, user_id=user, snapshot={'id':sid,'status':'recording'}))
    update={'update_id':31,'message':{'from':{'id':101},'chat':{'id':101,'type':'private'},'text':command}}
    with Session.begin() as s:
        handle(s,update,True,set());s.flush()
        handle(s,update,True,set())
    with Session() as s:
        commands=list(s.scalars(select(TelegramCommand)))
        assert len(commands)==1
        assert commands[0].kind=='stop' and commands[0].body=={'session_id':own}
        assert commands[0].user_id=='101'


def test_unknown_and_group_stop_cannot_stop_someone_elses_recording():
    from uuid import uuid4
    from sqlalchemy import select
    from app.db import Session, TelegramUser, TelegramResource, TelegramCard, TelegramCommand
    from app.telegram_ui import handle
    sid=str(uuid4())
    with Session.begin() as s:
        s.add(TelegramUser(user_id='202',chat_id='202'));s.flush()
        s.add(TelegramResource(kind='session',resource_id=sid,user_id='202'))
        s.add(TelegramCard(session_id=sid,user_id='202',snapshot={'id':sid,'status':'recording'}))
    with Session.begin() as s:
        for index,kind in enumerate(('private','group')):
            handle(s,{'update_id':index,'message':{'from':{'id':101},'chat':{'id':101,'type':kind},'text':'/stop'}},True,set())
    with Session() as s:
        assert list(s.scalars(select(TelegramCommand)))==[]
