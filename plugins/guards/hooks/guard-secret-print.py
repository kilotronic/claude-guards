#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash|Read|Grep hook (no-secret-print): keep secrets out of the transcript.

Whatever a command prints lands in the session transcript (and in any log of it). Refused:
- reading a `.env`/`.env.*`/`.envrc` file (not `.env.example`/`.sample`/`.template`/`.dist`), or a glob or
  brace list that can match one (`cat .env*`), with a printing reader (also through `xargs` or
  `find -exec`), the Read tool, Grep's content mode, `git show <rev>:.env`, or `python3 -c`/`node -e`
  code that prints it;
- `op read`, `op item get --reveal`, `gh auth token` and `security find-*-password -w` whose output
  reaches the terminal: a redirect, `--out-file`, `$(...)` or a pipe to a program that uses it is fine;
  a pipe to `cat`, `tee` or another program that just prints is not;
- bare `env`, `printenv` and `set`;
- `echo`/`printf`/`printenv`/`declare -p` of a variable whose name ends in a secret word (GH_TOKEN,
  OPENAI_API_KEY; not SSH_KEY_PATH), also through such a pipe.
Sourcing a `.env` prints nothing and passes. A floor rule: it can't be turned off.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardlib

RULE = "no-secret-print"
SAFE_SUFFIXES = {"example", "sample", "template", "dist"}
READERS = {
    *(
        "cat",
        "less",
        "more",
        "head",
        "tail",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "bat",
        "batcat",
    ),
    *("nl", "tac", "view", "xxd", "od", "strings", "column", "hexdump"),
    *("sed", "awk", "gawk", "diff", "sort", "uniq"),
    *("base64", "fold", "fmt", "paste", "jq", "yq", "cut", "rev", "tr", "perl"),
}
GREPS = {"grep", "egrep", "fgrep", "rg"}
# grep and rg short flags that take a value (the rest of the word, or the next word). Long flags
# are read only with `=`: a value read wrongly could hide a file, a word left in only adds one.
GREP_SHORT_VALUES = {"grep": set("efmABCdD"), "rg": set("efmABCdgtTjM")}
# Flags whose value names files to read.
GREP_FILE_FLAGS = {"-f", "-g", "--file", "--glob", "--iglob", "--include"}
# grep flags after which only counts or file names print.
QUIET_GREP = set("qclL")
QUIET_GREP_LONG = {
    "--quiet",
    "--silent",
    "--count",
    "--files-with-matches",
    "--files-without-match",
}
SECRET_WORDS = {
    *("TOKEN", "TOKENS", "SECRET", "SECRETS", "KEY", "KEYS", "PASSWORD", "PASSWORDS"),
    *("PASSWD", "PASS", "CREDENTIAL", "CREDENTIALS", "APIKEY", "PAT"),
}
PRINTERS = {"echo", "printf", "print"}
# Names a glob is tried against: could `cat .e*` print a .env file?
ENV_NAMES = (
    *(
        ".env",
        ".envrc",
        ".env.local",
        ".env.production",
        ".env.prod",
        ".env.development",
    ),
    *(".env.dev", ".env.test", ".env.staging", ".envrc.local"),
)
GLOB = re.compile(r"[*?\[]")
GLOB_TOKEN = re.compile(r"\*|\?|\[!?\]?[^\]]*\]")
# Programs that pass their input to their output (or the terminal) unchanged enough to read.
PASSTHROUGH = {
    *("cat", "tee", "less", "more", "head", "tail", "bat", "batcat", "nl", "tac"),
    *("column", "sort", "uniq", "xxd", "od", "hexdump", "strings", "fold", "fmt"),
    *("rev", "base64", "tr", "grep", "egrep", "fgrep", "rg", "sed", "jq", "yq"),
}
INTERPRETERS = re.compile(r"^(?:python[\d.]*|node|nodejs|deno|bun|ruby|perl|php)$")
INLINE_FLAGS = {"-c", "-e", "-p", "-pe", "-ne", "-E", "--eval", "--print", "-r"}
# A read of a .env file by name: `open('.env')`, `readFileSync(".env.local")`, `Path('.env').read_text()`.
INLINE_ENV = re.compile(
    r"""(?:(?:open|readFileSync|readFile|File\.read|IO\.read|dotenv_values|file_get_contents)\s*\(\s*"""
    r"""|Path\s*\(\s*(?=f?['"][^'"]*['"]\s*\)\s*\.(?:read_text|read_bytes|open)))"""
    r"""f?['"](?:[^'"]*/)?\.env(?:rc)?(?:\.([\w.-]+))?['"]"""
)
INLINE_PRINT = re.compile(
    r"\bprint\b|\bstdout\b|console\.(?:log|info|error|dir)|\bputs\b|\becho\b|\bp\(|\bsay\b"
)

