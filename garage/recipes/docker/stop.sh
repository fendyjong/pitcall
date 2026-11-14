#!/bin/sh
# Docker recipe, stop: stop the container and keep its disk, so caches survive.
set -eu
: "${PITCALL_RUNNER:?PITCALL_RUNNER is not set}"
name="pitcall-garage-$PITCALL_RUNNER"

if docker container inspect "$name" >/dev/null 2>&1; then
  docker stop "$name" >/dev/null
fi
