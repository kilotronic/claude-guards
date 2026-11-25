# SPDX-License-Identifier: MIT

"""Every test reads its own overrides file (none until a test writes it), never a real one: each test gets a
plugin root of its own, and `$CLAUDE_GUARDS_OVERRIDES` names a missing file so the user's file is never read."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The work plugin's scripts import by name (`import next_item`); conftest loads before any test module, so a
# test file run alone finds them too.
sys.path.append(
    str(Path(__file__).resolve().parent.parent / "plugins" / "work" / "scripts")
)


@pytest.fixture(autouse=True)
def overrides_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    root = tmp_path_factory.mktemp("plugin")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))
    monkeypatch.setenv("CLAUDE_GUARDS_OVERRIDES", str(root / "none.json"))
    return root / "overrides.json"