ENV_ALTERNATIVE = (
    "Instead: check a key is set without its value (`grep -c '^NAME=' .env`, `echo ${#NAME}`),\n"
    "list the names only (`cut -d= -f1 .env`), or load it with `source .env`."
)


def secret_name(name: str) -> bool:
    """Whether the last word of *name* is a secret word: GH_TOKEN yes, SSH_KEY_PATH no."""
    return name.rsplit("_", 1)[-1] in SECRET_WORDS


def is_env_file(path: str) -> bool:
    name = os.path.basename(path.rstrip("/"))
    if name in (".env", ".envrc"):
        return True
    stem, dot, suffix = name.partition(".env")[2].partition(".")
    return (
        name.startswith((".env.", ".envrc."))
        and stem in ("", "rc")
        and bool(dot)
        and suffix.rsplit(".", 1)[-1].lower() not in SAFE_SUFFIXES
    )


def braces(word: str) -> list[str]:
    """`{a,b}` alternatives of *word*, one level deep."""
    match = re.search(r"\{([^{}]*,[^{}]*)\}", word)
    if not match:
        return [word]
    return [
        word[: match.start()] + alt + word[match.end() :]
        for alt in match.group(1).split(",")
    ]


def _shortest(token: re.Match) -> str:
    """The shortest text a glob token matches: `*` none, `?` one character, `[ab]` its first."""
    text = token.group()
    if text == "*":
        return ""
    if text == "?" or text.startswith("[!"):
        return "x"
    return text[1:2]


def may_name_env_file(word: str, dotglob: bool = False) -> bool:
    """Whether *word* is, or is a glob or brace list that can match, a .env file. A shell glob matches a dot
    file only when it starts with a dot; with *dotglob* (a `find -name` pattern) it matches anyway."""
    for alt in braces(word):
        name = os.path.basename(alt.rstrip("/"))
        if not GLOB.search(name):
            if is_env_file(alt):
                return True
        elif (
            dotglob
            and any(fnmatch.fnmatchcase(c, name) for c in ENV_NAMES)
            or name.startswith(".")
            and (
                any(fnmatch.fnmatchcase(c, name) for c in ENV_NAMES)
                or is_env_file(GLOB_TOKEN.sub(_shortest, name))
            )
        ):
            return True
    return False


def quiet_grep(args: list[str]) -> bool:
    for word in args:
        if word in QUIET_GREP_LONG:
            return True
        if (
            word.startswith("-")
            and not word.startswith("--")
            and QUIET_GREP & set(word[1:])
        ):
            return True
    return False


def edits_in_place(name: str, args: list[str]) -> bool:
    if name == "sed":
        return any(w.startswith(("-i", "--in-place")) for w in args)
    return name in ("awk", "gawk") and "inplace" in args


def redirected(command: guardlib.Command) -> bool:
    """Output to a file or a `$(...)`: a pipe still reaches the terminal at its end."""
    return command.substituted or (command.stdout_captured() and not command.piped)


