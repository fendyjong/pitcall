#!/bin/sh
# LXD recipe, start: make this runner's container exist and run, unprivileged, on its own network,
# with its own Docker and the GitHub Actions runner installed. Exits 0 once `lxc exec` works.
set -eu
: "${PITCALL_RUNNER:?PITCALL_RUNNER is not set}"
name="pitcall-garage-$PITCALL_RUNNER"
net="pitcall-$PITCALL_RUNNER"  # a network's bridge name is limited to 15 characters
image="ubuntu:24.04"

if ! lxc network show "$net" >/dev/null 2>&1; then
  lxc network create "$net" >/dev/null
fi
if ! lxc info "$name" >/dev/null 2>&1; then
  # security.nesting lets the unprivileged container run its own Docker daemon.
  lxc init "$image" "$name" --network "$net" -c security.nesting=true \
    -c security.syscalls.intercept.mknod=true -c security.syscalls.intercept.setxattr=true >/dev/null
fi
if [ "$(lxc info "$name" | sed -n 's/^Status: //p')" != RUNNING ]; then
  lxc start "$name"
fi
i=0
until lxc exec "$name" -- true >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    echo "$name did not come up" >&2
    exit 1
  fi
  sleep 1
done
lxc exec "$name" -- cloud-init status --wait >/dev/null 2>&1 || true

# First use only: install Docker and the Actions runner. The marker makes this idempotent.
lxc exec "$name" -- sh -s <<'EOF'
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
lxc exec "$name" -- systemctl start docker
