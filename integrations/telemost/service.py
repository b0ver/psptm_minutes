"""Visible guest recorder. Native Telemost recording only; no cloud AI."""
import asyncio
import hashlib
import json
import os
import re
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4, UUID

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

try:
    from .recording_controls import start_native_recording, stop_native_recording
    from .devices import mute_devices
    from .checkpoints import Checkpoints, CheckpointError, atomic
    from .states import Status, TERMINAL, CONTROLS
    from .rollover import ROTATE_AFTER_SECONDS, ROTATION_TIMEOUT_SECONDS, rotate, prepare_stop
except ImportError:
    from recording_controls import start_native_recording, stop_native_recording
    from devices import mute_devices
    from checkpoints import Checkpoints, CheckpointError, atomic
    from states import Status, TERMINAL, CONTROLS
    from rollover import ROTATE_AFTER_SECONDS, ROTATION_TIMEOUT_SECONDS, rotate, prepare_stop

DATA = Path(os.environ.get('AGENT_DATA_DIR', '/data/telemost'))
API = os.environ.get('MINUTES_API_URL', 'http://api:8080')
sessions, stops, tasks = {}, {}, {}


def valid_link(value):
    u = urlsplit(value.strip())
    if u.scheme != 'https' or u.netloc not in {'telemost.yandex.ru', 'telemost.yandex.com'} or not re.fullmatch(r'/j/[A-Za-z0-9_-]{8,128}', u.path) or u.query or u.fragment:
        raise ValueError('Нужна прямая HTTPS-ссылка https://telemost.yandex.ru/j/…')
    return u.geturl()


class Start(BaseModel):
    command_key: str | None = Field(default=None, max_length=36)
    url: str = Field(max_length=400)

    @field_validator('command_key')
    @classmethod
    def command(cls, value):
        return str(UUID(value)) if value is not None else None
    title: str = Field(default='Тест Телемоста', min_length=1, max_length=200)
    title_from_telemost: bool = True
    duration_minutes: int = Field(default=15, ge=1, le=120)
    admission_deadline: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    recording_deadline: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    recording_authorized: bool = False
    analysis_types: list[str] = Field(default_factory=lambda: ['tldr', 'meeting_notes', 'action_items', 'key_meanings', 'chapters'])

    @field_validator('url')
    @classmethod
    def link(cls, value):
        return valid_link(value)


class AgentError(Exception):
    pass


def persist(sid, **updates):
    if 'status' in updates:
        updates['status'] = Status(updates['status']).value
    sessions[sid].update(updates, updated_at=time.time())
    folder = DATA / sid
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic(folder / 'state.json', json.dumps(sessions[sid], ensure_ascii=False).encode())
    event = {k: sessions[sid].get(k) for k in ('status', 'error_code', 'end_reason')}
    log = folder / 'events.jsonl'
    if event != sessions[sid].get('_last_event'):
        sessions[sid]['_last_event'] = event
        with log.open('a') as out:
            out.write(json.dumps({'at': time.time(), **event}) + '\n')
            out.flush()
            os.fsync(out.fileno())



def public(sid):
    s = sessions[sid]
    return {k: s.get(k) for k in ['id', 'title','title_source','camera_off', 'status', 'error_code', 'created_at', 'updated_at', 'recording_started_at', 'duration_minutes', 'meeting_id', 'bytes', 'has_preview', 'end_reason', 'recording_complete', 'chunks', 'checkpoint_bytes', 'last_checkpoint_at', 'capture_health', 'recovered_audio_seconds', 'retryable', 'native_rotations', 'capture_discontinuities', 'recording_issue', 'chat_control_available']}


