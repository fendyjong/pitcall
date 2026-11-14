# The garage

On a personal GitHub account a self-hosted runner is registered to one repo. With several repos
the runners are divided up front: one repo's runners sit idle while another repo's jobs wait in
its queue. The **garage** is one pool of runners on one machine that serves every repo that has
joined it. A pitcall background service polls the joined repos for queued jobs. When a runner is
free it goes to the next repo in turn, gets a one-job (just-in-time, "JIT") registration for that
repo, takes one job, is cleaned, and is free again. Runner containers start when there is work and
stop when there has been none for a while.

The garage is off until you start it. It runs on Linux (a systemd user service) and macOS (a
launchd agent), as your own user and never as root. It needs no GitHub organization: each
registration is made for the repo whose job it serves, which works on a personal account and for
organization repos alike.

The commands below are written `garage <command>`, short for

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/garage.py <command>
```

or, from a clone of this repository, `python3 scripts/garage.py <command>`.

## How a job gets a runner

Every `poll_seconds` the service:

1. **Polls.** For each joined repo, it lists the workflow runs that are queued or in progress (a
   run in progress can still have jobs waiting for a runner), then their jobs. Every request is
   conditional, carrying the last ETag, so an unchanged answer costs no rate limit. A job is
   wanted when it is queued and its `runs-on` label set **equals** one of the repo's confirmed
   sets, compared case-insensitively with order ignored. Jobs that ask for GitHub-hosted labels
   never match, so they stay where they are.
2. **Counts demand.** A repo's demand for a label set is its wanted jobs with that set, minus the
   runners already handed to it for that set that have not picked a job up yet. Without the
   subtraction, one queued job would get a new runner on every poll until it started.
3. **Hands out free runners, repos taking turns.** One runner to each repo with demand, round and
   round, until runners or demand run out. Within a repo, the label set whose oldest wanted job has
   waited longest goes first; which of its jobs runs is GitHub's choice.
4. **Starts** the runner's container if it is not running, **sets it up** with the repo's `setup`
   command if it has one that has not run on this runner (or has changed since), **registers** a
   JIT runner for the repo with exactly that label set, named `pitcall-<id>-<runner>-<n>`, and
   **runs** it. GitHub gives a runner one job from its repo whose labels are all among the
   runner's, so a runner registered with a larger set can take a job that asked for fewer; the next
   poll's demand count makes up for the job it left waiting.
5. **Cleans.** When the runner exits after its job, the service runs `cleanup`, deletes the
   registration if it still exists, and the runner is free again.
6. **Stops idle containers.** A runner free for `idle_timeout_minutes` has its container stopped,
   keeping its disk, so caches survive.

## What you need

- Linux with systemd, or macOS.
- Python 3. The garage uses the standard library only, so nothing has to stay installed for it.
- Docker or LXD for the example recipes, or a recipe of your own.
- A dedicated fine-grained personal access token.

## 1. Create the token

Create a fine-grained personal access token that is used for nothing else:

- **Repository access:** each repo you will join.
- **Repository permissions:** Administration read and write (register and delete runners),
  Actions read (see queued jobs), Contents read (read the default branch's config and workflows).

Put it in a file only you can read. Paste the token, press Enter, then Ctrl-D, so it never appears
on a command line:

```
mkdir -p ~/.config/pitcall
(umask 077; cat > ~/.config/pitcall/garage.token)
chmod 600 ~/.config/pitcall/garage.token
```

The garage refuses a token file that is not owned by you, or that your group or others have any
permission on, and says `chmod 600 <file>`. It sends the token only in the `Authorization` header
to `api.github.com`: never on a command line, in a log, or in the state file. `garage status` warns
from 7 days before the token expires, from the `GitHub-Authentication-Token-Expiration` header
GitHub returns. To renew it, create a new token, put it in the token file the same way, then run
`garage start`: the service reads the token only when it starts.

## 2. Write the machine config

The machine config is `~/.config/pitcall/garage.json` (honouring `$XDG_CONFIG_HOME`). It holds no
secrets. Copy the example the plugin ships, `garage/garage.example.json` in the plugin's directory;
every `garage` command except `status` prints its full path when the config is missing.

```json
{
  "runners": 3,
  "recipe": {
    "start": "~/.local/share/pitcall/garage/recipes/docker/start.sh",
    "stop": "~/.local/share/pitcall/garage/recipes/docker/stop.sh",
    "exec": "~/.local/share/pitcall/garage/recipes/docker/exec.sh",
    "cleanup": "~/.local/share/pitcall/garage/recipes/docker/cleanup.sh"
  },
  "idle_timeout_minutes": 10,
  "poll_seconds": 15,
  "pickup_deadline_minutes": 5,
  "token_file": "~/.config/pitcall/garage.token",
  "repos": {}
}
```

| Key | Meaning |
| --- | --- |
| `id` | Written by the first `garage start`; do not set it. Eight random hex characters that name this garage's registrations, `pitcall-<id>-<runner>-<n>`. Recovery deletes registrations by that prefix, so a second machine running a garage for the same repo leaves this one's runners alone; and a random id, unlike the hostname, says nothing about this machine to anyone who can see the repo's runner list. |
| `runners` | How many runners; they are named `r1` … `rN`. |
| `recipe` | The four recipe commands (below). |
| `idle_timeout_minutes` | Minutes a runner stays free before its container is stopped. Default 10. |
| `poll_seconds` | Seconds between polls. Default 15. |
| `pickup_deadline_minutes` | Minutes a registered runner may wait for its job before it is ended. Default 5. |
| `token_file` | The token file. |
| `repos` | Written by `garage join` and `garage leave`: each joined repo and its confirmed label sets. |

The service re-reads this file when it changes, so joining needs no restart. A change it cannot
read, such as a typo in the JSON, is not applied: the service carries on with the last config it
read, and `garage status` shows the error. `repos` and the three timing keys apply at once; a change
to `runners`, `recipe` or `token_file`, or a new token in the token file, applies at the next
`garage start`.

`garage start` installs the service's code to `~/.local/share/pitcall/garage/` (honouring
`$XDG_DATA_HOME`), and the service writes its state to `~/.local/state/pitcall/garage.json`
(honouring `$XDG_STATE_HOME`). The service reads those variables from its own environment, which is
the service manager's, not your shell's: if you set any `XDG_*` directory in your shell, set it for
the service manager too (`systemctl --user set-environment`, `launchctl setenv`).

## 3. Pick a recipe

pitcall does not handle containers itself. A **recipe** is four shell commands it calls; each is
run with `/bin/sh -c`, as your own user. Every command gets `PITCALL_RUNNER` (`r1` …), and
`PITCALL_REPO` (`owner/name`) whenever it acts for a repo.

| Command | When | Must |
| --- | --- | --- |
| `start` | before a job, when the runner's container is not running | Make the runner's container exist and run, creating it on first use with its own network namespace, its own Docker daemon, and the Actions runner software installed. Exit 0 once `exec` works. |
| `exec setup` | before every job of a repo that has a `setup` command | Run the shell command in `PITCALL_SETUP` as root inside the container. The service builds that command so it skips the repo's `setup` when the runner's own record says this exact text already ran. |
| `exec job` | to run a job | Start the Actions runner inside the container as its unprivileged user, carrying in every variable named in `PITCALL_ENV_NAMES` (the JIT config and the repo's `env`) without putting any value on a command line. Block until the runner exits, and exit with its status. |
| `cleanup` | after every job, after a failed pickup, on recovery, and on `garage stop` | Remove what one job leaves behind: the work directory, leftover processes, and the containers, networks and volumes the job's Docker created. Keep caches such as image layers and package downloads. Succeed on a stopped or missing container. |
| `stop` | after the idle timeout, and on `garage stop` | Stop the container and keep its disk, so caches survive. |

`exec` is called with its mode as an argument: `<exec> setup` or `<exec> job`. Each command gets up to
3600 seconds before it counts as failed, so a hung `apt-get` cannot hold a runner forever. Every
command must be idempotent: the service calls them again after a crash without knowing how far the last call
got.

The JIT config reaches the runner in the variable `ACTIONS_RUNNER_INPUT_JITCONFIG`. The runner reads
it like its `--jitconfig` argument and removes it from its environment once read, so job steps never
see it, and no command line shows it to anyone who can list processes.

Two examples ship with the plugin, each with a README that says what it does and why:
[`garage/recipes/docker/`](../garage/recipes/docker/README.md) and
[`garage/recipes/lxd/`](../garage/recipes/lxd/README.md). `garage start` installs them beside the
service's code and refreshes them on every start, so to change one, copy it somewhere else first
and point `recipe` at your copy.

Both give every runner its own network and its own Docker: two runners on a shared network collide
as soon as two repos' CI start a database on the same fixed port, and without cleaning, one job's
state leaks into the next. Membership of the `docker` or `lxd` group is root-equivalent on the host.
A Docker daemon inside a Docker container needs a privileged container, so the Docker example runs
each runner `--privileged`, and **a job in it can become root on the host**. The LXD example's
containers are unprivileged; it starts the Actions runner as the container's `runner` user with
`runuser`, and forces a slow `stop` after 30 seconds.

## 4. Start it

`garage start`:

- checks the machine config and the token file, and writes the `id` if it is missing;
- installs the service's code to `~/.local/share/pitcall/garage/` and points the service there,
  never at the plugin: Claude Code installs each plugin version in its own directory, and an update
  can remove the old one, which would leave a service pointing into it crashing in a restart loop.
  Run `garage start` again after every plugin update; `garage status` says when the installed code
  and the plugin differ;
- **Linux:** writes `~/.config/systemd/user/pitcall-garage.service` (`Restart=on-failure`,
  `WantedBy=default.target`, `TimeoutStopSec=5min`) and runs `systemctl --user enable --now`. If
  lingering is off for your user, the service stops when you log out; `start` says so and prints
  `loginctl enable-linger <user>`, which it does not run for you;
- **macOS:** writes `~/Library/LaunchAgents/dev.pitcall.garage.plist` (`RunAtLoad`, `KeepAlive`,
  `ExitTimeOut` 300), then runs `launchctl enable` and `launchctl bootstrap gui/<uid>`. The
  `enable` undoes the `disable` that `stop` leaves, so a stopped garage stays stopped across
  logins and a started one starts again;
- writes the `PATH` of the shell you run it in into the unit (`Environment=PATH=…`) or the plist
  (`EnvironmentVariables`), so the recipes find `docker` or `lxc` where that shell does. launchd's
  own `PATH` is `/usr/bin:/bin:/usr/sbin:/sbin`, which has neither Docker Desktop's nor Homebrew's
  `docker`. Run `garage start` again after you move a tool the recipe calls;
- on a garage that is already running, restarts it to pick up the refreshed code. That ends the
  jobs in progress, as `stop` does, so `start` names them first.

The service refuses to run as root. Root happens only inside a runner, through the recipe.

## Joining a repo

Run `garage join` in a repo's checkout, for the repo `gh repo view` names there, or anywhere with
`--repo owner/name`:

1. It refuses when the machine config or the token file is missing, or the token file fails the
   permission check.
2. **Public repos** are refused unless you pass `--allow-public`, after it prints the risk: anyone
   can open a pull request from a fork, and depending on the repo's settings its workflow can run on
   this machine, inside this network. With `--allow-public` it still prints the risk, and advises
   requiring approval for workflow runs from outside contributors.
3. **Token coverage.** It checks each permission the service needs, without registering anything
   (the registration token it asks for registers nothing and expires within the hour). On a
   failure it says exactly what to tick on the token's settings page: "Repository access: add
   owner/name. Repository permissions: Administration read and write, Actions read, Contents
   read", naming the missing permission when GitHub's answer does.
4. **Labels.** It reads the workflow files on the default branch and lists every `runs-on` label
   set that includes `self-hosted`, in the scalar, inline-list and block-list forms. A `runs-on`
   it cannot read, such as an expression or a runner group, is listed as unreadable, and so is a
   workflow file that is not UTF-8.
   `--labels "self-hosted,linux;self-hosted,large"` replaces the sets it found. Your confirmation is
   the real check, and only matching jobs are served, so existing workflows need no edits.
5. It prints one line about trust: "A joined repo is trusted like your own code: its jobs share
   runners and caches with every other joined repo's jobs."
6. Without `--yes` it stops there and writes nothing. Run it again with `--yes` to write the repo
   and its label sets into the machine config.

`garage leave` (in a checkout, or with `--repo`) removes the repo from the config. A job already
running for it finishes; nothing new is registered for it.

`/pitcall:setup` ends by offering to run `garage join` when this machine has a garage config.

## A repo's `runner` section

A joined repo may set two things for its jobs in its own `.pitcall/config.json`:

```json
"runner": {
  "env": { "TZ": "UTC" },
  "setup": "apt-get update && apt-get install -y postgresql-client"
}
```

- `env`: string variables set for every job of this repo. Each name is a plain variable name
  (letters, digits and `_`, not starting with a digit) and does not start with `ACTIONS_RUNNER_` or
  `PITCALL_`. It is also not a name the recipe's own host-side commands read, because the service
  hands each value to the recipe under its own name: `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`,
  `TMPDIR`, `PWD`, `OLDPWD`, `IFS`, `ENV`, `CDPATH`, `LANG`, `LANGUAGE`, `SNAP`, the shell's own
  `PPID`, `OPTIND`, `SHELLOPTS`, `BASHOPTS` and `BASH_ENV`, or a name starting with `LC_`, `LD_`,
  `DOCKER_`, `LXD_`, `LXC_`, `XDG_` or `SNAP_`. Set those inside the job (a workflow `env:`)
  instead. Each value is at most 65536 bytes in UTF-8, and the names together, space-separated, at
  most 32768 bytes, since the service passes them to the job in one variable.
- `setup`: a root command run once per runner, before that runner's first job for this repo, and
  again whenever its text changes. The record of which `setup` ran lives inside the runner, so a
  recreated container runs it again. It is at most 65536 bytes in UTF-8, each `'` counting as five,
  since the service single-quotes it.

