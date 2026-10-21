#!/usr/bin/env bash
# Fixed synthetic source; no production arguments, secrets, volumes or networks.
set -euo pipefail
umask 077
root=$(cd "$(dirname "$0")/../.." && pwd)
scratch=$(mktemp -d "${TMPDIR:-/tmp}/minutes-restore.XXXXXX")
project="psptm-minutes-restore-drill-$$"
case "$project" in psptm-minutes-restore-drill-[0-9]*) ;; *) exit 1 ;; esac
cat > "$scratch/compose.restore.yaml" <<'YAML'
services:
  tests:
    environment:
      RESTORE_DRILL_ISOLATED: '1'
      PYTHONPATH: /app
    volumes: [restore-data:/restore]
volumes:
  restore-data:
YAML
compose() {
  docker compose --env-file /dev/null -p "$project" -f "$root/compose.test.yaml" -f "$scratch/compose.restore.yaml" "$@"
}
cleanup() {
  result=$?
  compose down --volumes > "$scratch/cleanup.log" 2>&1 || result=1
  # Only synthetic artifacts exist here; retain them if verification failed.
  if [[ $result = 0 ]]; then rm -rf "$scratch"; else echo "Restore drill failed; diagnostics: $scratch" >&2; fi
  exit "$result"
}
trap cleanup EXIT
compose build tests > "$scratch/build.log" 2>&1
compose up -d --wait db
compose run --rm --no-deps -T --user 0:0 -e DATA_DIR=/restore/source tests python scripts/ops/restore_fixture.py seed < /dev/null
compose exec -T db pg_dump -U minutes -d minutes -Fc > "$scratch/database.dump"
compose run --rm --no-deps -T --user 0:0 tests python -c \
  'import tarfile,sys; t=tarfile.open(fileobj=sys.stdout.buffer,mode="w|"); t.add("/restore/source",arcname="source"); t.close()' > "$scratch/materials.tar" < /dev/null
compose exec -T db createdb -U minutes minutes_restored
compose exec -T db pg_restore --exit-on-error --no-owner -U minutes -d minutes_restored < "$scratch/database.dump"
# Remove the original to prove verification cannot succeed from surviving source files.
compose run --rm --no-deps -T --user 0:0 tests python -c \
  'import shutil; shutil.rmtree("/restore/source")' < /dev/null
# Extract only our freshly created synthetic archive into the other volume directory.
compose run --rm --no-deps -T --user 0:0 tests python -c \
  'import tarfile,sys,os; from pathlib import Path; t=tarfile.open(fileobj=sys.stdin.buffer,mode="r|*"); t.extractall("/restore",filter="data"); os.rename("/restore/source","/restore/restored"); p=Path("/restore/restored"); [os.chown(f,10001,10001) for f in [p,*p.rglob("*")]]' < "$scratch/materials.tar"
compose run --rm --no-deps -T -e DATA_DIR=/restore/restored tests python -c \
  'import runpy; from sqlalchemy import create_engine; import app.db,sys; app.db.engine=create_engine(app.db.engine.url.set(database="minutes_restored")); app.db.Session.configure(bind=app.db.engine); sys.argv=["restore_fixture.py","verify"]; runpy.run_path("scripts/ops/restore_fixture.py",run_name="__main__")' < /dev/null
printf 'restore_drill_passed\n'
