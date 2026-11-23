# SPDX-License-Identifier: MIT

"""goal: what to work on next in this repo, from what the repo states in writing.

It reads, from the working copy (so goals edited locally count right away):

- the README's `## How work runs here` section: the milestone or goals file, the todo file and the limit on
  work in progress. When its **Work is recorded in:** line names GitHub issues rather than a todo file, goal
  answers `record: issues` with no item, and the skills work through the issues;
- the todo file: its Defects and Next sections, top first, and the claim marks on their entries;
- `.claude/work.toml`, optional: other heading names, entries that need the owner, the merge method, and a
  plan kept as row files (`rows`): one markdown table row per `<n>.md` file, offered as `<rows_name> <n>` with
  the todo section its Phase maps to (`rows_sections`), once no row it waits on is left.

Claims are the exception: the work's own pull request is the claim. An item is in progress while an open pull
request into the default branch, found by asking GitHub (`gh repo view`, not the local `origin/HEAD`, which can be
stale), carries a claim mark on the item's line. A mark on the default branch, from before claims moved to pull
requests, still counts; a mark only the working copy has is reported and not counted. When GitHub can't be reached, goal says claims weren't checked and answers
from the local files alone.

Then it answers in a fixed order: your own claimed item; else the top free defect, one fix at a time; else the
top free Next item, with a warning when that goes over the limit; else a cleanup. Nothing here blocks: the
limit and the order are warnings and advice. When the repo doesn't say enough, it names what is missing and
exits 2; it never guesses.

`goal claim` commits the mark on one entry's line as the work branch's first commit; the claim holds once the
work's pull request is open. `goal release` commits the mark away. `goal merged <branch>` says whether a branch's
work is on main.

Exit codes: 0 an answer, 1 an error, 2 unclear (what is missing is named), 3 no (the item is someone else's,
or the branch is not merged).

Python 3.9 or later, standard library only.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import getpass
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

SECTION = "How work runs here"
STALE_DAYS = 3
OK, ERROR, UNCLEAR, NO = 0, 1, 2, 3
# The mark ends the entry's first line, so the entry still starts with its bold lead for any check that reads it.
MARK = re.compile(
    r" \[claimed by (?P<who>[^\]]+?) on (?P<machine>[^\],]+), (?P<date>\d{4}-\d{2}-\d{2})\](?=\r?\n?$)"
)
SETTINGS_PATH = ".claude/work.toml"


class Unclear(Exception):
    """The repo doesn't say enough to answer; the message names what is missing."""


class Refused(Exception):
    """The answer is no: the item is someone else's, or another claim on it is open."""


# --- settings ----------------------------------------------------------------------------------------------


@dataclass
class Settings:
    defects: list[str] = field(default_factory=lambda: ["Defects", "Bugs"])
    next: list[str] = field(default_factory=lambda: ["Next"])
    skip: list[str] = field(default_factory=lambda: ["(owner)"])
    merge_method: str = "squash"  # how the pr skill merges; goal only checks it
    branch_prefix: str = "work/"
    named_defects: bool = False  # true when the settings file named the defects heading
    rows: str = ""  # a folder of row files, `<n>.md`, each one markdown table row under its header
    rows_name: str = "row"  # a row's item title is `<rows_name> <n>`
    rows_sections: list[str] = field(
        default_factory=list
    )  # `<phase>=<section>`: rows of that Phase come just before that next section's entries


_KEYS = {
    "defects": list,
    "next": list,
    "skip": list,
    "merge_method": str,
    "branch_prefix": str,
    "rows": str,
    "rows_name": str,
    "rows_sections": list,
}


def _toml_subset(text: str) -> dict:
    """Flat `key = "string"` and `key = ["a", "b"]` lines, with comments: enough for this file on 3.9 and
    3.10, which have no tomllib."""
    out: dict = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)", line)
        if not m:
            raise ValueError(f"{SETTINGS_PATH} line {n}: expected `key = value`")
        key, value = m.groups()
        value = _strip_comment(value)
        try:
            out[key] = json.loads(value.replace("'", '"'))
        except json.JSONDecodeError:
            raise ValueError(
                f"{SETTINGS_PATH} line {n}: `{value}` is not a string or a list of strings"
            ) from None
    return out


def _strip_comment(value: str) -> str:
    in_str, quote = False, ""
    for i, ch in enumerate(value):
        if ch in "\"'" and (not in_str or ch == quote):
            in_str, quote = not in_str, ch
        elif ch == "#" and not in_str:
            return value[:i].strip()
    return value.strip()


def parse_settings(text: str, use_tomllib: bool = True) -> Settings:
    data: dict
    try:
        if not use_tomllib:
            raise ImportError
        import tomllib  # type: ignore[import-not-found]

        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"{SETTINGS_PATH}: {e}") from None
    except ImportError:
        data = _toml_subset(text)
    s = Settings()
    for key, value in data.items():
        kind = _KEYS.get(key)
        if kind is None:
            raise ValueError(
                f"{SETTINGS_PATH}: unknown setting `{key}` (known: {', '.join(_KEYS)})"
            )
        if kind is list and not (
            isinstance(value, list) and all(isinstance(v, str) for v in value)
        ):
            raise ValueError(f"{SETTINGS_PATH}: `{key}` is a list of strings")
        if kind is str and not isinstance(value, str):
            raise ValueError(f"{SETTINGS_PATH}: `{key}` is a string")
        setattr(s, key, value)
    if "defects" in data:
        s.named_defects = True
    bad = [v for v in s.rows_sections if "=" not in v]
    if bad:
        raise ValueError(
            f"{SETTINGS_PATH}: rows_sections pairs are `<phase>=<section>`, not `{bad[0]}`"
        )
    if s.merge_method not in ("squash", "rebase", "merge"):
        raise ValueError(f"{SETTINGS_PATH}: merge_method is squash, rebase or merge")
    return s


# --- the README section ------------------------------------------------------------------------------------


def parse_readme(text: str) -> dict[str, str] | None:
    """The section's facts by lower-case label, or None when there is no section. A line such as
    `- **Current milestone:** M2. **Done when:** it rings. **By:** no date` gives three facts."""
    lines = text.splitlines()
    start = next(
        (
            i
            for i, ln in enumerate(lines)
            if re.fullmatch(rf"##\s+{SECTION}\s*#*\s*", ln.strip(), re.IGNORECASE)
        ),
        None,
    )
    if start is None:
        return None
    items: list[str] = []
    for ln in lines[start + 1 :]:
        if re.match(r"#{1,2}\s", ln):
            break
        if ln.startswith("- "):
            items.append(ln[2:].strip())
        elif ln.startswith(" ") and ln.strip() and items:
            items[-1] += " " + ln.strip()
    facts: dict[str, str] = {}
    for item in items:
        parts = re.split(r"\*\*([^*]+?):\*\*", item)
        for label, value in zip(parts[1::2], parts[2::2]):
            facts[label.strip().lower()] = value.strip().rstrip(".").strip()
    return facts


def _path_in(value: str) -> str | None:
    m = re.search(r"\]\(([^)\s#]+)", value) or re.search(r"`([^`\s]+)`", value)
    return m.group(1) if m else None


