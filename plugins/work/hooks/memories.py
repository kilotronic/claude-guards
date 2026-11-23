#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""Offer to share what Claude saves to its memory, so team knowledge reaches the files every session reads.

Two hooks, both silent unless they have something to offer, and never failing the session:

- `after-write` (after Write or Edit): when the file written is a memory that is neither personal nor already
  decided, Claude is told to end its reply with a one-line offer: `/add-knowledge`.
- `sweep` (at session start, at most once a day): lists memories still undecided, and those whose sharing pull
  request may have merged (their memory can then go).

A memory is a file `~/.claude/projects/<folder>/memory/<name>.md` (not the MEMORY.md index). `shared:` in its
frontmatter records the decision: `keep` (personal, never offered again) or the URL of the pull request that
shared it. `$WORK_MEMORY_MATCH`, a regular expression (case ignored), limits both hooks to memories whose folder
name or text matches it, for example a team's repo names; unset, every project memory counts, and one that
doesn't compile turns the hooks off. Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

DAY = (
    20 * 3600
)  # "once a day": a session the next morning counts, even if it starts a little earlier
SHOWN = 9


def projects_dir() -> Path:
    return Path(
        os.environ.get("WORK_MEMORY_PROJECTS") or Path.home() / ".claude" / "projects"
    )


def stamp_file() -> Path:
    data = os.environ.get("CLAUDE_PLUGIN_DATA") or str(
        Path.home() / ".claude" / "plugins" / "data" / "work"
    )
    return Path(data) / "memory-sweep-last-run"


def fields(path: Path) -> dict:
    """The frontmatter's name, type and shared (only those present)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    match = re.match(r"---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}
    found = {}
    for key in ("name", "type", "shared"):
        value = re.search(rf"^\s*{key}:\s*(.+?)\s*$", match.group(1), re.MULTILINE)
        if value:
            found[key] = value.group(1).strip("'\"")
    return found


def is_memory(path: Path, projects: Path) -> bool:
    try:
        relative = path.resolve().relative_to(projects.resolve())
    except ValueError:
        return False
    parts = relative.parts
    return (
        len(parts) == 3
        and parts[1] == "memory"
        and path.suffix == ".md"
        and path.name != "MEMORY.md"
    )


def matches(path: Path) -> bool:
    """Whether the memory is in scope: no setting means every memory; a broken setting means none."""
    pattern = os.environ.get("WORK_MEMORY_MATCH")
    if not pattern:
        return True
    try:
        wanted = re.compile(pattern, re.IGNORECASE)
        folder = path.parent.parent.name
        return bool(
            wanted.search(folder) or wanted.search(path.read_text(encoding="utf-8"))
        )
    except (re.error, OSError):
        return False


def offerable(path: Path, projects: Path) -> bool:
    """A memory in scope that is not personal and not yet decided."""
    if not is_memory(path, projects) or not matches(path):
        return False
    found = fields(path)
    return found.get("type") != "user" and not found.get("shared")


def after_write(event: dict, projects: Path) -> dict | None:
    file_path = (event.get("tool_input") or {}).get("file_path")
    if not file_path:
        return None
    path = Path(file_path)
    if not offerable(path, projects):
        return None
    name = path.stem
    context = (
        f"You just saved the memory `{name}` ({path}). If it is a team or project rule, fact or how-to rather "
        "than something personal, and the repo's CLAUDE.md or the team's shared rules do not already say it, end "
        "your reply with exactly one line, and do not stop or ask about it otherwise: "
        '"Worth sharing with the team? `/add-knowledge` (or `/add-knowledge keep` to keep it personal)". '
        "If it is clearly personal, say nothing about it."
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": context,
        }
    }


def sweep(projects: Path, stamp: Path, now: float) -> str:
    """Once a day: the undecided memories and the ones whose pull request may have merged. Empty if none."""
    try:
        if now - float(stamp.read_text().strip()) < DAY:
            return ""
    except (OSError, ValueError):
        pass
    undecided, sent = [], []
    for path in sorted(projects.glob("*/memory/*.md")):
        if not is_memory(path, projects) or not matches(path):
            continue
        found = fields(path)
        if found.get("type") == "user":
            continue
        shared = found.get("shared", "")
        if not shared:
            undecided.append(path)
        elif shared.startswith("http"):
            sent.append((path, shared))
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(now))
    except OSError:
        pass
    if not undecided and not sent:
        return ""
    lines = [
        "Memory check (work plugin, once a day). Do not interrupt the person's request for this;"
    ]
    if undecided:
        names = ", ".join(f"`{p.stem}`" for p in undecided[:SHOWN])
        more = f" and {len(undecided) - SHOWN} more" if len(undecided) > SHOWN else ""
        lines.append(
            f"- {len(undecided)} memories not yet shared or kept: {names}{more}. At the end of your first reply, "
            'add one line: "Memories waiting to be shared with the team: … — `/add-knowledge` to share them '
            '(or `/add-knowledge keep`)". Skip any that are clearly personal.'
        )
    if sent:
        listed = "; ".join(f"`{p}` ({url})" for p, url in sent[:SHOWN])
        more = f" and {len(sent) - SHOWN} more" if len(sent) > SHOWN else ""
        lines.append(
            f"- Memories already sent as pull requests: {listed}{more}. When convenient, check each pull request once "
            "(`gh pr view <url> --json state`); if MERGED, delete that memory file and its line in its "
            "MEMORY.md, since the fact now lives in the shared file. If CLOSED unmerged, remove its `shared:` "
            "line so it is offered again."
        )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    try:
        if argv[:1] == ["after-write"]:
            out = after_write(json.load(sys.stdin), projects_dir())
            if out:
                print(json.dumps(out))
        elif argv[:1] == ["sweep"]:
            text = sweep(projects_dir(), stamp_file(), time.time())
            if text:
                print(text)
    except Exception:  # noqa: BLE001, S110 - a hook must never break the session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
