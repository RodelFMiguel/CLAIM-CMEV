#!/bin/sh
# Keep API/orchestration ownership and real image/document workers in one Compose invocation.
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"
export CMEV_PROFILE=lean

if [ "$#" -eq 0 ] || [ "$1" = "--help" ]; then
    cat <<'USAGE'
Usage: scripts/live-stack.sh <compose command> [arguments]

  scripts/live-stack.sh up --build -d   Start/rebuild all live modules
  scripts/live-stack.sh ps             Show service status
  scripts/live-stack.sh logs --tail 50 cmev-worker-ocr
  scripts/live-stack.sh down           Stop the stack, keeping stored volumes

Uses the base, image and document overlays together with the lean topology.
Requires infra/compose/.env and the selected local model registry entries.
USAGE
    exit 0
fi

if [ ! -f infra/compose/.env ]; then
    printf '%s\n' 'Missing infra/compose/.env; copy .env.example and configure local credentials.' >&2
    exit 1
fi

# On macOS keep long builds/inference startup awake; other hosts run Compose directly.
if command -v caffeinate >/dev/null 2>&1; then
    set -- caffeinate -i -s docker compose --env-file infra/compose/.env \
        -f infra/compose/docker-compose.yml \
        -f infra/compose/docker-compose.image.yml \
        -f infra/compose/docker-compose.document.yml --profile lean "$@"
else
    set -- docker compose --env-file infra/compose/.env \
        -f infra/compose/docker-compose.yml \
        -f infra/compose/docker-compose.image.yml \
        -f infra/compose/docker-compose.document.yml --profile lean "$@"
fi
exec "$@"
