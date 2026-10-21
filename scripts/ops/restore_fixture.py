"""Synthetic restore acceptance only. No production secrets or meeting content."""
import argparse
import os
from pathlib import Path
import wave

from cryptography.fernet import Fernet
from sqlalchemy import select, func

from app.db import Base, engine, Session, Meeting, MeetingAnalysis, MeetingReport, CalendarSubscription
from app.settings import DATA_DIR

MEETING_ID = '11111111-1111-4111-8111-111111111111'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['seed', 'verify'])
    args = parser.parse_args()
    if os.getenv('RESTORE_DRILL_ISOLATED') != '1' or DATA_DIR not in (Path('/restore/source'), Path('/restore/restored')):
        parser.error('Only the isolated synthetic restore harness is supported')
    if args.mode == 'seed':
        Base.metadata.create_all(engine)
        with Session() as session:
            if session.scalar(select(func.count()).select_from(Meeting)):
                raise SystemExit('fixture_database_not_empty')
        os.umask(0o077)
        DATA_DIR.mkdir(parents=True, exist_ok=False)
        key = Fernet.generate_key()
        (DATA_DIR / 'calendar_key').write_bytes(key)
        (DATA_DIR / 'recordings').mkdir()
        with wave.open(str(DATA_DIR / 'recordings' / 'fixture.wav'), 'wb') as audio:
            audio.setparams((1, 2, 16000, 0, 'NONE', 'none'))
            audio.writeframes(bytes(64000))
        with Session.begin() as session:
            session.add(Meeting(id=MEETING_ID, title='Synthetic restore fixture', recording='fixture.wav',
                                status='ready', transcript=[{'start': 0, 'end': 2, 'text': 'Synthetic fixture'}],
                                minutes='Synthetic protocol'))
            session.flush()
            session.add(MeetingAnalysis(meeting_id=MEETING_ID, status='ready', step='done'))
            session.add(MeetingReport(meeting_id=MEETING_ID, template_id='tldr', status='ready', content='Synthetic report'))
            session.add(CalendarSubscription(username='fixture@example.invalid', calendar_href='https://calendar.example.invalid/fixture',
                                            title='Synthetic calendar', credential=Fernet(key).encrypt(b'synthetic-password').decode()))
        print('synthetic_fixture_seeded')
    else:
        with Session() as session:
            meeting = session.get(Meeting, MEETING_ID)
            assert session.scalar(select(func.count()).select_from(Meeting)) == 1
            assert meeting.title == 'Synthetic restore fixture' and meeting.status == 'ready'
            assert meeting.transcript == [{'start': 0, 'end': 2, 'text': 'Synthetic fixture'}]
            assert meeting.minutes == 'Synthetic protocol'
            analysis = session.get(MeetingAnalysis, MEETING_ID)
            assert analysis.status == 'ready'
            report = session.scalar(select(MeetingReport).where(MeetingReport.meeting_id == MEETING_ID))
            assert report.content == 'Synthetic report' and report.status == 'ready'
            calendar = session.scalar(select(CalendarSubscription))
            assert Fernet((DATA_DIR / 'calendar_key').read_bytes()).decrypt(calendar.credential.encode()) == b'synthetic-password'
            with wave.open(str(DATA_DIR / 'recordings' / meeting.recording)) as audio:
                assert audio.getnframes() == 32000 and audio.getframerate() == 16000
                assert audio.readframes(32000) == bytes(64000)
        print('restore_verified:database,recording,calendar_key,credential,report')


if __name__ == '__main__':
    main()
