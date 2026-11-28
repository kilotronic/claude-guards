# SPDX-License-Identifier: MIT

"""The claim check (`claim-before-pr`): `gh pr create` is refused when the branch changes code and carries no
claim, unless the body says the owner asked for the work directly. It asks `next-item claimed`, never blocks when
that fails, and says nothing in a repo with no todo file to mark.

Each test runs the work plugin's Bash dispatcher the way Claude Code does, in a made-up project.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from hookrun import turn_off
from test_next_item_claimed import claim, work
from workrun import README, WORK, Project, make_project

DISPATCHER = WORK / "hooks" / "bash-hooks.py"
ESCAPE = "No backlog item: the owner asked for this directly."
CREATE = "gh pr create --draft --title 'feat: snooze' --body 'Adds the button.'"


def run(
    p: Project,
    command: str,
    cwd: Path | None = None,
    dispatcher: Path = DISPATCHER,
    who: str = "agent-a",
) -> tuple[dict | None, subprocess.CompletedProcess[str]]:
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(cwd or p.clone),
        "session_id": "1a2b3c4d5e6f",
    }
    done = subprocess.run(
        [sys.executable, str(dispatcher)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{p.bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_DIR": str(p.gh_dir),
            "GOAL_AS": who,
            "GOAL_MACHINE": "box1",
        },
        check=False,
        timeout=60,
    )
    return (json.loads(done.stdout) if done.stdout.strip() else None), done


def refusal(answer: dict | None) -> str:
    assert answer is not None
    specific = answer["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny", answer
    return specific["permissionDecisionReason"]


@pytest.fixture
def unclaimed(tmp_path: Path) -> Project:
    """A work branch with code and no claim mark."""
    p = make_project(tmp_path)
    work(p, {"src/snooze.py": "x = 1\n"})
    return p


def test_code_with_no_claim_is_refused_in_one_line_that_says_what_to_do(
    unclaimed: Project,
) -> None:
    for command in (CREATE, CREATE.replace("create", "new"), f"git push && {CREATE}"):
        answer, done = run(unclaimed, command)
        assert done.returncode == 0, done.stderr
        text = refusal(answer)
        first, label = text.split("\n")
        assert label == "[work:claim-before-pr]"
        assert first.startswith("work: ")
        assert "/work:start-work" in first and ESCAPE in first
        assert "src/snooze.py" in first


def test_a_branch_that_carries_its_claim_mark_opens(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    claim(p)
    work(p, {"src/snooze.py": "x = 1\n"})
    answer, done = run(p, CREATE)
    assert (answer, done.returncode, done.stderr) == (None, 0, "")


def test_the_escape_line_in_the_body_opens_it(unclaimed: Project) -> None:
    (unclaimed.clone / "body.md").write_text(f"Adds the button.\n\n{ESCAPE}\n")
    for command in (
        f'gh pr create --draft --title t --body "Adds the button.\n\n{ESCAPE}"',
        f"gh pr create --title t --body \"$(cat <<'EOF'\nAdds it.\n\n{ESCAPE.lower()}\nEOF\n)\"",
        f"gh pr create --title t --body '  {ESCAPE.rstrip('.')}  '",
        f"gh pr create --title t --body='{ESCAPE}'",
        "gh pr create --title t --body-file body.md",
        "gh pr create --title t -F body.md",
    ):
        answer, done = run(unclaimed, command)
        assert (answer, done.returncode) == (None, 0), command


def test_the_escape_line_counts_only_as_a_whole_line(unclaimed: Project) -> None:
    (unclaimed.clone / "body.md").write_text("Adds the button.\n")
    for command in (
        f"gh pr create --title t --body 'Not so: {ESCAPE}'",
        "gh pr create --title t --body 'No backlog item'",
        "gh pr create --title t --body-file body.md",
        "gh pr create --title t --body-file missing.md",
    ):
        refusal(run(unclaimed, command)[0])


def test_a_docs_only_pull_request_opens(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    work(p, {"README.md": README + "\nMore.\n", "docs/notes.txt": "x\n"})
    assert run(p, CREATE)[0] is None


def test_a_repo_with_no_work_record_is_left_alone(tmp_path: Path) -> None:
    p = make_project(tmp_path, readme="# Widgets\n", todo=None)
    work(p, {"src/snooze.py": "x = 1\n"})
    answer, done = run(p, CREATE)
    assert (answer, done.returncode, done.stderr) == (None, 0, "")


def test_a_broken_next_item_warns_and_lets_the_pull_request_open(
    unclaimed: Project, tmp_path: Path
) -> None:
    copy = tmp_path / "work"
    shutil.copytree(WORK, copy)
    (copy / "scripts" / "next_item.py").write_text("raise SystemExit('broken')\n")
    answer, done = run(unclaimed, CREATE, dispatcher=copy / "hooks" / "bash-hooks.py")
    assert done.returncode == 0 and answer is not None
    assert "permissionDecision" not in answer["hookSpecificOutput"]
    assert answer["systemMessage"].startswith("work: could not check")
    assert answer["hookSpecificOutput"]["additionalContext"] == answer["systemMessage"]


def test_settings_next_item_cannot_read_warn_and_let_it_open(tmp_path: Path) -> None:
    p = make_project(tmp_path, extra={".claude/work.toml": "docs = 3\n"})
    work(p, {"src/snooze.py": "x = 1\n"})
    answer, _ = run(p, CREATE)
    assert answer is not None
    assert "permissionDecision" not in answer["hookSpecificOutput"]
    assert "could not check" in answer["systemMessage"]


def test_a_repo_can_turn_the_rule_off(unclaimed: Project) -> None:
    turn_off(unclaimed.clone, "claim-before-pr")
    assert run(unclaimed, CREATE)[0] is None
    turn_off(unclaimed.clone, "claim-before-pr", reason=None)
    refusal(run(unclaimed, CREATE)[0])


def test_it_checks_the_folder_and_the_branch_the_command_names(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    p.branch("work/code", "2026-11-21", {"code.py": "x = 1\n"})
    p.branch("work/docs", "2026-11-21", {"notes.md": "x\n"})
    # The clone is on a detached origin/main: nothing ahead, so only a named head or folder is judged.
    assert run(p, CREATE)[0] is None
    assert "code.py" in refusal(run(p, f"{CREATE} --head work/code")[0])
    assert "code.py" in refusal(run(p, f"{CREATE} -H octo:work/code")[0])
    assert run(p, f"{CREATE} --head=work/docs")[0] is None
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    work(p, {"src/snooze.py": "x = 1\n"})
    assert "src/snooze.py" in refusal(
        run(p, f"cd {p.clone} && {CREATE}", cwd=elsewhere)[0]
    )
    assert run(p, CREATE, cwd=elsewhere)[0] is None


def test_a_folder_or_branch_only_the_shell_knows_warns(unclaimed: Project) -> None:
    for command in (f'cd "$WT" && {CREATE}', f'{CREATE} --head "$BRANCH"'):
        answer, _ = run(unclaimed, command)
        assert answer is not None, command
        assert "permissionDecision" not in answer["hookSpecificOutput"]
        assert "could not check" in answer["systemMessage"]


def test_a_line_that_changes_the_branch_first_warns(tmp_path: Path) -> None:
    # The hook runs before the line does, so it can't judge the branch the pull request will open from.
    p = make_project(tmp_path)
    work(p, {"README.md": README + "\nMore.\n"})
    for before in (
        "git commit -am 'feat: code'",
        "git add -A && git commit -m x && git push",
        "git switch work/other",
        "next-item claim 'Add a snooze button' && git push",
    ):
        answer, _ = run(p, f"{before} && {CREATE}")
        assert answer is not None, before
        assert "permissionDecision" not in answer["hookSpecificOutput"], before
        assert "could not check" in answer["systemMessage"], before
    assert run(p, f"git push -u origin work/snooze && {CREATE}")[0] is None
    assert run(p, f"{CREATE} && git commit -am later")[0] is None


def test_the_refusal_says_how_the_body_must_be_given(unclaimed: Project) -> None:
    text = refusal(run(unclaimed, 'gh pr create --title t --body-file "$B"')[0])
    assert "`--body` text or a `--body-file` the hook can read" in text


def test_other_commands_are_untouched(unclaimed: Project) -> None:
    turn_off(unclaimed.clone, "no-such-rule")  # gives the clone a GitHub origin
    for command in (
        "gh pr view 12",
        "gh pr list",
        'echo "gh pr create"',
        "git commit -m 'gh pr create --draft'",
        f"{CREATE} --web",
        f"{CREATE} --dry-run",
        f"{CREATE} --help",
        f"{CREATE} --repo octo/other",
        f"GH_REPO=octo/other {CREATE}",
        "",
    ):
        answer, done = run(unclaimed, command)
        assert (answer, done.returncode) == (None, 0), command
    refusal(run(unclaimed, f"{CREATE} --repo octo/sample")[0])
    refusal(run(unclaimed, f"{CREATE} -R github.com/Octo/Sample")[0])


def test_the_dispatcher_runs_it_and_the_parser_is_the_guards_own() -> None:
    assert '"claim-check.py"' in DISPATCHER.read_text()
    guards = WORK.parent / "guards" / "hooks" / "guardlib.py"
    assert (WORK / "hooks" / "guardlib.py").read_bytes() == guards.read_bytes()
