"""Process saved transcripts independently of GPU transcription jobs."""
import logging
import time

from sqlalchemy import text, update

from app import reports
from app.db import engine, Session, MeetingAnalysis, MeetingReport


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
        if not conn.scalar(text('SELECT pg_try_advisory_lock(90601009)')):
            logging.error('analysis_worker_already_active')
            return
        with Session.begin() as s:
            s.execute(update(MeetingAnalysis).where(MeetingAnalysis.status == 'processing').values(status='queued', error_code=None))
            s.execute(update(MeetingReport).where(MeetingReport.status == 'processing').values(status='queued', error_code=None))
        while True:
            conn.execute(text('SELECT 1'))
            if not reports.process_analysis_one():
                time.sleep(2)


if __name__ == '__main__':
    main()
