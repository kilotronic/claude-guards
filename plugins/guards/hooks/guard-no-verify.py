#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash hook — refuse a hook-bypassing commit of content pre-commit has not seen.

`--no-verify` is sanctioned for squashing, rewording and amending commits that
already passed, and forbidden for new changes. The two
are told apart by CONTENT, not by flags: the index tree is compared with HEAD's
and ORIG_HEAD's. A reword leaves it equal to HEAD; a squash after
`git reset --soft` leaves it equal to ORIG_HEAD; anything else is new content.
The command is tokenized shell-style so a quoted message, a heredoc body or a
`;` inside `-m` cannot hide or fake a commit. It refuses the ordinary spellings
and fails open on the rest — it is not a shell; it covers the cases an agent actually types.
`no-commit-on-main` off in the overrides file turns it off for a repo.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardlib

RULE = "no-commit-on-main"

_HEREDOC = re.compile(
    r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\s*$", re.DOTALL | re.MULTILINE
)
_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")"}
_ENV_ASSIGN = re.compile(r"^\w+=")
_REDIRECT = re.compile(r"^(?:[<>]{1,2}&?|&>>?)$")
_SHELLS = {"bash", "sh", "zsh", "dash"}
_GIT_GLOBAL_WITH_VALUE = {"-C", "-c"}
_GIT_GLOBAL_FLAGS = {"--no-pager", "--bare", "--no-replace-objects"}
_COMMIT_VALUE_FLAGS = {
    "-m", "-F", "-C", "-c", "-t", "--message", "--file", "--author", "--date",
    "--template", "--fixup", "--squash", "--reuse-message", "--reedit-message",
    "--trailer", "--cleanup", "--pathspec-from-file",
}  # fmt: skip
_OUTSIDE_INDEX = {"-a", "--all", "-i", "--include", "-o", "--only"}
_SHORT_FLAGS = re.compile(r"^-[a-zA-Z]+$")
# Anything that writes the index before the commit in the same line. `reset`
# is only benign as `--soft` (the sanctioned squash), handled in `writes_index`.
_INDEX_WRITERS = {
    "add", "rm", "mv", "restore", "checkout", "apply", "stash", "cherry-pick",
    "merge", "rebase", "revert", "read-tree", "update-index", "reset",
}  # fmt: skip


