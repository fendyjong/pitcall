#!/bin/sh
# LXD recipe, exec: `exec.sh setup` runs $PITCALL_SETUP as root; `exec.sh job` starts the Actions
# runner as its unprivileged user. Values go in on stdin: `lxc exec --env NAME=value` would put
# them on the command line, where anyone who can list processes reads them.
set -eu
export LC_ALL=C  # lxc's output untranslated: the scripts compare it as text
: "${PITCALL_RUNNER:?PITCALL_RUNNER is not set}"
name="pitcall-garage-$PITCALL_RUNNER"

case "${1:-}" in
  setup)
    printf '%s' "$PITCALL_SETUP" | lxc exec "$name" -- sh -s
    ;;
  job)
    # As root, then runuser: it gives `runner` its groups, `docker` among them; --user would not.
    {
      for var in ${PITCALL_ENV_NAMES:-}; do
        eval "value=\${$var}"
        printf "export %s='%s'\n" "$var" "$(printf '%s' "$value" | sed "s/'/'\\\\''/g")"
      done
      echo 'exec ./run.sh'
    } | lxc exec "$name" --cwd /home/runner/actions-runner --env HOME=/home/runner \
          -- runuser -u runner -- sh -s
    ;;
  *)
    echo "usage: exec.sh setup|job" >&2
    exit 2
    ;;
esac
