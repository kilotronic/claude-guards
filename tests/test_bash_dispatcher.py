# SPDX-License-Identifier: MIT

"""Each plugin's Bash hooks in one process (docs/design/2026-11-26-bash-guard-dispatcher.md).

A Bash call used to start one interpreter per hook, about a dozen, and under load the slowest timed out.
Each plugin now registers one PreToolUse entry for Bash, a dispatcher that runs its hooks in order in one
process. These tests run the dispatchers through their hooks.json command lines and check that what a
guard says, and when it fails open, is the same as when it runs alone.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
GUARDS = PLUGINS / "guards"
WORK = PLUGINS / "work"


def _bash_entries(plugin: Path) -> list[str]:
    hooks = json.loads((plugin / "hooks" / "hooks.json").read_text())["hooks"]
    return [
        hook["command"]
        for group in hooks.get("PreToolUse", [])
        if re.fullmatch(group.get("matcher", ".*") or ".*", "Bash")
        for hook in group["hooks"]
    ]


def _run(
    plugin: Path, command: str, event: dict, root: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        shell=True,
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(root or plugin)},
        check=False,
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
        + ["-c", "core.hooksPath=/dev/null", *args],
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


@pytest.mark.parametrize("plugin", [GUARDS, WORK], ids=lambda p: p.name)
def test_each_plugin_starts_one_process_on_a_bash_call(plugin: Path) -> None:
    entries = _bash_entries(plugin)
    assert len(entries) == 1, entries


def _dispatcher_module(name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), GUARDS / "hooks" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_dispatcher_lists_every_bash_guard() -> None:
    listed = set(_dispatcher_module("bash-guards").GUARDS)
    assert listed == {p.name for p in (GUARDS / "hooks").glob("guard-*.py")}


# One command each guard refuses in a dirty worktree on main, and nothing else does.
ALONE = {
    "guard-shared-stash.py": "git stash",
    "guard-admin-merge.py": "gh pr merge 1 --admin",
    "guard-github-polling.py": "gh run watch 1",
    "guard-secret-print.py": "cat .env",
    "guard-zsh-special-vars.py": "path=x",
}


@pytest.mark.parametrize("script", sorted(ALONE))
def test_a_refusal_reads_as_the_guard_alone_writes_it(
    dirty_main: Path, script: str
) -> None:
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": ALONE[script]},
        "cwd": str(dirty_main),
    }
    alone = _run(GUARDS, f'"{GUARDS}/hooks/{script}"', event)
    together = _run(GUARDS, _bash_entries(GUARDS)[0], event)
    assert alone.returncode == 2, alone.stderr
    assert (together.returncode, together.stderr) == (2, alone.stderr)
    assert together.stderr.rstrip().endswith("]"), together.stderr


def test_every_refusal_of_one_call_is_given(dirty_main: Path) -> None:
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr merge 1 --admin; path=x"},
        "cwd": str(dirty_main),
    }
    done = _run(GUARDS, _bash_entries(GUARDS)[0], event)
    assert done.returncode == 2
    assert "[guards:no-admin-merge]\n\nRefusing" in done.stderr, done.stderr
    assert done.stderr.endswith("[guards:no-zsh-special-vars]\n"), done.stderr


@pytest.mark.parametrize("stdin", ["git status", "not json", ""])
def test_a_harmless_or_unreadable_call_passes_quietly(
    dirty_main: Path, stdin: str
) -> None:
    command = _bash_entries(GUARDS)[0]
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": stdin},
        "cwd": str(dirty_main),
    }
    done = subprocess.run(
        command,
        shell=True,
        input=json.dumps(event) if stdin == "git status" else stdin,
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(GUARDS)},
        check=False,
    )
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


@pytest.mark.parametrize("failure", ["raise RuntimeError('boom')", "sys.exit(1)"])
def test_a_guard_that_fails_lets_the_call_through_for_itself_only(
    tmp_path: Path, dirty_main: Path, failure: str
) -> None:
    copy = tmp_path / "guards"
    shutil.copytree(GUARDS, copy)
    broken = copy / "hooks" / "guard-admin-merge.py"
    broken.write_text(
        broken.read_text().replace(
            "def refusal(event: dict) -> str | None:\n",
            f"def refusal(event: dict) -> str | None:\n    {failure}\n",
        )
    )
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr merge 1 --admin; path=x"},
        "cwd": str(dirty_main),
    }
    command = _bash_entries(GUARDS)[0].replace("${CLAUDE_PLUGIN_ROOT}", str(copy))
    done = _run(copy, command, event)
    assert done.returncode == 2
    assert "no-admin-merge" not in done.stderr, done.stderr
    assert done.stderr.endswith("[guards:no-zsh-special-vars]\n"), done.stderr


def _work_event(command: str, mode: str = "auto") -> dict:
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": "/nonexistent",
        "permission_mode": mode,
    }


def _fake_gh(tmp_path: Path, script: str) -> str:
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text("#!/bin/sh\n" + script)
    gh.chmod(0o755)
    (gh.parent / "python3").symlink_to(sys.executable)
    return f"{gh.parent}:/usr/bin:/bin"


def _work_run(command: str, path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _bash_entries(WORK)[0],
        shell=True,
        input=json.dumps(_work_event(command)),
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(WORK), "PATH": path},
        check=False,
    )


def test_the_work_hooks_answer_together_an_allow_and_a_warning(tmp_path: Path) -> None:
    path = _fake_gh(tmp_path, "echo '{\"comments\": []}'\n")
    done = _work_run("gh pr merge 3 --repo o/r --squash", path)
    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout)
    specific = answer["hookSpecificOutput"]
    assert specific["permissionDecision"] == "allow"
    assert "agents may merge" in specific["permissionDecisionReason"]
    assert "fresh-review comment" in specific["additionalContext"]
    assert "fresh-review comment" in answer["systemMessage"]


def test_one_work_hook_answering_is_passed_on_as_it_is(tmp_path: Path) -> None:
    path = _fake_gh(tmp_path, "echo '{\"comments\": []}'\n")
    done = subprocess.run(
        _bash_entries(WORK)[0],
        shell=True,
        input=json.dumps(_work_event("gh pr merge 3 --repo o/r", mode="default")),
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(WORK), "PATH": path},
        check=False,
    )
    answer = json.loads(done.stdout)
    assert "permissionDecision" not in answer["hookSpecificOutput"]
    assert "fresh-review comment" in answer["systemMessage"]


def test_a_deny_outranks_an_allow() -> None:
    hooks = _dispatcher_module_work()
    allow = {
        "hookSpecificOutput": {
            "permissionDecision": "allow",
            "permissionDecisionReason": "a",
        }
    }
    deny = {
        "hookSpecificOutput": {
            "permissionDecision": "deny",
            "permissionDecisionReason": "d",
        }
    }
    for answers in ([allow, deny], [deny, allow]):
        merged = hooks.merge(answers)["hookSpecificOutput"]
        assert (merged["permissionDecision"], merged["permissionDecisionReason"]) == (
            "deny",
            "d",
        )


def test_a_work_hook_that_raises_leaves_the_others_answer(tmp_path: Path) -> None:
    copy = tmp_path / "work"
    shutil.copytree(WORK, copy)
    broken = copy / "hooks" / "review-check.py"
    broken.write_text(broken.read_text().replace("def decide(\n", "def decide_(\n"))
    done = subprocess.run(
        _bash_entries(WORK)[0],
        shell=True,
        input=json.dumps(_work_event("gh pr merge 3 --squash")),
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(copy)},
        check=False,
    )
    answer = json.loads(done.stdout)
    assert answer["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "systemMessage" not in answer


def _dispatcher_module_work():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bash_hooks", WORK / "hooks" / "bash-hooks.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _broken_guards(tmp_path: Path, body: str) -> tuple[Path, str]:
    copy = tmp_path / "guards"
    shutil.copytree(GUARDS, copy)
    broken = copy / "hooks" / "guard-admin-merge.py"
    broken.write_text(
        broken.read_text().replace(
            "def refusal(event: dict) -> str | None:\n",
            f"def refusal(event: dict) -> str | None:\n    {body}\n",
        )
    )
    return copy, _bash_entries(GUARDS)[0].replace("${CLAUDE_PLUGIN_ROOT}", str(copy))


def test_a_crash_with_no_refusal_shows_as_a_hook_error(
    tmp_path: Path, dirty_main: Path
) -> None:
    """As when the guard runs alone: a guard with its own `main()` (not `guardlib.run`) exits 1 on a crash."""
    copy = tmp_path / "guards"
    shutil.copytree(GUARDS, copy)
    broken = copy / "hooks" / "guard-commit-on-main.py"
    broken.write_text(
        broken.read_text().replace(
            "def main() -> int:\n",
            "def main() -> int:\n    raise RuntimeError('boom')\n",
        )
    )
    command = _bash_entries(GUARDS)[0].replace("${CLAUDE_PLUGIN_ROOT}", str(copy))
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "cwd": str(dirty_main),
    }
    alone = _run(copy, f'"{broken}"', event)
    done = _run(copy, command, event)
    assert (alone.returncode, done.returncode) == (1, 1), done.stderr
    assert "RuntimeError: boom" in done.stderr


def test_a_guard_that_exits_2_refuses_with_what_it_wrote(
    tmp_path: Path, dirty_main: Path
) -> None:
    copy, command = _broken_guards(
        tmp_path, "sys.stderr.write('stopped\\n'); sys.exit(2)"
    )
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "cwd": str(dirty_main),
    }
    done = _run(copy, command, event)
    assert (done.returncode, done.stderr) == (2, "stopped\n")


def test_deny_outranks_ask_and_every_context_is_kept() -> None:
    hooks = _dispatcher_module_work()

    def said(decision: str, context: str) -> dict:
        return {
            "hookSpecificOutput": {
                "permissionDecision": decision,
                "permissionDecisionReason": decision,
                "additionalContext": context,
            }
        }

    merged = hooks.merge([said("deny", "one"), said("ask", "two")])
    specific = merged["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    assert specific["additionalContext"] == "one\ntwo"


def test_a_work_hook_crash_with_no_answer_shows_as_a_hook_error(tmp_path: Path) -> None:
    copy = tmp_path / "work"
    shutil.copytree(WORK, copy)
    broken = copy / "hooks" / "review-check.py"
    broken.write_text(broken.read_text().replace("def decide(\n", "def decide_(\n"))
    done = subprocess.run(
        _bash_entries(WORK)[0],
        shell=True,
        input=json.dumps(_work_event("gh pr merge 3", mode="default")),
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(copy)},
        check=False,
    )
    assert (done.returncode, done.stdout) == (1, ""), done.stderr
    assert "AttributeError" in done.stderr