All `env` names, values and `setup` together are at most 262144 bytes in UTF-8. These limits keep
every variable the recipe is handed under Linux's limit of 131072 bytes for a single variable, and
the whole environment under macOS's 1 MiB.

Both are optional; a repo with no `runner` section still gets runners. Both are read from the repo's
**default branch** through the API, never from a checkout, so a pull request cannot change a
command that runs as root in a runner shared with other repos. The key is documented with the
others in [`configuration.md`](configuration.md).

## `garage status`

`status` works whether the service runs or not. It reads the state file the service rewrites on
every loop, and shows:

- **the service:** running, stopped or not installed, and whether the installed code matches this
  plugin's version;
- **the token:** its file, the permission check, and the days left before it expires;
- **each runner:** stopped, idle, starting, setup, waiting for pickup, busy (with its repo and job),
  cleaning, or broken (with its last error and when it is retried);
- **each repo:** ok, blocked (and why), setup failing, or config invalid; its queued matching jobs;
  its confirmed label sets.

## Stopping

`garage stop` disables and stops the service: `systemctl --user disable --now` on Linux, and on
macOS `launchctl disable` and then `launchctl bootout`, so a login does not bring the agent back.
`garage start` enables it again. On its way down the service deletes registrations no
job has picked up yet, ends running jobs (each fails on GitHub, and `stop` names them), and runs
`cleanup` and `stop` on every runner, all at once, within the 5-minute stop timeout the unit and the
plist set. The service managers' own defaults, 90 seconds for systemd and about 20 for launchd, are
too short for several runners, and what they cut short would be left running, perhaps mid-job.

