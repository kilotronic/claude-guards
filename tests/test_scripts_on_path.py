# SPDX-License-Identifier: MIT

"""The SessionStart hook that puts the work plugin's scripts/ on the Bash tool's PATH.

Claude Code runs the export lines a SessionStart hook appends to $CLAUDE_ENV_FILE before each later Bash
command (https://code.claude.com/docs/en/hooks#persist-environment-variables), so bare `next-item` and
`park` work again without a top-level bin/. The env file here is a temp file, sourced by bash as Claude Code
does.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

WORK = Path(__file__).resolve().parents[1] / "plugins" / "work"
SCRIPTS = WORK / "scripts"
LINE = f'export PATH="{SCRIPTS}:$PATH"'


def _command() -> str:
    hooks = json.loads((WORK / "hooks" / "hooks.json").read_text())["hooks"]
    found = [
        (group.get("matcher"), hook["command"])
        for group in hooks["SessionStart"]
        for hook in group["hooks"]
        if "scripts-on-path.py" in hook["command"]
    ]
    assert len(found) == 1, found
    matcher, command = found[0]
    # Every source: startup, resume, clear, compact and fork each start with a fresh shell environment.
    assert matcher in (None, "", "*"), matcher
    return command


def _run(env: dict[str, str], source: str = "startup") -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if k != "CLAUDE_ENV_FILE"}
    return subprocess.run(
        _command(),
        shell=True,
        input=json.dumps({"hook_event_name": "SessionStart", "source": source}),
        capture_output=True,
        text=True,
        env={**base, "CLAUDE_PLUGIN_ROOT": str(WORK), **env},
        check=False,
    )


def test_appends_the_scripts_dir_to_path(tmp_path: Path) -> None:
    env_file = tmp_path / "env.sh"
    env_file.write_text("export OTHER=1\n")
    done = _run({"CLAUDE_ENV_FILE": str(env_file)})
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""
    assert env_file.read_text() == f"export OTHER=1\n{LINE}\n"


def test_bash_then_finds_the_bare_commands(tmp_path: Path) -> None:
    env_file = tmp_path / "env.sh"
    assert _run({"CLAUDE_ENV_FILE": str(env_file)}).returncode == 0
    found = subprocess.run(
        ["bash", "-c", f'. "{env_file}" && command -v next-item && command -v park'],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        check=True,
    )
    assert found.stdout.split() == [str(SCRIPTS / "next-item"), str(SCRIPTS / "park")]


def test_no_duplicate_on_resume_or_compact(tmp_path: Path) -> None:
    env_file = tmp_path / "env.sh"
    for source in ("startup", "resume", "compact", "clear"):
        assert _run({"CLAUDE_ENV_FILE": str(env_file)}, source).returncode == 0
    assert env_file.read_text().count(LINE) == 1


def test_adds_a_newline_before_its_line(tmp_path: Path) -> None:
    env_file = tmp_path / "env.sh"
    env_file.write_text("export OTHER=1")
    assert _run({"CLAUDE_ENV_FILE": str(env_file)}).returncode == 0
    assert env_file.read_text() == f"export OTHER=1\n{LINE}\n"


def test_does_nothing_without_an_env_file(tmp_path: Path) -> None:
    for env in ({}, {"CLAUDE_ENV_FILE": ""}):
        done = _run(env)
        assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


def test_never_fails_the_session(tmp_path: Path) -> None:
    for target in (tmp_path / "missing" / "env.sh", tmp_path):
        done = _run({"CLAUDE_ENV_FILE": str(target)})
        assert (done.returncode, done.stdout) == (0, ""), done.stderr


def test_only_the_commands_become_bare_names() -> None:
    """Everything executable in scripts/ is callable by bare name, so nothing else may live there."""
    runnable = sorted(
        p.name
        for p in SCRIPTS.iterdir()
        if p.is_file()
        and p.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    )
    assert runnable == ["next-item", "park", "park.py"]
