# SPDX-License-Identifier: MIT

"""Run a hook script the way Claude Code does: a JSON event on stdin, the exit code and stderr back."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "plugins" / "guards" / "hooks"


def run_hook(
    script: str, event: dict, env: dict[str, str] | None = None
) -> tuple[int, str]:
    done = subprocess.run(
        [sys.executable, str(HOOKS / script)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        check=False,
        timeout=60,
    )
    return done.returncode, done.stderr


def bash(
    script: str, command: str, cwd: str | Path | None = None, tool: str = "Bash"
) -> tuple[int, str]:
    event = {"tool_name": tool, "tool_input": {"command": command}}
    if cwd is not None:
        event["cwd"] = str(cwd)
    return run_hook(script, event)


def turn_off(
    repo: str | Path,
    rule: str,
    reason: str | None = "a reason",
    key: str = "octo/sample",
) -> None:
    """Give *repo* the GitHub `origin` *key*, and turn *rule* off for it in the overrides file."""
    remote = f"git@github.com:{key}.git"
    added = subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", remote],
        capture_output=True,
        check=False,
    )
    if added.returncode != 0:
        subprocess.run(
            ["git", "-C", str(repo), "remote", "set-url", "origin", remote],
            capture_output=True,
            check=True,
        )
    path = Path(os.environ["CLAUDE_PLUGIN_ROOT"]) / "overrides.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data.setdefault(key, {})[rule] = {} if reason is None else {"reason": reason}
    path.write_text(json.dumps(data))
