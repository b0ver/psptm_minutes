import logging
import time

import httpx
from sqlalchemy import select, text, update

from app import ai
from app.db import engine, Session, Meeting, Action
from app.registry import audit
from app.settings import DATA_DIR
from app.stt import create_model, release_model

log = logging.getLogger("minutes.worker")


def transcribe(path):
    from app.media import decode_local
    audio = decode_local(path)
    model = create_model()
    try:
        segments, _ = model.transcribe(audio, language="ru", beam_size=5, vad_filter=True)
        return [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments if s.text.strip()]
    finally:
        release_model(model)


def process_one(transcriber=transcribe, extractor=ai.extract, lock_connection=None):
    with Session.begin() as s:
        m = s.scalar(select(Meeting).where(Meeting.status == "queued").order_by(Meeting.created_at).with_for_update(skip_locked=True).limit(1))
        if m is None:
            return False
        m.status, m.error_code = "processing", None
        m.attempts += 1
        meeting_id, filename, transcript = m.id, m.recording, m.transcript
    try:
        if not transcript:
            transcript = transcriber(DATA_DIR / "recordings" / filename)
            if not transcript:
                raise ai.ProcessingError("empty_transcript")
            with Session.begin() as s:
                s.get(Meeting, meeting_id).transcript = transcript
        minutes, candidates = extractor(transcript)
        if lock_connection is not None:
            lock_connection.execute(text("SELECT 1"))
        with Session.begin() as s:
            m = s.get(Meeting, meeting_id)
            for candidate in candidates:
                action = Action(meeting_id=meeting_id, **candidate)
                s.add(action)
                s.flush()
                audit(s, action, "extracted", "local_ai")
            m.minutes, m.status = minutes, "ready"
            m.error_code = "unverified_action_candidates" if getattr(candidates, "unverified_count", 0) else None
    except Exception as e:
        # Never serialize exception text: HTTP exceptions may contain URLs/secrets.
        code = e.code if isinstance(e, ai.ProcessingError) else "local_processing_failed"
        retry = isinstance(e, (httpx.TimeoutException, httpx.ConnectError))
        if isinstance(e, httpx.HTTPStatusError):
            retry = e.response.status_code in (429, 502, 503, 504)
            code = "ollama_http_error"
        with Session.begin() as s:
            m = s.get(Meeting, meeting_id)
            m.error_code = code
            m.status = "queued" if retry and m.attempts < 3 else "failed"
        log.warning("job=%s code=%s", meeting_id, code)
        if retry:
            time.sleep(10)
    return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # A session-scoped PostgreSQL lock excludes any other project worker.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        if not conn.scalar(text("SELECT pg_try_advisory_lock(90601008)")):
            log.error("worker_already_active")
            return
        with Session.begin() as s:
            s.execute(update(Meeting).where(Meeting.status == "processing", Meeting.attempts < 3).values(status="queued", error_code="worker_recovered"))
            s.execute(update(Meeting).where(Meeting.status == "processing").values(status="failed", error_code="worker_interrupted"))
        while True:
            conn.execute(text("SELECT 1"))
            if not process_one(lock_connection=conn):
                time.sleep(2)


if __name__ == "__main__":
    main()