def todo_path(facts: dict[str, str]) -> str | None:
    value = facts.get("work is recorded in")
    path = _path_in(value) if value else None
    return path if path and "://" not in path else None


def in_issues(facts: dict[str, str]) -> bool:
    """The **Work is recorded in:** line names GitHub issues and no todo file."""
    value = facts.get("work is recorded in") or ""
    return todo_path(facts) is None and bool(
        re.search(r"\bgithub issues?\b", value, re.IGNORECASE)
    )


def goals_path(facts: dict[str, str]) -> str | None:
    value = facts.get("goals")
    return _path_in(value) if value else None


def wip_limit(facts: dict[str, str]) -> int | None:
    m = re.search(r"at most\s+(\d+)", facts.get("work in progress", ""), re.IGNORECASE)
    return int(m.group(1)) if m else None


# --- the todo file -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    who: str
    machine: str
    date: str
    # The open pull request whose branch carries the mark; None for a mark read from a file alone.
    pr: int | None = None
    author: str | None = None
    url: str | None = None
    head: str | None = None
    active: str | None = None  # the pull request's last activity, YYYY-MM-DD

    @property
    def mark(self) -> tuple[str, str, str]:
        return (self.who, self.machine, self.date)

    def holder(self) -> str:
        where = f"#{self.pr}" if self.pr else "a mark on the default branch"
        return f"{where} ({self.who} on {self.machine})"


@dataclass
class Entry:
    title: str
    section: str
    line: int
    text: str
    claim: Claim | None = None
    path: str = ""  # the file the entry's line is in; the todo file when empty
    waits: tuple[
        int, ...
    ] = ()  # a row's Waits on: rows that must be built (their files gone) first


@dataclass
class Todo:
    defects: list[Entry]
    next: list[Entry]
    missing: list[str]

    @property
    def entries(self) -> list[Entry]:
        return self.defects + self.next


def _title(first: str) -> str:
    body = MARK.sub("", first)[2:].strip()
    m = re.match(r"\*\*(.+?)\*\*", body)
    if m:
        title = re.sub(r"^\([^()]*\)\s*", "", m.group(1).strip())
    else:
        title = re.split(r"(?<=[.!?])\s", body, maxsplit=1)[0]
    return title.strip().rstrip(".:;,!?").strip()


def _sections(text: str) -> list[tuple[str, int, int]]:
    """(heading text, first line index, end index) for each `## ` heading; a section runs to the next `# ` or
    `## `, so its `###` subsections belong to it."""
    lines = text.splitlines()
    heads = [(i, ln[3:].strip()) for i, ln in enumerate(lines) if ln.startswith("## ")]
    ends = [i for i, ln in enumerate(lines) if re.match(r"#{1,2}\s", ln)] + [len(lines)]
    out = []
    for i, name in heads:
        end = next(e for e in ends if e > i)
        out.append((name, i, end))
    return out


def _matches(heading: str, name: str) -> bool:
    return re.match(rf"{re.escape(name)}(?!\w)", heading, re.IGNORECASE) is not None


def _entries(lines: list[str], section: str, start: int, end: int) -> list[Entry]:
    found: list[Entry] = []
    in_code = False
    for i in range(start + 1, end):
        ln = lines[i]
        if ln.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if ln.startswith("- "):
            m = MARK.search(ln)
            claim = Claim(m["who"], m["machine"], m["date"]) if m else None
            found.append(Entry(_title(ln), section, i + 1, ln, claim))
        elif found and ln.startswith((" ", "\t")) and ln.strip():
            found[-1].text += "\n" + ln
    return found


def parse_todo(text: str, settings: Settings) -> Todo:
    lines = text.splitlines()
    sections = _sections(text)
    missing: list[str] = []

    def take(names: list[str], first_only: bool) -> list[Entry] | None:
        got: list[Entry] = []
        any_found = False
        for name in names:
            hit = next(((h, s, e) for h, s, e in sections if _matches(h, name)), None)
            if hit is None:
                if not first_only:
                    missing.append(f"a `## {name}` heading")
                continue
            any_found = True
            got += _entries(lines, name, hit[1], hit[2])
            if first_only:
                break
        return got if any_found else None

    # By default the defects heading is `Defects` or `Bugs`, whichever is there; a settings file names its own.
    if settings.named_defects:
        defects = take(settings.defects, first_only=False) or []
    else:
        found = take(settings.defects, first_only=True)
        if found is None:
            missing.append(
                "a `## " + "` (or `## ".join(settings.defects) + "`) heading"
            )
        defects = found or []
    nxt = take(settings.next, first_only=False) or []
    return Todo(defects, nxt, missing)


def mark(text: str, line: int, claim: Claim) -> str:
    """The text with entry `line` (1-based) marked as claimed, at the end of that line."""
    lines = text.splitlines(keepends=True)
    ln = lines[line - 1]
    if not ln.startswith(("- ", "|")) or MARK.search(ln):
        raise ValueError(f"line {line} is not an unclaimed entry")
    body = ln.rstrip("\r\n")
    lines[line - 1] = (
        f"{body.rstrip()} [claimed by {claim.who} on {claim.machine}, {claim.date}]"
        + ln[len(body) :]
    )
    return "".join(lines)


def unmark(text: str, line: int) -> str:
    lines = text.splitlines(keepends=True)
    if not MARK.search(lines[line - 1]):
        raise ValueError(f"line {line} is not claimed")
    lines[line - 1] = MARK.sub("", lines[line - 1], count=1)
    return "".join(lines)


# --- a plan kept as row files ------------------------------------------------------------------------------


def _cells(line: str) -> list[str]:
    return [c.strip() for c in MARK.sub("", line).strip().strip("|").split("|")]


def _waits(cell: str) -> tuple[int, ...]:
    """The row numbers a Waits on cell names: `—`, or numbers and `a–b` spans, each with an optional `(why)`, then
    any `; check: ...` parts, which wait on no row."""
    out: list[int] = []
    for part in re.sub(r"\([^()]*\)", "", cell.split(";", 1)[0]).split(","):
        if part.strip().lower().startswith("check:"):
            break
        m = re.fullmatch(r"\s*(\d+)(?:\s*[–-]\s*(\d+))?\s*", part)
        if m:
            out += range(int(m[1]), int(m[2] or m[1]) + 1)
    return tuple(out)


def parse_rows(files: list[tuple[str, str]], settings: Settings) -> list[Entry]:
    """Each `<n>.md` file's one table row, as an entry titled `<rows_name> <n>`. ValueError names a file that is
    not one row numbered as the file is, so a slip drops no row silently."""
    sections = {
        k.strip().lower(): v.strip()
        for k, v in (pair.split("=", 1) for pair in settings.rows_sections)
    }
    out: list[Entry] = []
    for path, text in sorted(
        files,
        key=lambda f: (int(Path(f[0]).stem) if Path(f[0]).stem.isdigit() else -1, f[0]),
    ):
        stem = Path(path).stem
        lines = text.splitlines()
        table = [(i, ln) for i, ln in enumerate(lines) if ln.startswith("|")]
        if not stem.isdigit() or len(table) != 3:
            raise ValueError(
                f"{path}: not one table row under a header in a file named `<n>.md`"
            )
        names = [c.lower() for c in _cells(table[0][1])]
        i, ln = table[2]
        cells = _cells(ln)
        if (
            len(cells) != len(names)
            or "#" not in names
            or "step" not in names
            or cells[names.index("#")] != stem
        ):
            raise ValueError(
                f"{path}: its row is not numbered {stem}, with one cell for each column of a header that has `#`"
                " and `Step`"
            )

        by_name = dict(zip(names, cells))
        m = MARK.search(ln)
        out.append(
            Entry(
                f"{settings.rows_name} {stem}",
                sections.get(by_name.get("phase", "").lower(), ""),
                i + 1,
                # What `skip` reads: every cell but Waits on, whose checks name what is checked, not who works.
                " | ".join(c for n, c in by_name.items() if n != "waits on"),
                Claim(m["who"], m["machine"], m["date"]) if m else None,
                path,
                _waits(by_name.get("waits on", "")),
            )
        )
    return out


