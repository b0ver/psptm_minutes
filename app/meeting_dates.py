"""Meeting dates without treating import time as recording time."""
from datetime import datetime, timezone
from functools import lru_cache
import json
import math
from uuid import UUID

from sqlalchemy import select

from app.db import MeetingAnalysis, RecordingImport
from app.settings import DATA_DIR


@lru_cache(maxsize=256)
def _read_start(path, meeting_id, session_id, modified, size):
    # The stat values invalidate cached results after the agent's atomic write.
    try:
        with path.open('rb') as source:
            raw = source.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            return None
        state = json.loads(raw)
        if not isinstance(state, dict) or state.get('id') != session_id or state.get('meeting_id') != meeting_id:
            return None
        value = state.get('recording_started_at')
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            return None
        return datetime.fromtimestamp(value, timezone.utc)
    except (OSError, ValueError, OverflowError):
        return None


def recording_start(receipt, created_at):
    """Read the existing durable receipt; never infer a start from file mtime."""
    try:
        session_id = str(UUID(receipt.session_id))
        root = (DATA_DIR / 'telemost').resolve()
        path = (root / session_id / 'state.json').resolve()
        if not path.is_relative_to(root):
            return None
        stat = path.stat()
        if stat.st_size > 4 * 1024 * 1024:
            return None
        started = _read_start(path, receipt.meeting_id, session_id, stat.st_mtime_ns, stat.st_size)
        return started if started and started <= created_at else None
    except (OSError, ValueError, RuntimeError):
        return None


def dates(s, meetings):
    """Batch database reads for the list and the detail card. No schema change."""
    ids = [m.id for m in meetings]
    if not ids:
        return {}
    known_dates = dict(s.execute(select(MeetingAnalysis.meeting_id, MeetingAnalysis.meeting_date)
                                 .where(MeetingAnalysis.meeting_id.in_(ids))).all())
    receipts = {r.meeting_id: r for r in s.scalars(select(RecordingImport)
                                                .where(RecordingImport.meeting_id.in_(ids)))}
    return {m.id: {
        'created_at': m.created_at,
        'meeting_date': known_dates.get(m.id),
        'recording_started_at': recording_start(receipts[m.id], m.created_at) if m.id in receipts else None,
    } for m in meetings}
