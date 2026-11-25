# SPDX-License-Identifier: MIT

"""`goal` is the old name of `next-item`. It still runs, says once on stderr that the name is going, and
otherwise answers exactly as `next-item` does."""

from __future__ import annotations

from pathlib import Path

import pytest
from workrun import GOAL, NEXT_ITEM, make_project

NOTICE = "`goal` is now `next-item`; `goal` goes away in a later release"


@pytest.mark.parametrize(
    "args",
    [(), ("--list",), ("--json",), ("merged", "no-such-branch"), ("claim",)],
)
def test_goal_still_runs_and_says_it_is_now_next_item(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    p = make_project(tmp_path)
    rc_new, out_new, err_new = p.run(*args, "--no-fetch", command=NEXT_ITEM)
    rc_old, out_old, err_old = p.run(*args, "--no-fetch", command=GOAL)
    assert NOTICE not in err_new
    first, _, rest = err_old.partition("\n")
    assert first == NOTICE
    assert (rc_old, out_old, rest) == (rc_new, out_new, err_new)
