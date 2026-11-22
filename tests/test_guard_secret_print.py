# SPDX-License-Identifier: MIT

"""no-secret-print: secrets stay out of the transcript (.env files, `op read`, the environment)."""

from __future__ import annotations

import pytest
from hookrun import bash, run_hook, turn_off

SCRIPT = "guard-secret-print.py"


@pytest.mark.parametrize(
    "command",
    [
        "cat .env",
        "cat ./app/.env.local",
        "head -5 .env.production",
        "less .env",
        "tail -n 3 config/.env",
        "grep TOKEN .env",
        "grep -i token .env.local",
        "bat .env",
        "cat < .env",
        "cd app && cat .env",
        "cat .env | grep DB_",
        "op read op://vault/item/field",
        "op read 'op://vault/item/field' 2>/dev/null",
        "op read op://v/i/f >&2",
        "printenv",
        "env",
        "env | sort",
        "printenv | grep GH",
        "printenv GITHUB_TOKEN",
        "echo $GH_TOKEN",
        'echo "token: ${API_KEY}"',
        "printf '%s\\n' \"$DB_PASSWORD\"",
        "echo $AWS_SECRET_ACCESS_KEY && ls",
        "echo $OPENAI_API_KEY",
        "sed -n '1,40p' .env",
        "awk 1 .env",
        "diff .env .env.example",
        "git show HEAD:.env",
        "git show origin/main:app/.env.local",
        "gh auth token",
        "gh auth status --show-token",
        "gh auth status -t",
        "op item get GitHub --fields password --reveal",
        "security find-generic-password -s svc -w",
        "declare -p GITHUB_TOKEN",
        "typeset -p API_KEY",
        "set | grep -i token",
    ],
)
def test_printing_a_secret_is_refused(command: str) -> None:
    rc, err = bash(SCRIPT, command)
    assert rc == 2, err
    assert "[guards:no-secret-print]" in err
    assert "Instead" in err


@pytest.mark.parametrize(
    "command",
    [
        "source .env",
        ". ./.env",
        "set -a; source .env; set +a",
        "cat .env.example",
        "cat .env.sample",
        "grep -c TOKEN .env",
        "grep -q '^GH_TOKEN=' .env && echo present",
        "cp .env.example .env",
        "ls -la .env",
        "wc -l .env",
        "cat src/env.ts",
        "cat .env > /tmp/backup",
        "export $(cat .env | xargs)",
        "TOKEN=$(op read op://a/b/c)",
        "op read op://a/b/c | pbcopy",
        "op read op://a/b/c > secret.txt",
        'gh auth login --with-token <<< "$(op read op://x/y/z)"',
        'curl -H "Authorization: Bearer $(op read op://x/y/z)" https://example.com',
        "op read --out-file f op://x/y/z",
        "op item list",
        "printenv HOME",
        "env FOO=1 make",
        "env -u GH_TOKEN gh pr view",
        "echo $HOME $PATH",
        "echo ${#GH_TOKEN}",
        "echo ${GH_TOKEN:+set}",
        "echo '$GH_TOKEN'",
        "echo $GH_TOKEN | gh auth login --with-token",
        "printf '%s' \"$GH_TOKEN\" > token.txt",
        "git commit -m 'printenv and cat .env are refused'",
        "for key in a b; do echo $key; done",
        '[ -n "$GH_TOKEN" ] && echo set',
        "echo 'cat .env'",
        "echo $TOKENIZERS_PARALLELISM $SSH_KEY_PATH $KEYBOARD $MONKEY $KEYCHAIN_PATH",
        "sed -i '' 's/^X=.*/X=1/' .env",
        "cut -d= -f1 .env",
        "git show HEAD:.env.example",
        "TOKEN=$(gh auth token)",
        "gh auth token | docker login ghcr.io -u me --password-stdin",
        "gh auth status",
        "op item get GitHub --fields username",
        "security find-generic-password -s svc",
        "declare -p PATH",
        "set -euo pipefail",
    ],
)
def test_other_commands_pass(command: str) -> None:
    assert bash(SCRIPT, command) == (0, "")


def _read(path: str) -> tuple[int, str]:
    return run_hook(SCRIPT, {"tool_name": "Read", "tool_input": {"file_path": path}})


@pytest.mark.parametrize(
    "path", ["/repo/.env", "/repo/app/.env.local", "/repo/.env.production"]
)
def test_reading_an_env_file_is_refused(path: str) -> None:
    rc, err = _read(path)
    assert rc == 2, err
    assert "[guards:no-secret-print]" in err


