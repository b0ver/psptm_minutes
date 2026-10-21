from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
from datetime import date
import json
import hashlib

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from pydantic import TypeAdapter

from app.db import Session, Meeting, RecordingImport, Action, Audit, Outbox
from app.registry import action_dict, audit, locked_action, approve, change_status
from app.schemas import EditAction, Approval, ChangeStatus
from app import reports
from app.settings import DATA_DIR


@asynccontextmanager
async def lifespan(app):
    (DATA_DIR / "recordings").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "upload-tmp").mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Протоколы и поручения", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

from app.calendar_api import router as calendar_router
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
app.include_router(calendar_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    if request.url.path.startswith('/api/calendars'):
        # Pydantic's default errors can echo credential input back to the browser.
        return JSONResponse({'detail': 'Проверьте поля подключения и настройки календаря'}, status_code=422)
    return await request_validation_exception_handler(request, exc)




@app.middleware("http")
async def protect_headers(request, call_next):
    if request.url.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        # Local use without a key; prevent foreign web pages from issuing browser writes.
        origin = request.headers.get("origin")
        if (origin and origin != str(request.base_url).rstrip("/")) or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Откройте приложение напрямую для изменения данных"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'"
    return response


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static/index.html")


app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/healthz")
def health():
    try:
        with Session() as s:
            s.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "database_unavailable") from None


@app.get("/api/meetings")
def meetings():
    with Session() as s:
        return [{"id": m.id, "title": m.title, "status": m.status, "error_code": m.error_code, "created_at": m.created_at} for m in s.scalars(select(Meeting).order_by(Meeting.created_at.desc()).limit(100))]


@app.post("/api/meetings", status_code=201)
async def upload(title: str = Form(..., min_length=1, max_length=200), recording: UploadFile = File(...),
                 analysis_types: str = Form(default=''), meeting_date: str = Form(default=''), participants: str = Form(default='', max_length=2000),
                 import_session: str = Form(default='', max_length=36), recording_complete: str = Form(default='true'),
                 telemost_context: str = Form(default='',max_length=2000000)):
    # Missing means a complete API upload, not an unchecked HTML checkbox.
    # Recent FastAPI treats absent bool Form fields as False regardless of default.
    try:
        recording_complete = TypeAdapter(bool).validate_python(recording_complete)
    except ValueError:
        raise HTTPException(422, 'Некорректный признак полноты записи') from None
    try:
        types = reports.selected_types(json.loads(analysis_types) if analysis_types else reports.DEFAULT_TYPES)
        known_date = date.fromisoformat(meeting_date) if meeting_date else None
        from app.meeting_context import Context
        context_raw=json.loads(telemost_context) if telemost_context else {}
        context=Context.model_validate(context_raw).model_dump() if context_raw else {}
        if context and not import_session:raise ValueError('context_requires_session')
    except (ValueError, TypeError):
        raise HTTPException(422, "Проверьте типы разборов и дату встречи") from None
    if import_session:
        from uuid import UUID
        try:
            import_session = str(UUID(import_session))
        except ValueError:
            raise HTTPException(422, "Некорректный идентификатор импорта") from None
    elif not recording_complete:
        raise HTTPException(422, "Для неполной записи нужен идентификатор импорта")
    fields=[title.strip(), types, meeting_date, participants.strip(), recording_complete]
    if context:fields.append(json.dumps(context_raw,sort_keys=True,ensure_ascii=False))
    request_digest = hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()
    digest = hashlib.sha256()
    suffix = Path(recording.filename or "").suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".mp4", ".ogg", ".flac", ".webm"}:
        raise HTTPException(422, "Допустимы WAV, MP3, M4A, MP4, OGG, FLAC, WEBM")
    name = str(uuid4()) + suffix
    path = DATA_DIR / "recordings" / name
    total = 0
    try:
        with path.open("xb") as out:
            while chunk := await recording.read(1024 * 1024):
                total += len(chunk)
                out.write(chunk)
                digest.update(chunk)
        if total == 0:
            raise HTTPException(422, "Пустая запись")
        with Session.begin() as s:
            if import_session:
                # Serialize concurrent requests for the same key, including the first insert.
                s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {'key': 'recording-import:' + import_session})
                previous = s.get(RecordingImport, import_session)
                if previous:
                    if previous.recording_sha256 != digest.hexdigest() or previous.request_sha256 != request_digest:
                        raise HTTPException(409, "Сессия уже импортирована с другой записью или параметрами")
                    existing = s.get(Meeting, previous.meeting_id)
                    path.unlink(missing_ok=True)
                    return JSONResponse({'id': existing.id, 'status': existing.status, 'reused': True}, status_code=200)
            meeting = Meeting(title=title.strip(), recording=name)
            s.add(meeting)
            s.flush()
            if import_session:
                s.add(RecordingImport(session_id=import_session, meeting_id=meeting.id, recording_sha256=digest.hexdigest(), request_sha256=request_digest, complete=recording_complete))
            if context:
                from app.db import MeetingContext
                s.add(MeetingContext(meeting_id=meeting.id,observations=context))
                from app.meeting_context import roster
                participants=roster(context)[:2000]
            reports.enqueue(s, meeting, types, known_date, participants.strip())
            result = {"id": meeting.id, "status": meeting.status}
        return result
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        await recording.close()


