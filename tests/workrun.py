# SPDX-License-Identifier: MIT

"""Build a made-up project with an origin, and run `next-item` in it the way an agent does.

Each project is a bare `origin` plus a clone, so `next-item` reads `origin/main` as it does in real use. A fake
`gh` on PATH answers from JSON files the test writes and logs every call, so no test reaches GitHub.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

WORK = Path(__file__).resolve().parents[1] / "plugins" / "work"
NEXT_ITEM = WORK / "scripts" / "next-item"

README = """\
# Widgets

Kitchen timers for people who cook.

## How work runs here

- **Mission:** a kitchen timer anyone can start with one hand.
- **Current milestone:** M2, the phone app. **Done when:** a timer rings on a locked phone. **By:** no date
- **Work is recorded in:** `docs/todo.md`
- **Claim before starting:** `next-item claim '<the entry's title>'`
- **Work in progress:** at most 2 at once; defect fixes one at a time
- **Defects first:** the Defects section is fixed, top first, before anything new

## Licence

MIT.
"""

TODO = """\
# Todo

## Defects

- **The timer skips a second.** It jumps from 10 to 8 on a slow phone.
- **The bell is silent on mute.** The ring uses the media volume.

## Next

- **Add a snooze button.** Five more minutes from the ring.
- **Show the time left in the title.** So a background tab shows it.
"""

FAKE_GH = """\
#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_GH_DIR"]
with open(os.path.join(d, "calls.log"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if os.path.exists(os.path.join(d, "fail")):
    sys.exit(1)
args = sys.argv[1:]
if args[:2] == ["pr", "list"] and os.path.exists(os.path.join(d, "pr_list_fail")):
    print("could not list pull requests", file=sys.stderr)
    sys.exit(1)
if args[:2] == ["repo", "view"]:
    path = os.path.join(d, "default_branch")
    name = open(path).read().strip() if os.path.exists(path) else "main"
    print(json.dumps({"defaultBranchRef": {"name": name}}))
elif args[:2] == ["pr", "list"]:
    state = args[args.index("--state") + 1] if "--state" in args else "open"
    path = os.path.join(d, state + ".json")
    print(open(path).read() if os.path.exists(path) else "[]")
elif args[:2] == ["pr", "create"]:
    print("https://github.com/example/widgets/pull/7")
"""


def git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    done = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **(env or {})},
    )
    return done.stdout


IDENTITY = ("-c", "user.name=Test", "-c", "user.email=test@example.com")


@dataclass
class Project:
    clone: Path
    origin: Path
    gh_dir: Path
    bin_dir: Path

    def push_files(self, files: dict[str, str], message: str = "docs: update") -> None:
        """Commit files straight to origin's main, as a merged pull request would land."""
        for rel, text in files.items():
            path = self.clone / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(text))
        git(self.clone, "add", "-A")
        git(self.clone, *IDENTITY, "commit", "-q", "-m", message)
        git(self.clone, "push", "-q", "origin", "HEAD:main")

    def branch(self, name: str, date: str, files: dict[str, str] | None = None) -> str:
        """Push a branch with one commit dated `date` (YYYY-MM-DD); returns its tip."""
        git(self.clone, "checkout", "-q", "-B", name, "origin/main")
        for rel, text in (files or {f"{name.replace('/', '-')}.txt": name}).items():
            (self.clone / rel).write_text(text)
        stamp = {
            "GIT_COMMITTER_DATE": f"{date}T12:00:00",
            "GIT_AUTHOR_DATE": f"{date}T12:00:00",
        }
        git(self.clone, "add", "-A")
        git(self.clone, *IDENTITY, "commit", "-q", "-m", f"feat: {name}", env=stamp)
        git(self.clone, "push", "-q", "origin", f"HEAD:refs/heads/{name}")
        tip = git(self.clone, "rev-parse", "HEAD").strip()
        git(self.clone, "checkout", "-q", "--detach", "origin/main")
        return tip

    def prs(self, state: str, prs: list[dict]) -> None:
        (self.gh_dir / f"{state}.json").write_text(json.dumps(prs))

    def open_claim(
        self,
        title: str,
        who: str,
        number: int,
        updated: str = "2026-11-21",
        machine: str = "box1",
        then_delete: bool = False,
        replace_mark: bool = False,
        draft: bool = False,
    ) -> dict:
        """Push a work branch whose first commit marks `title` as `who`'s, and list its open pull request #`number`,
        last active on `updated`. With `then_delete`, a second commit deletes the entry, as finish-work does; with
        `replace_mark`, the branch swaps a mark main already has for its own; with `draft`, GitHub lists it as a draft,
        as start-work opens it."""
        name = f"work/{who}-{number}"
        base = self.origin_file("docs/todo.md")
        if replace_mark:
            base = re.sub(r" \[claimed by [^\]]+\]", "", base)
        tip = self.branch(
            name, updated, {"docs/todo.md": claimed(base, title, who, updated, machine)}
        )
        if then_delete:
            git(self.clone, "checkout", "-q", "-B", name, tip)
            text = (self.clone / "docs" / "todo.md").read_text()
            entry = next(
                ln for ln in text.splitlines() if ln.startswith(f"- **{title}")
            )
            (self.clone / "docs" / "todo.md").write_text(text.replace(entry + "\n", ""))
            git(self.clone, *IDENTITY, "commit", "-q", "-am", "feat: finish it")
            git(self.clone, "push", "-q", "origin", f"HEAD:refs/heads/{name}")
            tip = git(self.clone, "rev-parse", "HEAD").strip()
            git(self.clone, "checkout", "-q", "--detach", "origin/main")
        pr = {
            "number": number,
            "title": f"feat: {title.lower()}",
            "headRefName": name,
            "headRefOid": tip,
            "isCrossRepository": False,
            "author": {"login": f"{who}-login"},
            "url": f"https://github.com/example/widgets/pull/{number}",
            "updatedAt": f"{updated}T12:00:00Z",
            "isDraft": draft,
        }
        path = self.gh_dir / "open.json"
        listed = json.loads(path.read_text()) if path.exists() else []
        self.prs("open", [*listed, pr])
        return pr

    def gh_fails(self) -> None:
        (self.gh_dir / "fail").write_text("")

    def pr_list_fails(self) -> None:
        """GitHub answers which branch is the default but can't list pull requests."""
        (self.gh_dir / "pr_list_fail").write_text("")

    def default_branch_is(self, name: str) -> None:
        """What `gh repo view` says GitHub's default branch is."""
        (self.gh_dir / "default_branch").write_text(name)

    def edit_local(self, rel: str, old: str, new: str) -> None:
        """Change a file in the working copy only, without committing it."""
        path = self.clone / rel
        text = path.read_text()
        assert old in text, old
        path.write_text(text.replace(old, new))

    def gh_calls(self) -> list[list[str]]:
        log = self.gh_dir / "calls.log"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def origin_file(self, rel: str, ref: str = "main") -> str:
        return git(self.origin, "show", f"{ref}:{rel}")

    def run(
        self,
        *args: str,
        who: str | None = "agent-a",
        today: str = "2026-11-22",
        cwd: Path | None = None,
        command: Path = NEXT_ITEM,
    ) -> tuple[int, str, str]:
        env = {
            **os.environ,
            "PATH": f"{self.bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_DIR": str(self.gh_dir),
            "GOAL_MACHINE": "box1",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        env.pop("CURSOR_CONVERSATION_ID", None)
        env.pop("GOAL_AS", None)
        if who:
            env["GOAL_AS"] = who
        done = subprocess.run(
            [sys.executable, str(command), *args, "--today", today],
            cwd=cwd or self.clone,
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=120,
        )
        return done.returncode, done.stdout, done.stderr

    def json(self, *args: str, **kw: str) -> tuple[int, dict]:
        rc, out, err = self.run(*args, "--json", **kw)
        assert out, err
        return rc, json.loads(out)


def make_project(
    tmp: Path,
    readme: str = README,
    todo: str | None = TODO,
    extra: dict[str, str] | None = None,
) -> Project:
    origin = tmp / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True
    )
    seed = tmp / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    files = {
        "README.md": readme,
        **({"docs/todo.md": todo} if todo is not None else {}),
        **(extra or {}),
    }
    for rel, text in files.items():
        path = seed / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(seed, "add", "-A")
    git(seed, *IDENTITY, "commit", "-q", "-m", "chore: start")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "main")
    clone = tmp / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    git(clone, "checkout", "-q", "--detach", "origin/main")
    gh_dir = tmp / "gh"
    gh_dir.mkdir()
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH.replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
    gh.chmod(0o755)
    return Project(clone, origin, gh_dir, bin_dir)


def claimed(todo: str, title: str, who: str, date: str, machine: str = "box1") -> str:
    """The todo text with `title`'s entry marked as claimed."""
    lead = f"- **{title}"
    assert lead in todo, title
    start = todo.index(lead)
    end = todo.index("\n", start)
    cr = "\r" if todo[end - 1] == "\r" else ""
    line = todo[start : end - len(cr)]
    return (
        todo[:start]
        + f"{line} [claimed by {who} on {machine}, {date}]{cr}"
        + todo[end:]
    )
