#!/bin/sh
# LXD recipe, exec: `exec.sh setup` runs $PITCALL_SETUP as root; `exec.sh job` starts the Actions
# runner as its unprivileged user. Values go in on stdin: `lxc exec --env NAME=value` would put
# them on the command line, where anyone who can list processes reads them.
set -eu
export LC_ALL=C  # lxc's output untranslated: the scripts compare it as text
: "${PITCALL_RUNNER:?PITCALL_RUNNER is not set}"
# This script's own variables start PITCALL_, a prefix no repo env name may use, so none of them
# can overwrite a repo value it passes on.
PITCALL_CONTAINER="pitcall-garage-$PITCALL_RUNNER"

case "${1:-}" in
  setup)
    printf '%s' "$PITCALL_SETUP" | lxc exec "$PITCALL_CONTAINER" -- sh -s
    ;;
  job)
    # As root, then runuser: it gives `runner` its groups, `docker` among them; --user would not.
    {
      for PITCALL_VAR in ${PITCALL_ENV_NAMES:-}; do
        eval "PITCALL_VALUE=\${$PITCALL_VAR}"
        # The x keeps the value's trailing newlines, which $(...) would strip; %x drops it again.
        PITCALL_VALUE=$(printf '%sx' "$PITCALL_VALUE" | sed "s/'/'\\\\''/g")
        printf "export %s='%s'\n" "$PITCALL_VAR" "${PITCALL_VALUE%x}"
      done
      echo 'exec ./run.sh'
    } | lxc exec "$PITCALL_CONTAINER" --cwd /home/runner/actions-runner --env HOME=/home/runner \
          -- runuser -u runner -- sh -s
    ;;
  *)
    echo "usage: exec.sh setup|job" >&2
    exit 2
    ;;
esac