@app.get('/api/recording-imports/{session_id}')
def recording_import(session_id: str):
    from uuid import UUID
    try:
        session_id = str(UUID(session_id))
    except ValueError:
        raise HTTPException(422, 'Некорректный идентификатор импорта') from None
    with Session() as s:
        receipt = s.get(RecordingImport, session_id)
        if receipt is None:
            raise HTTPException(404, 'Импорт пока не найден')
        m = s.get(Meeting, receipt.meeting_id)
        return {'id': m.id, 'status': m.status, 'recording_complete': receipt.complete, 'recording_sha256': receipt.recording_sha256, 'request_sha256': receipt.request_sha256}


@app.get("/api/meetings/{meeting_id}")
def meeting(meeting_id: str):
    with Session() as s:
        m = s.get(Meeting, meeting_id)
        if not m:
            raise HTTPException(404, "Встреча не найдена")
        receipt = s.scalar(select(RecordingImport).where(RecordingImport.meeting_id == m.id))
        from app.meeting_context import data,annotate
        context=data(s,m.id)
        return {"recording_complete": receipt.complete if receipt else True, "id": m.id, "title": m.title, "status": m.status, "error_code": m.error_code, "transcript": annotate(m.transcript,context) if m.transcript else m.transcript, "minutes": m.minutes,"telemost_context":context,
                "actions": [action_dict(a) for a in s.scalars(select(Action).where(Action.meeting_id == m.id))],
                "analysis": reports.state(s, m.id)}


@app.get("/api/actions")
def actions():
    with Session() as s:
        return [action_dict(a) for a in s.scalars(select(Action).order_by(Action.id).limit(500))]


@app.patch("/api/actions/{action_id}")
def edit(action_id: str, body: EditAction):
    with Session.begin() as s:
        a = locked_action(s, action_id)
        if not a:
            raise HTTPException(404, "Поручение не найдено")
        if a.status != "draft" or a.version != body.version:
            raise HTTPException(409, "Можно редактировать только текущий черновик")
        a.text, a.owner, a.due_on, a.timezone = body.text, body.owner, body.due_on, body.timezone
        a.version += 1
        audit(s, a, "edited", "web")
        return action_dict(a)


@app.post("/api/actions/{action_id}/approve")
def approve_action(action_id: str, body: Approval):
    with Session.begin() as s:
        a = locked_action(s, action_id)
        if not a:
            raise HTTPException(404, "Поручение не найдено")
        try:
            approve(s, a, body.version)
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        return action_dict(a)


@app.post("/api/actions/{action_id}/status")
def status(action_id: str, body: ChangeStatus):
    with Session.begin() as s:
        a = locked_action(s, action_id)
        if not a:
            raise HTTPException(404, "Поручение не найдено")
        try:
            change_status(s, a, body.version, body.status, "web")
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        return action_dict(a)


