"""Each guard as Claude Code actually runs it: the command line in hooks.json, through a shell.

The per-guard suites call `main()` in-process, so they cannot see a guard that is
unregistered, not executable, has a broken shebang, or drops its exit code.
Claude only blocks a tool call when the hook process exits 2.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "plugins" / "guards"
HOOKS = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]


def _commands(event: str, tool: str) -> dict[str, str]:
    """Script name -> command line, for every hook Claude runs on *event* for *tool*."""
    out: dict[str, str] = {}
    for group in HOOKS.get(event, []):
        if re.fullmatch(group.get("matcher", ".*") or ".*", tool):
            for hook in group["hooks"]:
                name = Path(hook["command"].split()[0].strip('"')).name
                out[name] = hook["command"]
    return out


def _run(
    command: str, payload: dict, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(ROOT), **(env or {})}
    return subprocess.run(
        command,
        shell=True,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def dirty_main(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("one\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "one")
    (repo / "f.txt").write_text("two\n")
    return repo


# One command each guard must refuse in a dirty worktree on main.
BASH_GUARDS = {
    "guard-shared-stash.py": "git stash",
    "guard-hard-reset.py": "git reset --hard HEAD",
    "guard-no-verify.py": "git commit -a --no-verify -m x",
    "guard-commit-on-main.py": "git commit -m x",
    "guard-admin-merge.py": "gh pr merge 1 --admin",
    "guard-github-polling.py": "gh run watch 1",
    "guard-secret-print.py": "cat .env",
    "guard-branch-switch.py": "git checkout -b x",
    "guard-zsh-special-vars.py": "path=x",
    "guard-ours-theirs.py": "git checkout --theirs f.txt",
}


def _dispatcher() -> str:
    """The one Bash command line; it runs every guard in bash-guards.py's GUARDS."""
    commands = _commands("PreToolUse", "Bash")
    assert list(commands) == ["bash-guards.py"], commands
    return commands["bash-guards.py"]


def _listed() -> set[str]:
    text = (ROOT / "hooks" / "bash-guards.py").read_text()
    return set(re.findall(r'"(guard-[a-z-]+\.py)"', text))


@pytest.mark.parametrize("script", sorted(BASH_GUARDS))
def test_every_bash_guard_is_registered_and_blocks_through_its_command_line(
    dirty_main: Path, script: str
) -> None:
    assert script in _listed(), f"{script} is not in bash-guards.py's GUARDS"
    done = _run(
        _dispatcher(),
        {
            "tool_name": "Bash",
            "tool_input": {"command": BASH_GUARDS[script]},
            "cwd": str(dirty_main),
        },
    )
    assert done.returncode == 2, (done.returncode, done.stderr)
    assert done.stderr.startswith("Refusing"), done.stderr


def test_every_bash_guard_allows_a_harmless_command(dirty_main: Path) -> None:
    done = _run(
        _dispatcher(),
        {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "cwd": str(dirty_main),
        },
    )
    assert (done.returncode, done.stderr) == (0, "")


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit", "NotebookEdit"])
def test_the_main_edit_guard_is_registered_for_every_edit_tool_and_blocks(
    dirty_main: Path, tool: str
) -> None:
    commands = _commands("PreToolUse", tool)
    assert "block-main-edits.py" in commands, (
        f"block-main-edits.py is not registered for {tool}"
    )
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    payload = {
        "tool_name": tool,
        "tool_input": {key: str(dirty_main / "f.txt")},
    }
    done = _run(
        commands["block-main-edits.py"],
        payload,
        {"CLAUDE_PROJECT_DIR": str(dirty_main)},
    )
    assert done.returncode == 2, (done.returncode, done.stderr)


def test_the_secret_guard_also_runs_on_read(dirty_main: Path) -> None:
    command = _commands("PreToolUse", "Read")["guard-secret-print.py"]
    payload = {
        "tool_name": "Read",
        "tool_input": {"file_path": str(dirty_main / ".env")},
    }
    assert _run(command, payload).returncode == 2


def test_the_polling_guard_also_runs_on_monitor() -> None:
    command = _commands("PreToolUse", "Monitor")["guard-github-polling.py"]
    payload = {
        "tool_name": "Monitor",
        "tool_input": {"command": "while true; do gh run list; sleep 9; done"},
    }
    assert _run(command, payload).returncode == 2


def test_every_hook_script_is_registered() -> None:
    registered = {
        Path(hook["command"].split()[0].strip('"')).name
        for groups in HOOKS.values()
        for group in groups
        for hook in group["hooks"]
    }
    scripts = {p.name for p in (ROOT / "hooks").glob("*.py") if p.name != "guardlib.py"}
    assert scripts <= registered | _listed(), scripts - registered - _listed()


def test_the_secret_guard_also_runs_on_grep(dirty_main: Path) -> None:
    command = _commands("PreToolUse", "Grep")["guard-secret-print.py"]
    payload = {
        "tool_name": "Grep",
        "tool_input": {
            "pattern": "X",
            "path": str(dirty_main / ".env"),
            "output_mode": "content",
        },
    }
    assert _run(command, payload).returncode == 2


PLUGINS = Path(__file__).resolve().parents[1] / "plugins"


def test_no_hook_times_out_under_ten_seconds() -> None:
    # Under load a dozen interpreters start on one tool call; 5 s timed out
    # allow-merge.py and block-main-edits.py, and each timeout stalls the agent.
    short = [
        f"{path.parent.parent.name}: {hook['command']} ({hook['timeout']} s)"
        for path in sorted(PLUGINS.glob("*/hooks/hooks.json"))
        for groups in json.loads(path.read_text())["hooks"].values()
        for group in groups
        for hook in group["hooks"]
        if hook["timeout"] < 10
    ]
    assert not short, short