def with_rows(todo: Todo, rows: list[Entry], settings: Settings) -> Todo:
    """The todo with the rows added to its Next list: each row just before its section's entries, moved up to
    the earliest section of any row that waits on it; a row with no section comes last."""
    order = {name.lower(): n for n, name in enumerate(settings.next)}
    last = len(settings.next)
    rank = {key(r.title): order.get(r.section.lower(), last) for r in rows}
    by_num = {int(Path(r.path).stem): r for r in rows}
    changed = True
    while changed:
        changed = False
        for r in rows:
            for n in r.waits:
                dep = by_num.get(n)
                if dep is not None and rank[key(dep.title)] > rank[key(r.title)]:
                    rank[key(dep.title)] = rank[key(r.title)]
                    changed = True
    present = set(by_num)
    for r in rows:
        r.waits = tuple(n for n in r.waits if n in present)
        if rank[key(r.title)] < last:
            r.section = settings.next[rank[key(r.title)]]

    def place(e: Entry) -> tuple[int, int]:
        if e.path:
            return (rank[key(e.title)], 0)
        return (order.get(e.section.lower(), last), 1)

    nxt = sorted(todo.next + rows, key=place)  # stable: file order within a place
    return Todo(todo.defects, nxt, todo.missing)


def read_todo(path: str, settings: Settings, get, ls, strict: bool = True) -> Todo:
    """The todo file and the plan's rows, read through `get(path)` and `ls(folder)` (the working copy, or a commit).
    With `strict`, a bad row file raises ValueError; otherwise it is left out."""
    todo = parse_todo(get(path) or "", settings)
    if not settings.rows:
        return todo
    files = []
    for name in ls(settings.rows) or []:
        if name.endswith(".md"):
            rel = f"{settings.rows.rstrip('/')}/{name}"
            files.append((rel, get(rel) or ""))
    try:
        rows = parse_rows(files, settings)
    except ValueError:
        if strict:
            raise
        rows = []
        for f in files:
            try:
                rows += parse_rows([f], settings)
            except ValueError:
                continue
    return with_rows(todo, rows, settings)


def key(text: str) -> str:
    """Case, quotes, backticks, trailing punctuation and spacing do not tell titles apart."""
    text = re.sub(r"[`'\"‘’“”*]", "", text)
    return re.sub(r"\s+", " ", text).strip().lower().rstrip(".,;:!?").strip()


def slug(text: str) -> str:
    """A branch-safe name for a title. A title with nothing ASCII to keep, or cut at 50 characters, gets a short
    hash of the whole title, so two titles never share a branch by sharing a start."""
    full = re.sub(r"[^a-z0-9]+", "-", key(text)).strip("-")
    short = hashlib.sha1(key(text).encode("utf-8")).hexdigest()[:6]
    if not full:
        return f"item-{short}"
    if len(full) > 50:
        return f"{full[:50].rstrip('-')}-{short}"
    return full


def _safe(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._:@/+-]+", "-", label).strip("-") or "unknown"


# --- the answer --------------------------------------------------------------------------------------------


@dataclass
class Me:
    who: str
    machine: str
    named: bool = (
        True  # False: only the login name, which every session of this user shares
    )
    branch: str | None = (
        None  # the work branch checked out here; None on the default branch or a detached HEAD
    )

    def holds(self, claim: Claim) -> bool:
        """The claim is ours. On a work branch, a pull request's claim is ours when it comes from this branch: two
        sessions can share a name (subagents of one session, one stable $GOAL_AS), never a branch. Elsewhere, and
        for a mark on the default branch, which has no branch, the name decides."""
        if claim.pr and self.branch:
            return claim.head == self.branch
        return (claim.who, claim.machine) == (self.who, self.machine)


