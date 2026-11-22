# SPDX-License-Identifier: MIT

"""goal: what to work on next in this repo, from what the repo states in writing.

It reads, from the default branch on origin (never the checkout, which may be behind):

- the README's `## How work runs here` section: the milestone or goals file, the todo file and the limit on
  work in progress;
- the todo file: its Defects and Next sections, top first, and the claim marks on their entries;
- `.claude/work.toml`, optional: other heading names, entries that need the owner, the merge method.

Then it answers in a fixed order: your own claimed item; else the top free defect, one fix at a time; else the
top free Next item, with a warning when that goes over the limit; else a cleanup. Nothing here blocks: the
limit and the order are warnings and advice. When the repo doesn't say enough, it names what is missing and
exits 2; it never guesses.

Claims live in the todo file. `goal claim` opens a pull request that marks one entry's line, and the claim
holds once that pull request merges. Two claims on one line conflict, so git is the lock. `goal release`
removes a mark the same way. `goal merged <branch>` says whether a branch's work is on main.

Exit codes: 0 an answer, 1 an error, 2 unclear (what is missing is named), 3 no (the item is someone else's,
or the branch is not merged).

Python 3.9 or later, standard library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

SECTION = "How work runs here"
STALE_DAYS = 3
OK, ERROR, UNCLEAR, NO = 0, 1, 2, 3
CLAIM_PREFIX, RELEASE_PREFIX = "claim/", "release/"
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
    merge_method: str = "squash"
    branch_prefix: str = "work/"
    named_defects: bool = False  # true when the settings file named the defects heading


_KEYS = {
    "defects": list,
    "next": list,
    "skip": list,
    "merge_method": str,
    "branch_prefix": str,
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
    return _path_in(value) if value else None


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


@dataclass
class Entry:
    title: str
    section: str
    line: int
    text: str
    claim: Claim | None = None


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
    if not ln.startswith("- ") or MARK.search(ln):
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


def key(text: str) -> str:
    """Case, quotes, backticks, trailing punctuation and spacing do not tell titles apart."""
    text = re.sub(r"[`'\"‘’“”*]", "", text)
    return re.sub(r"\s+", " ", text).strip().lower().rstrip(".,;:!?").strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", key(text)).strip("-")[:50].rstrip("-")


def _safe(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._:@/+-]+", "-", label).strip("-") or "unknown"


# --- the answer --------------------------------------------------------------------------------------------


@dataclass
class Me:
    who: str
    machine: str


def _date(text: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _days(a: str, b: dt.date) -> int:
    return (b - dt.date.fromisoformat(a)).days


def is_stale(
    entry: Entry, today: dt.date, prs: list[dict] | None, branches: dict[str, dt.date]
) -> bool:
    """No open pull request names the item, and no branch named for it has a commit, for STALE_DAYS."""
    if entry.claim is None or _days(entry.claim.date, today) < STALE_DAYS:
        return False
    k, s = key(entry.title), slug(entry.title)
    for pr in prs or []:
        head = pr.get("headRefName") or ""
        if head.startswith((CLAIM_PREFIX, RELEASE_PREFIX)):
            continue
        if (
            k in key(pr.get("title") or "")
            or k in key(pr.get("body") or "")
            or (s and s in head)
        ):
            return False
    for name, last in branches.items():
        named = s and s in name and not name.startswith((CLAIM_PREFIX, RELEASE_PREFIX))
        if named and (today - last).days < STALE_DAYS:
            return False
    return True


def _free(e: Entry, settings: Settings) -> bool:
    return e.claim is None and not any(m in e.text for m in settings.skip)


def _item(e: Entry, settings: Settings | None = None) -> dict:
    """The entry as the answer names it; `branch` is the work branch the next and work-start skills create."""
    prefix = (settings or Settings()).branch_prefix
    return {
        "title": e.title,
        "section": e.section,
        "line": e.line,
        "branch": prefix + slug(e.title),
    }


def decide(
    todo: Todo, me: Me, limit: int, settings: Settings
) -> tuple[dict, list[str]]:
    """The next step and any warnings, in the fixed order."""
    claims = [e for e in todo.entries if e.claim]
    own = [
        e
        for e in claims
        if e.claim and (e.claim.who, e.claim.machine) == (me.who, me.machine)
    ]
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
        self.base = self._base()
        if fetch:
            done = run(
                ["git", "fetch", "-q", "origin"], self.root, check=False, timeout=60
            )
            if done.returncode != 0:
                self.warnings.append(
                    f"could not fetch origin; read the last fetched origin/{self.base}"
                )
        self.ref = f"origin/{self.base}"
        if (
            run(
                ["git", "rev-parse", "--verify", "-q", self.ref], self.root, check=False
            ).returncode
            != 0
        ):
            raise RuntimeError(
                f"no {self.ref}: run goal in a clone whose origin has a default branch"
            )

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

    def branches(self) -> dict[str, dt.date]:
        done = run(
            [
                "git",
                "for-each-ref",
                "refs/remotes/origin",
                "--format=%(refname:lstrip=3)%09%(committerdate:short)",
            ],
            self.root,
            check=False,
        )
        out: dict[str, dt.date] = {}
        for ln in done.stdout.splitlines():
            name, _, day = ln.partition("\t")
            try:
                out[name] = dt.date.fromisoformat(day)
            except ValueError:
                continue
        return out


@dataclass
class State:
    repo: Repo
    facts: dict[str, str]
    settings: Settings
    todo_path: str
    todo_text: str
    todo: Todo


def load(repo: Repo, today: dt.date) -> tuple[State | None, dict, list[str]]:
    """Everything the answer needs, or the list of what is missing."""
    missing: list[str] = []
    readme = repo.show("README.md")
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
    if not goal_info["milestone"] and not goal_info["goals_file"]:
        missing.append(
            "no milestone stated: add **Current milestone:** with **Done when:**, or **Goals:** linking the file"
            " that states it"
        )
    by = goal_info["by"] or ""
    due = _date(by) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", by) else None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", by) and due is None:
        missing.append(f"the **By:** date {by} is not a real date")
    if due is not None and due < today:
        missing.append(
            f"{goal_info['milestone'] or 'the milestone'} was due {by}: is it still the milestone, with a new date?"
        )
    path = todo_path(facts)
    if path is None:
        missing.append(
            "no **Work is recorded in:** line naming the todo file in backticks or a link"
        )
    limit = wip_limit(facts)
    if limit is None:
        missing.append("no **Work in progress:** line saying `at most <N>`")
    text = repo.show(SETTINGS_PATH)
    try:
        settings = parse_settings(text) if text is not None else Settings()
    except ValueError as e:
        missing.append(str(e))
        settings = Settings()
    todo_text = repo.show(path) if path else None
    if path and todo_text is None:
        missing.append(f"{path} is not on {repo.ref}")
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
    return State(repo, facts, settings, path, todo_text, todo), goal_info, []


def me_from_env(label: str | None) -> Me:
    who = label or os.environ.get("GOAL_AS")
    if not who:
        sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get(
            "CURSOR_CONVERSATION_ID"
        )
        who = f"session-{sid[:8]}" if sid else getpass.getuser()
    machine = os.environ.get("GOAL_MACHINE") or socket.gethostname().split(".")[0]
    return Me(_safe(who), _safe(machine))


# --- the commands ------------------------------------------------------------------------------------------


def answer(args: argparse.Namespace, today: dt.date) -> tuple[int, dict]:
    repo = Repo(Path(args.C or "."), fetch=not args.no_fetch)
    state, goal_info, missing = load(repo, today)
    out: dict = {
        "status": "ok",
        "goal": goal_info,
        "warnings": list(repo.warnings),
        "source": repo.ref,
    }
    if "frozen" in goal_info:
        out["next"] = {
            "kind": "frozen",
            "item": None,
            "reason": f"this repo is frozen: {goal_info['frozen']}",
        }
        return OK, out
    if state is None:
        out.update(status="unclear", missing=missing, next=None)
        return UNCLEAR, out
    me = me_from_env(args.as_)
    limit = wip_limit(state.facts)
    assert limit is not None
    prs = gh_json(
        [
            "pr",
            "list",
            "--state",
            "open",
            "--limit",
            "200",
            "--json",
            "number,title,headRefName,body",
        ],
        repo.root,
    )
    if prs is None:
        out["warnings"].append(
            "open pull requests not checked (gh failed): a stale flag may be wrong"
        )
    branches = repo.branches()
    claims = []
    for e in state.todo.entries:
        if e.claim:
            claims.append(
                {
                    **_item(e),
                    **asdict(e.claim),
                    "stale": is_stale(e, today, prs, branches),
                }
            )
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
    else:
        lines.append(f"goal: see {g['goals_file']}")
    n = out["next"]
    if n["item"]:
        i = n["item"]
        lines.append(
            f'next: {_VERB[n["kind"]]} "{i["title"]}" ({out["todo"]} line {i["line"]}): {n["reason"]}'
        )
    else:
        lines.append(f"next: {n['reason']}")
    ip = out["in_progress"]
    lines.append(f"in progress: {ip['count']} of {ip['limit']}")
    for c in ip["claims"]:
        if c["stale"]:
            lines.append(
                f'stale: "{c["title"]}" claimed by {c["who"]} on {c["machine"]}, {c["date"]}: no open pull request'
                f" or branch activity for {STALE_DAYS} days (flagged only; the holder or the owner releases it)"
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
            f"  {i['section']}: {i['title']} (line {i['line']})" for i in out["free"]
        ] or ["  none"]
        held = [c for c in ip["claims"]]
        if held:
            lines.append("claimed:")
            lines += [
                f"  {c['title']}: {c['who']} on {c['machine']}, {c['date']}"
                for c in held
            ]
    return "\n".join(lines)


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


def change(
    args: argparse.Namespace, today: dt.date, claiming: bool
) -> tuple[int, dict]:
    """Open the tiny pull request that marks (claim) or unmarks (release) one entry."""
    repo = Repo(Path(args.C or "."), fetch=not args.no_fetch)
    state, _, missing = load(repo, today)
    if state is None:
        raise Unclear("; ".join(missing) or "this repo is frozen")
    entry = find(state.todo, args.item)
    me = me_from_env(args.as_)
    if claiming:
        if entry.claim and (entry.claim.who, entry.claim.machine) == (
            me.who,
            me.machine,
        ):
            return OK, {
                "status": "ok",
                "message": f'"{entry.title}" is already yours on {repo.ref}',
            }
        if entry.claim:
            c = entry.claim
            raise Refused(
                f'"{entry.title}" is claimed by {c.who} on {c.machine}, {c.date}: pick another item'
            )
        held = [m for m in state.settings.skip if m in entry.text]
        if held:
            raise Refused(
                f'"{entry.title}" is marked {held[0]}: it needs the owner or planned work first. Pick another item'
            )
        claim = Claim(me.who, me.machine, today.isoformat())
        text = mark(state.todo_text, entry.line, claim)
        branch = CLAIM_PREFIX + slug(entry.title)
        subject = f"chore: claim {entry.title}"
        body = (
            f"Marks the todo entry as taken by {claim.who} on {claim.machine}, so no other agent starts it. The"
            " claim holds once this merges; a second claim on the same line conflicts and cannot merge."
        )
    else:
        if entry.claim is None:
            raise RuntimeError(f'"{entry.title}" is not claimed')
        holder = args.holder or me.who
        if entry.claim.who != holder:
            c = entry.claim
            raise Refused(
                f'"{entry.title}" is claimed by {c.who} on {c.machine}, {c.date}, not you: to release it for them,'
                f" pass --holder {c.who}"
            )
        text = unmark(state.todo_text, entry.line)
        branch = RELEASE_PREFIX + slug(entry.title)
        subject = f"chore: release the claim on {entry.title}"
        body = f"Removes {entry.claim.who}'s claim mark so the item is free again; the work was not finished."
    remote = run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
        repo.root,
        check=False,
    )
    if remote.returncode == 0:
        old = remote.stdout.split()[0] if remote.stdout.split() else ""
        if claiming and _own_open_claim(repo, state, entry, me, branch):
            # Git sees edits on adjacent lines as one conflict, so a claim on the next entry blocks ours though
            # the item is still free on main: rebuild ours on the new main, and its pull request updates.
            _push_change(repo, state.todo_path, text, branch, subject, body, lease=old)
            if not args.no_merge:
                _arm(repo, branch, state.settings)
            msg = (
                f"rebuilt your claim on {branch} on the current {repo.ref}, since the item is still free there;"
                " its pull request updates itself. Wait for it to merge, then run `goal`."
            )
            return OK, {
                "status": "ok",
                "branch": branch,
                "url": None,
                "message": msg,
                "warnings": repo.warnings,
            }
        raise Refused(
            f"origin already has {branch}: another {'claim' if claiming else 'release'} of this item is open. If its"
            f" pull request is closed, delete the branch (git push origin --delete {branch}) and try again"
        )
    _push_change(repo, state.todo_path, text, branch, subject, body)
    url = _open_pr(repo, branch, subject, body, args, state.settings)
    msg = (
        f"claim pull request: {url}\nThe item is yours once it merges. Wait with `gh pr checks <n> --watch"
        " --interval 60`, then run `goal`: it names the item as yours. If the pull request conflicts, run `goal"
        " claim` again: it rebuilds the claim while the item is still free, and exits 3 when someone else took it."
        if claiming
        else f"release pull request: {url}"
    )
    return OK, {
        "status": "ok",
        "branch": branch,
        "url": url,
        "message": msg,
        "warnings": repo.warnings,
    }


def _own_open_claim(
    repo: Repo, state: State, entry: Entry, me: Me, branch: str
) -> bool:
    """The open claim branch for this item marks it with our name: ours, not someone else's."""
    text = repo.show(state.todo_path, ref=f"origin/{branch}")
    if text is None:
        return False
    try:
        theirs = find(parse_todo(text, state.settings), entry.title)
    except Unclear:
        return False
    return theirs.claim is not None and (theirs.claim.who, theirs.claim.machine) == (
        me.who,
        me.machine,
    )


