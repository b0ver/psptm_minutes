"""Real end-to-end timing, ONLY inside the isolated capacity Compose project.

Output has timings and counters only. Inputs/LLM output remain in private volume.
"""
import argparse
import json
import math
import os
import shutil
import time
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('recordings', nargs='+', type=Path)
    parser.add_argument('--templates', default='tldr,meeting_notes,action_items,key_meanings,chapters')
    parser.add_argument('--budget-hours', type=float, default=24)
    args = parser.parse_args()
    if os.environ.get('CAPACITY_ACK_ISOLATED') != '1' or os.environ.get('DB_HOST') != 'capacity-db':
        parser.error('Use compose.capacity.yaml; production DB access is forbidden')
    from sqlalchemy import select, func
    from app.db import Base, engine, Session, Meeting, MeetingAnalysis, MeetingReport
    from app.settings import DATA_DIR
    from app import reports, worker
    Base.metadata.create_all(engine)
    with Session() as session:
        if session.scalar(select(func.count()).select_from(Meeting)):
            parser.error('Capacity database must be empty; use a fresh isolated volume/project')
    kinds = reports.selected_types(args.templates.split(','))
    folder = DATA_DIR / 'recordings'
    folder.mkdir(parents=True, exist_ok=True)
    metrics = []
    for index, source in enumerate(args.recordings):
        name = str(uuid4()) + source.suffix.lower()
        shutil.copyfile(source, folder / name)
        with Session.begin() as session:
            meeting = Meeting(title='Capacity fixture', recording=name)
            session.add(meeting)
            session.flush()
            reports.enqueue(session, meeting, kinds)
            identifier = meeting.id
        phases = {}
        def transcribe(path):
            started = time.monotonic()
            result = worker.transcribe(path)
            phases['stt_seconds'] = time.monotonic() - started
            phases['speech_end_seconds'] = max((segment['end'] for segment in result), default=0)
            return result
        started = time.monotonic()
        worker.process_one(transcriber=transcribe)
        phases['primary_seconds'] = time.monotonic() - started
        started = time.monotonic()
        reports.process_analysis_one()
        phases['analysis_seconds'] = time.monotonic() - started
        with Session() as session:
            meeting = session.get(Meeting, identifier)
            analysis = session.get(MeetingAnalysis, identifier)
            rows = list(session.scalars(select(MeetingReport).where(MeetingReport.meeting_id == identifier)))
            phases.update(index=index, bytes=source.stat().st_size, templates=len(kinds),
                          status=meeting.status, analysis_status=analysis.status,
                          reports_ready=sum(row.status == 'ready' for row in rows),
                          error_code=meeting.error_code or analysis.error_code,
                          total_seconds=phases['primary_seconds'] + phases['analysis_seconds'])
        metrics.append(phases)
        print(json.dumps(phases), flush=True)
    durations = sorted(row['total_seconds'] for row in metrics)
    all_success = all(row['status'] == 'ready' and row['analysis_status'] == 'ready'
                      and row['reports_ready'] == len(kinds) for row in metrics)
    p95 = durations[math.ceil(.95 * len(durations)) - 1]
    print(json.dumps({'samples': len(metrics), 'all_success': all_success,
                      'p95_serial_seconds': p95, 'utilization_budget': .6,
                      'planning_meetings_per_budget': math.floor(.6 * args.budget_hours * 3600 / p95)
                      if all_success else None,
                      'validated_daily_capacity': False}), flush=True)
    raise SystemExit(0 if all_success else 1)


if __name__ == '__main__':
    main()
