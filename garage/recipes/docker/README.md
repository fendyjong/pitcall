# The Docker recipe

An example recipe for the garage. Each runner is one Docker container, created on first use from
`ubuntu:24.04`, with the GitHub Actions runner and its own Docker daemon installed inside it.

`garage start` installs this directory beside the service's code and refreshes it every time it
runs. To change a script, copy the directory somewhere else first, edit the copy, and point the
machine config's `recipe` at it.

## What each command does

| Command | What it does |
|---|---|
| `start.sh` | On first use, creates the network `pitcall-garage-<runner>` and the container of the same name (`--privileged`, `--init`, the inner Docker's data on the volume `pitcall-garage-<runner>-docker`) and installs Docker and the latest Actions runner inside. Then starts the container and its Docker daemon, and exits 0 once `docker exec` works. |
| `exec.sh setup` | Runs `$PITCALL_SETUP` as root inside the container. |
| `exec.sh job` | Starts the Actions runner as the unprivileged user `runner`, carrying in every variable named in `$PITCALL_ENV_NAMES` with `docker exec -e NAME`. |
| `cleanup.sh` | Ends every process of the `runner` user, removes the containers, networks and volumes the job's Docker created, and empties the runner's work directory and `/tmp`. Image layers and package caches stay. Succeeds when the container is missing, and starts a stopped one first so its leftovers can be removed. |
| `stop.sh` | Stops the container. Its disk stays, so caches survive. |

## Why each runner has its own network and its own Docker

Two runners on a shared network collide as soon as two repos' CI start a database on the same
fixed port. With one Docker shared by all runners, and no cleanup, one job's containers and volumes
are still there when the next job starts. A network and a Docker daemon per runner, and
`cleanup.sh` after every job, keep the jobs apart.

## The JIT config stays off the command line

`docker exec -e NAME`, written without `=value`, takes the value from the caller's environment
instead of from the command line, so neither the JIT config nor a repo's `env` shows up in `ps`.
Inside, the Actions runner reads `ACTIONS_RUNNER_INPUT_JITCONFIG` and removes it from its
environment before any step runs.

## What this recipe costs in trust

- Membership of the `docker` group is root-equivalent on the host. The garage service runs as your
  own user, so that user needs it.
- A Docker daemon inside a Docker container needs a privileged container, so every runner's
  container runs `--privileged`. **A job in it can become root on the host.** Join only repos you
  trust like your own code. The LXD recipe's containers are unprivileged.
- Runners keep caches between jobs, so a job from one repo can read what an earlier job from
  another repo left in them. Make your copy of `cleanup.sh` remove more if you want less sharing.