def _push_change(
    repo: Repo,
    path: str,
    text: str,
    branch: str,
    subject: str,
    body: str,
    lease: str | None = None,
) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="goal-"))
    work = tmp / "w"
    try:
        run(
            ["git", "worktree", "add", "-q", "--detach", str(work), repo.ref], repo.root
        )
        target = work / path
        if target.is_symlink() or not target.resolve().is_relative_to(work.resolve()):
            raise RuntimeError(
                f"{path} is a link or outside the repo: goal only writes a plain file"
            )
        target.write_bytes(text.encode("utf-8"))
        run(["git", "add", "--", path], work)
        run(["git", "commit", "-q", "-m", subject, "-m", body], work, timeout=300)
        run(
            ["git", "push", "-q", "origin", f"HEAD:refs/heads/{branch}"]
            + ([f"--force-with-lease=refs/heads/{branch}:{lease}"] if lease else []),
            work,
            timeout=120,
        )
    finally:
        run(["git", "worktree", "remove", "--force", str(work)], repo.root, check=False)
        shutil.rmtree(tmp, ignore_errors=True)


def _open_pr(
    repo: Repo,
    branch: str,
    subject: str,
    body: str,
    args: argparse.Namespace,
    s: Settings,
) -> str:
    done = run(
        [
            "gh",
            "pr",
            "create",
            "--head",
            branch,
            "--base",
            repo.base,
            "--title",
            subject,
            "--body",
            body,
        ],
        repo.root,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(
            f"pushed {branch}, but `gh pr create` failed: {done.stderr.strip()}. Open its pull request by hand"
        )
    url = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else branch
    if not args.no_merge:
        _arm(repo, url, s)
    return url


def _arm(repo: Repo, pr: str, s: Settings) -> None:
    done = run(
        ["gh", "pr", "merge", pr, "--auto", f"--{s.merge_method}"],
        repo.root,
        check=False,
    )
    if done.returncode != 0:
        repo.warnings.append(
            f"auto-merge not armed ({done.stderr.strip() or 'gh failed'}): the claim holds only once its pull"
            " request merges, so merge it by the repo's rules"
        )


def merged(args: argparse.Namespace) -> tuple[int, dict]:
    """The merged-branch test: every commit on main as the same patch, or a merged pull request whose head is the
    branch's tip (a squash merge). The local branch and origin's copy are both tested, so unpushed commits on a
    branch whose pull request merged are not taken for merged."""
    name = args.branch
    if name.startswith("-") or not name.strip():
        raise RuntimeError(f"`{name}` is not a branch name")
    repo = Repo(Path(args.C or "."), fetch=not args.no_fetch)
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
        hit = next((pr for pr in prs if pr.get("headRefOid") == tip), None)
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
        "claim", parents=[sub_common], help="open the pull request that claims an item"
    )
    c.add_argument("item", help="the entry's title")
    c.add_argument(
        "--no-merge",
        action="store_true",
        help="open the pull request without arming auto-merge",
    )
    r = sub.add_parser(
        "release",
        parents=[sub_common],
        help="open the pull request that removes a claim mark",
    )
    r.add_argument("item", help="the entry's title")
    r.add_argument(
        "--holder", help="release a claim another session holds (an owner call)"
    )
    r.add_argument(
        "--no-merge",
        action="store_true",
        help="open the pull request without arming auto-merge",
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
            rc, out = change(args, today, claiming=True)
        elif args.command == "release":
            rc, out = change(args, today, claiming=False)
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