## When something fails

| What | What happens |
| --- | --- |
| No pickup | A runner registered but not busy after `pickup_deadline_minutes` (another runner took the job, or it was cancelled) is ended: its registration is deleted first, then the runner is stopped and cleaned. GitHub refuses to delete a busy runner, so a job that arrives in that same moment keeps running. |
| Dies mid-job | The runner or its container dies before the job finishes: it is cleaned, its registration deleted, and it is free. The job is never re-run; that is a person's decision, made on GitHub. |
| Recipe or container failure | A non-zero exit from `start` or `cleanup`, or a runner exiting before it picked up a job, marks that runner broken in `status`, with the error, and it is retried after 1, 2, 4 … minutes, at most 30. The other runners carry on. A runner whose `cleanup` failed gets no new job until a retried `cleanup` succeeds, so no job ever starts in a dirty runner. |
| Setup failing | `status` shows "setup failing" with the end of its output. That repo's jobs stay queued, the runner is freed (it is not broken), and setup is retried with the same backoff. |
| Config invalid | A repo whose `.pitcall/config.json` cannot be used shows "config invalid" with the reason: a file that is not UTF-8, is not valid JSON or is nested too deeply, or a `runner` section that breaks the rules (a NUL or a lone surrogate in a value, a value or `setup` over 65536 bytes, the names together over 32768, or names, values and `setup` together over 262144, among them). It gets no runner, so its jobs stay queued, until a fixed `.pitcall/config.json` reaches its default branch. The other repos carry on. |
| Token or repo access | A 401, 403 or 404 for a repo marks it blocked, with the reason; the others carry on, and the repo is checked again with backoff. A missing `.pitcall/config.json` is not an access problem, just a repo with no `runner` section. |
| GitHub errors and rate limits | The service backs off on a 5xx. On a primary rate limit it waits until the `x-ratelimit-reset` time; on a secondary one it honours `retry-after`. |
| Crash or reboot | The service manager restarts the service, which recovers before it polls: it runs `cleanup` on every runner, then deletes every registration in the joined repos whose name starts with `pitcall-<id>-`, since no runner is live right after a start. Cleaning first ends any job the crash orphaned before its registration goes. |

