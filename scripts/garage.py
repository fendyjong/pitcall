#!/usr/bin/env python3
"""The garage's CLI: `start`, `stop`, `status`, `join`, `leave`.

The garage itself -- the service, its scheduling, its recipes -- lives in this plugin's
`garage/` directory. `start` copies that directory to `$XDG_DATA_HOME/pitcall/garage/` and
points the service there (decision 11): a plugin update can remove the versioned directory
it came from, and a service pointing into it would crash-loop. This file is the one garage
file that imports from `scripts/` (`tracker.origin_repo`, for `join` and `leave` run in a
checkout); nothing under `garage/` imports anything outside it.

Every subcommand except `status` refuses without the machine config and says where to copy
it from. Service-manager commands go through an injected `run`, so tests never call
`systemctl`, `launchctl` or `loginctl`.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
GARAGE = PLUGIN / "garage"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(GARAGE))
import tracker  # noqa: E402
import garage_config  # noqa: E402
import garage_github  # noqa: E402
import garage_join  # noqa: E402
import garage_paths  # noqa: E402
import garage_state  # noqa: E402
import garage_units  # noqa: E402

PLATFORMS = ("linux", "darwin")


@dataclass
class Ctx:
    env: dict
    run: Callable
    platform: str
    uid: int


def plugin_version():
    return json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())["version"]


def fail(message):
    print(f"garage: {message}", file=sys.stderr)
    return 1


def unsupported(ctx):
    return fail(f"unsupported platform {ctx.platform!r}: the garage runs on Linux (systemd) "
                f"and macOS (launchd).")


def call(ctx, argv):
    """One service-manager command; 127 when the program is not installed."""
    try:
        return ctx.run(argv, capture_output=True, text=True)
    except FileNotFoundError:
        return subprocess.CompletedProcess(argv, 127, "", f"{argv[0]}: not found")


def call_all(ctx, commands):
    for argv in commands:
        result = call(ctx, argv)
        if result.returncode != 0:
            why = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
            return fail(f"{' '.join(argv)} failed: {why}")
    return 0


def unit_path(ctx):
    if ctx.platform == "linux":
        return garage_units.systemd_unit_path(ctx.env)
    return garage_units.launchd_plist_path(ctx.env)


def service_running(ctx):
    return call(ctx, garage_units.status_command(ctx.platform, ctx.uid)).returncode == 0


def busy_jobs(ctx):
    """`  r1: owner/name job` for each runner the state file says is busy."""
    state = garage_state.read_state(garage_paths.state_file_path(ctx.env)) or {}
    runners = state.get("runners")  # a hand-edited or foreign file can hold anything here
    runners = runners if isinstance(runners, dict) else {}
    return [f"  {name}: {runner.get('repo')} {runner.get('job') or '(job not yet named)'}"
            for name, runner in sorted(runners.items())
            if isinstance(runner, dict) and runner.get("state") == "busy"]


def install(ctx):
    """Put this plugin's `garage/` in the install directory; returns (target, previous).

    The copy is staged beside the installed one and swapped in by renames, so a failure leaves the
    installed code as it was. `previous` keeps the code it replaced until the start succeeds.
    """
    target = garage_paths.install_dir(ctx.env)
    fresh, previous = (target.with_name(target.name + suffix) for suffix in (".new", ".old"))
    for leftover in (fresh, previous):
        shutil.rmtree(leftover, ignore_errors=True)
    try:
        shutil.copytree(GARAGE, fresh, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (fresh / "VERSION").write_text(plugin_version() + "\n")
        if target.exists():
            target.rename(previous)
        fresh.rename(target)
    except OSError:
        shutil.rmtree(fresh, ignore_errors=True)
        put_back(target, previous)
        raise
    return target, previous


def put_back(target, previous):
    """Return the code a failed start replaced; a first install has none to return."""
    if previous.exists():
        shutil.rmtree(target, ignore_errors=True)
        previous.rename(target)


def write_unit(ctx, target):
    entry = target / "garage_service.py"
    path = unit_path(ctx)
    search = ctx.env.get("PATH")  # the recipes find docker or lxc where this shell does
    path.parent.mkdir(parents=True, exist_ok=True)
    if ctx.platform == "linux":
        path.write_text(garage_units.systemd_unit(sys.executable, entry, search))
        return
    log = garage_paths.state_home(ctx.env) / "pitcall" / "garage.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(garage_units.launchd_plist(sys.executable, entry, log, search))


def warn_linger(ctx):
    user = ctx.env.get("USER") or pwd.getpwuid(ctx.uid).pw_name
    result = call(ctx, ["loginctl", "show-user", user, "-p", "Linger"])
    if result.returncode == 0 and garage_units.linger_enabled(result.stdout):
        return
    print(f"garage: lingering is off for {user}, so the service stops when you log out. "
          f"To keep it running: loginctl enable-linger {user}")


def cmd_start(ctx, cfg, config_path):
    if ctx.platform not in PLATFORMS:
        return unsupported(ctx)
    try:
        garage_github.check_token_file(garage_paths.expand(cfg["token_file"], ctx.env))
    except garage_github.TokenFileError as exc:
        return fail(str(exc))
    garage_id = garage_config.ensure_id(config_path)
    running = service_running(ctx)
    try:
        target, previous = install(ctx)
    except OSError as exc:
        return fail(f"cannot install the garage's code: {exc}; the installed code is unchanged.")
    try:
        write_unit(ctx, target)
    except OSError as exc:
        put_back(target, previous)
        return fail(f"cannot write {unit_path(ctx)}: {exc}")
    if running:
        jobs = busy_jobs(ctx)
        print("garage: restarting the running service to pick up the refreshed code"
              + ("; that ends these jobs, which fail on GitHub:" if jobs else "."))
        for line in jobs:
            print(line)
    if call_all(ctx, garage_units.start_commands(ctx.platform, ctx.uid, running, ctx.env)):
        put_back(target, previous)
        return 1
    shutil.rmtree(previous, ignore_errors=True)
    if ctx.platform == "linux":
        warn_linger(ctx)
    print(f"garage: started -- id {garage_id}, {cfg['runners']} runners, code in {target}.")
    return 0


def cmd_stop(ctx):
    if ctx.platform not in PLATFORMS:
        return unsupported(ctx)
    if not unit_path(ctx).exists():
        print("garage: not installed, so there is nothing to stop.")
        return 0
    running = service_running(ctx)
    jobs = busy_jobs(ctx) if running else []  # a stopped service's state file is history
    if jobs:
        print("garage: stopping; these jobs end now and fail on GitHub:")
        for line in jobs:
            print(line)
    if call_all(ctx, garage_units.stop_commands(ctx.platform, ctx.uid, running)):
        return 1
    if not running:
        print("garage: already stopped; the service is disabled, so it stays stopped.")
        return 0
    print("garage: stopped. On its way down the service deleted unclaimed registrations "
          "and cleaned and stopped every runner, keeping their disks.")
    return 0


def service_line(ctx):
    if ctx.platform not in PLATFORMS:
        return f"unsupported platform {ctx.platform!r}"
    if not unit_path(ctx).exists():
        return "not installed (run garage start)"
    state = "running" if service_running(ctx) else "stopped"
    version_file = garage_paths.install_dir(ctx.env) / "VERSION"
    have = version_file.read_text().strip() if version_file.exists() else "none"
    want = plugin_version()
    code = (f"code {want} matches this plugin" if have == want
            else f"installed {have}, plugin {want}: run garage start")
    return f"{state}; {code}"


def token_line(ctx, cfg):
    if cfg is None:
        return "unknown (no machine config)"
    path = garage_paths.expand(cfg["token_file"], ctx.env)
    try:
        garage_github.check_token_file(path)
    except garage_github.TokenFileError as exc:
        return str(exc)
    return f"{path} (readable by its owner only)"


def cmd_status(ctx):
    cfg = None
    try:
        cfg = garage_config.load_machine_config(garage_paths.machine_config_path(ctx.env))
    except garage_config.ConfigError as exc:
        print(f"config: {exc}")
    state = garage_state.read_state(garage_paths.state_file_path(ctx.env))
    print(garage_state.render_status(service_line(ctx), token_line(ctx, cfg), state, time.time()),
          end="")  # render_status ends its last line itself
    return 0


def resolve_repo(args):
    if args.repo:
        return args.repo
    try:
        return tracker.origin_repo()
    except FileNotFoundError:  # no gh on this machine
        raise tracker.TrackerError("gh: not found") from None


def cmd_join(ctx, args, cfg, config_path):
    try:
        token = garage_github.read_token(garage_paths.expand(cfg["token_file"], ctx.env))
    except garage_github.TokenFileError as exc:
        return fail(str(exc))
    try:
        repo = resolve_repo(args)
    except tracker.TrackerError as exc:
        return fail(f"cannot tell which repo this checkout is ({exc}); pass --repo owner/name.")
    return garage_join.run_join(garage_github.GitHub(token), repo, config_path,
                                args.labels, args.allow_public, args.yes)


def cmd_leave(args, config_path):
    try:
        repo = resolve_repo(args)
    except tracker.TrackerError as exc:
        return fail(f"cannot tell which repo this checkout is ({exc}); pass --repo owner/name.")
    return garage_join.run_leave(repo, config_path)


def main(argv=None, env=None, run=subprocess.run, platform=sys.platform, uid=None):
    ap = argparse.ArgumentParser(
        prog="garage", description="One pool of self-hosted runners serving every joined repo.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start", help="install the service's code, then enable and start it")
    sub.add_parser("stop", help="end running jobs, clean and stop every runner, "
                                "disable the service")
    sub.add_parser("status", help="the service, the token, each runner and each repo")
    p_join = sub.add_parser("join", help="show what joining a repo means; --yes writes it")
    p_join.add_argument("--repo", help="owner/name (default: this checkout's GitHub repo)")
    p_join.add_argument("--labels", help="label sets replacing the found ones: "
                                         '"self-hosted,linux;self-hosted,large"')
    p_join.add_argument("--allow-public", action="store_true",
                        help="join a public repo despite the fork pull request risk")
    p_join.add_argument("--yes", action="store_true", help="write the repo into the machine config")
    p_leave = sub.add_parser("leave", help="remove a repo from the machine config")
    p_leave.add_argument("--repo", help="owner/name (default: this checkout's GitHub repo)")
    args = ap.parse_args(argv)

    ctx = Ctx(os.environ if env is None else env, run, platform,
              os.getuid() if uid is None else uid)
    if args.cmd == "status":
        return cmd_status(ctx)
    config_path = garage_paths.machine_config_path(ctx.env)
    try:
        cfg = garage_config.load_machine_config(config_path)
    except garage_config.ConfigError as exc:
        return fail(str(exc))
    if args.cmd == "start":
        return cmd_start(ctx, cfg, config_path)
    if args.cmd == "stop":
        return cmd_stop(ctx)
    if args.cmd == "join":
        return cmd_join(ctx, args, cfg, config_path)
    return cmd_leave(args, config_path)


if __name__ == "__main__":
    sys.exit(main())