def _tokens(text: str) -> list[str]:
    """Shell words; operators and unquoted newlines come out as their own tokens."""
    lex = shlex.shlex(text, posix=True, punctuation_chars=";&|()<>\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError:
        tokens = text.split()
    return _without_redirections(tokens)


def _without_redirections(tokens: list[str]) -> list[str]:
    """Drop `> file`, `2>&1`, `< in`: their operands are not pathspecs."""
    out: list[str] = []
    skip = False
    for token in tokens:
        if skip:
            skip = False
            continue
        if _REDIRECT.match(token):
            if out and out[-1].isdigit():
                out.pop()
            skip = True
            continue
        out.append(token)
    return out


def _segments(command: str) -> list[list[str]]:
    """Simple commands, split on shell operators; heredoc bodies are dropped first.

    `bash -c '…'` and `eval '…'` are unwrapped in place, so the commands inside
    a quoted string are judged like the ones outside it.
    """
    segments: list[list[str]] = [[]]
    for token in _tokens(_HEREDOC.sub("", command)):
        if token in _SEPARATORS or token.startswith((";", "\n")):
            if segments[-1]:
                segments.append([])
        else:
            segments[-1].append(token)
    out: list[list[str]] = []
    for segment in (s for s in segments if s):
        inner = _wrapped_script(segment)
        out.extend(_segments(inner) if inner is not None else [segment])
    return out


def _wrapped_script(segment: list[str]) -> str | None:
    head = os.path.basename(segment[0])
    if head in _SHELLS and "-c" in segment[1:]:
        at = segment.index("-c")
        return segment[at + 1] if at + 1 < len(segment) else None
    if head == "eval":
        return " ".join(segment[1:])
    return None


class _Git:
    """One `git <sub> …` segment, with its prefix and global flags parsed off."""

    def __init__(
        self, env: list[str], globals_: list[str], sub: str, args: list[str]
    ) -> None:
        self.env, self.globals, self.sub, self.args = env, globals_, sub, args

    @classmethod
    def parse(cls, segment: list[str]) -> _Git | None:
        """Anything before the first `git` word is a prefix: env, nice, timeout, sudo…"""
        try:
            i = next(
                k for k, tok in enumerate(segment) if os.path.basename(tok) == "git"
            )
        except StopIteration:
            return None
        env = [tok for tok in segment[:i] if _ENV_ASSIGN.match(tok)]
        i += 1
        globals_: list[str] = []
        while i < len(segment):
            token = segment[i]
            if token in _GIT_GLOBAL_WITH_VALUE and i + 1 < len(segment):
                globals_ += [token, segment[i + 1]]
                i += 2
            elif token in _GIT_GLOBAL_FLAGS or token.startswith(
                ("--git-dir=", "--work-tree=")
            ):
                globals_.append(token)
                i += 1
            else:
                break
        if i >= len(segment):
            return None
        return cls(env, globals_, segment[i], segment[i + 1 :])

    def bypass(self) -> str | None:
        if self.sub != "commit":
            return None
        if any("core.hooksPath" in g for g in self.globals):
            return "core.hooksPath"
        if any(e.startswith("SKIP=") for e in self.env):
            return "SKIP="
        for token in self._flags():
            if token.startswith("--no-ver"):  # git accepts any unique prefix
                return token
            if _SHORT_FLAGS.match(token) and "n" in token:
                return token
        return None

    def _flags(self) -> list[str]:
        out, skip = [], False
        for token in self.args:
            if skip:
                skip = False
                continue
            if token == "--":
                break
            if token in _COMMIT_VALUE_FLAGS:
                skip = True
                continue
            if token.startswith("-"):
                out.append(token)
        return out

    def commits_outside_the_index(self) -> bool:
        """`-a`, `--include`, `--only` or a pathspec: content the index tree cannot vouch for."""
        skip = False
        for token in self.args:
            if skip:
                skip = False
                continue
            if token == "--":
                return True
            if token in _COMMIT_VALUE_FLAGS:
                skip = True
                continue
            if token in _OUTSIDE_INDEX or (_SHORT_FLAGS.match(token) and "a" in token):
                return True
            if not token.startswith("-"):
                return True  # a bare pathspec
        return False

    def writes_index(self) -> bool:
        if self.sub == "reset":
            return "--soft" not in self.args
        return self.sub in _INDEX_WRITERS

    def disarms_hooks(self) -> bool:
        return self.sub == "config" and any("core.hooksPath" in a for a in self.args)

    def cwd(self, base: str) -> str:
        for i, token in enumerate(self.globals):
            if token == "-C" and i + 1 < len(self.globals):
                base = os.path.join(base, self.globals[i + 1])
        return base


def _git(cwd: str, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


_KNOWN_SUBS = _INDEX_WRITERS | {
    "commit",
    "config",
    "status",
    "log",
    "diff",
    "push",
    "fetch",
    "pull",
}
_ALIAS_DEPTH = 5  # git chains aliases; a cycle stops here


def expand_alias(git: _Git, cwd: str) -> _Git:
    """`git cm x` with `alias.cm = commit -m` is a commit; resolve it before judging.

    Inline `-c alias.x=…` wins over config, chains resolve like git resolves
    them, and a `!shell` alias stays opaque (fail open).
    """
    for _ in range(_ALIAS_DEPTH):
        if git.sub in _KNOWN_SUBS:
            return git
        inline = [
            g.split("=", 1)[1] for g in git.globals if g.startswith(f"alias.{git.sub}=")
        ]
        expansion = (
            inline[-1] if inline else _git(cwd, "config", "--get", f"alias.{git.sub}")
        )
        if not expansion or expansion.startswith("!"):
            return git
        words = _tokens(expansion)
        if not words:
            return git
        git = _Git(git.env, git.globals, words[0], [*words[1:], *git.args])
    return git


def index_already_passed(cwd: str) -> bool | None:
    """True when the index tree equals HEAD's or ORIG_HEAD's; None = cannot tell."""
    tree = _git(cwd, "write-tree")
    if tree is None:
        return None
    for ref in ("HEAD", "ORIG_HEAD"):
        if _git(cwd, "rev-parse", "--verify", "--quiet", ref + "^{tree}") == tree:
            return True
    return False


def refusal(command: str, cwd: str) -> tuple[str, str] | None:
    """(The reason to refuse this command line, the worktree it acts in), or None to allow it."""
    here = cwd
    index_written = False
    disarmed: str | None = None
    for segment in _segments(command):
        if segment[0] == "cd" and len(segment) > 1:
            here = os.path.join(here, os.path.expanduser(segment[1]))
            continue
        if segment[0] == "export" and any(a.startswith("SKIP=") for a in segment[1:]):
            disarmed = "export SKIP="
            continue
        git = _Git.parse(segment)
        if git is None:
            continue
        if git.disarms_hooks():
            disarmed = "git config core.hooksPath"
            continue
        git = expand_alias(git, git.cwd(here))
        if git.sub != "commit":  # only `git commit` runs pre-commit
            index_written = index_written or git.writes_index()
            continue
        kind = git.bypass() or disarmed
        if kind is None:
            continue
        place = git.cwd(here)
        reason = _commit_refusal(git, kind, index_written, here)
        if reason is not None and not guardlib.off(RULE, place):
            return reason, place
    return None


def _commit_refusal(git: _Git, kind: str, index_written: bool, here: str) -> str | None:
    if index_written:
        return f"`{kind}` after an index write in the same command"
    if git.commits_outside_the_index():
        return f"`{kind}` with `-a`/`--include`/`--only` or a pathspec"
    if index_already_passed(git.cwd(here)) is False:
        return f"`{kind}` on an index tree that matches neither HEAD nor ORIG_HEAD"
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail open: an unreadable payload is not evidence of a bypass
    command = (payload.get("tool_input") or {}).get("command") or ""
    found = refusal(command, payload.get("cwd") or os.getcwd())
    if found is None:
        return 0
    reason, place = found
    return guardlib.refuse(
        RULE,
        f"Refusing {reason}: this bypasses pre-commit on content it has not seen.\n\n"
        "A hook bypass is allowed only when the committed content already passed:\n"
        "  • rewording:  `git commit --amend --no-edit --no-verify` with nothing staged\n"
        "  • squashing:  `git reset --soft origin/main && git commit --no-verify -m ...`\n"
        "The index tree must equal HEAD's or ORIG_HEAD's; `git add` in the same line,\n"
        "`-a`, `--include`, `--only` and pathspecs always count as new content.\n\n"
        "For new changes, run the hooks — `make format` first if they are formatting.\n",
        place,
    )


if __name__ == "__main__":
    sys.exit(main())
