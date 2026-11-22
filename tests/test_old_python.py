# SPDX-License-Identifier: MIT

"""The hooks run under whatever `python3` is first on PATH, which can be macOS's own 3.9."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from hookrun import HOOKS

SCRIPTS = sorted(p.name for p in HOOKS.glob("*.py"))


@pytest.mark.parametrize("script", SCRIPTS)
def test_every_hook_defers_annotations_so_3_9_can_load_it(script: str) -> None:
    # `str | None` in a signature raises TypeError on 3.9 unless annotations are deferred.
    assert "from __future__ import annotations" in (HOOKS / script).read_text()


def _python39() -> str | None:
    for name in ("python3.9", "/usr/bin/python3"):
        path = shutil.which(name)
        if path:
            done = subprocess.run(
                [path, "-c", "import sys; print(sys.version_info[:2] == (3, 9))"],
                capture_output=True,
                text=True,
                check=False,
            )
            if done.stdout.strip() == "True":
                return path
    return None


def test_the_commit_on_main_guards_refuse_under_3_9(tmp_path: Path) -> None:
    python = _python39()
    if python is None:
        pytest.skip("no Python 3.9 on this machine; the annotations test covers it")
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@e",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
        check=True,
    )
    for script, command in [
        ("guard-commit-on-main.py", "git commit -m x"),
        ("guard-shared-stash.py", "git stash"),
        ("guard-hard-reset.py", "git reset --hard HEAD"),
    ]:
        event = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(tmp_path),
        }
        done = subprocess.run(
            [python, str(HOOKS / script)],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 2, (script, done.stderr)
