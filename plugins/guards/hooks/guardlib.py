# SPDX-License-Identifier: MIT

"""Shared by the deny-list guards: a small shell parser and the refusal plumbing.

`parse` reads a Bash command the way a shell would, closely enough to tell a command that runs from text
that is only quoted (a commit message, an `echo`, a heredoc body). It knows separators, pipes, quoting,
`$(...)` and backticks (parsed, and marked `substituted`), subshells, redirections, heredocs, loops, `cd`,
wrapper words (`env`, `command`, `sudo`, ...) and `bash -c`/`eval` strings. It never raises: odd input
gives its best reading.

A guard calls `run(RULE, refusal)`: `refusal(event)` returns why the call is refused (and the safe
alternative), or None. The refusal names the rule as `[guards:<rule>]` (or the name of a plugin that bundles this file); an overrides file may turn a rule off
for a repo, with a reason (`rule_setting`). Any error in a guard lets the call through.
"""

from __future__ import annotations

import fnmatch
import functools
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NAME_OK = re.compile(r"[a-z0-9][a-z0-9-]*")
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?\+?=")
SUBSTITUTION = "$(...)"
# Words that start a command without changing which program runs.
WRAPPERS = {"command", "builtin", "exec", "nohup", "time", "noglob", "nocorrect"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
# Reserved words dropped from the front of a command.
RESERVED = {"!", "{", "}", "do", "then", "else", "elif", "if", "fi", "esac"}
STDOUT_REDIRECTS = {">", ">>", ">|", "&>", "&>>"}


@dataclass(eq=False)
class Loop:
    kind: str  # while, until, for, select, repeat
    # The condition of a while/until loop, filled in when it is read.
    condition: list[str] = field(default_factory=list)
    # The variables each pass sets: a `for` variable, or the names a `while read` reads.
    vars: list[str] = field(default_factory=list)


@dataclass
class Command:
    words: list[str] = field(default_factory=list)
    # Per word, the variables it expands to their value (not `${#X}` or `${X:+...}`).
    refs: list[list[str]] = field(default_factory=list)
    assignments: list[str] = field(default_factory=list)
    redirects: list[tuple[str, str]] = field(default_factory=list)
    piped: bool = False
    substituted: bool = False
    loops: tuple[str, ...] = ()
    # The loops themselves, innermost last; the same object for every command in one loop.
    loop_refs: tuple[Loop, ...] = ()
    cwd: str | None = None
    # How many commands were already emitted when this one began: later ones came from its `$(...)`.
    first: int = 0
    # "for"/"select" for a loop header (`words` then holds `NAME in ...`), else None.
    keyword: str | None = None

    @property
    def argv(self) -> list[str]:
        """The words with wrapper commands taken off the front."""
        return unwrap(self.words) if self.keyword is None else []

    def stdout_captured(self) -> bool:
        """Whether standard output goes somewhere other than the terminal."""
        if self.piped or self.substituted:
            return True
        return any(
            op in STDOUT_REDIRECTS or (op in ("1>", "1>>", "1>|"))
            for op, target in self.redirects
            if not target.startswith("&")
        )


def unwrap(words: list[str]) -> list[str]:
    words = list(words)
    while words:
        head = os.path.basename(words[0])
        if head in WRAPPERS:
            words = words[1:]
            while words and words[0].startswith("-") and words[0] != "-":
                words = words[1:]
        elif head == "env":
            words = words[1:]
            while words and (words[0].startswith("-") or ASSIGNMENT.match(words[0])):
                takes = words[0] in (
                    "-u",
                    "--unset",
                    "-C",
                    "--chdir",
                    "-S",
                    "--split-string",
                )
                words = words[2:] if takes else words[1:]
        elif head == "sudo":
            words = words[1:]
            while words and words[0].startswith("-"):
                takes = words[0] in (
                    "-u",
                    "-g",
                    "-h",
                    "-p",
                    "-C",
                    "-D",
                    "-r",
                    "-t",
                    "-U",
                )
                words = words[2:] if takes else words[1:]
        elif head == "nice":
            words = words[1:]
            if words[:1] == ["-n"]:
                words = words[2:]
            elif words and words[0].startswith("-"):
                words = words[1:]
        elif head == "timeout":
            words = words[1:]
            while words and words[0].startswith("-"):
                words = (
                    words[2:]
                    if words[0] in ("-s", "--signal", "-k", "--kill-after")
                    else words[1:]
                )
            words = words[1:]
        else:
            break
    return words


class _Parser:
    def __init__(self, text: str, cwd: str | None) -> None:
        self.text = text
        self.i = 0
        self.cwd = cwd
        # The directory `cd -` returns to: unknown (None) until a `cd` in this command.
        self.oldcwd: str | None = None
        # `NAME=value` set by a command of only assignments, for a later `$NAME args`.
        self.programs: dict[str, str] = {}
        self.loops: list[Loop] = []
        self.out: list[Command] = []
        self.heredocs: list[tuple[str, bool]] = []

    def peek(self, n: int = 0) -> str:
        j = self.i + n
        return self.text[j] if j < len(self.text) else ""

    # One list of commands, up to *stop* (")" or "`") or the end.
    def parse_list(self, stop: str | None, substituted: bool) -> None:
        command = Command(substituted=substituted, first=len(self.out))
        word: list[str] = []
        refs: list[str] = []
        started = False
        redirect: str | None = None

        def end_word() -> None:
            nonlocal word, refs, started, redirect
            if started:
                text = "".join(word)
                if redirect is not None:
                    command.redirects.append((redirect, text))
                    if redirect.lstrip("0123456789") in ("<<", "<<-"):
                        self.heredocs.append(
                            (text.strip("'\""), redirect.endswith("-"))
                        )
                    redirect = None
                elif (
                    not command.words and not command.keyword and ASSIGNMENT.match(text)
                ):
                    command.assignments.append(text)
                else:
                    command.words.append(text)
                    command.refs.append(refs)
            word, refs, started = [], [], False

        def end_command(piped: bool = False, subshell: bool = False) -> None:
            """*subshell*: the command ran in a subshell (before a pipe, or sent to the background), so
            its `cd` ends with it. The last part of a pipeline keeps its `cd`, as in zsh (the macOS shell);
            bash without `lastpipe` would drop it."""
            nonlocal command
            end_word()
            command.piped = piped
            outer = (self.cwd, self.oldcwd)
            self.finish(command)
            if subshell:
                self.cwd, self.oldcwd = outer
            command = Command(substituted=substituted, first=len(self.out))

        while self.i < len(self.text):
            ch = self.peek()
            if stop and ch == stop:
                self.i += 1
                end_command()
                return
            if ch in " \t":
                end_word()
                self.i += 1
            elif ch == "\n":
                end_command()
                self.i += 1
                self.skip_heredocs()
            elif ch == "#" and not started:
                while self.i < len(self.text) and self.peek() != "\n":
                    self.i += 1
            elif ch == "\\":
                if self.peek(1) == "\n":
                    self.i += 2
                else:
                    word.append(self.peek(1))
                    started = True
                    self.i += 2
            elif ch == "'":
                end = self.text.find("'", self.i + 1)
                end = len(self.text) if end < 0 else end
                word.append(self.text[self.i + 1 : end])
                started = True
                self.i = end + 1
            elif ch == '"':
                self.i += 1
                self.double_quoted(word, refs)
                started = True
            elif ch == "$" or ch == "`":
                self.dollar(word, refs)
                started = True
            elif ch in "<>" and self.peek(1) == "(":
                self.i += 2
                self.parse_list(")", substituted=True)
                word.append(SUBSTITUTION)
                started = True
            elif ch in "<>" or (ch == "&" and self.peek(1) == ">"):
                fd = "".join(word) if started and "".join(word).isdigit() else ""
                if fd:
                    word, refs, started = [], [], False
                else:
                    end_word()
                j = self.i
                while j < len(self.text) and self.text[j] in "<>&|-" and j - self.i < 3:
                    if (
                        self.text[j] in "&|-"
                        and j > self.i
                        and self.text[j - 1] not in "<>"
                    ):
                        break
                    j += 1
                op = self.text[self.i : j]
                self.i = j
                redirect = fd + op
                if (
                    op.endswith("&")
                    and self.peek()
                    and (self.peek().isdigit() or self.peek() == "-")
                ):
                    redirect = fd + op[:-1]
                    word, started = ["&"], True
            elif ch in ";&|":
                two = self.text[self.i : self.i + 2]
                if two in ("&&", "||", ";;", "|&"):
                    self.i += 2
                    end_command(piped=two == "|&", subshell=two == "|&")
                else:
                    self.i += 1
                    end_command(piped=ch == "|", subshell=ch in "|&")
            elif ch == "(":
                end_command()
                self.i += 1
                # A subshell's `cd` ends with it.
                outer = (self.cwd, self.oldcwd)
                self.parse_list(")", substituted=substituted)
                self.cwd, self.oldcwd = outer
            elif ch == ")":
                end_command()
                self.i += 1
            else:
                word.append(ch)
                started = True
                self.i += 1
        end_command()

    def double_quoted(self, word: list[str], refs: list[str]) -> None:
        while self.i < len(self.text):
            ch = self.peek()
            if ch == '"':
                self.i += 1
                return
            if ch == "\\" and self.peek(1) in '"\\$`\n':
                if self.peek(1) != "\n":
                    word.append(self.peek(1))
                self.i += 2
            elif ch in "$`":
                self.dollar(word, refs)
            else:
                word.append(ch)
                self.i += 1

    def dollar(self, word: list[str], refs: list[str]) -> None:
        if self.peek() == "`":
            self.i += 1
            self.parse_list("`", substituted=True)
            word.append(SUBSTITUTION)
            return
        nxt = self.peek(1)
        if nxt == "(" and self.peek(2) == "(":
            depth, j = 0, self.i + 1
            while j < len(self.text):
                depth += {"(": 1, ")": -1}.get(self.text[j], 0)
                j += 1
                if depth == 0:
                    break
            word.append(self.text[self.i : j])
            self.i = j
        elif nxt == "(":
            self.i += 2
            self.parse_list(")", substituted=True)
            word.append(SUBSTITUTION)
        elif nxt == "{":
            end = self.text.find("}", self.i + 2)
            end = len(self.text) if end < 0 else end
            inner = self.text[self.i + 2 : end]
            name = NAME.match(inner)
            if name and not re.match(r"[A-Za-z_][A-Za-z0-9_]*:?\+", inner):
                refs.append(name.group())
            word.append(self.text[self.i : end + 1])
            self.i = end + 1
        elif nxt == "'":
            j = self.i + 2
            while j < len(self.text) and self.text[j] != "'":
                j += 2 if self.text[j] == "\\" else 1
            word.append(self.text[self.i + 2 : j])
            self.i = j + 1
        else:
            name = NAME.match(self.text, self.i + 1)
            if name:
                refs.append(name.group())
                word.append("$" + name.group())
                self.i = name.end()
            else:
                word.append("$")
                self.i += 1

    @staticmethod
    def strip_reserved(command: Command) -> None:
        while command.words and command.words[0] in RESERVED:
            command.words.pop(0)
            command.refs.pop(0)
        # Assignments after a reserved word (`do x=1`) were read as words.
        while (
            command.words
            and not command.assignments
            and ASSIGNMENT.match(command.words[0])
        ):
            command.assignments.append(command.words.pop(0))
            command.refs.pop(0)

    def expand_program(self, command: Command) -> None:
        """`g=git; $g checkout x`: a first word that is only a variable assigned a literal earlier."""
        if command.keyword is None and command.assignments and not command.words:
            for text in command.assignments:
                name, _, value = text.partition("=")
                if name.endswith("+"):
                    self.programs.pop(name[:-1], None)
                elif "$" in value or "`" in value or not value.strip():
                    self.programs.pop(name, None)
                else:
                    self.programs[name] = value
            return
        if not command.words or len(command.refs[0]) != 1:
            return
        name = command.refs[0][0]
        if command.words[0] in (f"${name}", f"${{{name}}}") and name in self.programs:
            expanded = self.programs[name].split()
            command.words[:1] = expanded
            command.refs[:1] = [[] for _ in expanded]

    def skip_heredocs(self) -> None:
        while self.heredocs:
            delimiter, strip_tabs = self.heredocs.pop(0)
            while self.i < len(self.text):
                end = self.text.find("\n", self.i)
                end = len(self.text) if end < 0 else end
                line = self.text[self.i : end]
                self.i = min(end + 1, len(self.text))
                if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                    break

    def finish(self, command: Command) -> None:
        self.strip_reserved(command)
        head = command.words[0] if command.words else ""
        if head in ("while", "until"):
            loop = Loop(head)
            self.loops.append(loop)
            command.words.pop(0)
            command.refs.pop(0)
            self.strip_reserved(command)
            loop.condition = unwrap(command.words)
            # A `$(gh ...)` in the condition runs every pass too.
            for inner in self.out[command.first :]:
                inner.loop_refs += (loop,)
                inner.loops += (loop.kind,)
            if loop.condition[:1] == ["read"]:
                loop.vars = [w for w in loop.condition[1:] if NAME.fullmatch(w)]
        elif head in ("for", "select"):
            self.loops.append(Loop(head, vars=command.words[1:2]))
            command.keyword = head
            command.words.pop(0)
            command.refs.pop(0)
        elif head == "repeat":
            # zsh `repeat N { body }` or `repeat N do body done`: the body may follow on this line.
            self.loops.append(Loop(head))
            del command.words[:2], command.refs[:2]
            self.strip_reserved(command)
        elif head == "done":
            if self.loops:
                self.loops.pop()
            command.words.pop(0)
            command.refs.pop(0)
        in_loops = self.loops if command.keyword is None else self.loops[:-1]
        command.loop_refs = tuple(in_loops)
        command.loops = tuple(loop.kind for loop in in_loops)
        command.cwd = self.cwd
        self.expand_program(command)
        if not (
            command.words or command.assignments or command.redirects or command.keyword
        ):
            return
        self.out.append(command)
        argv = command.argv
        if argv[:1] == ["cd"] and not command.substituted:
            target = argv[1] if len(argv) > 1 else "~"
            before = self.cwd
            if target == "-":
                # Unknown (no earlier `cd` here): stay put rather than lose track.
                self.cwd = self.oldcwd or self.cwd
            else:
                target = os.path.expanduser(target)
                if os.path.isabs(target):
                    self.cwd = os.path.normpath(target)
                elif self.cwd:
                    self.cwd = os.path.normpath(os.path.join(self.cwd, target))
            self.oldcwd = before
        script = _inner_script(argv)
        if script is not None:
            for inner in parse(script, cwd=self.cwd):
                inner.substituted = inner.substituted or command.substituted
                inner.piped = inner.piped or command.piped
                inner.redirects = inner.redirects + command.redirects
                inner.loops = command.loops + inner.loops
                inner.loop_refs = command.loop_refs + inner.loop_refs
                self.out.append(inner)


def _inner_script(argv: list[str]) -> str | None:
    """The script a `bash -c '...'` or `eval ...` runs, or None."""
    if not argv:
        return None
    if argv[0] == "eval":
        return " ".join(argv[1:])
    if os.path.basename(argv[0]) not in SHELLS:
        return None
    # Options come first, `-c` among them, and the script is the first word after them (and after `--`).
    # Each `o`/`O` in a `-`/`+` group takes the next word as its value (`-euo pipefail`, `-oc pipefail`).
    words, k, values, script = argv[1:], 0, 0, False
    while k < len(words):
        word = words[k]
        k += 1
        if values:
            values -= 1
        elif word == "--":
            break
        elif word in ("--rcfile", "--init-file"):
            values = 1
        elif word.startswith("--"):
            continue
        elif len(word) > 1 and word[0] in "-+":
            values = word.count("o") + word.count("O")
            script = script or (word[0] == "-" and "c" in word[1:])
        else:
            k -= 1
            break
    return words[k] if script and k < len(words) else None


def parse(text: str, cwd: str | None = None) -> list[Command]:
    """Every simple command in *text*, including those inside `$(...)`, backticks and `bash -c`."""
    parser = _Parser(text, cwd)
    try:
        while parser.i < len(parser.text):
            parser.parse_list(None, substituted=False)
    except (IndexError, ValueError, RecursionError):
        pass
    return parser.out


GIT_VALUE_OPTIONS = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--config-env",
    "--exec-path",
}


