#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""Put the plugin's scripts/ on the Bash tool's PATH, so bare `next-item` and `park` run.

A SessionStart hook: Claude Code runs the lines appended to `$CLAUDE_ENV_FILE` before each later Bash command
(https://code.claude.com/docs/en/hooks#persist-environment-variables). The line is added once, so a resume or
a compaction that reuses the file adds no second entry. Without the variable it does nothing, and it never
fails the session. Standard library only.
"""

from __future__ import annotations

import os
from pathlib import Path


def export_line(scripts: str) -> str:
    quoted = "".join("\\" + c if c in '"$`\\' else c for c in scripts)
    return f'export PATH="{quoted}:$PATH"'


def main() -> None:
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if not env_file:
        return
    scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    # A colon would split PATH and a newline the line; no quoting helps either.
    if ":" in scripts or "\n" in scripts:
        return
    line = os.fsencode(export_line(scripts))
    path = Path(env_file)
    text = path.read_bytes() if path.exists() else b""
    if line in text.splitlines():
        return
    with path.open("ab") as out:
        out.write((b"\n" if text and not text.endswith(b"\n") else b"") + line + b"\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001, S110 - a hook must never break the session
        pass
