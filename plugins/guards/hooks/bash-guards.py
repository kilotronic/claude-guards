#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash hook: every Bash guard in one process, in order (docs/design/2026-11-26-bash-guard-dispatcher.md).

Each guard stays its own module and runs through its own entry point, with the event on a stand-in stdin and
its stderr kept: its refusal text, label and overrides are its own. Every guard runs; the refusals are joined
and the call exits 2. A guard that raises fails open alone.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guardlib

# The order hooks.json ran them in.
GUARDS = (
    "guard-shared-stash.py",
    "guard-hard-reset.py",
    "guard-no-verify.py",
    "guard-commit-on-main.py",
    "guard-admin-merge.py",
    "guard-branch-switch.py",
    "guard-zsh-special-vars.py",
    "guard-ours-theirs.py",
    "guard-github-polling.py",
    "guard-secret-print.py",
)


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        name[:-3].replace("-", "_"), os.path.join(HERE, name)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_one(name: str, event: str) -> tuple[int, str]:
    """(The guard's exit code, what it wrote to stderr); a crash is (1, its traceback), as when it runs alone."""
    err = io.StringIO()
    try:
        module = load(name)
        entry = getattr(module, "main", None) or (
            lambda: guardlib.run(module.RULE, module.refusal)
        )
        sys.stdin = io.StringIO(event)
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = entry()
    except SystemExit as stop:
        code = stop.code
    except Exception:  # noqa: BLE001 - a broken guard must not block work, nor stop the others
        return 1, traceback.format_exc()
    finally:
        sys.stdin = sys.__stdin__
    return (code, err.getvalue()) if code in (1, 2) else (0, "")


def main() -> int:
    try:
        event = sys.stdin.read()
    except Exception:  # noqa: BLE001
        return 0
    results = [run_one(g, event) for g in GUARDS]
    # A refusal blocks; else a crash exits 1, which Claude Code shows as a hook error and lets the call through.
    for want in (2, 1):
        texts = [text for code, text in results if code == want]
        if texts:
            sys.stderr.write("\n".join(t.rstrip("\n") + "\n" for t in texts))
            return want
    return 0


if __name__ == "__main__":
    sys.exit(main())
