#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash hook: the work plugin's Bash hooks in one process (docs/design/2026-11-26-bash-guard-dispatcher.md).

Each hook stays its own module; this calls each one's `decide(event)` and merges their answers into one: the
strictest permission decision with its reason, and every warning. A hook that raises leaves the others' answers.
Standard library only; never fails the call.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = ("allow-merge.py", "review-check.py")
# The strictest decision wins.
STRICTNESS = ("allow", "ask", "deny")


def answer(name: str, event: dict) -> dict | None:
    try:
        spec = importlib.util.spec_from_file_location(
            name[:-3].replace("-", "_"), os.path.join(HERE, name)
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        found = module.decide(event)
    except Exception:  # noqa: BLE001 - a broken hook leaves the others' answers
        sys.stderr.write(traceback.format_exc())
        return None
    return found if isinstance(found, dict) else None


def merge(answers: list[dict]) -> dict | None:
    if not answers:
        return None
    if len(answers) == 1:
        return answers[0]
    specific: dict = {"hookEventName": "PreToolUse"}
    messages, context, decision = [], [], None
    for found in answers:
        if found.get("systemMessage"):
            messages.append(found["systemMessage"])
        inner = found.get("hookSpecificOutput") or {}
        if inner.get("additionalContext"):
            context.append(inner["additionalContext"])
        mine = inner.get("permissionDecision")
        if mine in STRICTNESS and (
            decision is None or STRICTNESS.index(mine) > STRICTNESS.index(decision)
        ):
            decision = mine
            specific["permissionDecision"] = mine
            specific["permissionDecisionReason"] = inner.get(
                "permissionDecisionReason", ""
            )
    if context:
        specific["additionalContext"] = "\n".join(context)
    merged: dict = {"hookSpecificOutput": specific}
    if messages:
        merged["systemMessage"] = "\n".join(messages)
    return merged


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 - bad input (even nesting deep enough to recurse) never fails the call
        return 0
    if not isinstance(event, dict):
        return 0
    crashed = io.StringIO()
    with contextlib.redirect_stderr(crashed):
        merged = merge([a for a in (answer(h, event) for h in HOOKS) if a])
    if merged:
        print(json.dumps(merged))
        return 0
    # With no answer to give, a crash exits 1: Claude Code shows it as a hook error and lets the call through.
    sys.stderr.write(crashed.getvalue())
    return 1 if crashed.getvalue() else 0


if __name__ == "__main__":
    sys.exit(main())
