# SPDX-License-Identifier: MIT

"""`missing_tags.py` names each plugin version that has no `<plugin>--v<version>` tag yet, and the commit
where that version landed, so the release workflow can tag it. Claude Code resolves a dependency's version
constraint against these tags, so a missing one leaves every plugin that needs that version turned off."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "missing_tags.py"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "tag.gpgSign", "false")
    _git(repo, "config", "commit.gpgSign", "false")
    return repo


def _write_plugin(repo: Path, name: str, version: str, description: str = "d") -> None:
    manifest = repo / "plugins" / name / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {"name": name, "version": version, "description": description}, indent=2
        )
        + "\n"
    )


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _missing(repo: Path) -> list[str]:
    done = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.splitlines()


def test_an_untagged_version_is_named_with_the_commit_it_landed_in(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _write_plugin(repo, "work", "1.0.0")
    landed = _commit(repo, "add work")
    _commit(repo, "something else")
    assert _missing(repo) == [f"work--v1.0.0 {landed}"]


def test_a_tagged_version_is_not_named(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_plugin(repo, "work", "1.0.0")
    _commit(repo, "add work")
    _git(repo, "tag", "work--v1.0.0")
    assert _missing(repo) == []


def test_a_bump_is_tagged_at_the_bump_not_at_a_later_change_to_the_manifest(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _write_plugin(repo, "work", "1.0.0")
    _commit(repo, "add work")
    _git(repo, "tag", "work--v1.0.0")
    _write_plugin(repo, "work", "1.1.0")
    bump = _commit(repo, "release work 1.1.0")
    _write_plugin(repo, "work", "1.1.0", description="better words")
    _commit(repo, "reword work")
    assert _missing(repo) == [f"work--v1.1.0 {bump}"]


def test_each_plugin_is_checked_on_its_own(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_plugin(repo, "guards", "1.1.1")
    _write_plugin(repo, "work", "1.4.0")
    both = _commit(repo, "add both")
    _git(repo, "tag", "guards--v1.1.1")
    assert _missing(repo) == [f"work--v1.4.0 {both}"]


def test_a_change_to_a_dependency_constraint_does_not_move_the_tag(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    manifest = repo / "plugins" / "jl" / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)

    def write(version: str, needs: str) -> None:
        body = {
            "name": "jl",
            "version": version,
            "dependencies": [{"name": "work", "version": needs}],
        }
        manifest.write_text(json.dumps(body, indent=2) + "\n")

    write("1.0.0", "^1.1.0")
    _commit(repo, "add jl")
    _git(repo, "tag", "jl--v1.0.0")
    write("1.1.0", "^1.1.0")
    bump = _commit(repo, "release jl 1.1.0")
    write("1.1.0", "^1.4.0")
    _commit(repo, "need a newer work")
    assert _missing(repo) == [f"jl--v1.1.0 {bump}"]


def test_a_version_set_back_to_an_older_value_is_tagged_where_it_returned(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _write_plugin(repo, "work", "1.0.0")
    _commit(repo, "add work")
    _write_plugin(repo, "work", "1.1.0")
    _commit(repo, "release work 1.1.0")
    _git(repo, "tag", "work--v1.1.0")
    _write_plugin(repo, "work", "1.0.0")
    back = _commit(repo, "go back to 1.0.0")
    assert _missing(repo) == [f"work--v1.0.0 {back}"]


def test_a_version_that_is_not_a_version_number_is_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_plugin(repo, "work", "1.0 beta")
    _commit(repo, "add work")
    done = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    assert done.returncode != 0
    assert "1.0 beta" in done.stderr