@app.get("/api/actions/{action_id}/history")
def history(action_id: str):
    with Session() as s:
        return [{"event": a.event, "actor": a.actor, "snapshot": a.snapshot, "created_at": a.created_at} for a in s.scalars(select(Audit).where(Audit.action_id == action_id).order_by(Audit.id))]


@app.get("/api/outbox")
def outbox():
    with Session() as s:
        return [{"action_id": o.action_id, "version": o.version, "status": o.status, "attempts": o.attempts, "error_code": o.error_code} for o in s.scalars(select(Outbox).order_by(Outbox.id.desc()).limit(500))]


@app.get("/api/analysis-types")
def analysis_types():
    return reports.catalog()


@app.get("/api/meetings/{meeting_id}/analysis")
def get_analysis(meeting_id: str):
    with Session() as s:
        if s.get(Meeting, meeting_id) is None:
            raise HTTPException(404, "Встреча не найдена")
        return reports.state(s, meeting_id)


@app.post("/api/meetings/{meeting_id}/analysis", status_code=202)
def request_analysis(meeting_id: str, body: dict):
    try:
        types = reports.selected_types(body.get('types'))
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    with Session.begin() as s:
        meeting = s.scalar(select(Meeting).where(Meeting.id == meeting_id).with_for_update())
        if meeting is None:
            raise HTTPException(404, "Встреча не найдена")
        if not meeting.transcript:
            raise HTTPException(409, "Дождитесь транскрибации")
        reports.enqueue(s, meeting, types)
        s.flush()
        return reports.state(s, meeting_id)


async def telemost_request(method, path, body=None):
    import httpx
    async with httpx.AsyncClient(base_url='http://telemost-agent:8090', timeout=10, trust_env=False, follow_redirects=False) as client:
        try:
            response = await client.request(method, path, json=body)
        except httpx.RequestError:
            raise HTTPException(503, 'Агент Телемоста пока недоступен') from None
    return response


@app.get('/api/telemost/sessions')
async def telemost_sessions():
    r = await telemost_request('GET', '/sessions')
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post('/api/telemost/sessions', status_code=202)
async def telemost_start(body: dict):
    r = await telemost_request('POST', '/sessions', body)
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post('/api/telemost/sessions/{session_id}/stop', status_code=202)
async def telemost_stop(session_id: str):
    from uuid import UUID
    try:
        session_id = str(UUID(session_id))
    except ValueError:
        raise HTTPException(422, 'Некорректный идентификатор сессии') from None
    r = await telemost_request('POST', '/sessions/' + session_id + '/stop')
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get('/api/telemost/sessions/{session_id}/preview')
async def telemost_preview(session_id: str):
    from uuid import UUID
    from fastapi.responses import Response
    try:
        session_id = str(UUID(session_id))
    except ValueError:
        raise HTTPException(422, 'Некорректный идентификатор сессии') from None
    r = await telemost_request('GET', '/sessions/' + session_id + '/preview')
    if r.status_code != 200:
        raise HTTPException(r.status_code, 'Снимок пока недоступен')
    return Response(r.content, media_type='image/png')


@app.post('/api/telemost/sessions/{session_id}/import', status_code=202)
async def telemost_import(session_id: str):
    from uuid import UUID
    try:
        session_id = str(UUID(session_id))
    except ValueError:
        raise HTTPException(422, 'Некорректный идентификатор сессии') from None
    r = await telemost_request('POST', '/sessions/' + session_id + '/import')
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get('/api/telemost/sessions/{session_id}')
async def telemost_session(session_id: str):
    from uuid import UUID
    try:
        session_id = str(UUID(session_id))
    except ValueError:
        raise HTTPException(422, 'Некорректный идентификатор сессии') from None
    r = await telemost_request('GET', '/sessions/' + session_id)
    return JSONResponse(r.json(), status_code=r.status_code)