def revealed(argv: list[str]) -> str | None:
    """The secret-printing command *argv* is, or None."""
    name = os.path.basename(argv[0]) if argv else ""
    if (
        name == "op"
        and argv[1:2] == ["read"]
        and not any(
            w in ("-o", "--out-file") or w.startswith("--out-file=") for w in argv[2:]
        )
    ):
        return "`op read`"
    if name == "op" and argv[1:3] == ["item", "get"] and "--reveal" in argv:
        return "`op item get --reveal`"
    if name == "gh" and argv[1:3] == ["auth", "token"]:
        return "`gh auth token`"
    if (
        name == "gh"
        and argv[1:3] == ["auth", "status"]
        and ("-t" in argv or "--show-token" in argv)
    ):
        return "`gh auth status --show-token`"
    if (
        name == "security"
        and argv[1:2]
        and argv[1].startswith("find-")
        and ("-w" in argv or "-g" in argv)
    ):
        return f"`security {argv[1]} -w`"
    return None


def file_redirected(command: guardlib.Command) -> bool:
    return command.substituted or any(
        (op in guardlib.STDOUT_REDIRECTS or op in ("1>", "1>>", "1>|"))
        and not target.startswith("&")
        for op, target in command.redirects
    )


def receiver(commands: list[guardlib.Command], i: int) -> int | None:
    """The index of the command that reads command *i*'s pipe."""
    for k in range(i + 1, len(commands)):
        later = commands[k]
        if later.first == i + 1 and later.substituted == commands[i].substituted:
            return k
    return None


def xargs_command(argv: list[str]) -> list[str]:
    """The command `xargs [options] <command>` runs (`echo` when none is named)."""
    words = argv[1:]
    takes = {"-n", "-I", "-L", "-d", "-E", "-s", "-P", "-a", "-i", "-l", "-e"}
    while words and words[0].startswith("-"):
        flag = words.pop(0)
        if flag == "--":
            break
        if flag in takes and words and flag not in ("-i", "-l", "-e"):
            words.pop(0)
    return words or ["echo"]


def passes_through(argv: list[str]) -> bool:
    name = os.path.basename(argv[0]) if argv else ""
    if name == "xargs":
        return (
            passes_through(xargs_command(argv))
            or os.path.basename(xargs_command(argv)[0]) in PRINTERS
        )
    if name in GREPS:
        return not quiet_grep(argv[1:])
    return name in PASSTHROUGH


def reaches_terminal(commands: list[guardlib.Command], i: int) -> bool:
    """Whether command *i*'s output reaches the terminal, through pipes to programs that just print it."""
    for _ in range(len(commands)):
        command = commands[i]
        if file_redirected(command):
            return False
        if not command.piped:
            return True
        k = receiver(commands, i)
        if k is None or not passes_through(commands[k].argv):
            return False
        i = k
    return False


def producer(commands: list[guardlib.Command], i: int) -> guardlib.Command | None:
    """The command whose output is piped into command *i*."""
    for j in range(i - 1, -1, -1):
        if commands[j].piped and receiver(commands, j) == i:
            return commands[j]
    return None


def reader_refusal(
    argv: list[str], command: guardlib.Command, named: list[str] = ()
) -> str | None:
    """A printing reader in *argv* of a .env file named in its arguments, or matched by a `find` pattern
    in *named*."""
    name = os.path.basename(argv[0]) if argv else ""
    if name not in READERS or redirected(command):
        return None
    files = (
        grep_files(name, argv[1:])
        if name in GREPS
        else [w for w in argv[1:] if not w.startswith("-")]
    )
    files += [
        target for op, target in command.redirects if op.lstrip("0123456789") == "<"
    ]
    quiet = (name in GREPS and quiet_grep(argv[1:])) or (
        name == "cut" and names_only(argv[1:])
    )
    if (
        (
            any(may_name_env_file(f) for f in files)
            or any(may_name_env_file(f, dotglob=True) for f in named)
        )
        and not quiet
        and not edits_in_place(name, argv[1:])
    ):
        return f"`{name}` of a .env file: its secrets would land in the transcript.\n\n{ENV_ALTERNATIVE}"
    return None


