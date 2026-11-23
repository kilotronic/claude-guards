#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""Print `<plugin>--v<version> <commit>` for each plugin whose current version has no tag yet.

Claude Code resolves a dependency's version constraint (say `work ^1.4.0`) against tags named
`<plugin>--v<version>` in the repository that hosts the plugin. A version with no tag cannot satisfy any
constraint, so a plugin that needs it stays turned off. The commit printed is where the manifest last took
on its current version: the newest commit to the manifest whose parent had another version (or none).
Run from the repository root with its full history and tags.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?")


def _version_at(commit: str, manifest: Path) -> str | None:
    shown = subprocess.run(
        ["git", "show", f"{commit}:{manifest.as_posix()}"],
        check=False,  # a commit that deleted the manifest has no version
        capture_output=True,
        text=True,
    )
    if shown.returncode != 0:
        return None
    try:
        return json.loads(shown.stdout).get("version")
    except ValueError:
        return None


def _landed(manifest: Path, version: str) -> str:
    """The oldest commit in the newest unbroken run of commits whose manifest has this version."""
    landed = ""
    for commit in _git("log", "--format=%H", "--", str(manifest)).splitlines():
        if _version_at(commit, manifest) != version:
            break
        landed = commit
    return landed


def main() -> None:
    tags = set(_git("tag", "--list").splitlines())
    for manifest in sorted(Path("plugins").glob("*/.claude-plugin/plugin.json")):
        plugin = json.loads(manifest.read_text())
        version = str(plugin.get("version", ""))
        if not VERSION.fullmatch(version):
            sys.exit(
                f"{manifest}: version {version!r} is not a version number like 1.4.0"
            )
        tag = f"{plugin['name']}--v{version}"
        if tag in tags:
            continue
        print(f"{tag} {_landed(manifest, version)}")


if __name__ == "__main__":
    main()