def _date(text: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def is_stale(claim: Claim, today: dt.date) -> bool:
    """No activity on the claim's pull request for STALE_DAYS. A mark read from a file alone, when GitHub can't be
    reached, has no pull request to judge."""
    active = _date(claim.active or "")
    return active is not None and (today - active).days >= STALE_DAYS


def _free(e: Entry, settings: Settings) -> bool:
    return (
        e.claim is None and not e.waits and not any(m in e.text for m in settings.skip)
    )


def _item(e: Entry, settings: Settings | None = None) -> dict:
    """The entry as the answer names it; `branch` is the work branch the next and work-start skills create."""
    prefix = (settings or Settings()).branch_prefix
    return {
        "title": e.title,
        "section": e.section,
        "line": e.line,
        **({"path": e.path} if e.path else {}),
        "branch": prefix + slug(e.title),
    }


def decide(
    todo: Todo, me: Me, limit: int, settings: Settings
) -> tuple[dict, list[str]]:
    """The next step and any warnings, in the fixed order."""
    claims = [e for e in todo.entries if e.claim]
    own = [e for e in claims if e.claim and me.holds(e.claim)]
    if own:
        return {
            "kind": "own",
            "item": _item(own[0], settings),
            "reason": "you hold this claim: finish it before starting another",
        }, []
    in_flight = [e for e in todo.defects if e.claim]
    free_defects = [e for e in todo.defects if _free(e, settings)]
    note = ""
    if free_defects and not in_flight:
        return {
            "kind": "defect",
            "item": _item(free_defects[0], settings),
            "reason": "the top free defect; defects come before anything new",
        }, []
    if free_defects and in_flight:
        holder = in_flight[0].claim
        assert holder is not None
        note = (
            f'; a defect fix is in flight ("{in_flight[0].title}", held by {holder.who}), and defect fixes run'
            " one at a time"
        )
    free_next = [e for e in todo.next if _free(e, settings)]
    if free_next:
        warnings = []
        if len(claims) + 1 > limit:
            warnings.append(
                f"taking this makes {len(claims) + 1} in progress, over the limit of {limit}"
            )
        return {
            "kind": "next",
            "item": _item(free_next[0], settings),
            "reason": f"the top free item in {free_next[0].section}{note}",
        }, warnings
    return {
        "kind": "cleanup",
        "item": None,
        "reason": "nothing is free: take a risk-free cleanup (delete code, fix a test or a doc)"
        + note,
    }, []


# --- git and GitHub ----------------------------------------------------------------------------------------


def run(
    cmd: list[str], cwd: Path, check: bool = True, timeout: int = 60
) -> subprocess.CompletedProcess:
    try:
        done = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        if check:
            raise RuntimeError(f"`{' '.join(cmd[:3])}` failed: {e}") from None
        return subprocess.CompletedProcess(cmd, 127, "", str(e))
    if check and done.returncode != 0:
        raise RuntimeError(
            f"`{' '.join(cmd)}` failed: {done.stderr.strip() or done.stdout.strip()}"
        )
    return done


def gh_json(args: list[str], cwd: Path) -> list[dict] | None:
    done = run(["gh", *args], cwd, check=False)
    if done.returncode != 0:
        return None
    try:
        out = json.loads(done.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, list) else None


class Repo:
    def __init__(self, cwd: Path, fetch: bool) -> None:
        top = run(["git", "rev-parse", "--show-toplevel"], cwd, check=False)
        if top.returncode != 0:
            raise RuntimeError(f"{cwd} is not inside a git repository")
        self.root = Path(top.stdout.strip())
        self.warnings: list[str] = []
        self.asked_github = self._github_base()
        self.base = self.asked_github or self._base()
        if fetch:
            done = run(
                ["git", "fetch", "-q", "origin"], self.root, check=False, timeout=60
            )
            if done.returncode != 0:
                self.warnings.append(
                    f"could not fetch origin; read the last fetched origin/{self.base}"
                )
        self.ref = f"origin/{self.base}"
        self.has_ref = (
            run(
                ["git", "rev-parse", "--verify", "-q", self.ref], self.root, check=False
            ).returncode
            == 0
        )

    def require_ref(self) -> None:
        """Claiming, releasing and the merged test build on origin's default branch: it must be here."""
        if self.asked_github is None:
            self.warnings.append(
                f"could not ask GitHub for the default branch; used {self.ref}, which may be stale"
            )
        if not self.has_ref:
            raise RuntimeError(
                f"no {self.ref}: run goal in a clone whose origin has a default branch"
            )

    def _github_base(self) -> str | None:
        """The default branch, as GitHub says it (the local origin/HEAD can be stale), or None."""
        done = run(
            ["gh", "repo", "view", "--json", "defaultBranchRef"], self.root, check=False
        )
        if done.returncode != 0:
            return None
        try:
            name = json.loads(done.stdout)["defaultBranchRef"]["name"]
        except (ValueError, KeyError, TypeError):
            return None
        ok = isinstance(name, str) and re.fullmatch(
            r"[A-Za-z0-9_][A-Za-z0-9._/-]*", name
        )
        return name if ok else None

    def _base(self) -> str:
        done = run(
            ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            self.root,
            check=False,
        )
        if done.returncode == 0 and done.stdout.strip().startswith("origin/"):
            return done.stdout.strip()[len("origin/") :]
        for name in ("main", "master"):
            if (
                run(
                    ["git", "rev-parse", "--verify", "-q", f"origin/{name}"],
                    self.root,
                    check=False,
                ).returncode
                == 0
            ):
                return name
        return "main"

    def show(self, path: str, ref: str | None = None) -> str | None:
        """A file as committed, line endings and all (text mode would turn CRLF into LF, and a claim would
        then rewrite every line)."""
        try:
            done = subprocess.run(
                ["git", "show", f"{ref or self.ref}:{path}"],
                cwd=self.root,
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return done.stdout.decode("utf-8", "replace") if done.returncode == 0 else None

    def read(self, path: str) -> str | None:
        """A file in the working copy, line endings and all; None when it isn't a plain file inside the repo."""
        target = self.root / path
        try:
            if (
                not target.resolve().is_relative_to(self.root.resolve())
                or not target.is_file()
            ):
                return None
            return target.read_bytes().decode("utf-8", "replace")
        except OSError:
            return None

    def ls(self, folder: str, ref: str | None = None) -> list[str] | None:
        """The file names in a folder as committed, or in the working copy with ref "" (None when it isn't there)."""
        if ref == "":
            target = self.root / folder
            try:
                if not target.resolve().is_relative_to(self.root.resolve()):
                    return None
                return sorted(x.name for x in target.iterdir() if x.is_file())
            except OSError:
                return None
        done = run(
            ["git", "ls-tree", "--name-only", f"{ref or self.ref}:{folder}"],
            self.root,
            check=False,
        )
        return done.stdout.splitlines() if done.returncode == 0 else None

    def todo_at(self, path: str, settings: Settings, ref: str | None = None) -> Todo:
        """The todo and the plan's rows as committed at ref (origin's default branch when None); a bad row file is
        left out."""
        return read_todo(
            path,
            settings,
            lambda rel: self.show(rel, ref=ref),
            lambda folder: self.ls(folder, ref=ref),
            strict=False,
        )

    def branch_name(self) -> str | None:
        """The checked-out branch, or None on a detached HEAD."""
        done = run(
            ["git", "symbolic-ref", "--short", "-q", "HEAD"], self.root, check=False
        )
        return (done.stdout.strip() or None) if done.returncode == 0 else None

    def has_commit(self, rev: str) -> bool:
        return (
            run(
                ["git", "rev-parse", "--verify", "-q", rev + "^{commit}"],
                self.root,
                check=False,
            ).returncode
            == 0
        )


@dataclass
class State:
    repo: Repo
    facts: dict[str, str]
    settings: Settings
    todo_path: str
    todo_text: str
    todo: Todo
    claims_checked: bool = False
    uncounted: list[str] = field(
        default_factory=list
    )  # marked only in the working copy
    on_base: list[str] = field(
        default_factory=list
    )  # held by a mark on the default branch
    conflicts: list[tuple[str, list[Claim]]] = field(default_factory=list)
    unread: list[int] = field(
        default_factory=list
    )  # open pull requests whose branch couldn't be read


@dataclass
class Live:
    """The claims by title key, first come first: marks on the default branch, then the open pull requests' marks;
    and the default branch's marks alone, which every branch made from it carries."""

    holders: dict[str, list[Claim]]
    on_base: dict[str, Claim]
    unread: list[int] = field(
        default_factory=list
    )  # open pull requests whose branch couldn't be read


def _pr_head(repo: Repo, pr: dict) -> str | None:
    """A local name for the pull request's head: its exact commit when fetched, else origin's branch of that name.
    The caller never asks for a fork's pull request."""
    oid = pr.get("headRefOid")
    if (
        isinstance(oid, str)
        and re.fullmatch(r"[0-9a-f]{40}", oid)
        and repo.has_commit(oid)
    ):
        return oid
    name = pr.get("headRefName")
    if not isinstance(name, str) or not name:
        return None
    ref = f"refs/remotes/origin/{name}"
    return ref if repo.has_commit(ref) else None


def _branch_marks(
    repo: Repo, head: str, path: str, settings: Settings, keys: set[str]
) -> dict[str, Claim]:
    """The marks a branch carries, by title key. An entry its tip no longer has is judged by the latest commit on
    the branch that still had it: work-finish deletes the claimed entry in the work's last commit, and the claim
    holds until the pull request merges."""
    entries = {key(e.title): e for e in repo.todo_at(path, settings, ref=head).entries}
    out = {k: e.claim for k, e in entries.items() if e.claim}
    gone = keys - set(entries)
    if not gone:
        return out
    revs = run(
        ["git", "rev-list", "--max-count=20", f"{repo.ref}..{head}", "--", path]
        + ([settings.rows] if settings.rows else []),
        repo.root,
        check=False,
    ).stdout.split()
    for rev in revs:
        older = {key(e.title): e for e in repo.todo_at(path, settings, ref=rev).entries}
        for k in gone & set(older):
            claim = older[k].claim
            if claim:
                out[k] = claim
        gone -= set(older)
        if not gone:
            break
    return out


def _fork_marks(
    repo: Repo, head: str, path: str, settings: Settings
) -> dict[str, Claim] | None:
    """The marks on the default branch where the branch left it, by title key; None when that can't be found. A
    mark the branch has only because it was there then is not the pull request's claim, even once it is gone
    from the default branch."""
    done = run(["git", "merge-base", repo.ref, head], repo.root, check=False)
    base = done.stdout.strip()
    if done.returncode != 0 or not base:
        return None
    return {
        key(e.title): e.claim
        for e in repo.todo_at(path, settings, ref=base).entries
        if e.claim
    }


def _live_claims(
    repo: Repo, path: str, settings: Settings, keys: set[str]
) -> Live | None:
    """The claims the open pull requests into the default branch carry, or None when they can't be read."""
    if repo.asked_github is None:
        repo.warnings.append(
            "claims weren't checked: could not ask GitHub for the default branch; answering from the local files"
        )
        return None
    if not repo.has_ref:
        repo.warnings.append(
            f"claims weren't checked: no {repo.ref} here; answering from the local files"
        )
        return None
    prs = gh_json(
        [
            "pr",
            "list",
            "--state",
            "open",
            "--base",
            repo.base,
            "--limit",
            "200",
            "--json",
            "number,headRefName,headRefOid,isCrossRepository,author,url,updatedAt",
        ],
        repo.root,
    )
    if prs is None:
        repo.warnings.append(
            "claims weren't checked: could not list the open pull requests on GitHub; answering from the local files"
        )
        return None
    on_base = {
        key(e.title): e.claim for e in repo.todo_at(path, settings).entries if e.claim
    }
    # A mark on the default branch, made before claims moved to pull requests, still holds, and holds first.
    holders: dict[str, list[Claim]] = {
        k: [dataclasses.replace(c, active=c.date)] for k, c in on_base.items()
    }
    unread: list[int] = []
    # Never a fork's pull request: anyone can open one, so it can't hold an item.
    for pr in sorted(
        (
            pr
            for pr in prs
            if isinstance(pr.get("number"), int)
            and pr.get("isCrossRepository") is False
        ),
        key=lambda pr: pr["number"],
    ):
        head = _pr_head(repo, pr)
        if head is None:
            unread.append(pr["number"])
            repo.warnings.append(
                f"could not read the branch of #{pr['number']}: a claim it carries isn't counted"
            )
            continue
        author = pr.get("author")
        before = _fork_marks(repo, head, path, settings)
        if before is None:
            before = on_base
        for k, c in _branch_marks(repo, head, path, settings, keys).items():
            if k in before and before[k].mark == c.mark:
                continue  # on the default branch where this branch left it, so not this pull request's
            holders.setdefault(k, []).append(
                Claim(
                    c.who,
                    c.machine,
                    c.date,
                    pr=pr["number"],
                    author=author.get("login") if isinstance(author, dict) else None,
                    url=pr.get("url"),
                    head=pr.get("headRefName"),
                    active=(pr.get("updatedAt") or "")[:10] or None,
                )
            )
    return Live(holders, on_base, unread)


def load(
    repo: Repo, today: dt.date, need_goal: bool = True
) -> tuple[State | None, dict, list[str]]:
    """Everything the answer needs, or the list of what is missing. The files come from the working copy; the
    claims from the open pull requests, or from the working copy's marks when GitHub can't be reached."""
    missing: list[str] = []
    readme = repo.read("README.md")
    facts = parse_readme(readme or "")
    goal_info: dict = {}
    if facts is None:
        missing.append(
            f"the README has no `## {SECTION}` section: what is the current milestone, how will we know it's done,"
            " where is the work recorded, and how many items may be in progress at once?"
        )
        return None, goal_info, missing
    if "frozen" in facts:
        return None, {"frozen": facts["frozen"]}, []
    goal_info = {
        "mission": facts.get("mission"),
        "milestone": facts.get("current milestone"),
        "done_when": facts.get("done when"),
        "by": facts.get("by"),
        "goals_file": goals_path(facts),
    }
    if in_issues(facts):
        # The repo keeps its work in GitHub issues: goal has no list to read, and says so.
        goal_info["recorded_in"] = facts["work is recorded in"]
        return None, goal_info, []
    # Claiming and releasing need only the todo file and the settings, so an overdue milestone never stops an
    # agent from dropping its claim.
    if need_goal and not goal_info["milestone"] and not goal_info["goals_file"]:
        missing.append(
            "no milestone stated: add **Current milestone:** with **Done when:**, or **Goals:** linking the file"
            " that states it"
        )
    by = goal_info["by"] or ""
    due = _date(by) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", by) else None
    if need_goal and re.fullmatch(r"\d{4}-\d{2}-\d{2}", by) and due is None:
        missing.append(f"the **By:** date {by} is not a real date")
    if need_goal and due is not None and due < today:
        missing.append(
            f"{goal_info['milestone'] or 'the milestone'} was due {by}: is it still the milestone, with a new date?"
        )
    path = todo_path(facts)
    if path is None:
        missing.append(
            "the **Work is recorded in:** line names neither a todo file (in backticks or a link) nor GitHub"
            " issues"
            if "work is recorded in" in facts
            else "no **Work is recorded in:** line naming the todo file (in backticks or a link) or GitHub issues"
        )
    limit = wip_limit(facts)
    if limit is None:
        missing.append("no **Work in progress:** line saying `at most <N>`")
    text = repo.read(SETTINGS_PATH)
    try:
        settings = parse_settings(text) if text is not None else Settings()
    except ValueError as e:
        missing.append(str(e))
        settings = Settings()
    todo_text = repo.read(path) if path else None
    if path and todo_text is None:
        missing.append(f"{path} is not in the working copy")
    try:
        todo = read_todo(
            path or "",
            settings,
            lambda rel: todo_text if rel == path else repo.read(rel),
            lambda folder: repo.ls(folder, ref=""),
        )
    except ValueError as e:
        missing.append(str(e))
        todo = parse_todo(todo_text or "", settings)
    if todo_text is not None:
        missing += [f"{path} has no {m}" for m in todo.missing]
        for e in todo.entries:
            if e.claim and _date(e.claim.date) is None:
                missing.append(
                    f"{path} line {e.line}: the claim date {e.claim.date} is not a real date"
                )
    if missing:
        return None, goal_info, missing
    assert path is not None and todo_text is not None
    state = State(repo, facts, settings, path, todo_text, todo)
    live = _live_claims(repo, path, settings, {key(e.title) for e in todo.entries})
    if live is None:  # unchecked: the marks stay as the working copy has them
        return state, goal_info, []
    state.claims_checked = True
    state.unread = live.unread
    for e in todo.entries:
        k = key(e.title)
        held = live.holders.get(k, [])
        if e.claim and not any(h.mark == e.claim.mark for h in held):
            state.uncounted.append(e.title)
        if k in live.on_base:
            state.on_base.append(e.title)
        e.claim = held[0] if held else None
        if len(held) > 1:
            state.conflicts.append((e.title, held))
    return state, goal_info, []


def me_from_env(label: str | None) -> Me:
    who = label or os.environ.get("GOAL_AS")
    if not who:
        sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get(
            "CURSOR_CONVERSATION_ID"
        )
        if not sid:
            machine = (
                os.environ.get("GOAL_MACHINE") or socket.gethostname().split(".")[0]
            )
            return Me(_safe(getpass.getuser()), _safe(machine), named=False)
        who = f"session-{sid[:8]}"
    machine = os.environ.get("GOAL_MACHINE") or socket.gethostname().split(".")[0]
    return Me(_safe(who), _safe(machine))


ISSUES = (
    "this repo records its work in GitHub issues: find, claim and finish work there, by the repo's own"
    " issue-tracking skill or rules when it has them, else by the steps in the work-start skill"
)


def no_todo(goal_info: dict) -> str:
    """Why claim and release have no todo file to mark."""
    if "recorded_in" in goal_info:
        return "this repo records its work in GitHub issues, not a todo file: claim the issue instead"
    return "this repo is frozen"


NO_NAME = (
    "no name to claim as: every session of this user on this machine would share one, so set GOAL_AS (or pass"
    " --as), or run inside a Claude Code session"
)


# --- the commands ------------------------------------------------------------------------------------------


def answer(args: argparse.Namespace, today: dt.date) -> tuple[int, dict]:
    repo = Repo(Path(args.C or "."), fetch=not args.no_fetch)
    state, goal_info, missing = load(repo, today)
    out: dict = {
        "status": "ok",
        "goal": goal_info,
        "warnings": list(repo.warnings),
        "source": "working copy",
        "claims_from": repo.ref if state and state.claims_checked else None,
    }
    if "frozen" in goal_info:
        out["next"] = {
            "kind": "frozen",
            "item": None,
            "reason": f"this repo is frozen: {goal_info['frozen']}",
        }
        return OK, out
    if "recorded_in" in goal_info:
        out.update(
            record="issues", next={"kind": "issues", "item": None, "reason": ISSUES}
        )
        return OK, out
    if state is None:
        out.update(status="unclear", missing=missing, next=None)
        return UNCLEAR, out
    out["record"] = "todo"
    me = me_from_env(args.as_)
    work = repo.branch_name()
    me.branch = work if work != repo.base else None
    out["uncounted_claims"] = state.uncounted
    out["warnings"] += [
        f'the claim mark on "{t}" is not counted: no open pull request carries it (push its branch and open the'
        " pull request)"
        for t in state.uncounted
    ] + [
        f'"{t}" is held by a mark on {repo.ref}, made before claims moved to pull requests: it counts until the'
        " work deletes the entry, or its holder (or the owner, with goal release --holder) removes the mark"
        for t in state.on_base
    ]
    out["conflicts"] = []
    for title, held in state.conflicts:
        first, later = held[0], held[1:]
        out["conflicts"].append(
            {
                "title": title,
                "holder": asdict(first),
                "later": [asdict(c) for c in later],
            }
        )
        out["warnings"].append(
            f'"{title}" is marked by more than one open pull request: {first.holder()} came first and holds it;'
            f" {', '.join(c.holder() for c in later)} came later and should stop"
        )
        out["warnings"] += [
            f'you hold the later claim on "{title}" in #{c.pr}: stop, and close it, or move its work to another'
            " item with goal release"
            for c in later
            if me.holds(c)
        ]
    if not me.named:
        out["warnings"].append(
            f"{NO_NAME}; your own claims are told apart by the login name only"
        )
    limit = wip_limit(state.facts)
    assert limit is not None
    claims = [
        {
            **_item(e, state.settings),
            **asdict(e.claim),
            "stale": is_stale(e.claim, today),
        }
        for e in state.todo.entries
        if e.claim
    ]
    nxt, warnings = decide(state.todo, me, limit, state.settings)
    out.update(
        todo=state.todo_path,
        next=nxt,
        in_progress={"count": len(claims), "limit": limit, "claims": claims},
        stale=[c["title"] for c in claims if c["stale"]],
        needs_owner=[
            e.title
            for e in state.todo.entries
            if e.claim is None and any(m in e.text for m in state.settings.skip)
        ],
        me=asdict(me),
    )
    out["warnings"] += warnings
    if args.list:
        out["free"] = [
            _item(e, state.settings)
            for e in state.todo.entries
            if _free(e, state.settings)
        ]
    return OK, out


_VERB = {"own": "finish", "defect": "fix", "next": "take"}


def render(out: dict) -> str:
    lines: list[str] = []
    g = out.get("goal") or {}
    if out["status"] == "unclear":
        return "\n".join(f"unclear: {m}" for m in out["missing"])
    if g.get("frozen"):
        return f"frozen: {g['frozen']}"
    if g.get("milestone"):
        bits = [f"done when: {g['done_when']}"] if g.get("done_when") else []
        if g.get("by") and g["by"] != "no date":
            bits.append(f"by {g['by']}")
        lines.append(
            f"goal: {g['milestone']}" + (f" ({'; '.join(bits)})" if bits else "")
        )
    elif g.get("goals_file"):
        lines.append(f"goal: see {g['goals_file']}")
    n = out["next"]
    if out.get("record") == "issues":
        lines += [f"record: {g['recorded_in']}", f"next: {n['reason']}"]
        return "\n".join(lines + [f"warning: {w}" for w in out["warnings"]])
    if n["item"]:
        i = n["item"]
        lines.append(
            f'next: {_VERB[n["kind"]]} "{i["title"]}" ({i.get("path") or out["todo"]} line {i["line"]}): {n["reason"]}'
        )
    else:
        lines.append(f"next: {n['reason']}")
    ip = out["in_progress"]
    lines.append(f"in progress: {ip['count']} of {ip['limit']}")
    for c in ip["claims"]:
        if c["stale"]:
            lines.append(
                (
                    f'stale: "{c["title"]}" in #{c["pr"]} by {c["who"]} on {c["machine"]}: no activity for'
                    f" {STALE_DAYS} days (flagged only; its holder, or the owner, closes the pull request)"
                )
                if c.get("pr")
                else (
                    f'stale: "{c["title"]}" claimed by {c["who"]} on {c["machine"]}, {c["date"]}, by a mark on the'
                    f" default branch: {STALE_DAYS} days old (flagged only; its holder, or the owner with goal"
                    " release --holder, removes the mark)"
                )
            )
    lines += [f"warning: {w}" for w in out["warnings"]]
    if out.get("needs_owner"):
        lines.append(
            "held for the owner or planned work: " + "; ".join(out["needs_owner"])
        )
    if "free" in out:
        lines.append("")
        lines.append("free, in order:")
        lines += [
            f"  {i['section']}: {i['title']} ({i['path'] + ' ' if i.get('path') else ''}line {i['line']})"
            for i in out["free"]
        ] or ["  none"]
        if ip["claims"]:
            lines.append("claimed:")
            lines += [
                f"  {c['title']}: {c['who']} on {c['machine']}, {c['date']}"
                + (f", #{c['pr']}" if c.get("pr") else "")
                for c in ip["claims"]
            ]
    return "\n".join(lines)


def _file(state: State, entry: Entry) -> tuple[str, str]:
    """The path and working-copy text of the file the entry's line is in."""
    if not entry.path:
        return state.todo_path, state.todo_text
    text = state.repo.read(entry.path)
    if text is None:
        raise RuntimeError(f"{entry.path} is not in the working copy")
    return entry.path, text


def find(todo: Todo, name: str) -> Entry:
    k = key(name)
    hits = [e for e in todo.entries if key(e.title) == k]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise Unclear(
            f'{len(hits)} entries are titled "{name}" (lines {", ".join(str(e.line) for e in hits)})'
        )
    close = [e.title for e in todo.entries if k in key(e.title) or key(e.title) in k]
    hint = ("; did you mean: " + "; ".join(f'"{t}"' for t in close)) if close else ""
    raise Unclear(f'no entry titled "{name}"{hint}')


def _work_branch(repo: Repo, verb: str) -> str:
    """The branch the mark is committed on: the work's own, never the default branch."""
    name = repo.branch_name()
    if not name or name == repo.base:
        raise RuntimeError(
            f"the mark goes on the work's own branch: run goal {verb} in the work's worktree, not on"
            f" {name or 'a detached HEAD'}"
        )
    return name


def _commit(repo: Repo, path: str, text: str, subject: str, body: str) -> None:
    """Write the todo file and commit only it. The file must have no other changes, so the commit is just the
    mark."""
    target = repo.root / path
    if target.is_symlink() or not target.resolve().is_relative_to(repo.root.resolve()):
        raise RuntimeError(
            f"{path} is a link or outside the repo: goal only writes a plain file"
        )
    if run(
        ["git", "diff", "--quiet", "HEAD", "--", path], repo.root, check=False
    ).returncode:
        raise RuntimeError(
            f"{path} has uncommitted changes: commit or drop them first, so this commit holds only the mark"
        )
    target.write_bytes(text.encode("utf-8"))
    try:
        run(["git", "add", "--", path], repo.root)
        run(
            ["git", "commit", "-q", "-m", subject, "-m", body, "--", path],
            repo.root,
            timeout=300,
        )
    except RuntimeError:
        run(["git", "checkout", "HEAD", "--", path], repo.root, check=False)
        raise


def claim(args: argparse.Namespace, today: dt.date) -> tuple[int, dict]:
    """Commit the mark on the work branch. Once the work's pull request is open, it carries the mark, and that is
    the claim."""
    repo = Repo(Path(args.C or "."), fetch=not args.no_fetch)
    branch = _work_branch(repo, "claim")
    state, goal_info, missing = load(repo, today, need_goal=False)
    if state is None:
        raise Unclear("; ".join(missing) or no_todo(goal_info))
    entry = find(state.todo, args.item)
    me = me_from_env(args.as_)
    me.branch = branch
    if not me.named:
        raise Unclear(NO_NAME)
    if not state.claims_checked:
        raise RuntimeError(
            f"{'; '.join(repo.warnings)}. Claim once GitHub answers: an open pull request may already hold the item"
        )
    if state.unread:
        raise RuntimeError(
            f"could not read the branch of {', '.join(f'#{n}' for n in state.unread)}, which may hold this item:"
            " fetch origin and try again"
        )
    held = entry.claim
    # A pull request is ours when its branch is this one: two sessions can share a name (subagents of one session,
    # or one stable $GOAL_AS), never a branch. A mark on the default branch has no branch, so its name decides.
    if held and me.holds(held):
        return OK, {
            "status": "ok",
            "branch": branch,
            "message": f'"{entry.title}" is already yours: {held.holder()} carries your mark',
            "warnings": repo.warnings,
        }
    if held:
        # Ties are settled on GitHub: the first holder wins (a mark on the default branch, else the lowest pull
        # request number), and a later pull request of ours closes itself.
        ours = next(
            (
                c
                for title, holders in state.conflicts
                if title == entry.title
                for c in holders[1:]
                if me.holds(c)
            ),
            None,
        )
        if ours:
            why = (
                f"#{held.pr} marked this item first"
                if held.pr
                else "this item was already claimed on the default branch"
            )
            raise Refused(
                f'"{entry.title}" is held by {held.holder()}, which came first: close your #{ours.pr} and pick'
                f" another item (gh pr close {ours.pr} --comment 'Closing: {why}.')"
            )
        raise Refused(
            f'"{entry.title}" is held by {held.holder()}: pick another item (if this branch\'s pull request also'
            " marks it, close that pull request)"
        )
    if entry.waits:
        raise Refused(
            f'"{entry.title}" waits on '
            + ", ".join(f"{state.settings.rows_name} {n}" for n in entry.waits)
            + ", still in the plan: pick another item"
        )
    owner = [m for m in state.settings.skip if m in entry.text]
    if owner:
        raise Refused(
            f'"{entry.title}" is marked {owner[0]}: it needs the owner or planned work first. Pick another item'
        )
    fpath, ftext = _file(state, entry)
    line = ftext.splitlines(keepends=True)[entry.line - 1]
    current = MARK.search(line)
    if current and (current["who"], current["machine"]) == (me.who, me.machine):
        if run(
            ["git", "diff", "--quiet", "HEAD", "--", fpath],
            repo.root,
            check=False,
        ).returncode:
            raise RuntimeError(
                f"{fpath} has uncommitted changes: commit or drop them first"
            )
        return OK, {
            "status": "ok",
            "branch": branch,
            "message": f'"{entry.title}" is already marked as yours on {branch}: push it and open the work\'s pull'
            " request, which is the claim",
            "warnings": repo.warnings,
        }
    text = ftext
    if (
        current
    ):  # a mark nothing counts: on no open pull request, and not on the default branch
        text = unmark(text, entry.line)
    mine = Claim(me.who, me.machine, today.isoformat())
    _commit(
        repo,
        fpath,
        mark(text, entry.line, mine),
        f"chore: claim {entry.title}",
        f"Marks the todo entry as taken by {mine.who} on {mine.machine}. While the work's pull request is open, it"
        " carries this mark, and that is the claim.",
    )
    return OK, {
        "status": "ok",
        "branch": branch,
        "message": f'marked "{entry.title}" on {branch}. Now push the branch and open the work\'s pull request'
        " (ready, auto-merge not armed): once it is open, it is the claim.",
        "warnings": repo.warnings,
    }


def release(args: argparse.Namespace, today: dt.date) -> tuple[int, dict]:
    """Commit the mark away on the work branch, for work that goes on under another item. To drop the work, close
    its pull request instead."""
    repo = Repo(Path(args.C or "."), fetch=not args.no_fetch)
    branch = _work_branch(repo, "release")
    state, goal_info, missing = load(repo, today, need_goal=False)
    if state is None:
        raise Unclear("; ".join(missing) or no_todo(goal_info))
    entry = find(state.todo, args.item)
    me = me_from_env(args.as_)
    if not me.named and not args.holder:
        raise Unclear(NO_NAME)
    fpath, ftext = _file(state, entry)
    current = MARK.search(ftext.splitlines(keepends=True)[entry.line - 1])
    if current is None:
        raise RuntimeError(f'"{entry.title}" is not marked on {branch}')
    holder = args.holder or me.who
    if current["who"] != holder:
        raise Refused(
            f'"{entry.title}" is marked by {current["who"]} on {current["machine"]}, not you: to remove their mark,'
            f" an owner call, pass --holder {current['who']}"
        )
    _commit(
        repo,
        fpath,
        unmark(ftext, entry.line),
        f"chore: release the claim on {entry.title}",
        f"Removes {current['who']}'s claim mark, so the item is free again once this is pushed.",
    )
    return OK, {
        "status": "ok",
        "branch": branch,
        "message": f'removed the mark on "{entry.title}" on {branch}. Push it, and the pull request no longer claims'
        " the item. To drop the work instead, close the pull request (gh pr close <n>).",
        "warnings": repo.warnings,
    }


def merged(args: argparse.Namespace) -> tuple[int, dict]:
    """The merged-branch test: every commit on main as the same patch, or a merged pull request whose head is the
    branch's tip (a squash merge). The local branch and origin's copy are both tested, so unpushed commits on a
    branch whose pull request merged are not taken for merged."""
    name = args.branch
    if name.startswith("-") or not name.strip():
        raise RuntimeError(f"`{name}` is not a branch name")
    repo = Repo(Path(args.C or "."), fetch=not args.no_fetch)
    repo.require_ref()
    refs = [
        ref
        for ref in (f"refs/heads/{name}", f"refs/remotes/origin/{name}")
        if run(
            ["git", "rev-parse", "--verify", "-q", ref + "^{commit}"],
            repo.root,
            check=False,
        ).returncode
        == 0
    ]
    if not refs:
        raise RuntimeError(f"no branch {name} here or on origin")
    prs: list[dict] | None = None
    asked = False
    results: list[tuple[int, str]] = []
    for ref in refs:
        short = ref.replace("refs/heads/", "").replace("refs/remotes/", "")
        tip = run(["git", "rev-parse", ref], repo.root).stdout.strip()
        cherry = run(["git", "cherry", repo.ref, ref], repo.root).stdout.splitlines()
        ahead = len([ln for ln in cherry if ln.startswith("+")])
        if not ahead:
            results.append(
                (OK, f"{short}: every commit is on {repo.ref} as the same patch")
            )
            continue
        if not asked:
            prs = gh_json(
                [
                    "pr",
                    "list",
                    "--state",
                    "merged",
                    "--head",
                    name,
                    "--json",
                    "number,headRefOid",
                ],
                repo.root,
            )
            asked = True
        if prs is None:
            results.append(
                (
                    UNCLEAR,
                    f"{short}: {ahead} commits are not on {repo.ref} as the same patch; merged pull requests not checked",
                )
            )
            continue
        hit = next(
            (
                pr
                for pr in prs
                if pr.get("headRefOid") == tip
                or _ancestor(repo, tip, pr.get("headRefOid"))
            ),
            None,
        )
        if hit:
            results.append(
                (OK, f"{short}: pull request #{hit.get('number')} merged at this tip")
            )
        else:
            results.append(
                (
                    NO,
                    f"{short}: {ahead} commits are not on {repo.ref}, and no merged pull request ends at this tip",
                )
            )
    rank = {OK: 0, UNCLEAR: 1, NO: 2}
    rc = max((r for r, _ in results), key=lambda r: rank[r])
    verdict = {OK: True, UNCLEAR: None, NO: False}[rc]
    return rc, {
        "branch": name,
        "refs": refs,
        "merged": verdict,
        "reason": "; ".join(t for _, t in results),
    }


def _common(suppress: bool) -> argparse.ArgumentParser:
    """The shared options. The subcommands' copies default to SUPPRESS, so an option typed before the subcommand
    (`goal --json claim X`) is not overwritten by the copy's default."""
    c = argparse.ArgumentParser(add_help=False)

    def d(value: object) -> object:
        return argparse.SUPPRESS if suppress else value

    c.add_argument(
        "-C",
        metavar="DIR",
        default=d(None),
        help="run in DIR instead of the current directory",
    )
    c.add_argument(
        "--json", action="store_true", default=d(False), help="print JSON for scripts"
    )
    c.add_argument(
        "--no-fetch",
        action="store_true",
        default=d(False),
        help="read the last fetched origin, don't fetch",
    )
    c.add_argument("--today", default=d(None), help=argparse.SUPPRESS)
    c.add_argument(
        "--as",
        dest="as_",
        metavar="LABEL",
        default=d(None),
        help="who you are (default: $GOAL_AS, else the session)",
    )
    return c


def _ancestor(repo: Repo, tip: str, head: str | None) -> bool:
    """`tip` is in the history of `head` (a local copy behind the branch a pull request merged)."""
    if not head or not re.fullmatch(r"[0-9a-f]{40}", head):
        return False
    return (
        run(
            ["git", "merge-base", "--is-ancestor", tip, head], repo.root, check=False
        ).returncode
        == 0
    )


def build_parser() -> argparse.ArgumentParser:
    common = _common(suppress=False)
    sub_common = _common(suppress=True)
    p = argparse.ArgumentParser(
        prog="goal",
        parents=[common],
        description="What to work on next in this repo. Exit 0 an answer, 1 an error, 2 unclear, 3 no.",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="also list every free item in order, and the claims",
    )
    sub = p.add_subparsers(dest="command")
    c = sub.add_parser(
        "claim",
        parents=[sub_common],
        help="commit the mark that claims an item, on the work's own branch",
    )
    c.add_argument("item", help="the entry's title")
    r = sub.add_parser(
        "release",
        parents=[sub_common],
        help="commit the mark away, on the work's own branch",
    )
    r.add_argument("item", help="the entry's title")
    r.add_argument(
        "--holder", help="remove a mark another session made (an owner call)"
    )
    m = sub.add_parser(
        "merged",
        parents=[sub_common],
        help="is this branch's work on the default branch?",
    )
    m.add_argument("branch")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    except ValueError:
        print("goal: --today is YYYY-MM-DD", file=sys.stderr)
        return ERROR
    try:
        if args.command == "claim":
            rc, out = claim(args, today)
        elif args.command == "release":
            rc, out = release(args, today)
        elif args.command == "merged":
            rc, out = merged(args)
        else:
            rc, out = answer(args, today)
    except Unclear as e:
        rc, out = UNCLEAR, {"status": "unclear", "missing": [str(e)]}
    except Refused as e:
        rc, out = NO, {"status": "no", "message": str(e)}
    except RuntimeError as e:
        if args.json:
            print(json.dumps({"status": "error", "message": str(e)}))
        print(f"goal: {e}", file=sys.stderr)
        return ERROR
    if args.json:
        print(json.dumps(out, indent=2))
    elif args.command in ("claim", "release") and out.get("status") == "ok":
        print(out["message"])
        for w in out.get("warnings") or []:
            print(f"warning: {w}")
    elif args.command == "merged":
        print(
            f"{'merged' if out['merged'] else 'not merged' if out['merged'] is False else 'unclear'}: {out['reason']}"
        )
    elif out.get("status") == "no":
        print(out["message"])
    else:
        print(render(out))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
