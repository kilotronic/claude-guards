#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Park work at a usage limit on its pull request, and resume it from any machine.

`park.py hook` (the StopFailure hook on a usage limit): commits the changes to files git already tracks on the
work's branch as a work-in-progress commit (new files stay put, so nothing unreviewed is published), pushes it, adds the `parked` label to the branch's pull request and comments with the
machine, the session id and the reason. `park.py notice` (session start): one line per open pull request
labeled `parked`. `park resume <n>`: takes the label off, so the work is yours, and says where it is. Taking a
label off is not atomic, so two sessions can rarely both take one. Standard library only; the hook never fails.
"""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
import time

LABEL = "parked"
AGENT = "🤖 **Agent update** — Claude Code"
# Seconds for all of one run's git and gh calls together; each hook's limit in hooks.json is longer.
DEADLINE = 50
_END = time.monotonic() + DEADLINE
NOTICE_DEADLINE = 8


def quiet_env() -> dict[str, str]:
    """git and gh never stop to ask for a password or passphrase: no one is there to answer."""
    return {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": "ssh -o BatchMode=yes",
        "GH_PROMPT_DISABLED": "1",
    }


def _run(args: list[str], cwd: str) -> tuple[int, str]:
    left = _END - time.monotonic()
    if left <= 1:
        return 1, ""
    try:
        done = subprocess.run(
            args,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=left,
            check=False,
            env=quiet_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return done.returncode, done.stdout


def _out(run, args: list[str], cwd: str) -> str | None:
    code, out = run(args, cwd)
    return out.strip() if code == 0 else None


def park(hook: dict, run, host: str) -> str:
    if hook.get("agent_id"):
        return ""  # a helper agent's stop; its parent decides
    top = _out(run, ["git", "rev-parse", "--show-toplevel"], hook.get("cwd") or ".")
    if not top:
        return ""
    branch = _out(run, ["git", "branch", "--show-current"], top)
    default = (
        _out(run, ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], top)
        or "origin/main"
    ).split("/", 1)[-1]
    if not branch or branch == default:
        return f"park: nothing to park on {branch or 'a detached HEAD'}"
    for ref in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "REBASE_HEAD"):
        if run(["git", "rev-parse", "-q", "--verify", ref], top)[0] == 0:
            return f"park: a {ref.split('_')[0].lower()} is in progress on {branch}; nothing touched"
    status = _out(run, ["git", "status", "--porcelain"], top) or ""
    untracked = sum(1 for ln in status.splitlines() if ln.startswith("??"))
    if any(not ln.startswith("??") for ln in status.splitlines() if ln.strip()):
        run(["git", "add", "-u"], top)
        # The gates cannot run in a stop hook: the next session fixes the commit or folds it in.
        run(["git", "commit", "--no-verify", "-m", "wip: parked at a usage limit"], top)
    pushed = run(["git", "push", "origin", "HEAD"], top)[0] == 0
    found = _out(run, ["gh", "pr", "view", "--json", "number,url,state"], top)
    try:
        pr = json.loads(found) if found else {}
        number = str(pr["number"]) if pr.get("state") == "OPEN" else None
    except (ValueError, KeyError, TypeError, AttributeError):
        number = None
    where = "pushed" if pushed else f"not pushed: still only on {host}"
    if untracked:
        where += f"; {untracked} untracked file{'s' if untracked > 1 else ''} not saved, still only on {host}"
    if not number:
        return f"park: {branch} {where}; no open pull request to label"
    edit = ["gh", "pr", "edit", number, "--add-label", LABEL]
    if run(edit, top)[0] != 0:
        run(["gh", "label", "create", LABEL, "--color", "fbca04"], top)
        run(edit, top)
    raw = hook.get("error_details") or hook.get("error") or "usage limit"
    reason = (str(raw).strip().splitlines() or ["usage limit"])[0][:200]
    session = hook.get("session_id") or "unknown"
    body = (
        f"{AGENT} · session `{session[:8]}` · {host}\n\n"
        f"Parked at a usage limit: {reason}\n\n"
        f"- Branch `{branch}`, {where}.\n"
        f"- Same machine: `claude --resume {session}` in the work's folder.\n"
        f"- Any machine: `park resume {number}`, then work on `{branch}`."
    )
    run(["gh", "pr", "comment", number, "--body", body], top)
    return f"park: #{number} labeled {LABEL}; {where}"


def notice(cwd: str, run) -> str:
    fields = "number,title,headRefName"
    found = _out(
        run,
        ["gh", "pr", "list", "--label", LABEL, "--state", "open", "--json", fields],
        cwd,
    )
    try:
        prs = json.loads(found) if found else []
    except ValueError:
        return ""
    if not prs:
        return ""
    lines = ["Parked work (take one with `park resume <n>`):"]
    lines += [f"  #{p['number']} {p['title']} ({p['headRefName']})" for p in prs]
    return "\n".join(lines)


def resume(number: str, cwd: str, session: str, host: str, run) -> tuple[int, str]:
    fields = "headRefName,labels,state,isCrossRepository"
    found = _out(run, ["gh", "pr", "view", number, "--json", fields], cwd)
    try:
        pr = json.loads(found) if found else None
    except ValueError:
        pr = None
    if not pr:
        return 1, f"park: could not read pull request {number}"
    if pr.get("state") != "OPEN" or pr.get("isCrossRepository") is not False:
        return 1, f"park: #{number} is not an open pull request from this repo"
    if LABEL not in [x.get("name") for x in pr.get("labels") or []]:
        return 1, f"park: #{number} is not parked (someone may have taken it)"
    branch = pr["headRefName"]
    run(["gh", "pr", "edit", number, "--remove-label", LABEL], cwd)
    body = f"{AGENT} · session `{session[:8]}` · {host}\n\nResumed here."
    run(["gh", "pr", "comment", number, "--body", body], cwd)
    slug = shlex.quote("../" + branch.replace("/", "-"))
    name = shlex.quote(branch)
    return 0, (
        f"park: #{number} is yours. Its work is on `{branch}`:\n"
        f"  git fetch origin {name} && git worktree add {slug} {name}"
    )


def machine() -> str:
    """This machine's name for comments: $GOAL_MACHINE (as `goal` uses it; set it to a name you are happy to
    publish in a public repo), else the host name."""
    return os.environ.get("GOAL_MACHINE") or socket.gethostname().split(".")[0]


def main(argv: list[str]) -> int:
    host = machine()
    if argv[:1] == ["hook"]:
        try:
            hook = json.loads(sys.stdin.read() or "{}")
            text = park(hook if isinstance(hook, dict) else {}, _run, host)
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
        ) as e:  # never fail the stop
            text = f"park: failed ({e})"
        if text:
            print(text)
        return 0
    if argv[:1] == ["notice"]:
        global _END
        _END = (
            time.monotonic() + NOTICE_DEADLINE
        )  # inside the start-up hook's 10 s limit
        text = notice(".", _run)
        if text:
            print(text)
        return 0
    if len(argv) == 2 and argv[0] == "resume":
        session = os.environ.get("CLAUDE_CODE_SESSION_ID", "unknown")
        code, text = resume(argv[1], ".", session, host, _run)
        print(text, file=sys.stderr if code else sys.stdout)
        return code
    print("usage: park resume <pull request number>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
