#!/bin/sh
# Docker recipe, cleanup: remove what one job leaves behind and keep the caches. Succeeds on a
# missing container. A stopped one is started first (start.sh is idempotent), so that what a job
# left in it, and in its Docker, can be removed.
set -eu
: "${PITCALL_RUNNER:?PITCALL_RUNNER is not set}"
name="pitcall-garage-$PITCALL_RUNNER"
here=$(cd "$(dirname "$0")" && pwd)

if ! docker container inspect "$name" >/dev/null 2>&1; then
  exit 0
fi
"$here/start.sh"
docker exec -i -u 0 "$name" sh -s <<'EOF'
set -eu
# Every process the job left: the runner, its steps, and anything they started.
pkill -KILL -u runner 2>/dev/null || true
# What the job's Docker created. Image layers stay: they are the cache.
ids=$(docker ps -aq)
if [ -n "$ids" ]; then docker rm -f $ids >/dev/null; fi
docker network prune -f >/dev/null
docker volume prune -a -f >/dev/null
# The work directory and the one-job runner's own files.
cd /home/runner/actions-runner
rm -rf _work _diag .runner .credentials .credentials_rsaparams
find /tmp -mindepth 1 -delete
EOF