def grep_files(name: str, args: list[str]) -> list[str]:
    """The files a grep reads: its operands and file-naming flag values, less the pattern (the first
    operand, unless `-e`/`--regexp`/`-f`/`--file` gave one)."""
    operands: list[str] = []
    files: list[str] = []
    pattern_given = False
    short = GREP_SHORT_VALUES["rg" if name == "rg" else "grep"]
    words = iter(args)
    for word in words:
        if word == "--":
            operands += list(words)
            break
        if word.startswith("--"):
            flag, eq, value = word.partition("=")
            # getopt takes any unambiguous prefix: `--regex=` is `--regexp=`.
            regexp = len(flag) >= 5 and "--regexp".startswith(flag)
            if flag in ("--regexp", "--file") or regexp:
                pattern_given = True
                value = value if eq else next(words, "")
            if flag in GREP_FILE_FLAGS:
                files.append(value if eq else next(words, ""))
        elif word.startswith("-") and len(word) > 1:
            for k, letter in enumerate(word[1:], 1):
                if letter in short:
                    value = word[k + 1 :] or next(words, "")
                    pattern_given = pattern_given or letter in "ef"
                    if f"-{letter}" in GREP_FILE_FLAGS:
                        files.append(value)
                    break
        else:
            operands.append(word)
    return files + (operands if pattern_given else operands[1:])


def names_only(args: list[str]) -> bool:
    """`cut -d= -f1`: only the first field, the names of a .env file."""
    words = " ".join(args).replace("-f ", "-f").split()
    return "-f1" in words or "--fields=1" in words


def kept(words: list[str]) -> list[str]:
    """*words* less the patterns `find` excludes (`-not -name .env`, `! -path ...`)."""
    out: list[str] = []
    skip = 0
    for word in words:
        if skip:
            skip -= 1
        elif word in ("-not", "!"):
            skip = 2
        else:
            out.append(word)
    return out


def find_exec(argv: list[str]) -> list[str] | None:
    """The command a `find ... -exec <command> ;` runs, or None."""
    for k, word in enumerate(argv):
        if word in ("-exec", "-execdir", "-ok", "-okdir"):
            rest = argv[k + 1 :]
            end = next((n for n, w in enumerate(rest) if w in (";", "+")), len(rest))
            return rest[:end]
    return None


def inline_refusal(argv: list[str]) -> str | None:
    """`python3 -c`/`node -e` code that reads and prints a .env file."""
    if not argv or not INTERPRETERS.match(os.path.basename(argv[0])):
        return None
    for k, word in enumerate(argv[1:-1], 1):
        if word not in INLINE_FLAGS:
            continue
        script = argv[k + 1]
        names = [m.group(1) or "" for m in INLINE_ENV.finditer(script)]
        prints = word in ("-p", "--print", "-pe") or INLINE_PRINT.search(script)
        if prints and any(
            n.rsplit(".", 1)[-1].lower() not in SAFE_SUFFIXES for n in names
        ):
            return (
                f"`{os.path.basename(argv[0])} {word}` code that prints a .env file: its secrets would land\n"
                f"in the transcript.\n\n{ENV_ALTERNATIVE}"
            )
    return None


def substituted_reveal(commands: list[guardlib.Command], i: int) -> str | None:
    """A secret-printing command inside command *i*'s `$(...)`, backticks or `<(...)`, when command *i*
    prints what it is given (`echo "$(gh auth token)"`, `cat <(op read ...)`) to the terminal."""
    command = commands[i]
    argv = command.argv
    name = os.path.basename(argv[0]) if argv else ""
    if not (name in PRINTERS or name in PASSTHROUGH or name in READERS):
        return None
    if not reaches_terminal(commands, i):
        return None
    for inner in commands[command.first : i]:
        if inner.substituted and not file_redirected_inside(inner):
            shown = revealed(inner.argv)
            if shown:
                return shown
    return None


def file_redirected_inside(command: guardlib.Command) -> bool:
    """Inside a substitution, output sent to a file (not the substitution) is not handed on."""
    return any(
        (op in guardlib.STDOUT_REDIRECTS or op in ("1>", "1>>", "1>|"))
        and not target.startswith("&")
        for op, target in command.redirects
    )