def git_args(argv: list[str]) -> tuple[str, list[str], list[str]] | None:
    """`git` argv as (subcommand, its arguments, the `-C` directories), or None for another program."""
    if not argv or os.path.basename(argv[0]) != "git":
        return None
    rest = argv[1:]
    dirs: list[str] = []
    while rest and rest[0].startswith("-"):
        option = rest[0]
        if option in GIT_VALUE_OPTIONS:
            if option == "-C" and len(rest) > 1:
                dirs.append(rest[1])
            rest = rest[2:]
        else:
            rest = rest[1:]
    if not rest:
        return None
    return rest[0], rest[1:], dirs


def gh_args(argv: list[str]) -> tuple[list[str], str | None] | None:
    """`gh` argv as (arguments without `-R/--repo`, the repo or None), or None for another program."""
    if not argv or os.path.basename(argv[0]) != "gh":
        return None
    out: list[str] = []
    repo = None
    words = iter(argv[1:])
    for word in words:
        if word in ("-R", "--repo"):
            repo = next(words, None)
        elif word.startswith("--repo="):
            repo = word.split("=", 1)[1]
        elif word.startswith("-R") and len(word) > 2:
            repo = word[2:]
        else:
            out.append(word)
    return out, repo


def where(command: Command, dirs: list[str] = ()) -> str | None:
    """The directory *command* acts in: its `cwd` moved by any `git -C` directories."""
    place = command.cwd
    for step in dirs:
        step = os.path.expanduser(step)
        place = (
            step
            if os.path.isabs(step)
            else (os.path.join(place, step) if place else None)
        )
    return os.path.normpath(place) if place else None