@asynccontextmanager
async def lifespan(app):
    DATA.mkdir(parents=True, exist_ok=True)
    seed = Path('/agent/nssdb')
    if seed.is_dir():
        shutil.copytree(seed, Path.home() / '.pki/nssdb', dirs_exist_ok=True)
    for path in DATA.glob('*/state.json'):
        try:
            state = json.loads(path.read_text())
            sid = str(__import__('uuid').UUID(state['id']))
            sessions[sid] = state
            if state['status'] not in TERMINAL:
                recover_partial(sid, 'agent_interrupted')
        except (ValueError, KeyError, OSError):
            continue
    yield
    # Give the browser a chance to finish its native download on orderly shutdown.
    for stop in stops.values():
        stop.set()
    if tasks:
        await asyncio.wait(list(tasks.values()), timeout=45)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get('/healthz')
def health():
    return {'status': 'ok'}


@app.get('/sessions')
def listing():
    return [public(sid) for sid in sorted(sessions, key=lambda x: sessions[x]['created_at'], reverse=True)][:30]


@app.get('/sessions/{sid}')
def session_status(sid: str):
    if sid not in sessions:
        raise HTTPException(404, 'Сессия не найдена')
    return public(sid)


@app.post('/sessions', status_code=202)
async def start(body: Start):
    if not body.recording_authorized:
        raise HTTPException(422, 'Подтвердите разрешение организатора и участников на запись')
    if body.command_key and body.command_key in sessions:
        prior = sessions[body.command_key].get('start_request',sessions[body.command_key])
        if any(prior.get(k) != v for k, v in body.model_dump().items()):
            raise HTTPException(409, 'Ключ запуска использован с другими параметрами')
        return public(body.command_key)
    if body.admission_deadline is not None and body.admission_deadline <= time.time():
        raise HTTPException(422, 'Время подключения истекло')
    if any(s['status'] not in TERMINAL for s in sessions.values()):
        raise HTTPException(409, 'Агент уже занят встречей. Сначала завершите её.')
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        try:
            available = (await client.get(API + '/api/analysis-types')).json()
        except Exception:
            raise HTTPException(503, 'Сервис протоколов недоступен') from None
    valid = {item['id'] for item in available}
    if not body.analysis_types or any(item not in valid for item in body.analysis_types):
        raise HTTPException(422, 'Выберите известные типы разборов')
    # Reconcile a concurrently accepted request before rechecking occupancy.
    if body.command_key and body.command_key in sessions:
        prior = sessions[body.command_key].get('start_request',sessions[body.command_key])
        if any(prior.get(k) != v for k, v in body.model_dump().items()):
            raise HTTPException(409, 'Ключ запуска использован с другими параметрами')
        return public(body.command_key)
    if any(s['status'] not in TERMINAL for s in sessions.values()):
        raise HTTPException(409, 'Агент уже занят встречей')
    if body.admission_deadline is not None and body.admission_deadline <= time.time():
        raise HTTPException(422, 'Время подключения истекло')
    sid = body.command_key or str(uuid4())
    sessions[sid] = {'id': sid, **body.model_dump(), 'start_request':body.model_dump(), 'created_at': time.time()}
    persist(sid, status='starting', error_code=None, bytes=0, has_preview=False, recording_complete=True, capture_health='not_started')
    stops[sid] = asyncio.Event()
    tasks[sid] = asyncio.create_task(run(sid))
    return public(sid)


@app.post('/sessions/{sid}/stop', status_code=202)
async def stop(sid: str):
    if sid not in sessions:
        raise HTTPException(404, 'Сессия не найдена')
    if sid in stops and sessions[sid]['status'] not in TERMINAL and sessions[sid]['status'] != 'uploading':
        stops[sid].set()
        if sessions[sid]['status'] in {'starting', 'opening', 'preparing', 'waiting_host'}:
            persist(sid, status='cancelled', error_code='cancelled_before_recording')
            tasks[sid].cancel()
        else:
            persist(sid, status='stopping')
    return public(sid)


