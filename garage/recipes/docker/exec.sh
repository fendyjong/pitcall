#!/bin/sh
# Docker recipe, exec: `exec.sh setup` runs $PITCALL_SETUP as root; `exec.sh job` starts the
# Actions runner as its unprivileged user. Values go in with `docker exec -e NAME`, which reads
# each value from this process's environment, never from the command line.
set -eu
: "${PITCALL_RUNNER:?PITCALL_RUNNER is not set}"
# This script's own variables start PITCALL_, a prefix no repo env name may use: `-e NAME` reads
# the value from this shell's environment, where a variable of ours would have overwritten it.
PITCALL_CONTAINER="pitcall-garage-$PITCALL_RUNNER"

case "${1:-}" in
  setup)
    exec docker exec -u 0 -e PITCALL_SETUP "$PITCALL_CONTAINER" sh -c 'eval "$PITCALL_SETUP"'
    ;;
  job)
    set --
    for PITCALL_VAR in ${PITCALL_ENV_NAMES:-}; do
      set -- "$@" -e "$PITCALL_VAR"
    done
    exec docker exec -u runner -w /home/runner/actions-runner "$@" "$PITCALL_CONTAINER" ./run.sh
    ;;
  *)
    echo "usage: exec.sh setup|job" >&2
    exit 2
    ;;
esac
