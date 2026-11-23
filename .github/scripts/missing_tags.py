#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""Print `<plugin>--v<version> <commit>` for each plugin whose current version has no tag yet.

Claude Code resolves a dependency's version constraint (say `work ^1.4.0`) against tags named
`<plugin>--v<version>` in the repository that hosts the plugin. A version with no tag cannot satisfy any
constraint, so a plugin that needs it stays turned off. The commit printed is the last one that changed
the version line in the plugin's manifest: where this version landed. Run from the repository root with
its full history and tags.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def main() -> None:
    tags = set(_git("tag", "--list").splitlines())
    for manifest in sorted(Path("plugins").glob("*/.claude-plugin/plugin.json")):
        plugin = json.loads(manifest.read_text())
        tag = f"{plugin['name']}--v{plugin['version']}"
        if tag in tags:
            continue
        landed = _git(
            "log", "-1", "--format=%H", "-G", '"version"', "--", str(manifest)
        )
        print(f"{tag} {landed}")


if __name__ == "__main__":
    main()
