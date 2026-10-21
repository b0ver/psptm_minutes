#!/usr/bin/env bash
# Fixed synthetic project; refuse existing staging resources before installing a trap.
set -euo pipefail
umask 077
repo=$(cd "$(dirname "$0")/../.." && pwd)
project=psptm-minutes-staging-smoke
if [ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$project")" ] ||
   [ -n "$(docker volume ls -q --filter "label=com.docker.compose.project=$project")" ]; then
  echo 'staging_smoke_existing_resources_refused' >&2
  exit 1
fi
scratch=$(mktemp -d "${TMPDIR:-/tmp}/minutes-staging-smoke.XXXXXX")
compose() {
  docker compose --env-file "$scratch/staging.env" -p "$project" \
    -f "$scratch/compose.staging-smoke.yaml" "$@"
}
cleanup() {
  result=$?
  if [ -f "$scratch/staging.env" ]; then
    compose down --volumes > "$scratch/cleanup.log" 2>&1 || result=1
  fi
  if [ "$result" = 0 ]; then rm -rf "$scratch"; else echo 'staging_smoke_failed_private_diagnostics_retained' >&2; fi
  exit "$result"
}
trap cleanup EXIT
revision=$(git -C "$repo" rev-parse HEAD)
docker build --target runtime --label "org.opencontainers.image.revision=$revision" \
  --iidfile "$scratch/image-id" "$repo" > "$scratch/build.log" 2>&1
python3 - "$scratch" "$repo" <<'PY'
import base64, pathlib, secrets, shutil, sys
root, repo = (pathlib.Path(p).resolve() for p in sys.argv[1:])
folder = root / 'secrets'
folder.mkdir(mode=0o700)
for name, value in [('db_password', secrets.token_urlsafe(36)),
                    ('calendar_key', base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())]:
    target = folder / name
    target.write_text(value + '\n')
    target.chmod(0o444)
shutil.copyfile(repo / 'compose.staging-smoke.yaml', root / 'compose.staging-smoke.yaml')
(root / 'staging.env').write_text('STAGING_RUNTIME_IMAGE=' + (root / 'image-id').read_text().strip() +
                                 '\nSTAGING_SECRETS_DIR=' + str(folder) + '\n')
PY
compose config --quiet
compose up -d --no-build --wait api > "$scratch/start.log" 2>&1
python3 "$repo/scripts/ops/staging_smoke_probe.py" --root "$scratch"