## Security

- The service runs as you, never as root. Root happens only inside a runner, through the recipe;
  with the Docker example a runner's container is privileged, so root there is root on the host.
- A joined repo is trusted like your own code. Runners keep caches between jobs, so a job from one
  repo can read, or tamper with, what an earlier job from another repo left behind. A recipe's
  `cleanup` may wipe more than the minimum.
- Public repos need `--allow-public`, after the fork pull request risk is printed.
- The token file must be readable by its owner alone; the token goes only to `api.github.com`, in a
  header.
- The JIT config appears in no command line and no log.
- A repo's `setup` and `env` come from its default branch only.

## Manual acceptance check

pitcall's CI never touches real runners or writes to GitHub. This check, on a real machine, covers
the rest:

1. Create a fine-grained token and its file (`chmod 600`), copy the example config, pick the Docker
   recipe with 2 runners, and run `garage start`. `garage status` shows the service running and
   both runners stopped.
2. Join two private test repos whose workflows have a `runs-on: [self-hosted, linux]` job that
   sleeps a minute and prints whether `ACTIONS_RUNNER_INPUT_JITCONFIG` is set.
3. Trigger both. Each repo gets a runner, the containers start on demand, and both jobs run.
4. While they run, no command line on the machine (`ps`) and nothing in the service's log shows the
   JIT config. The job output says the variable is not set. The runner was started with no
   `--jitconfig` argument and still registered: this is where the environment-variable route is
   verified.
5. Queue four jobs in one repo and one in the other. The other repo's job gets the next free
   runner.
6. Cancel a run before it is picked up. After the pickup deadline its registration is gone from the
   repo's runner list.
7. Stop a container mid-job. The job fails on GitHub, is not re-run, and the runner is cleaned and
   free.
8. Run a job that starts a service on a fixed port in both runners at once. No collision.
9. Wait out the idle timeout. Both containers stop.
10. Kill the service with `kill -9`, and reboot once. The service comes back, and stale
    registrations are deleted.
11. `chmod 644` the token file. `garage start` refuses and says why.

Repeat steps 1, 3 and 10 on macOS where one is available.