def bash_refusal(commands: list[guardlib.Command], i: int) -> str | None:
    command = commands[i]
    argv = command.argv
    name = os.path.basename(argv[0]) if argv else ""
    found = reader_refusal(argv, command)
    if not found and name == "xargs":
        # The file names may come down the pipe: `find . -name .env | xargs cat`.
        source = producer(commands, i)
        found = reader_refusal(
            xargs_command(argv), command, kept(source.argv[1:]) if source else []
        )
    if not found and name == "find":
        inner = find_exec(argv)
        if inner:
            found = reader_refusal(inner, command, kept(argv[1:]))
    found = found or (None if redirected(command) else inline_refusal(argv))
    if found:
        return found
    git = guardlib.git_args(argv)
    if (
        git
        and git[0] in ("show", "cat-file")
        and not redirected(command)
        and any(
            ":" in w and may_name_env_file(w.split(":", 1)[1])
            for w in git[1]
            if not w.startswith("-")
        )
    ):
        return f"`git {git[0]}` of a .env file: its secrets would land in the transcript.\n\n{ENV_ALTERNATIVE}"
    printed = reaches_terminal(commands, i)
    shown = revealed(argv) or (
        None if command.substituted else substituted_reveal(commands, i)
    )
    if shown and printed:
        return (
            f"{shown} to the terminal: the secret would land in the transcript.\n\n"
            "Instead: use it without printing it: `X=$(...)`, a pipe straight to the command that needs\n"
            "it (`... | pbcopy`), `op read --out-file <path>`, or `op run -- <command>`."
        )
    dumps = command.words in (["env"], ["set"]) or argv == ["printenv"]
    # `export`, `declare -p`, `typeset -p` with no names print every variable, secrets included.
    dumps = dumps or (
        name in ("export", "declare", "typeset")
        and command.words == argv
        and all(w.startswith("-") for w in argv[1:])
        and not any(
            set(w) & set("fF") for w in argv[1:]
        )  # `declare -f` lists functions
    )
    if dumps and not redirected(command):
        return (
            f"`{command.words[0]}` with no arguments: it prints every secret in the environment.\n\n"
            "Instead: print one variable that is not a secret (`printenv HOME`), or check one is set\n"
            "with `echo ${#NAME}`."
        )
    secret = None
    if name in PRINTERS and printed:
        secret = next(
            (v for refs in command.refs[1:] for v in refs if secret_name(v)), None
        )
    elif (
        name == "printenv" or (name in ("declare", "typeset") and "-p" in argv)
    ) and not redirected(command):
        secret = next((w for w in argv[1:] if secret_name(w)), None)
    if secret:
        return (
            f"printing `{secret}`: a secret's value would land in the transcript.\n\n"
            f'Instead: check it is set with `echo ${{#{secret}}}` or `[ -n "${secret}" ]`, or pipe it\n'
            "straight to the command that needs it."
        )
    return None


def grep_refusal(args: dict) -> str | None:
    if args.get("output_mode") != "content":
        return None  # file names or counts only (files_with_matches is the default)
    path, glob = args.get("path") or "", args.get("glob") or ""
    glob_name = glob.rsplit("/", 1)[-1] if isinstance(glob, str) else ""
    glob_hits = may_name_env_file(glob_name)
    if (isinstance(path, str) and is_env_file(path)) or glob_hits:
        return f"Grep of a .env file's content: its secrets would land in the transcript.\n\n{ENV_ALTERNATIVE}"
    return None


def refusal(event: dict) -> str | None:
    tool = event.get("tool_name")
    args = event.get("tool_input") or {}
    if tool == "Read":
        path = args.get("file_path") or ""
        if isinstance(path, str) and is_env_file(path):
            return f"reading {path}: its secrets would land in the transcript.\n\n{ENV_ALTERNATIVE}"
        return None
    if tool == "Grep":
        return grep_refusal(args)
    if tool != "Bash":
        return None
    commands = guardlib.bash_commands(event)
    for i in range(len(commands)):
        reason = bash_refusal(commands, i)
        if reason:
            return reason
    return None


if __name__ == "__main__":
    sys.exit(guardlib.run(RULE, refusal))