@app.post('/sessions/{sid}/import', status_code=202)
async def retry_import(sid: str):
    if sid not in sessions:
        raise HTTPException(404, 'Сессия не найдена')
    if sid in tasks:
        raise HTTPException(409, 'Импорт уже выполняется')
    if sessions[sid]['status'] not in {'partial_saved', 'upload_uncertain'}:
        raise HTTPException(409, 'Нет сохранённой записи для импорта')
    file = DATA / sid / sessions[sid].get('recording_file', 'recording.webm')
    if not file.is_file():
        raise HTTPException(409, 'Файл записи недоступен')
    async def work():
        try:
            await import_recording(sid, file, sessions[sid].get('recording_complete', True))
        finally:
            tasks.pop(sid, None)
    persist(sid, status='uploading', error_code=None)
    tasks[sid] = asyncio.create_task(work())
    return public(sid)


def recover_partial(sid, reason):
    folder = DATA / sid
    if sessions[sid].get('status') in {'saved', 'uploading'} and (folder / sessions[sid].get('recording_file', 'recording.webm')).is_file():
        persist(sid, status='upload_uncertain', error_code='upload_result_unknown', retryable=True)
        return
    try:
        result = Checkpoints(folder).assemble(folder / 'recovered.webm')
        persist(sid, status='partial_saved', error_code=reason, end_reason=reason,
                recording_complete=False, recording_file='recovered.webm', bytes=(folder / 'recovered.webm').stat().st_size,
                capture_health='recovered', retryable=False, **result)
    except Exception:
        persist(sid, status='failed', error_code=reason, retryable=reason in {'browser_crash', 'agent_interrupted', 'waiting_room_timeout'} or reason.startswith('ui_timeout_'))


async def cleanup_and_import(sid, file, complete, browser):
    # Closing our isolated browser disconnects only this guest. Do not click
    # departure controls: Telemost may replace them as the meeting ends.
    try:
        await browser.close()
    except Exception:
        persist(sid, cleanup_error_code='browser_close_after_save_failed')
    await import_recording(sid, file, complete=complete)


async def import_recording(sid, file, complete=True):
    state = sessions[sid]
    persist(sid, status='uploading', recording_complete=complete, recording_file=file.name)
    context=state.get('meeting_context') or {}
    fields=[state['title'].strip(), list(dict.fromkeys(state['analysis_types'])), '', '', complete]
    if context:fields.append(json.dumps(context,sort_keys=True,ensure_ascii=False))
    request_digest = hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()
    digest = hashlib.sha256()
    with file.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10), trust_env=False, follow_redirects=False) as client:
        for attempt in range(3):
            try:
                receipt = await client.get(API + '/api/recording-imports/' + sid)
                if receipt.status_code == 200:
                    saved = receipt.json()
                    if saved['recording_sha256'] != digest.hexdigest() or saved['recording_complete'] != complete or saved['request_sha256'] != request_digest:
                        persist(sid, status='failed', error_code='import_key_conflict', retryable=False)
                        return
                    persist(sid, status='uploaded', meeting_id=saved['id'], error_code=None)
                    return
                if receipt.status_code != 404:
                    receipt.raise_for_status()
                with file.open('rb') as audio:
                    response = await client.post(API + '/api/meetings', data={
                        'title': state['title'], 'analysis_types': json.dumps(state['analysis_types']),
                        'import_session': sid, 'recording_complete': str(complete).lower(),
                        'telemost_context':json.dumps(context,ensure_ascii=False) if context else ''},
                        files={'recording': ('telemost.webm', audio, 'audio/webm')})
                response.raise_for_status()
                persist(sid, status='uploaded', meeting_id=response.json()['id'], error_code=None)
                return
            except httpx.HTTPStatusError as error:
                if error.response.status_code < 500 and error.response.status_code != 429:
                    persist(sid, status='failed', error_code='recording_import_rejected', retryable=False)
                    return
            except (httpx.RequestError, ValueError, KeyError):
                pass
            if attempt < 2:
                await asyncio.sleep(attempt + 1)
    persist(sid, status='upload_uncertain', error_code='upload_result_unknown', retryable=True)


