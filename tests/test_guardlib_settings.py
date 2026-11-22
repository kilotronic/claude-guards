# SPDX-License-Identifier: MIT

"""Per-repo overrides: which file is read, and how an entry turns a rule off."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "plugins" / "guards" / "hooks"
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import guardlib
from hookrun import turn_off


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "r"
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init", "-q", "-b", "main"], check=True)
    return path


def write(path: Path, data: object) -> None:
    path.write_text(json.dumps(data))


def remote(repo: Path, url: str) -> None:
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", url], check=True)


def test_a_rule_off_with_a_reason_for_the_origin_repo_is_off(repo: Path) -> None:
    turn_off(repo, "some-rule", "drafts gate the preview deploy")
    assert guardlib.rule_setting("some-rule", str(repo)) == (False, None)
    assert guardlib.rule_setting("main-checkout", str(repo)) == (True, None)


def test_another_repos_entry_does_not_apply(repo: Path, overrides_file: Path) -> None:
    remote(repo, "https://github.com/octo/mine.git")
    write(overrides_file, {"octo/other": {"some-rule": {"reason": "x"}}})
    assert guardlib.rule_setting("some-rule", str(repo)) == (True, None)


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:octo/Mine.git",
        "git@github-work:octo/mine.git",
        "ssh://git@github.com/octo/mine",
        "https://github.com/octo/mine/",
    ],
)
def test_the_repo_key_comes_from_origin_with_any_host_alias(
    repo: Path, overrides_file: Path, url: str
) -> None:
    remote(repo, url)
    write(overrides_file, {"octo/mine": {"some-rule": {"reason": "x"}}})
    assert guardlib.rule_setting("some-rule", str(repo))[0] is False


def test_only_origin_counts(repo: Path, overrides_file: Path) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "add",
            "upstream",
            "git@github.com:octo/mine.git",
        ],
        check=True,
    )
    write(overrides_file, {"octo/mine": {"some-rule": {"reason": "x"}}})
    assert guardlib.rule_setting("some-rule", str(repo)) == (True, None)


def test_without_a_reason_the_rule_stays_on(repo: Path) -> None:
    turn_off(repo, "some-rule", reason=None)
    assert guardlib.rule_setting("some-rule", str(repo)) == (True, None)
    turn_off(repo, "some-rule", reason="  ")
    guardlib.clear_caches()
    assert guardlib.rule_setting("some-rule", str(repo)) == (True, None)


def test_a_group_pattern_applies_to_a_repo_the_file_does_not_name(
    repo: Path, overrides_file: Path
) -> None:
    remote(repo, "git@github.com:Acme/Tool.git")
    write(overrides_file, {"acme/*": {"some-rule": {"reason": "group"}}})
    assert guardlib.rule_setting("some-rule", str(repo)) == (False, None)


def test_a_pattern_that_does_not_match_and_an_upper_case_key_do_not_count(
    repo: Path, overrides_file: Path
) -> None:
    remote(repo, "git@github.com:acme/tool.git")
    write(
        overrides_file,
        {
            "octo/*": {"some-rule": {"reason": "other group"}},
            "Acme/Tool": {"some-rule": {"reason": "keys are lower case"}},
        },
    )
    assert guardlib.rule_setting("some-rule", str(repo)) == (True, None)


def test_the_exact_name_wins_over_a_pattern(repo: Path, overrides_file: Path) -> None:
    remote(repo, "git@github.com:acme/tool.git")
    write(
        overrides_file,
        {"acme/*": {"some-rule": {"reason": "group"}}, "acme/tool": {}},
    )
    assert guardlib.rule_setting("some-rule", str(repo)) == (True, None)


def test_a_locked_rule_stays_on_and_says_so(repo: Path) -> None:
    turn_off(repo, "no-secret-print")
    on, note = guardlib.rule_setting("no-secret-print", str(repo))
    assert on is True and "can't be turned off" in note and "overrides.json" in note


def _lock(overrides_file: Path, ids: object) -> None:
    data = json.loads(overrides_file.read_text())
    data["_locked"] = ids
    write(overrides_file, data)


def test_the_files_locked_list_keeps_a_rule_on(
    repo: Path, overrides_file: Path
) -> None:
    turn_off(repo, "some-rule")
    _lock(overrides_file, ["some-rule"])
    on, note = guardlib.rule_setting("some-rule", str(repo))
    assert on is True and "can't be turned off" in note


def test_the_files_locked_list_replaces_the_built_in_one(
    repo: Path, overrides_file: Path
) -> None:
    """The file's own list is the one read; the built-in is a fallback."""
    turn_off(repo, "no-secret-print")
    _lock(overrides_file, ["no-admin-merge"])
    assert guardlib.rule_setting("no-secret-print", str(repo)) == (False, None)


@pytest.mark.parametrize("ids", ["some-rule", [], [3], {"some-rule": 1}, None])
def test_an_odd_locked_list_falls_back_to_the_built_in_one(
    repo: Path, overrides_file: Path, ids: object
) -> None:
    turn_off(repo, "no-secret-print")
    turn_off(repo, "some-rule")
    _lock(overrides_file, ids)
    assert guardlib.rule_setting("no-secret-print", str(repo))[0] is True
    assert guardlib.rule_setting("some-rule", str(repo)) == (False, None)


