#!/bin/sh
# LXD recipe, stop: stop the container and keep its disk, so caches survive.
set -eu
: "${PITCALL_RUNNER:?PITCALL_RUNNER is not set}"
name="pitcall-garage-$PITCALL_RUNNER"

if ! lxc info "$name" >/dev/null 2>&1; then
  exit 0
fi
if [ "$(lxc info "$name" | sed -n 's/^Status: //p')" = RUNNING ]; then
  lxc stop "$name" --timeout 30
fi