def bash_commands(event: dict) -> list[Command]:
    """The commands of a Bash (or Monitor) tool call, or [] for another tool."""
    command = (event.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command:
        return []
    return parse(command, cwd=event.get("cwd") or os.getcwd())


# Rules a repo can't turn off, a fallback only: an overrides file may list its own under LOCKED_KEY, and that
# list is the one read (`locked_rules`). With no file nothing is off, so the fallback matters only for a file
# without the key.
LOCKED_KEY = "_locked"
LOCKED = {"no-secret-print", "no-admin-merge", "no-github-polling"}
OVERRIDES_FILE = "overrides.json"
OVERRIDES_ENV = "CLAUDE_GUARDS_OVERRIDES"
USER_OVERRIDES = "~/.config/claude-guards/overrides.json"
OVERRIDES = "overrides.json"
# Where an override is set, for refusal messages.
WHERE = f"{OVERRIDES_FILE} (`${OVERRIDES_ENV}`, else {USER_OVERRIDES})"


def plugin_root() -> str:
    """The plugin's root: `$CLAUDE_PLUGIN_ROOT` (Claude Code sets it for a plugin's hooks), else the folder
    above this file's."""
    return os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )


def overrides_path() -> str | None:
    """The overrides file to read, the first that is set: `overrides.json` at the plugin root (a plugin that
    ships its own), `$CLAUDE_GUARDS_OVERRIDES`, `~/.config/claude-guards/overrides.json`. None when none
    exists: every rule on."""
    beside = os.path.join(plugin_root(), OVERRIDES_FILE)
    if os.path.isfile(beside):
        return beside
    named = os.environ.get(OVERRIDES_ENV)
    if named:
        return os.path.expanduser(named)
    user = os.path.expanduser(USER_OVERRIDES)
    return user if os.path.isfile(user) else None


