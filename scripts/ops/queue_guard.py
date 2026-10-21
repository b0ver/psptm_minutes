"""Hold real PostgreSQL queue locks until the deployment controller releases stdin."""
import sys

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import engine


def main():
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("LOCK TABLE meetings, meeting_analysis, meeting_reports IN EXCLUSIVE MODE"))
            for table in ('meetings', 'meeting_analysis', 'meeting_reports'):
                if connection.scalar(text("SELECT count(*) FROM " + table + " WHERE status='processing'")):
                    print('busy', flush=True)
                    return 1
            print('idle_locked', flush=True)
            sys.stdin.readline()
    except SQLAlchemyError:
        # Never send database errors, credentials or row contents to deployment logs.
        print('queue_lock_unavailable', flush=True)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
