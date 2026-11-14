"""`garage join` and `garage leave`: the checks before a repo shares this machine's runners.

Step 1 of the spec's `garage join` -- the machine config and the token file -- is the CLI's;
everything after it is here, behind an injected GitHub client, so it is tested without a
network. Nothing is written without `yes` (decision 12): Claude runs commands
non-interactively, so a prompt waiting for "y" would hang, and the two-step form reads the
same to a person at a terminal and to Claude relaying the list.
"""

from __future__ import annotations

from pathlib import Path

import garage_config
import garage_github
import garage_runs_on

TRUST_LINE = ("A joined repo is trusted like your own code: its jobs share runners and caches "
              "with every other joined repo's jobs.")
PUBLIC_RISK = ("This repo is public. Anyone can open a pull request from a fork, and depending "
               "on the repo's settings its workflow can run on this machine, inside this network.")
PUBLIC_ADVICE = ("Require approval for workflow runs from outside contributors: the repo's "
                 "Settings > Actions > General > Fork pull request workflows.")

#: What the service needs, keyed by `GitHub.probe`'s names, as the token settings page names it.
PERMISSIONS = {
    "contents": "Contents read",
    "actions": "Actions read",
    "admin-read": "Administration read",
    "admin-write": "Administration write",
}
WORKFLOWS = ".github/workflows"


def advice(repo):
    return (f"Repository access: add {repo}. Repository permissions: Administration read "
            f"and write, Actions read, Contents read")


def parse_labels(arg):
    """`"self-hosted,linux;self-hosted,large"` -> one list per `;`-separated set."""
    sets = []
    for part in arg.split(";"):
        labels = [label.strip() for label in part.split(",") if label.strip()]
        if labels:
            sets.append(labels)
    if not sets:
        raise ValueError(f"--labels {arg!r} names no label set; "
                         f"write e.g. \"self-hosted,linux;self-hosted,large\"")
    return sets


def coverage_message(repo, errors):
    lines = [f"The token cannot do everything the garage needs on {repo}:"]
    for permission, name in PERMISSIONS.items():
        err = errors.get(permission)
        if err is None:
            continue
        wanted = (f"; GitHub names the permission it wanted: {err.accepted_permissions}"
                  if err.accepted_permissions else "")
        lines.append(f"  - {name}: {err}{wanted}")
    lines.append("On the token's GitHub settings page, tick:")
    lines.append(f"  {advice(repo)}")
    return "\n".join(lines)


def discover(api, repo, ref):
    """Label sets that include self-hosted, and unreadable runs-on lines, on `ref`."""
    files = {}
    for name in sorted(api.list_dir(repo, WORKFLOWS, ref) or []):
        if not name.endswith((".yml", ".yaml")):
            continue
        path = f"{WORKFLOWS}/{name}"
        text = api.file_text(repo, path, ref)
        if text is not None:
            files[path] = text
    return garage_runs_on.self_hosted_sets(files)


def run_join(api, repo, config_path, labels_arg, allow_public, yes, out=print):
    try:
        return _run_join(api, repo, config_path, labels_arg, allow_public, yes, out)
    except OSError as exc:  # urllib's URLError (no network, DNS, TLS) or a failed write
        out(f"garage join: {exc}; nothing written.")
        return 1


def _run_join(api, repo, config_path, labels_arg, allow_public, yes, out):
    try:
        info = api.repo_info(repo)
    except garage_github.ApiError as exc:
        out(f"garage join: cannot read {repo} with this token ({exc}).")
        out(advice(repo))
        return 1

    if not info.get("private"):
        out(PUBLIC_RISK)
        if not allow_public:
            out(f"garage join: refusing to join public {repo}; "
                f"pass --allow-public to join it anyway.")
            return 1
        out(PUBLIC_ADVICE)

    errors = {}
    for permission in PERMISSIONS:
        err = api.probe(repo, permission)
        # An empty repo answers 404 for its contents; a token without Contents read gets 403.
        if err is not None and not (permission == "contents" and err.status == 404):
            errors[permission] = err
    if errors:
        out(coverage_message(repo, errors))
        return 1

    if labels_arg is not None:
        try:
            sets, unreadable = parse_labels(labels_arg), []
        except ValueError as exc:
            out(f"garage join: {exc}")
            return 1
        out("Label sets from --labels:")
    else:
        ref = info["default_branch"]
        try:
            sets, unreadable = discover(api, repo, ref)
        except garage_github.ApiError as exc:
            out(f"garage join: cannot read {repo}'s workflows on {ref} ({exc}).")
            return 1
        out(f"Label sets that include self-hosted in {repo}'s workflows on {ref}:")
    for labels in sets:
        out(f"  - {', '.join(labels)}")
    if not sets:
        out("  (none)")
    if unreadable:
        out("runs-on values that could not be read (an expression or a runner group); "
            "name their label sets with --labels to serve them:")
        for where in unreadable:
            out(f"  - {where}")
    out(TRUST_LINE)

    if not yes:
        if sets:
            out(f"Nothing written. Re-run with --yes to join {repo} with these label sets.")
        else:
            out(f"Nothing written. There is no label set to join {repo} with: "
                f"pass --labels \"self-hosted,linux\".")
        return 0
    if not sets:
        out(f"garage join: no label set to join {repo} with; pass --labels \"self-hosted,linux\".")
        return 1
    try:
        garage_config.set_repo(Path(config_path), repo, sets)
    except garage_config.ConfigError as exc:
        out(f"garage join: cannot write {config_path}: {exc}")
        return 1
    out(f"Joined {repo}. Only jobs whose runs-on equals one of these sets are served; "
        f"the service picks this up without a restart.")
    return 0


def run_leave(repo, config_path, out=print):
    try:
        removed = garage_config.remove_repo(Path(config_path), repo)
    except garage_config.ConfigError as exc:
        out(f"garage leave: cannot update {config_path}: {exc}")
        return 1
    if removed:
        out(f"Left {repo}. A job already running for it finishes; nothing new is registered for it.")
    else:
        out(f"{repo} was not joined.")
    return 0
