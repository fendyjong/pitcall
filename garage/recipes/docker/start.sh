#!/bin/sh
# Docker recipe, start: make this runner's container exist and run, with its own network and its
# own Docker daemon, and the GitHub Actions runner installed. Exits 0 once `docker exec` works.
set -eu
: "${PITCALL_RUNNER:?PITCALL_RUNNER is not set}"
name="pitcall-garage-$PITCALL_RUNNER"
image="ubuntu:24.04"

if ! docker network inspect "$name" >/dev/null 2>&1; then
  docker network create "$name" >/dev/null
fi
if ! docker container inspect "$name" >/dev/null 2>&1; then
  # --privileged: a Docker daemon inside a container needs it (see README.md).
  # --init: the container stops at once instead of waiting out the stop timeout.
  docker run -d --name "$name" --hostname "$name" --network "$name" --privileged --init \
    -v "$name-docker:/var/lib/docker" "$image" sleep infinity >/dev/null
fi
if [ "$(docker container inspect -f '{{.State.Running}}' "$name")" != true ]; then
  docker start "$name" >/dev/null
fi

# First use only: install Docker and the Actions runner. The marker makes this idempotent.
docker exec -i -u 0 "$name" sh -s <<'EOF'
set -eu
if [ -f /opt/pitcall-garage/installed ]; then exit 0; fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl tar git sudo procps docker.io
id runner >/dev/null 2>&1 || useradd --create-home --shell /bin/bash runner
usermod -aG docker runner
version=$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest |
  sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p')
[ -n "$version" ] || { echo "cannot read the latest Actions runner version" >&2; exit 1; }
case "$(uname -m)" in
  x86_64) arch=x64 ;;
  aarch64) arch=arm64 ;;
  *) echo "unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac
mkdir -p /home/runner/actions-runner
curl -fsSL "https://github.com/actions/runner/releases/download/v$version/actions-runner-linux-$arch-$version.tar.gz" |
  tar -xz -C /home/runner/actions-runner
/home/runner/actions-runner/bin/installdependencies.sh
chown -R runner:runner /home/runner
mkdir -p /opt/pitcall-garage /var/lib/pitcall-garage/setup
touch /opt/pitcall-garage/installed
EOF

# The runner's own Docker daemon, started when it is not running.
if ! docker exec -u 0 "$name" pgrep -x dockerd >/dev/null 2>&1; then
  docker exec -d -u 0 "$name" sh -c 'dockerd >/var/log/dockerd.log 2>&1'
fi
i=0
until docker exec -u 0 "$name" docker info >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    echo "the Docker daemon in $name did not start" >&2
    exit 1
  fi
  sleep 1
done
