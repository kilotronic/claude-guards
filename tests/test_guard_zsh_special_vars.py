# SPDX-License-Identifier: MIT

"""no-zsh-special-vars: `path`, `status`, `UID`, `GID` are zsh's own (`path` is tied to PATH)."""

from __future__ import annotations

import pytest
from hookrun import bash

SCRIPT = "guard-zsh-special-vars.py"


@pytest.mark.parametrize(
    "command",
    [
        "path=$(git rev-parse --show-toplevel)",
        "status=$?",
        "UID=5 ./run",
        "GID=20",
        "x=1; path=/tmp/wt && cd $path",
        "local path=/tmp",
        "export status=ok",
        "typeset -g path=a",
        "for path in a b; do echo $path; done",
        "if true; then status=1; fi",
        "path+=(/opt/bin)",
        "zsh -c 'status=$(gh pr view --json state)'",
    ],
)
def test_assigning_a_special_name_is_refused(command: str) -> None:
    rc, err = bash(SCRIPT, command)
    assert rc == 2, err
    assert "[guards:no-zsh-special-vars]" in err
    assert "zsh treats these as special" in err
    assert "wt" in err


@pytest.mark.parametrize(
    "command",
    [
        "wt=$(git rev-parse --show-toplevel)",
        "rc=$?",
        "PATH=/opt/bin:$PATH make",
        "STATUS=ok",
        "Path=x",
        "echo path=x status=1",
        "git commit -m 'status=$? and path=foo'",
        "gh pr view --json state,status",
        "make path=foo",
        "env path=x ./tool",
        "echo $path $status",
        "grep -r 'UID=' .",
        "for wt in a b; do echo $wt; done",
    ],
)
def test_other_names_and_plain_text_pass(command: str) -> None:
    assert bash(SCRIPT, command) == (0, "")
