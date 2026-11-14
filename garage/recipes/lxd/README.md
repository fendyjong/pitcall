# The LXD recipe

An example recipe for the garage. Each runner is one unprivileged LXD container, created on first
use from `ubuntu:24.04`, with the GitHub Actions runner and its own Docker daemon installed inside
it.

`garage start` installs this directory beside the service's code and refreshes it every time it
runs. To change a script, copy the directory somewhere else first, edit the copy, and point the
machine config's `recipe` at it.

## What each command does

| Command | What it does |
|---|---|
| `start.sh` | On first use, creates the network `pitcall-<runner>` (LXD picks its address range) and the container `pitcall-garage-<runner>` with `security.nesting=true`, and installs Docker and the latest Actions runner inside. Then starts the container and its Docker daemon, and exits 0 once `lxc exec` works. |
| `exec.sh setup` | Runs `$PITCALL_SETUP` as root inside the container, sending the script on stdin. |
| `exec.sh job` | Starts the Actions runner as the unprivileged user `runner`, with its groups (`runuser`), so it can use the container's Docker. Every variable named in `$PITCALL_ENV_NAMES` goes in on stdin as an `export` line. |
| `cleanup.sh` | Ends every process of the `runner` user, removes the containers, networks and volumes the job's Docker created, and empties the runner's work directory and `/tmp`. Image layers and package caches stay. Succeeds when the container is missing, and starts a stopped one first so its leftovers can be removed. |
| `stop.sh` | Stops the container, and forces it to stop when it has not stopped after 30 seconds. Its disk stays, so caches survive. |

## Why each runner has its own network and its own Docker

Two runners on a shared network collide as soon as two repos' CI start a database on the same
fixed port. With one Docker shared by all runners, and no cleanup, one job's containers and volumes
are still there when the next job starts. A network and a Docker daemon per runner, and
`cleanup.sh` after every job, keep the jobs apart.

## The JIT config stays off the command line

`lxc exec --env NAME=value` puts the value on the command line, where anyone who can list
processes reads it. So `exec.sh` writes each variable as an `export` line to the container shell's
stdin instead. Inside, the Actions runner reads `ACTIONS_RUNNER_INPUT_JITCONFIG` and removes it from
its environment before any step runs.

## What this recipe costs in trust

- Membership of the `lxd` group is root-equivalent on the host. The garage service runs as your own
  user, so that user needs it.
- The containers are unprivileged: root inside one is not root on the host. Docker runs inside
  through `security.nesting`.
- Runners keep caches between jobs, so a job from one repo can read what an earlier job from
  another repo left in them. Make your copy of `cleanup.sh` remove more if you want less sharing.