def test_the_locked_key_is_never_read_as_a_repo_or_pattern(
    repo: Path, overrides_file: Path
) -> None:
    remote(repo, "git@github.com:octo/sample.git")
    write(
        overrides_file,
        {
            "_locked": ["no-admin-merge"],
            "octo/*": {"some-rule": {"reason": "x"}},
        },
    )
    assert guardlib.rule_setting("some-rule", str(repo)) == (False, None)
    assert guardlib.locked_rules() == {"no-admin-merge"}


def test_with_no_file_the_built_in_list_is_used(overrides_file: Path) -> None:
    assert not overrides_file.exists()
    assert guardlib.locked_rules() == guardlib.LOCKED


@pytest.mark.parametrize(
    "text",
    [
        "this is {not json",
        '"a string"',
        '{"octo/sample": 3}',
        '{"octo/sample": {"some-rule": "off"}}',
        '{"octo/sample": {"some-rule": {"reason": 3}}}',
    ],
)
def test_an_unreadable_or_odd_file_leaves_the_rule_on_quietly(
    repo: Path, overrides_file: Path, text: str
) -> None:
    remote(repo, "git@github.com:octo/sample.git")
    overrides_file.write_text(text)
    assert guardlib.rule_setting("some-rule", str(repo)) == (True, None)


def test_a_missing_file_leaves_the_rule_on_quietly(
    repo: Path, overrides_file: Path
) -> None:
    remote(repo, "git@github.com:octo/sample.git")
    assert not overrides_file.exists()
    assert guardlib.rule_setting("some-rule", str(repo)) == (True, None)


def test_no_origin_or_no_repo_leaves_the_rule_on(repo: Path, tmp_path: Path) -> None:
    assert guardlib.rule_setting("some-rule", str(repo)) == (True, None)
    assert guardlib.rule_setting("some-rule", str(tmp_path)) == (True, None)
    assert guardlib.rule_setting("some-rule", None) == (True, None)


def test_the_overrides_file_beside_the_plugin_wins_over_the_env_var(
    repo: Path, overrides_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote(repo, "git@github.com:octo/sample.git")
    elsewhere = tmp_path / "elsewhere.json"
    write(elsewhere, {"octo/sample": {"some-rule": {"reason": "env"}}})
    monkeypatch.setenv("CLAUDE_GUARDS_OVERRIDES", str(elsewhere))
    write(overrides_file, {})
    assert guardlib.rule_setting("some-rule", str(repo)) == (True, None)
    write(overrides_file, {"octo/sample": {"some-rule": {"reason": "plugin"}}})
    write(elsewhere, {})
    guardlib.clear_caches()
    assert guardlib.rule_setting("some-rule", str(repo)) == (False, None)


def test_the_env_var_is_read_when_the_plugin_has_no_file(
    repo: Path, overrides_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote(repo, "git@github.com:octo/sample.git")
    assert not overrides_file.exists()
    monkeypatch.setenv("HOME", str(tmp_path))
    write(tmp_path / "o.json", {"octo/sample": {"some-rule": {"reason": "x"}}})
    monkeypatch.setenv("CLAUDE_GUARDS_OVERRIDES", "~/o.json")
    assert guardlib.overrides_path() == str(tmp_path / "o.json")
    assert guardlib.rule_setting("some-rule", str(repo)) == (False, None)


def test_with_no_file_anywhere_every_rule_stays_on(
    repo: Path, overrides_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote(repo, "git@github.com:octo/sample.git")
    monkeypatch.delenv("CLAUDE_GUARDS_OVERRIDES")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert guardlib.overrides_path() is None
    assert guardlib.rule_setting("some-rule", str(repo)) == (True, None)
    assert guardlib.locked_rules() == guardlib.LOCKED
    monkeypatch.setenv("CLAUDE_GUARDS_OVERRIDES", str(tmp_path / "missing.json"))
    assert guardlib.rule_setting("some-rule", str(repo)) == (True, None)


def test_without_claude_plugin_root_the_plugin_is_the_folder_above_guardlib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT")
    assert guardlib.plugin_root() == str(HOOKS.parent)


def test_the_user_file_is_read_when_nothing_else_is_set(
    repo: Path, overrides_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote(repo, "git@github.com:octo/sample.git")
    monkeypatch.delenv("CLAUDE_GUARDS_OVERRIDES")
    monkeypatch.setenv("HOME", str(tmp_path))
    user = tmp_path / ".config" / "claude-guards" / "overrides.json"
    user.parent.mkdir(parents=True)
    write(user, {"octo/sample": {"some-rule": {"reason": "x"}}})
    assert guardlib.overrides_path() == str(user)
    assert guardlib.rule_setting("some-rule", str(repo)) == (False, None)


def test_the_env_var_wins_over_the_user_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    user = tmp_path / ".config" / "claude-guards" / "overrides.json"
    user.parent.mkdir(parents=True)
    write(user, {})
    monkeypatch.setenv("CLAUDE_GUARDS_OVERRIDES", str(tmp_path / "named.json"))
    assert guardlib.overrides_path() == str(tmp_path / "named.json")
