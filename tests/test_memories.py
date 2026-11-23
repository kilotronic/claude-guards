# SPDX-License-Identifier: MIT

"""The memory hooks: offer to share what Claude saves to its memory, and a daily list of what still waits."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

WORK = Path(__file__).resolve().parents[1] / "plugins" / "work"
SCRIPT = WORK / "hooks" / "memories.py"

_spec = importlib.util.spec_from_file_location("memories", SCRIPT)
assert _spec and _spec.loader
memories = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(memories)


def memory(
    folder: Path,
    name: str,
    body: str,
    kind: str = "feedback",
    shared: str | None = None,
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    extra = f"shared: {shared}\n" if shared else ""
    path = folder / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: d\n{extra}metadata:\n  type: {kind}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture(autouse=True)
def no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORK_MEMORY_MATCH", raising=False)


@pytest.fixture
def projects(tmp_path: Path) -> Path:
    return tmp_path / "projects"


def test_fields_reads_name_type_and_shared(tmp_path: Path) -> None:
    p = memory(tmp_path / "x" / "memory", "a", "rule", kind="project", shared="keep")
    assert memories.fields(p) == {"name": "a", "type": "project", "shared": "keep"}
    bare = tmp_path / "m.md"
    bare.write_text("just text\n", encoding="utf-8")
    assert memories.fields(bare) == {}


def test_with_no_setting_every_undecided_project_memory_is_offered(
    projects: Path,
) -> None:
    p = memory(projects / "-Users-x-Code-gh-widgets" / "memory", "t", "tests first")
    assert memories.offerable(p, projects)


def test_the_setting_limits_offers_to_memories_whose_folder_or_text_matches(
    projects: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORK_MEMORY_MATCH", "acme|widget")
    by_folder = memory(projects / "-Users-x-Code-gh-Widgets" / "memory", "a", "x")
    by_text = memory(projects / "-Users-x-Code-gh" / "memory", "b", "ACME's deploy")
    other = memory(projects / "-Users-x-Code-gh-game" / "memory", "c", "tests first")
    assert memories.offerable(by_folder, projects)
    assert memories.offerable(by_text, projects)
    assert not memories.offerable(other, projects)


def test_a_broken_setting_offers_nothing_and_never_fails(
    projects: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WORK_MEMORY_MATCH", "(unclosed")
    p = memory(projects / "-Users-x-Code-gh-widgets" / "memory", "a", "x")
    assert not memories.offerable(p, projects)
    assert memories.sweep(projects, tmp_path / "stamp", now=time.time()) == ""


def test_personal_and_decided_memories_are_not_offered(projects: Path) -> None:
    folder = projects / "-Users-x-Code-gh-widgets" / "memory"
    assert not memories.offerable(memory(folder, "me", "I", kind="user"), projects)
    assert not memories.offerable(memory(folder, "k", "r", shared="keep"), projects)


def test_the_index_and_files_outside_memory_are_not_offered(
    projects: Path, tmp_path: Path
) -> None:
    idx = projects / "-Users-x-Code-gh-widgets" / "memory" / "MEMORY.md"
    idx.parent.mkdir(parents=True)
    idx.write_text("- [a](a.md)\n", encoding="utf-8")
    assert not memories.offerable(idx, projects)
    other = projects / "-Users-x-Code-gh-widgets" / "notes.md"
    other.write_text("x\n", encoding="utf-8")
    assert not memories.offerable(other, projects)
    assert not memories.offerable(tmp_path / "repo" / "CLAUDE.md", projects)


def test_the_offer_names_the_memory_and_the_skill(projects: Path) -> None:
    p = memory(projects / "-Users-x-Code-gh-widgets" / "memory", "plan-pr", "by PR")
    event = {"tool_name": "Write", "tool_input": {"file_path": str(p)}}
    out = memories.after_write(event, projects)
    context = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "`plan-pr`" in context
    assert "`/add-knowledge`" in context
    assert "`/add-knowledge keep`" in context


def test_other_files_are_silent(tmp_path: Path) -> None:
    event = {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "a.py")}}
    assert memories.after_write(event, tmp_path / "projects") is None


@pytest.fixture
def swept(projects: Path) -> Path:
    folder = projects / "-Users-x-Code-gh-widgets" / "memory"
    memory(folder, "open-one", "rule a")
    memory(folder, "kept", "rule b", shared="keep")
    memory(folder, "sent", "rule c", shared="https://github.com/octo/widgets/pull/1")
    memory(folder, "me", "rule d", kind="user")
    return projects


def test_the_sweep_lists_undecided_and_sent_once_a_day(
    swept: Path, tmp_path: Path
) -> None:
    stamp = tmp_path / "data" / "last-sweep"
    text = memories.sweep(swept, stamp, now=1_000_000.0)
    assert text.startswith("Memory check (work plugin")
    assert "open-one" in text
    assert "https://github.com/octo/widgets/pull/1" in text
    assert "`kept`" not in text and "`me`" not in text
    assert memories.sweep(swept, stamp, now=1_000_000.0 + 3600) == ""
    assert "open-one" in memories.sweep(swept, stamp, now=1_000_000.0 + 86_400)


def test_the_sweep_follows_the_setting(
    swept: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory(swept / "-Users-x-Code-gh-acme" / "memory", "deploy", "rule e")
    monkeypatch.setenv("WORK_MEMORY_MATCH", "acme")
    text = memories.sweep(swept, tmp_path / "stamp", now=time.time())
    assert "`deploy`" in text and "open-one" not in text


def test_nothing_to_say_is_silent(tmp_path: Path) -> None:
    assert memories.sweep(tmp_path / "empty", tmp_path / "s", now=time.time()) == ""


def test_long_lists_are_capped(projects: Path, tmp_path: Path) -> None:
    folder = projects / "-Users-x-Code-gh-widgets" / "memory"
    for i in range(15):
        memory(folder, f"m{i:02d}", "rule")
    text = memories.sweep(projects, tmp_path / "s", now=time.time())
    assert "and 6 more" in text


def _command(event: str, matcher: str | None) -> str:
    hooks = json.loads((WORK / "hooks" / "hooks.json").read_text())["hooks"]
    found = [
        hook["command"]
        for group in hooks[event]
        if group.get("matcher") == matcher
        for hook in group["hooks"]
        if "memories.py" in hook["command"]
    ]
    assert len(found) == 1, found
    return found[0]


def _run(command: str, stdin: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        shell=True,
        input=stdin,
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(WORK), **env},
        check=False,
    )


def test_as_claude_code_runs_them(projects: Path, tmp_path: Path) -> None:
    p = memory(projects / "-Users-x-Code-gh-widgets" / "memory", "plan-pr", "by PR")
    env = {
        "WORK_MEMORY_PROJECTS": str(projects),
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"),
    }
    event = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(p)}})
    done = _run(_command("PostToolUse", "Write|Edit|MultiEdit"), event, env)
    assert done.returncode == 0, done.stderr
    context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "`plan-pr`" in context
    done = _run(_command("SessionStart", None), "{}", env)
    assert done.returncode == 0, done.stderr
    assert "plan-pr" in done.stdout
    assert (tmp_path / "data" / "memory-sweep-last-run").exists()


def test_bad_input_never_fails_the_session(tmp_path: Path) -> None:
    for stdin in ("not json", "[" * 100_000 + "]" * 100_000):
        done = _run(
            _command("PostToolUse", "Write|Edit|MultiEdit"),
            stdin,
            {"WORK_MEMORY_PROJECTS": str(tmp_path)},
        )
        assert (done.returncode, done.stdout) == (0, "")


def test_it_defers_annotations_so_3_9_can_load_it() -> None:
    assert "from __future__ import annotations" in SCRIPT.read_text()