def repo_root(place: str | None) -> str | None:
    if not place or not os.path.isdir(place):
        return None
    try:
        done = subprocess.run(
            ["git", "-C", place, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    root = done.stdout.strip()
    return root if done.returncode == 0 and root else None


def slug(url: str) -> str | None:
    """owner/repo of a GitHub remote URL, lowercased."""
    # Any host, so an SSH alias (`git@github-work:o/r.git`) counts too.
    match = re.search(r"[:/]([^/:]+/[^/:]+?)(?:\.git)?/?$", url.strip())
    return match.group(1).lower() if match else None


@functools.lru_cache(maxsize=64)
def origin_repo(root: str) -> str | None:
    """owner/name of the repo at *root*, from its `origin` remote, lowercased."""
    try:
        done = subprocess.run(
            ["git", "-C", root, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return slug(done.stdout) if done.returncode == 0 and done.stdout.strip() else None


@functools.lru_cache(maxsize=4)
def _overrides(path: str | None) -> dict:
    """The overrides file, `{"<owner>/<repo>" or a pattern: {"<rule>": {"reason": ...}}}`; {} when missing or
    unreadable."""
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001 - a missing or broken file leaves every rule on
        return {}
    return data if isinstance(data, dict) else {}


def locked_rules() -> set[str]:
    """The rules no repo can turn off: the overrides file's LOCKED_KEY list, else (no file, no key, or an
    empty or odd list) the built-in LOCKED."""
    ids = _overrides(overrides_path()).get(LOCKED_KEY)
    if isinstance(ids, list) and ids and all(isinstance(i, str) for i in ids):
        return set(ids)
    return set(LOCKED)


def rule_setting(rule: str, place: str | None) -> tuple[bool, str | None]:
    """(whether *rule* is on in the repo at *place*, a note to print with a refusal or None).

    The one reader of the overrides file (`overrides_path`): a rule is off for the repo `origin` names
    when its entry, else the first group pattern (`my-org/*`) that matches it, lists the rule with a
    non-empty `reason`. A locked rule (`locked_rules`, the file's `_locked` list, never read as a repo)
    stays on regardless, and the note says so. A missing or unreadable file, or no `origin`, leaves every
    rule on, quietly.
    """
    root = repo_root(place)
    if not root:
        return True, None

    repo = origin_repo(root)
    if not repo:
        return True, None
    data = _overrides(overrides_path())
    entry = data.get(repo)
    if entry is None:
        entry = next(
            (
                v
                for k, v in data.items()
                if set(k) & set("*?[") and fnmatch.fnmatchcase(repo, k.lower())
            ),
            None,
        )
    found = entry.get(rule) if isinstance(entry, dict) else None
    reason = found.get("reason") if isinstance(found, dict) else None
    if not isinstance(reason, str) or not reason.strip():
        return True, None
    if rule in locked_rules():
        return (
            True,
            f"{OVERRIDES} turns off {rule} for {repo}, but it can't be turned off.",
        )
    return False, None


def clear_caches() -> None:
    for cached in (origin_repo, _overrides, _off):
        cached.cache_clear()


@functools.lru_cache(maxsize=64)
def _off(rule: str, place: str | None, overrides: str | None) -> bool:
    return not rule_setting(rule, place)[0]


def off(rule: str, place: str | None) -> bool:
    """Whether the overrides file turns *rule* off for the repo at *place*: guards skip such commands."""
    return _off(rule, place, overrides_path())


def commands_for(rule: str, event: dict) -> list[Command]:
    """`bash_commands`, less those acting in a repo whose settings turn *rule* off."""

    def place(command: Command) -> str | None:
        git = git_args(command.argv)
        return where(command, git[2] if git else [])

    return [c for c in bash_commands(event) if not off(rule, place(c))]


@functools.lru_cache(maxsize=1)
def plugin_name() -> str:
    """The name in `.claude-plugin/plugin.json` of the plugin this file ships in, else "guards": a plugin that
    bundles guardlib labels its refusals with its own name."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(
            os.path.join(here, ".claude-plugin", "plugin.json"), encoding="utf-8"
        ) as f:
            name = json.load(f).get("name")
    except Exception:  # noqa: BLE001 - a label must never break a guard
        return "guards"
    return name if isinstance(name, str) and NAME_OK.fullmatch(name) else "guards"


def label(rule: str) -> str:
    return f"[{plugin_name()}:{rule}]"


def run(
    rule: str, refusal: Callable[[dict], str | tuple[str, str | None] | None]
) -> int:
    """Read the hook event from stdin; exit 2 with the refusal on stderr, else 0. Fails open.

    *refusal* returns the reason, or (reason, the directory it acts in) when that is not the
    event's `cwd`, for the note on its setting. Turning a rule off is the guard's
    to honour per command (`commands_for`, or `off` for a place of its own), since one call may act
    in several repos.
    """
    try:
        event = json.load(sys.stdin)
        found = refusal(event) if isinstance(event, dict) else None
        if not found:
            return 0
        reason, place = found if isinstance(found, tuple) else (found, event.get("cwd"))
        return refuse(rule, f"Refusing {reason}", place)
    except Exception:  # noqa: BLE001 - a broken guard must not block work
        return 0


def refuse(rule: str, message: str, place: str | None) -> int:
    """Write *message*, the note on *rule*'s setting for the repo at *place*, and the rule's label; 2."""
    try:
        note = rule_setting(rule, place)[1]
    except Exception:  # noqa: BLE001 - the note is extra; the refusal stands without it
        note = None
    extra = f"\n{note}" if note else ""
    sys.stderr.write(f"{message.rstrip()}{extra}\n{label(rule)}\n")
    return 2


def ask(rule: str, question: Callable[[dict], str | None]) -> int:
    """Like `run`, but a hit asks the person (a PreToolUse "ask" decision) instead of refusing."""
    try:
        event = json.load(sys.stdin)
        reason = question(event) if isinstance(event, dict) else None
    except Exception:  # noqa: BLE001 - a broken guard must not block work
        return 0
    if reason:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": f"{reason.rstrip()}\n{label(rule)}",
            }
        }
        sys.stdout.write(json.dumps(output) + "\n")
    return 0