@app.get('/sessions/{sid}/preview')
def preview(sid: str):
    if sid not in sessions or not (DATA / sid / 'preview.png').is_file():
        raise HTTPException(404, 'Снимок пока недоступен')
    return FileResponse(DATA / sid / 'preview.png', headers={'Cache-Control': 'no-store'})


async def capture_preview(page, sid):
    try:
        await page.screenshot(path=str(DATA / sid / 'preview.png'))
        persist(sid, has_preview=True)
        controls = []
        for child in page.frames:
            controls.append({'private_join': '/private-join/' in child.url, 'controls': await child.locator('button, input, [role=button]').evaluate_all("items => items.map(x => ({tag:x.tagName, role:x.getAttribute('role'), aria:x.getAttribute('aria-label'), type:x.getAttribute('type'), text:(x.innerText||'').slice(0,120)}))")})
        (DATA / sid / 'controls.json').write_text(json.dumps(controls, ensure_ascii=False))
        persist(sid, has_preview=True)
    except Exception:
        pass


async def run(sid):
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    state, stop = sessions[sid], stops[sid]
    browser, page, driver, promo_task = None, None, None, None
    phase = 'browser'
    try:
        driver = await async_playwright().start()
        p = driver
        browser = await p.chromium.launch(headless=True, env={**os.environ, 'XDG_CONFIG_HOME': '/tmp/telemost-config', 'XDG_CACHE_HOME': '/tmp/telemost-cache'}, executable_path=os.environ.get('CHROMIUM_EXECUTABLE_PATH'), args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--use-file-for-fake-audio-capture=/agent/silence.wav', '--autoplay-policy=no-user-gesture-required', '--disable-dev-shm-usage'])
        context = await browser.new_context(locale='ru-RU', accept_downloads=True, viewport={'width': 1280, 'height': 900})
        await context.add_init_script("""(() => {
          window.__agentStreams = [];
          if (!navigator.mediaDevices) return;
          const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
          navigator.mediaDevices.getUserMedia = async c => {
            const stream = await original(c); stream.getTracks().forEach(t => {t.enabled=false}); window.__agentStreams.push(stream); return stream;
          };
        })();""")
        checkpoints = Checkpoints(DATA / sid)
        capture_frame = None
        async def checkpoint_binding(source, packet):
            nonlocal capture_frame
            summary = await asyncio.to_thread(checkpoints.accept, packet)
            capture_frame = source['frame']
            persist(sid, capture_health='healthy', **summary)
            return {'accepted': True}
        await context.expose_binding('agentCheckpoint', checkpoint_binding)
        async def lifecycle_binding(source, reason):
            if isinstance(reason, str) and reason in {'meeting_ended', 'removed', 'chat_stop'} and not sessions[sid].get('observed_end_reason'):
                persist(sid, observed_end_reason=reason)
        await context.expose_binding('agentLifecycle', lifecycle_binding)
        await context.add_init_script(path=str(Path(__file__).with_name('checkpoint.js')))
        await context.add_init_script(path=str(Path(__file__).with_name('meeting_observer.js')))
        await context.add_init_script(path=str(Path(__file__).with_name('lifecycle.js')))
        # Only synthetic devices exist in the container; host devices are not mounted.
        await context.grant_permissions(['microphone', 'camera'])
        page = await context.new_page()
        network_errors, browser_errors = [], []
        def failed_request(request):
            host = urlsplit(request.url).hostname
            code = re.search(r'net::[A-Z_]+', request.failure or '')
            if len(network_errors) < 30:
                network_errors.append({'host': host, 'code': code.group() if code else 'request_failed'})
        page.on('requestfailed', failed_request)
        page.on('pageerror', lambda error: browser_errors.append(type(error).__name__))
        console_codes = set()
        def classify_console_error(message):
            if message.type != 'error':
                return
            # Never persist console text: it can contain meeting content.
            known = {'QuotaExceededError': 'quota_exceeded', 'FILE_ERROR_NO_SPACE': 'no_space',
                     'Failed to write blobs': 'blob_write_failed',
                     'ERR_INSUFFICIENT_RESOURCES': 'insufficient_resources',
                     'OutOfMemoryError': 'out_of_memory'}
            found = {code for token, code in known.items() if token in message.text}
            if found - console_codes:
                console_codes.update(found)
                persist(sid, native_console_codes=sorted(console_codes))
        page.on('console', classify_console_error)

        async def dismiss_promos():
            next_preview = 0
            while True:
                try:
                    for scope in page.frames:
                        for label in ('Закрыть ознакомление', 'Закрыть окно'):
                            close = scope.locator('button[aria-label="' + label + '"]')
                            if await close.is_visible() and await close.is_enabled():
                                await close.evaluate('(button) => button.click()')
                except Exception:
                    pass
                if time.monotonic() > next_preview and sessions[sid]['status'] in {'preparing', 'waiting_host'}:
                    await capture_preview(page, sid)
                    persist(sid, network_errors=network_errors, browser_errors=browser_errors[:30])
                    next_preview = time.monotonic() + 10
                await asyncio.sleep(0.5)
        promo_task = asyncio.create_task(dismiss_promos())

        # Keep native download alive and save it immediately; path never comes from remote filename.
        new_download = asyncio.Event()
        download_errors = []
        async def save(download, index):
            try:
                name = 'recording.webm' if index == 0 else f'native-{index:03d}.webm'
                await download.save_as(str(DATA / sid / name))
            except Exception:
                download_errors.append('recording_download_failed')
        download_tasks = []
        def on_download(download):
            download_tasks.append(asyncio.create_task(save(download, len(download_tasks))))
            new_download.set()
        page.on('download', on_download)
        phase = 'navigation'
        persist(sid, status='opening')
        await page.goto(state['url'], wait_until='domcontentloaded', timeout=60000)
        phase = 'guest'
        persist(sid, status='preparing')
        phase = 'guest'
        continue_button = page.locator('button').filter(has_text=re.compile(r'^Продолжить в браузере$'))
        frame = None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if await continue_button.is_visible():
                await continue_button.evaluate('(button) => button.click()')
                await capture_preview(page, sid)
            frame = next((child for child in page.frames if '/private-join/' in child.url), None)
            if frame and await frame.locator('input[type=text]').is_visible():
                break
            await asyncio.sleep(0.5)
        else:
            raise AgentError('telemost_guest_frame_timeout')
        phase = 'guest_name'
        await frame.locator('input[type=text]').fill('Ассистент протокола · запись', timeout=15000)
        phase = 'guest_devices'
        await mute_devices([page,frame],require_camera=True)
        persist(sid,camera_off=True)
        if stop.is_set():
            raise AgentError('cancelled_before_join')
        phase = 'guest_join'
        await frame.locator('button').filter(has_text=re.compile(r'^Подключиться$')).evaluate('(button) => button.click()')
        persist(sid, status='waiting_host')
        await capture_preview(page, sid)
        leave = frame.get_by_role('button', name=CONTROLS['leave'], exact=True)
        phase = 'admission'
        admission_ms = 180000 if state.get('admission_deadline') is None else max(1, int((state['admission_deadline'] - time.time()) * 1000))
        await leave.wait_for(timeout=admission_ms)
        try:
            await mute_devices([frame],require_camera=False)
        except Exception:
            pass  # Synthetic tracks are already disabled by the init script.
        persist(sid, status='joined')
        # Cloud summarization onboarding may cover the call controls briefly.
        close = frame.locator('button[aria-label="Закрыть окно"]')
        try:
            if await close.is_visible():
                await close.evaluate('(button) => button.click()')
        except Exception:
            pass
        # Meeting name is exposed by Telemost's call-information popup.
        share=frame.locator('[data-testid="share-button"]')
        try:
            if await share.count() and await share.first.is_visible():
                await share.first.evaluate('(button)=>button.click()')
                await frame.locator('[data-testid="formatted-call-info"]').wait_for(timeout=3000)
                await frame.evaluate('()=>window.__agentSampleMeeting?.()')
                await page.keyboard.press('Escape')
        except Exception:
            pass
        try:
            observed=await frame.evaluate('()=>window.__agentMeetingObserver || {}')
        except Exception:
            observed={}
        if state.get('title_from_telemost',True):
            room_title=observed.get('title')
            named=bool(room_title and room_title.strip() not in {'Яндекс Телемост','Звонок в Яндекс Телемосте'})
            if not named:
                room_title='Телемост · '+state['url'].rsplit('/',1)[-1]
            persist(sid,title=room_title[:200],title_source='telemost' if named else 'meeting_link')
        phase = 'record_start'
        if stop.is_set():
            persist(sid, status='cancelled', error_code='cancelled_before_recording')
            return
        # Keep the room open even when recorder controls fail after admission.
        try:
            chat = frame.get_by_role('button', name='Чат', exact=True).or_(
                frame.get_by_role('button', name='Чат встречи', exact=True)).first
            if await chat.is_visible():
                await chat.evaluate('(button) => button.click()')
        except Exception:
            pass
        for scope in page.frames:
            try:
                await scope.evaluate('() => window.__agentArmCommands?.()')
            except Exception:
                pass
        recording_issue = None
        try:
            if await start_native_recording(page, frame, should_stop=lambda: stop.is_set() or bool(state.get('observed_end_reason'))) is False:
                persist(sid, status='cancelled', error_code='cancelled_before_recording')
                return
        except Exception:
            recording_issue = 'recording_start_failed'
        started = time.time()
        origins=[]
        for scope in page.frames:
            try:
                origins.append(await scope.evaluate('()=>window.__agentRecordingStartedAt || null'))
            except Exception:
                pass
        native_start=next((value for value in origins if value),None)
        if native_start:
            for scope in page.frames:
                try:
                    await scope.evaluate('(at)=>{window.__agentRecordingStartedAt=at}',native_start)
                except Exception:
                    pass
        persist(sid, status='recording', recording_started_at=started)
        native_started, rotations, discontinuities = started, 0, 0
        rotation_attempted = False
        persist(sid, native_rotations=0, capture_discontinuities=0,
                recording_issue=recording_issue, recording_complete=not bool(recording_issue))
        await capture_preview(page, sid)
        phase = 'recording'
        end_reason = 'user_stop'
        rotation_failed = False
        while True:
            if state.get('observed_end_reason') in {'meeting_ended', 'removed', 'chat_stop'}:
                end_reason = state['observed_end_reason']
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=3)
                break
            except asyncio.TimeoutError:
                pass
            if not browser.is_connected() or page.is_closed():
                raise AgentError('browser_crash')  # The browser has actually gone away.
            try:
                lifecycle = [await scope.evaluate('() => {window.__agentArmCommands?.();return window.__agentSampleLifecycle?.() || {}}')
                             for scope in page.frames]
                reason = next((item.get('reason') for item in lifecycle
                               if item.get('reason') in {'meeting_ended', 'removed', 'chat_stop'}), None)
                if reason:
                    end_reason = reason
                    break
                persist(sid, chat_control_available=any(item.get('chat_available') for item in lifecycle))
                # A missing toolbar can be a reconnect or a new frame. It is not
                # a reason to disconnect our guest or infer the end of a call.
                if not await leave.is_visible():
                    current = next((child for child in page.frames if '/private-join/' in child.url), None)
                    if current:
                        frame = current
                        leave = frame.get_by_role('button', name=CONTROLS['leave'], exact=True)
                    persist(sid, capture_health='ui_unavailable')
                    continue
                await mute_devices([frame], require_camera=False)
                try:
                    observed = await frame.evaluate('()=>{window.__agentSampleMeeting?.();return window.__agentMeetingObserver || {}}')
                    paused = any([await scope.evaluate('()=>!!window.__agentRecordingHadPause') for scope in page.frames])
                    observed['timing_reliable'] = bool(native_start and not paused)
                    if not observed['timing_reliable']: observed['turns'] = []
                    if observed.get('source') == 'telemost_ui': persist(sid, meeting_context=observed)
                except Exception:
                    pass
                checkpoint = await (capture_frame or frame).evaluate('() => window.__agentCheckpoint || {state: "unsupported"}')
                if checkpoint['state'] == 'final':
                    # Do not restart a participant's native stop, and do not
                    # leave the room. Wait for the owner's explicit exit rule.
                    recording_issue = recording_issue or 'native_recording_stopped'
                    diagnostic = await (capture_frame or frame).evaluate('() => window.__agentContinuousCapture?.nativeStopDiagnostic || {}')
                    safe_diagnostic = {
                        'trigger': diagnostic.get('trigger') if diagnostic.get('trigger') in {'native_event', 'agent_rotation', 'agent_stop', 'page_stop'} else 'unknown',
                        'error': diagnostic.get('error') if diagnostic.get('error') in {'none', 'other', 'UnknownError', 'InvalidModificationError', 'SecurityError', 'NotSupportedError', 'OutOfMemoryError'} else 'unknown',
                        'pagehide': diagnostic.get('pagehide') is True,
                    }
                    storage = [await scope.evaluate('() => ({errors: window.__agentContinuousCapture?.storageErrors || [], blob: window.__agentContinuousCapture?.blobWriteFailed === true})') for scope in page.frames]
                    safe_diagnostic['storage_errors'] = sorted({code for item in storage for code in item.get('errors', []) if code in {'QuotaExceededError', 'UnknownError', 'DataError', 'AbortError', 'ConstraintError', 'InvalidStateError', 'TransactionInactiveError', 'other'}})
                    safe_diagnostic['blob_write_failed'] = any(item.get('blob') is True for item in storage)
                    persist(sid, capture_health='native_stopped', recording_issue=recording_issue,
                            recording_complete=False, native_stop_diagnostic=safe_diagnostic)
                    continue
                last_ack = checkpoint.get('lastAck', started * 1000) / 1000
                health = 'unavailable' if checkpoint['state'] in {'unsupported', 'idle'} else ('failed' if checkpoint['state'] == 'failed' else ('delayed' if time.time() - last_ack > 45 else 'healthy'))
                persist(sid, capture_health='rotation_failed' if rotation_failed else health)
                captures = [await scope.evaluate('() => window.__agentContinuousCapture || {}')
                            for scope in page.frames]
                rotations = max(rotations, sum(c.get('rotations', 0) for c in captures))
                if rotations != sessions[sid].get('native_rotations'):
                    persist(sid, native_rotations=rotations)
                discontinuities = max(discontinuities, sum(c.get('discontinuities', 0) for c in captures))
                if discontinuities != sessions[sid].get('capture_discontinuities'):
                    persist(sid, capture_discontinuities=discontinuities, recording_complete=False)
                if not rotation_failed and time.time() - native_started >= ROTATE_AFTER_SECONDS:
                    rotation_attempted = True
                    persist(sid, capture_health='rotating')
                    try:
                        async def start_next_recording(page, frame):
                            return await start_native_recording(page, frame, should_stop=lambda: stop.is_set() or bool(state.get('observed_end_reason')))
                        continued = await rotate(page, frame, leave, stop, float('inf'),
                                                 start_next_recording, stop_native_recording)
                    except Exception:
                        recording_issue = 'native_rotation_failed'
                        rotation_failed = True
                        persist(sid, capture_health='rotation_failed', recording_issue=recording_issue,
                                recording_complete=False)
                        continue
                    if continued:
                        rotations += 1
                        native_started = time.time()
                        persist(sid, native_rotations=rotations, capture_health='healthy')
            except Exception:
                # Temporary UI/metadata errors must not close a live browser.
                persist(sid, capture_health='ui_unavailable')
        phase = 'finalization'
        try:
            observed=await frame.evaluate('()=>{window.__agentStopMeetingObserver?.();return window.__agentMeetingObserver || {}}')
            paused=any([await scope.evaluate('()=>!!window.__agentRecordingHadPause') for scope in page.frames])
            observed['timing_reliable']=bool(native_start and not paused)
            if not observed['timing_reliable']:observed['turns']=[]
            if observed.get('source')=='telemost_ui':persist(sid,meeting_context=observed)
        except Exception:pass
        persist(sid, status='finishing', end_reason=end_reason)
        try:
            await prepare_stop(page)
            await stop_native_recording(frame, leave, end_reason)
        except Exception:
            recording_issue = recording_issue or 'recording_stop_failed'
        try:
            capture_scope = capture_frame or frame
            await capture_scope.evaluate('() => window.__agentFinalizeCheckpoint?.()')
            final_deadline = time.monotonic() + 15
            while time.monotonic() < final_deadline:
                if await capture_scope.evaluate('() => ["final", "failed", "idle", "unsupported"].includes(window.__agentCheckpoint?.state)'):
                    break
                await asyncio.sleep(.1)
        except Exception:
            pass
        try:
            captures = [await scope.evaluate('() => window.__agentContinuousCapture || {}') for scope in page.frames]
            rotations = max(rotations, sum(c.get('rotations', 0) for c in captures))
            persist(sid, native_rotations=rotations)
        except Exception:
            pass
        async def wait_downloads():
            while len(download_tasks) < rotations + 1:
                await new_download.wait()
                new_download.clear()
            await asyncio.gather(*download_tasks)
        await asyncio.wait_for(wait_downloads(), timeout=90)
        if download_errors:
            raise AgentError(download_errors[0])
        file = DATA / sid / 'recording.webm'
        if rotations or (rotation_attempted and checkpoints.summary()['chunks']):
            file = DATA / sid / 'continuous.webm'
            try:
                result = await asyncio.to_thread(checkpoints.assemble, file)
            except CheckpointError:
                raise AgentError('continuous_recording_invalid') from None
            if not result['checkpoint_final']:
                raise AgentError('continuous_recording_not_final')
            captures = [await scope.evaluate('() => window.__agentContinuousCapture || {}')
                        for scope in page.frames]
            discontinuities = max(discontinuities, sum(c.get('discontinuities', 0) for c in captures))
            persist(sid, capture_discontinuities=discontinuities)
        if not file.is_file() or not file.stat().st_size:
            raise AgentError('empty_recording_file')
        complete = not discontinuities and not recording_issue and end_reason != 'removed'
        persist(sid, status='saved', recording_file=file.name, recording_complete=complete, bytes=file.stat().st_size)
        await cleanup_and_import(sid, file, complete, browser)
        browser = None
    except AgentError as e:
        if str(e) == 'cancelled_before_join':
            persist(sid, status='cancelled', error_code=str(e), retryable=False)
        else:
            recover_partial(sid, str(e))
    except httpx.RequestError:
        persist(sid, status='upload_uncertain', error_code='upload_result_unknown')
    except httpx.HTTPStatusError:
        persist(sid, status='failed', error_code='recording_import_rejected')
    except PWTimeout as error:
        flags = [label for label in ('intercepts pointer events', 'not enabled', 'not stable', 'outside of the viewport', 'detached', 'waiting for locator') if label in str(error)]
        persist(sid, diagnostic_flags=flags)
        reason = 'waiting_room_timeout' if phase == 'admission' else 'ui_timeout_' + phase
        recover_partial(sid, reason)
    except asyncio.TimeoutError:
        recover_partial(sid, 'recording_download_timeout')
    except Exception:
        recover_partial(sid, 'browser_crash' if phase in {'recording', 'finalization'} else 'ui_failed_' + phase)
    finally:
        if promo_task:
            promo_task.cancel()
        if browser:
            if page:
                await capture_preview(page, sid)
            try:
                await browser.close()
            except Exception:
                pass
        if driver:
            try:
                await driver.stop()
            except Exception:
                pass
        stops.pop(sid, None)
        tasks.pop(sid, None)
