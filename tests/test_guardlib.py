# SPDX-License-Identifier: MIT

"""The shared shell-command parser the deny-list guards read commands with."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "plugins" / "guards" / "hooks"
sys.path.insert(0, str(HOOKS))
import guardlib


def argvs(text: str) -> list[list[str]]:
    return [c.argv for c in guardlib.parse(text) if c.argv]


def test_separators_split_commands() -> None:
    assert argvs("a 1 && b 2; c | d || e & f") == [
        ["a", "1"],
        ["b", "2"],
        ["c"],
        ["d"],
        ["e"],
        ["f"],
    ]


def test_quoted_text_is_one_word_not_a_command() -> None:
    assert argvs("git commit -m 'gh pr merge --admin; x'") == [
        ["git", "commit", "-m", "gh pr merge --admin; x"]
    ]
    assert argvs('echo "a && b"') == [["echo", "a && b"]]


def test_command_substitution_is_parsed_and_marked() -> None:
    commands = guardlib.parse('x=$(op read "op://a/b") && echo "$(gh pr view)"')
    inner = [c for c in commands if c.substituted]
    assert [c.argv for c in inner] == [["op", "read", "op://a/b"], ["gh", "pr", "view"]]
    assert next(c.assignments for c in commands if not c.substituted) == ["x=$(...)"]


def test_backticks_are_substitution() -> None:
    commands = guardlib.parse("echo `gh pr view`")
    assert [c.argv for c in commands if c.substituted] == [["gh", "pr", "view"]]


def test_single_quotes_do_not_substitute() -> None:
    assert [
        c.argv for c in guardlib.parse("echo '$(gh pr merge --admin)'") if c.substituted
    ] == []


def test_heredoc_bodies_are_not_commands() -> None:
    text = "git commit -F - <<'EOF'\ngh pr merge 1 --admin\n1) item\nEOF\ngit push"
    assert argvs(text) == [["git", "commit", "-F", "-"], ["git", "push"]]
    nested = "git commit -m \"$(cat <<'EOF'\nfix: x\n\nwhile true; do gh run list; done\nEOF\n)\""
    assert argvs(nested) == [["cat"], ["git", "commit", "-m", "$(...)"]]


def test_subshells_and_groups() -> None:
    assert argvs("(cd x && gh pr view) ; { a; }") == [
        ["cd", "x"],
        ["gh", "pr", "view"],
        ["a"],
    ]


def test_pipes_and_redirects() -> None:
    first, second = guardlib.parse("op read x 2>/dev/null | pbcopy")
    assert first.piped and not second.piped
    assert not first.stdout_captured() or first.piped
    (only,) = guardlib.parse("op read x > out.txt")
    assert only.stdout_captured()
    (to_err,) = guardlib.parse("op read x 2> err.txt")
    assert not to_err.stdout_captured()
    (both,) = guardlib.parse("op read x &> out")
    assert both.stdout_captured()
    (dup,) = guardlib.parse("op read x >&2")
    assert not dup.stdout_captured()


def test_loops_are_tracked() -> None:
    commands = guardlib.parse("while true; do gh run list; sleep 5; done; gh pr view")
    loops = {" ".join(c.argv): c.loops for c in commands if c.argv}
    assert loops["true"] == ("while",)
    assert loops["gh run list"] == ("while",)
    assert loops["sleep 5"] == ("while",)
    assert loops["gh pr view"] == ()


def test_for_header_is_not_a_command() -> None:
    commands = guardlib.parse("for n in 1 2; do gh pr view $n; done")
    header = [c for c in commands if c.keyword == "for"]
    assert header and header[0].words[:2] == ["n", "in"]
    assert [c.argv for c in commands if c.argv and c.keyword is None] == [
        ["gh", "pr", "view", "$n"]
    ]


def test_wrappers_are_stripped_from_argv() -> None:
    assert argvs("env GH_REPO=a/b gh pr merge 1") == [["gh", "pr", "merge", "1"]]
    assert argvs("command git switch x") == [["git", "switch", "x"]]
    assert argvs("sudo -u me cat .env") == [["cat", ".env"]]
    assert argvs("time nohup gh run watch") == [["gh", "run", "watch"]]


def test_shell_dash_c_and_eval_are_parsed() -> None:
    assert ["gh", "pr", "merge", "1", "--admin"] in argvs(
        "bash -c 'gh pr merge 1 --admin'"
    )
    assert ["gh", "pr", "merge", "1", "--admin"] in argvs(
        "zsh -lc 'gh pr merge 1 --admin'"
    )
    assert ["git", "switch", "x"] in argvs("eval 'git switch x'")


def test_assignments_are_separate_from_argv() -> None:
    (c,) = guardlib.parse("path=foo FOO=1 make")
    assert c.assignments == ["path=foo", "FOO=1"]
    assert c.argv == ["make"]
    (bare,) = guardlib.parse("status=$?")
    assert bare.assignments == ["status=$?"] and bare.argv == []


def test_variable_references_are_recorded_outside_single_quotes() -> None:
    (c,) = guardlib.parse(
        "echo $GH_TOKEN \"${API_KEY}\" '$NOT_THIS' ${#LEN_TOKEN} ${SET_TOKEN:+set}"
    )
    assert c.refs == [[], ["GH_TOKEN"], ["API_KEY"], [], [], []]


def test_cd_moves_the_cwd_of_later_commands() -> None:
    commands = guardlib.parse(
        "cd /tmp/a && git switch x; cd b; git status", cwd="/home"
    )
    cwds = {" ".join(c.argv): c.cwd for c in commands}
    assert cwds["git switch x"] == "/tmp/a"
    assert cwds["git status"] == "/tmp/a/b"


def test_git_and_gh_invocations() -> None:
    assert guardlib.git_args(
        ["git", "-C", "x", "-c", "a=b", "--no-pager", "checkout", "y"]
    ) == (
        "checkout",
        ["y"],
        ["x"],
    )
    assert guardlib.git_args(["ls"]) is None
    assert guardlib.gh_args(["gh", "-R", "o/r", "pr", "merge", "5"]) == (
        ["pr", "merge", "5"],
        "o/r",
    )
    assert guardlib.gh_args(["/opt/bin/gh", "pr", "view", "--repo=o/r"]) == (
        ["pr", "view"],
        "o/r",
    )


def test_comments_and_line_continuations() -> None:
    assert argvs("gh pr view \\\n 5 # gh pr merge --admin\ngit status") == [
        ["gh", "pr", "view", "5"],
        ["git", "status"],
    ]


def test_unbalanced_input_does_not_raise() -> None:
    for text in [
        'echo "unterminated',
        "echo $(gh pr view",
        "a ) b",
        "echo `x",
        "done; done",
        "${",
    ]:
        guardlib.parse(text)


def test_a_here_string_is_not_a_heredoc() -> None:
    assert argvs('gh auth login --with-token <<< "$T"\ngh pr view') == [
        ["gh", "auth", "login", "--with-token"],
        ["gh", "pr", "view"],
    ]


def test_cd_dash_goes_back_to_the_previous_directory() -> None:
    commands = guardlib.parse(
        "cd /tmp/a && cd /tmp/b && cd - && git status", cwd="/home"
    )
    assert commands[-1].cwd == "/tmp/a"
    # With no earlier `cd`, the previous directory is unknown: stay put, as guards did before.
    assert guardlib.parse("cd - && git status", cwd="/home")[-1].cwd == "/home"


def test_a_variable_assigned_a_program_in_the_same_command_is_expanded() -> None:
    assert argvs("g=git; $g checkout x") == [["git", "checkout", "x"]]
    assert argvs('g="git -C /r"; ${g} switch y') == [["git", "-C", "/r", "switch", "y"]]
    # Not assigned here, or assigned from a substitution: left alone.
    assert argvs("$g checkout x") == [["$g", "checkout", "x"]]
    assert argvs("g=$(which git); $g checkout x")[-1] == ["$g", "checkout", "x"]


def test_the_label_names_the_plugin_that_ships_guardlib() -> None:
    assert guardlib.label("no-admin-merge") == "[guards:no-admin-merge]"


def test_a_plugin_that_bundles_guardlib_labels_with_its_own_name(
    tmp_path: Path,
) -> None:
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name": "mine"}')
    (tmp_path / "hooks").mkdir()
    copy = tmp_path / "hooks" / "guardlib.py"
    copy.write_text((HOOKS / "guardlib.py").read_text())
    done = subprocess.run(
        [sys.executable, "-c", "import guardlib; print(guardlib.label('x'))"],
        cwd=tmp_path / "hooks",
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout.strip() == "[mine:x]"


def test_an_unreadable_plugin_json_falls_back_to_guards(tmp_path: Path) -> None:
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text("not json")
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "guardlib.py").write_text((HOOKS / "guardlib.py").read_text())
    done = subprocess.run(
        [sys.executable, "-c", "import guardlib; print(guardlib.label('x'))"],
        cwd=tmp_path / "hooks",
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout.strip() == "[guards:x]"


def test_a_cd_inside_a_subshell_stays_inside_it() -> None:
    commands = guardlib.parse("(cd /elsewhere && make); git status", cwd="/repo")
    assert [(c.argv[:2], c.cwd) for c in commands] == [
        (["cd", "/elsewhere"], "/repo"),
        (["make"], "/elsewhere"),
        (["git", "status"], "/repo"),
    ]
