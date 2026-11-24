#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""Offer to share what Claude saves to its memory, so team knowledge reaches the files every session reads.

Two hooks, both silent unless they have something to offer, and never failing the session:

- `after-write` (after Write or Edit): when the file written is a memory that is neither personal nor already
  decided, Claude is told to end its reply with a one-line offer: `/add-knowledge`.
- `sweep` (at session start, at most once a day): lists memories still undecided, and those whose sharing pull
  request may have merged (their memory can then go).

`list` (run by the add-knowledge skill, not a hook) prints every undecided memory, one per line with its summary.

A memory is a file `~/.claude/projects/<folder>/memory/<name>.md` (not the MEMORY.md index). `shared:` in its
frontmatter records the decision: `keep` (personal, never offered again) or the URL of the pull request that
shared it. `$WORK_MEMORY_MATCH`, a regular expression (case ignored), limits both hooks to memories whose folder
name or text matches it, for example a team's repo names. Unset, the lists other plugins write in
`~/.config/claude-guards/memory-match/` (one file per plugin, a pattern per line, `#` for comments) are used
together; with neither, every project memory counts. A setting that doesn't compile turns the hooks off; a
plugin's line that doesn't compile is skipped, and when none compiles the hooks are off.
Standard library only.
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
SUMMARY = 160  # characters of a memory's summary shown in a list


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
    """The frontmatter's name, type, shared and description (only those present)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    match = re.match(r"---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}
    found = {}
    for key in ("name", "type", "shared"):
        value = re.search(
            rf"^[ \t]*{key}:[ \t]*(\S.*?)[ \t]*$", match.group(1), re.MULTILINE
        )
        if value:
            found[key] = value.group(1).strip("'\"")
    # A top-level description, on its line or folded onto the indented lines after `>-` or `|`.
    value = re.search(
        r"^description:[ \t]*(.*)\n((?:[ \t]+\S.*(?:\n|$))*)",
        match.group(1),
        re.MULTILINE,
    )
    if value:
        head = value.group(1).strip()
        words = ([] if head[:1] in (">", "|") else [head]) + value.group(2).split()
        found["description"] = " ".join(" ".join(words).split()).strip("'\"")
    return found


def summary(path: Path) -> str:
    """One line on what the memory says: its description, else the first line of its text."""
    text = fields(path).get("description", "")
    if not text:
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            body = ""
        body = re.sub(r"\A---\n.*?\n---\n", "", body, flags=re.DOTALL)
        text = next((line.strip() for line in body.splitlines() if line.strip()), "")
    return text if len(text) <= SUMMARY else text[: SUMMARY - 1].rstrip() + "…"


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


def plugin_lists() -> list[str]:
    """The patterns other plugins supplied, a line each; an unreadable file counts as one broken pattern."""
    folder = Path.home() / ".config" / "claude-guards" / "memory-match"
    patterns = []
    for path in sorted(folder.glob("*")):
        if path.name.startswith(".") or not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, ValueError):
            patterns.append("(")
            continue
        patterns += [
            line.strip()
            for line in lines
            if line.strip() and not line.strip().startswith("#")
        ]
    return patterns


def wanted() -> list[re.Pattern] | None:
    """The patterns in force: None for every memory, an empty list for none.

    The setting wins; a broken setting means none. Each plugin line is compiled on its own and a broken one is
    skipped, so one plugin's mistake doesn't turn off the others; when every line is broken, none.
    """
    setting = os.environ.get("WORK_MEMORY_MATCH")
    patterns = [setting] if setting else plugin_lists()
    if not patterns:
        return None
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            pass
    return compiled


def matches(path: Path, patterns: list[re.Pattern] | None) -> bool:
    """Whether the memory is in scope under `wanted()`'s patterns: its folder name or text matches one."""
    try:
        if patterns is None:
            return True
        folder = path.parent.parent.name
        text = path.read_text(encoding="utf-8") if patterns else ""
        return any(p.search(folder) or p.search(text) for p in patterns)
    except (OSError, ValueError):
        return False


def offerable(path: Path, projects: Path) -> bool:
    """A memory in scope that is not personal and not yet decided."""
    if not is_memory(path, projects):
        return False
    try:
        patterns = wanted()
    except (OSError, ValueError):
        return False
    if not matches(path, patterns):
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


def waiting(projects: Path) -> tuple[list[Path], list[tuple[Path, str]]]:
    """In scope and not `type: user`: the undecided memories, and the sent ones with their pull request."""
    undecided, sent = [], []
    try:
        patterns = wanted()
    except (OSError, ValueError):
        patterns = []
    for path in sorted(projects.glob("*/memory/*.md")):
        if not is_memory(path, projects) or not matches(path, patterns):
            continue
        found = fields(path)
        if found.get("type") == "user":
            continue
        shared = found.get("shared", "")
        if not shared:
            undecided.append(path)
        elif shared.startswith("http"):
            sent.append((path, shared))
    return undecided, sent


def listing(projects: Path, paths: list[Path] | None = None) -> list[str]:
    """The undecided memories (or `paths`), one line each: name and summary."""
    if paths is None:
        paths = waiting(projects)[0]
    return [f"- `{p.stem}`: {summary(p)}" for p in paths]


def sweep(projects: Path, stamp: Path, now: float) -> str:
    """Once a day: the undecided memories and the ones whose pull request may have merged. Empty if none."""
    try:
        if now - float(stamp.read_text().strip()) < DAY:
            return ""
    except (OSError, ValueError):
        pass
    undecided, sent = waiting(projects)
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
        more = f" (and {len(undecided) - SHOWN} more)" if len(undecided) > SHOWN else ""
        lines.append(
            f"- {len(undecided)} memories not yet shared or kept{more}. At the end of your first reply, add "
            '"Memories waiting to be shared with the team (`/add-knowledge` to share them, `/add-knowledge keep` '
            'to keep them personal, `/add-knowledge list` for all of them):" and then these, one per line as '
            "given, leaving out any that are clearly personal:"
        )
        lines += ["  " + line for line in listing(projects, undecided[:SHOWN])]
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
        elif argv[:1] == ["list"]:
            lines = listing(projects_dir())
            print(
                "\n".join(lines)
                if lines
                else "No memories are waiting to be shared or kept."
            )
        elif argv[:1] == ["sweep"]:
            text = sweep(projects_dir(), stamp_file(), time.time())
            if text:
                print(text)
    except Exception:  # noqa: BLE001, S110 - a hook must never break the session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