@pytest.mark.parametrize(
    "path",
    ["/repo/.env.example", "/repo/.env.sample", "/repo/src/env.py", "/repo/envrc.md"],
)
def test_reading_other_files_passes(path: str) -> None:
    assert _read(path) == (0, "")


@pytest.mark.parametrize(
    "tool_input",
    [
        {"pattern": "TOKEN", "path": "/repo/.env", "output_mode": "content"},
        {"pattern": "X", "path": "/repo", "glob": ".env*", "output_mode": "content"},
    ],
)
def test_grep_tool_content_of_an_env_file_is_refused(tool_input: dict) -> None:
    rc, err = run_hook(SCRIPT, {"tool_name": "Grep", "tool_input": tool_input})
    assert rc == 2, err


@pytest.mark.parametrize(
    "tool_input",
    [
        {"pattern": "TOKEN", "path": "/repo/.env", "output_mode": "files_with_matches"},
        {"pattern": "TOKEN", "path": "/repo/.env", "output_mode": "count"},
        {"pattern": "TOKEN", "path": "/repo/.env"},
        {"pattern": "TOKEN", "path": "/repo/src"},
        {
            "pattern": "X",
            "path": "/repo",
            "glob": ".env.example",
            "output_mode": "content",
        },
        {"pattern": "X", "path": "/repo", "glob": "*.py", "output_mode": "content"},
    ],
)
def test_grep_tool_names_counts_and_other_files_pass(tool_input: dict) -> None:
    assert run_hook(SCRIPT, {"tool_name": "Grep", "tool_input": tool_input}) == (0, "")


@pytest.mark.parametrize(
    "command",
    [
        "cat .env*",
        "cat .e*",
        "head .*",
        "cat app/.env.*",
        "cat {.env,README.md}",
        "cat .envrc",
        "cat app/.envrc",
        'echo "$GH_TOKEN" | cat',
        "echo $API_KEY | tee /tmp/k",
        "op read op://v/i/f | cat",
        "op read op://v/i/f | tee key.txt",
        "gh auth token | cat -v",
        "op read op://v/i/f | head -c 40 | cat",
        "python3 -c \"print(open('.env').read())\"",
        "python -c 'import sys; sys.stdout.write(open(\".env.local\").read())'",
        "node -e \"console.log(require('fs').readFileSync('.env','utf8'))\"",
        "echo .env | xargs cat",
        "find . -name .env | xargs cat",
        "ls -a | xargs -n1 cat .env",
        "find . -name '.env*' -exec cat {} \\;",
        "printf '%s\\n' .env | xargs -I{} head {}",
    ],
)
def test_the_known_gaps_are_refused(command: str) -> None:
    rc, err = bash(SCRIPT, command)
    assert rc == 2, err
    assert "[guards:no-secret-print]" in err


@pytest.mark.parametrize(
    "command",
    [
        "cat *.md",
        "cat .github/*.yml",
        "cat .env.example*",
        "ls .env*",
        'echo "$GH_TOKEN" | gh auth login --with-token',
        "op read op://v/i/f | tee key.txt > /dev/null",
        "op read op://v/i/f | cat > key.txt",
        "python3 -c \"from dotenv import load_dotenv; load_dotenv('.env')\"",
        "python3 -c 'print(1)'",
        "node -e \"console.log('hi')\"",
        "echo a b | xargs cat",
        "find . -name .env | xargs wc -l",
        "find . -name .env -exec wc -l {} \\;",
        "find . -name '*.py' -exec cat {} \\;",
        "find src/* -name '*.py' -exec cat {} +",
        "find * -name '*.py' -exec cat {} +",
        "ls * | xargs cat",
        "ls src/* | xargs cat",
        "echo *rc | xargs cat",
        "find . -not -name '*env' -exec cat {} +",
    ],
)
def test_near_misses_of_the_gaps_pass(command: str) -> None:
    assert bash(SCRIPT, command) == (0, "")


def test_an_envrc_is_refused_on_read() -> None:
    assert _read("/repo/.envrc")[0] == 2


