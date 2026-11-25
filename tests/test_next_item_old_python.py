# SPDX-License-Identifier: MIT

"""`next-item` runs under whatever `python3` is first on PATH, which can be macOS's own 3.9."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from workrun import NEXT_ITEM, WORK, make_project


def test_next_item_defers_annotations_so_3_9_can_load_it() -> None:
    assert (
        "from __future__ import annotations"
        in (WORK / "scripts" / "next_item.py").read_text()
    )


def _python39() -> str | None:
    for name in ("python3.9", "/usr/bin/python3"):
        path = shutil.which(name)
        if path:
            done = subprocess.run(
                [path, "-c", "import sys; print(sys.version_info[:2] == (3, 9))"],
                capture_output=True,
                text=True,
                check=False,
            )
            if done.stdout.strip() == "True":
                return path
    return None


def test_next_item_answers_under_3_9(tmp_path: Path) -> None:
    python = _python39()
    if python is None:
        pytest.skip("no Python 3.9 on this machine; the annotations test covers it")
    p = make_project(tmp_path)
    done = subprocess.run(
        [python, str(NEXT_ITEM), "--today", "2026-11-22", "--no-fetch"],
        cwd=p.clone,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "The timer skips a second" in done.stdout
