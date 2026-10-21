#!/usr/bin/env bash
# Managed host deployment only. Never run against an unmanaged live installation.
set -euo pipefail
umask 077
root=${1:?absolute managed deployment root required}
project=${2:?Compose project required}
[[ "$root" = /* && "$root" != / && "$root" != *$'\n'* ]]
[[ "$project" = psptm-minutes || "$project" = psptm-minutes-staging ]]
[[ ${RELEASE_SHA:?} =~ ^[0-9a-f]{40}$ ]]
for ref in "${RELEASE_RUNTIME:?}" "${RELEASE_GPU:?}" "${RELEASE_TELEMOST:?}"; do
  [[ "$ref" =~ ^ghcr.io/[a-z0-9/_.-]+@sha256:[0-9a-f]{64}$ ]]
done
[[ -d "$root/shared/secrets" && -f "$root/shared/.env" ]]
mkdir -p "$root/releases" "$root/backups"
exec 9>"$root/deploy.lock"
flock -n 9
source_dir=$(cd "$(dirname "$0")/../.." && pwd)
release="$root/releases/$RELEASE_SHA"
# Never rewrite the manifest of an existing release.
[[ ! -e "$release" ]]
mkdir "$release"
cp "$source_dir"/compose{,.gpu,.telemost}.yaml "$release/"
ln -s "$root/shared/secrets" "$release/secrets"
ln -s "$root/shared/.env" "$release/.env"
cat > "$release/compose.release.yaml" <<EOF
services:
  init: {image: $RELEASE_RUNTIME}
  api: {image: $RELEASE_RUNTIME}
  worker: {image: $RELEASE_GPU}
  analysis-worker: {image: $RELEASE_RUNTIME}
  telegram: {image: $RELEASE_RUNTIME}
  calendar-scheduler: {image: $RELEASE_RUNTIME}
  ollama-check: {image: $RELEASE_RUNTIME}
  telemost-agent: {image: $RELEASE_TELEMOST}
EOF
compose() {
  local directory=$1; shift
  docker compose --project-directory "$directory" -p "$project" \
    -f "$directory/compose.yaml" -f "$directory/compose.gpu.yaml" \
    -f "$directory/compose.telemost.yaml" -f "$directory/compose.release.yaml" \
    --profile ai --profile telegram --profile calendar --profile telemost "$@"
}
compose "$release" config --quiet
compose "$release" pull
previous=''
if [[ -L "$root/current" ]]; then previous=$(readlink -f "$root/current"); fi
if [[ -z "$previous" ]]; then
  [[ "$project" = psptm-minutes-staging ]]
  # Initial staging must have no containers or existing database/recordings volumes.
  [[ -z $(docker ps -aq --filter "label=com.docker.compose.project=$project") ]]
  [[ -z $(docker volume ls -q --filter "label=com.docker.compose.project=$project") ]]
else
  [[ -f "$previous/compose.release.yaml" ]]
fi
frozen=0
changed=0
recover() {
  result=$?
  if [[ $result -ne 0 && -n "$previous" && $frozen = 1 ]]; then
    if [[ $changed = 0 ]]; then
      compose "$previous" start worker analysis-worker telegram calendar-scheduler || true
    else
      compose "$release" stop telegram calendar-scheduler || true
      echo 'Deployment failed. Producers stopped; inspect active work and follow the rollback runbook.' >&2
    fi
  fi
  exit "$result"
}
trap recover EXIT
if [[ -n "$previous" ]]; then
  frozen=1
  compose "$previous" stop telegram calendar-scheduler
  # Admission must also be closed to local web/CLI users during this window.
  compose "$previous" exec -T telemost-agent python - < "$source_dir/scripts/ops/agent_idle.py"
  compose "$previous" exec -T api python - < "$source_dir/scripts/ops/quiescence.py"
  # Hold table locks while stopping consumers so they cannot claim a queued job
  # between the idle check and SIGTERM.
  python3 "$source_dir/scripts/ops/freeze_workers.py" "$previous" "$project"
  changed=1
  compose "$previous" stop api telemost-agent
  backup="$root/backups/$(date -u +%Y%m%dT%H%M%SZ)-$RELEASE_SHA"
  mkdir "$backup"
  compose "$previous" exec -T db pg_dump -U minutes -d minutes -Fc > "$backup/database.dump"
  # All recording writers are stopped. Read the existing mounted volume without starting API.
  compose "$previous" run --rm --no-deps --no-build -T api python -c \
    'import tarfile,sys; t=tarfile.open(fileobj=sys.stdout.buffer,mode="w|"); t.add("/data",arcname="data"); t.close()' > "$backup/recordings.tar"
  cp -a "$root/shared/secrets" "$backup/"
  cp "$root/shared/.env" "$backup/environment.env"
  cp "$previous/compose.release.yaml" "$backup/previous-images.yaml"
  (cd "$backup" && sha256sum database.dump recordings.tar > SHA256SUMS)
  # Existing schema must already match candidate; create_all is not an upgrade strategy.
  compose "$release" run --rm --no-deps --no-build -T init python - < "$source_dir/scripts/ops/schema_check.py"
fi
compose "$release" up -d --no-build --wait --wait-timeout 90 db
compose "$release" run --rm --no-deps --no-build -T init
# Verify model policy and real CUDA kernels before starting producers.
compose "$release" run --rm --no-deps --no-build -T ollama-check
compose "$release" run --rm --no-deps --no-build -T worker python -m app.check_stt
compose "$release" up -d --no-build --no-deps --wait --wait-timeout 120 api telemost-agent
compose "$release" up -d --no-build --no-deps worker analysis-worker
compose "$release" up -d --no-build --no-deps telegram calendar-scheduler
sleep 5
compose "$release" ps --format json > "$release/services.json"
python3 "$source_dir/scripts/ops/check_services.py" "$release/services.json"
ln -s "$release" "$root/current.next"
mv -Tf "$root/current.next" "$root/current"
echo "Deployed $RELEASE_SHA to $project. Perform the acceptance checks in docs/ci-cd.md."