def test_the_floor_rule_cannot_be_turned_off(tmp_path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    turn_off(tmp_path, "no-secret-print", "trust me")
    rc, err = bash(SCRIPT, "cat .env", cwd=tmp_path)
    assert rc == 2, err
    assert "can't be turned off" in err


@pytest.mark.parametrize(
    "command", ["cat .env.c*", "cat .env.ci*", "cat .envrc.l?cal", "grep X .env.[ps]*"]
)
def test_globs_beyond_the_usual_names_are_refused(command: str) -> None:
    assert bash(SCRIPT, command)[0] == 2


@pytest.mark.parametrize("glob", [".env.ci*", "{.env,*.md}", ".env.c?"])
def test_grep_globs_beyond_the_usual_names_are_refused(glob: str) -> None:
    tool_input = {
        "pattern": "X",
        "path": "/repo",
        "glob": glob,
        "output_mode": "content",
    }
    assert run_hook(SCRIPT, {"tool_name": "Grep", "tool_input": tool_input})[0] == 2


@pytest.mark.parametrize(
    "command",
    [
        "python3 -c \"import os; print(os.path.exists('.env'))\"",
        "python3 -c \"print('.env' in open('.gitignore').read())\"",
    ],
)
def test_code_that_mentions_a_env_file_without_reading_it_passes(command: str) -> None:
    assert bash(SCRIPT, command) == (0, "")


@pytest.mark.parametrize(
    "command",
    [
        "op read op://v/i/f | sed -n p",
        "op item get x --fields password --reveal | jq -r .password",
        "base64 .env",
        "fold .env",
        "paste .env",
        "jq . .env",
        "cut -d= -f2- .env",
    ],
)
def test_more_printing_programs_are_refused(command: str) -> None:
    assert bash(SCRIPT, command)[0] == 2


@pytest.mark.parametrize(
    "command",
    [
        "python3 -c \"from pathlib import Path; print(Path('.env').exists())\"",
        "find . -name '*.py' -not -name .env | xargs cat",
        "find . ! -name .env -name '*.py' -exec cat {} +",
        "cut -d= -f1 .env",
        "cut -f 1 -d = .env",
    ],
)
def test_more_near_misses_pass(command: str) -> None:
    assert bash(SCRIPT, command) == (0, "")


def test_a_path_read_of_a_env_file_is_refused() -> None:
    assert (
        bash(
            SCRIPT,
            "python3 -c \"from pathlib import Path; print(Path('.env').read_text())\"",
        )[0]
        == 2
    )


@pytest.mark.parametrize(
    "command",
    [
        "cat .env.local.example",
        "cat config/.env.production.sample",
        "python3 -c \"print(open('.env.local.example').read())\"",
        "grep .env .gitignore",
        "grep -n '^.env' .gitignore .dockerignore",
        "grep -rn '.env' src/",
        "rg .env README.md",
        "grep -A2 -m 1 .env .gitignore",
    ],
)
def test_samples_and_a_pattern_that_only_looks_like_a_name_pass(command: str) -> None:
    assert bash(SCRIPT, command) == (0, "")


@pytest.mark.parametrize(
    "command",
    [
        "grep -e TOKEN .env",
        "grep --regexp=TOKEN .env",
        "grep -f patterns.txt .env",
        "grep -A 2 TOKEN .env",
        "rg -e TOKEN .env",
        "grep -ieTOKEN .env",
        "grep -T TOKEN .env",
        "grep DB .env",
        "rg -g .env TOKEN",
        "rg -g.env TOKEN",
        "rg --glob .env TOKEN",
        "rg --glob=.env TOKEN",
        "grep --include .env -r TOKEN .",
        "grep --include=.env -r TOKEN .",
        "grep --context TOKEN .env",
        "grep --regex=TOKEN .env",
        "grep id .env",
        "rg id .env",
        "grep -rn TOKEN -- .env",
        "cat .env.local.example .env",
    ],
)
def test_a_env_file_after_the_pattern_is_still_refused(command: str) -> None:
    assert bash(SCRIPT, command)[0] == 2


@pytest.mark.parametrize(
    "command",
    [
        "declare -p",
        "typeset -p",
        "export",
        "export -p",
        "declare -x",
        "declare",
        "find . -name '*env' -exec cat {} \\;",
        "find . -name '*env' | xargs cat",
        "find . -name '*.env.qa' -exec cat {} \\;",
        "find . -iname '*ENV*' -exec cat {} \\;",
        "find . -path '*/.env' -exec cat {} +",
    ],
)
def test_more_ways_to_print_a_secret_are_refused(command: str) -> None:
    rc, err = bash(SCRIPT, command)
    assert rc == 2, command
    assert "[guards:no-secret-print]" in err


@pytest.mark.parametrize(
    "command",
    [
        'TOKEN="$(gh auth token)"',
        'curl -H "Authorization: Bearer $(gh auth token)" https://example.com',
        "export -p > /tmp/env.txt",
        "export FOO=1",
        "declare -p HOME",
        "declare -f",
        "find . -name '*.py' -exec cat {} \\;",
    ],
)
def test_uses_that_print_no_secret_pass(command: str) -> None:
    assert bash(SCRIPT, command)[0] == 0, command
