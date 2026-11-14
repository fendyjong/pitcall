#!/bin/sh
# LXD recipe, exec: `exec.sh setup` runs $PITCALL_SETUP as root; `exec.sh job` starts the Actions
# runner as its unprivileged user. Values go in on stdin: `lxc exec --env NAME=value` would put
# them on the command line, where anyone who can list processes reads them.
set -eu
: "${PITCALL_RUNNER:?PITCALL_RUNNER is not set}"
name="pitcall-garage-$PITCALL_RUNNER"

case "${1:-}" in
  setup)
    printf '%s' "$PITCALL_SETUP" | lxc exec "$name" -- sh -s
    ;;
  job)
    uid=$(lxc exec "$name" -- id -u runner)
    gid=$(lxc exec "$name" -- id -g runner)
    {
      for var in ${PITCALL_ENV_NAMES:-}; do
        eval "value=\${$var}"
        printf "export %s='%s'\n" "$var" "$(printf '%s' "$value" | sed "s/'/'\\\\''/g")"
      done
      echo 'exec ./run.sh'
    } | lxc exec "$name" --user "$uid" --group "$gid" --cwd /home/runner/actions-runner \
          --env HOME=/home/runner -- sh -s
    ;;
  *)
    echo "usage: exec.sh setup|job" >&2
    exit 2
    ;;
esac
