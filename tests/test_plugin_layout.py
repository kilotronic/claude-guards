# SPDX-License-Identifier: MIT

"""The plugins' layout, as claude.ai reads it before it installs one.

claude.ai and Cowork refuse to install a plugin with a top-level bin/ directory
(https://claude.com/docs/plugins/build.md, "Check what each app loads"), so the plugin
never syncs into Claude Code cloud sessions. The work plugin's commands live in scripts/,
and its skills call them by "${CLAUDE_PLUGIN_ROOT}/scripts/<name>".
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
WORK = PLUGINS / "work"


def test_no_plugin_has_a_top_level_bin():
    assert [p.name for p in PLUGINS.iterdir() if (p / "bin").exists()] == []


def test_the_commands_are_in_scripts():
    for name in ("next-item", "park"):
        assert (WORK / "scripts" / name).is_file(), name


# A code-block line that starts with the bare name. Prose may name a command, but a skill that does must say its path.
BARE_LINE = re.compile(r"^\s*(next-item|park)( |$)")


def test_the_skills_call_the_commands_by_their_path():
    for skill in sorted(WORK.glob("skills/*/SKILL.md")):
        text = skill.read_text()
        bare = [line for line in text.splitlines() if BARE_LINE.match(line)]
        assert bare == [], skill
        for name, used in (("next-item", "next-item"), ("park", "park resume")):
            if used in text:
                assert f'"${{CLAUDE_PLUGIN_ROOT}}/scripts/{name}"' in text, (
                    skill,
                    name,
                )
