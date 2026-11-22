# SPDX-License-Identifier: MIT

"""goal's readers: the README section, the todo file, the claim mark and the settings file. All pure."""

from __future__ import annotations

import goal
import pytest
from workrun import README, TODO, claimed


def test_the_readme_section_gives_each_fact_by_its_label() -> None:
    facts = goal.parse_readme(README)
    assert facts["mission"] == "a kitchen timer anyone can start with one hand"
    assert facts["current milestone"] == "M2, the phone app"
    assert facts["done when"] == "a timer rings on a locked phone"
    assert facts["by"] == "no date"
    assert facts["work is recorded in"] == "`docs/todo.md`"
    assert goal.todo_path(facts) == "docs/todo.md"
    assert goal.wip_limit(facts) == 2


def test_a_readme_without_the_section_gives_no_facts() -> None:
    assert (
        goal.parse_readme("# Widgets\n\n## Install\n\n- **Mission:** not here\n")
        is None
    )


def test_the_section_ends_at_the_next_heading() -> None:
    facts = goal.parse_readme(README + "\n## More\n\n- **Frozen:** no\n")
    assert facts is not None and "frozen" not in facts


def test_a_linked_goals_file_is_read_as_its_path() -> None:
    text = "## How work runs here\n\n- **Goals:** [the goals](docs/goals.md) § _Priority_\n"
    assert goal.goals_path(goal.parse_readme(text)) == "docs/goals.md"


def test_todo_entries_carry_their_section_line_and_title() -> None:
    todo = goal.parse_todo(TODO, goal.Settings())
    assert [(e.section, e.title, e.line) for e in todo.defects] == [
        ("Defects", "The timer skips a second", 5),
        ("Defects", "The bell is silent on mute", 6),
    ]
    assert [e.title for e in todo.next] == [
        "Add a snooze button",
        "Show the time left in the title",
    ]


def test_a_size_tag_in_the_bold_lead_is_not_part_of_the_title() -> None:
    todo = goal.parse_todo(
        "## Defects\n\n- **(M, seen: test) Scores vanish.** x\n\n## Next\n",
        goal.Settings(),
    )
    assert todo.defects[0].title == "Scores vanish"


def test_an_entry_without_a_bold_lead_is_titled_by_its_first_sentence() -> None:
    todo = goal.parse_todo(
        "## Defects\n\n## Next\n\n- Add a dark mode. Users asked.\n", goal.Settings()
    )
    assert todo.next[0].title == "Add a dark mode"


def test_continuation_lines_and_subheadings_stay_in_their_section() -> None:
    text = "## Defects\n\n## Next\n\n- **One.** a\n  more of one\n\n### Smaller\n\n- **Two.** b\n\n## Later\n\n- **Three.**\n"
    todo = goal.parse_todo(text, goal.Settings())
    assert [e.title for e in todo.next] == ["One", "Two"]
    assert "more of one" in todo.next[0].text


def test_a_claim_mark_is_read_from_the_entry_line() -> None:
    text = claimed(TODO, "Add a snooze button", "agent-b", "2026-11-20", machine="box2")
    entry = goal.parse_todo(text, goal.Settings()).next[0]
    assert entry.title == "Add a snooze button"
    assert entry.claim == goal.Claim("agent-b", "box2", "2026-11-20")


def test_marking_and_unmarking_change_only_the_entry_line() -> None:
    lines = TODO.splitlines(keepends=True)
    marked = goal.mark(TODO, 10, goal.Claim("agent-a", "box1", "2026-11-22"))
    assert marked.splitlines(keepends=True)[:9] == lines[:9]
    # The bold lead stays first, so a repo's own checks of entries still read it; the mark ends the line.
    assert marked.splitlines()[9] == (
        "- **Add a snooze button.** Five more minutes from the ring. [claimed by agent-a on box1, 2026-11-22]"
    )
    assert goal.unmark(marked, 10) == TODO


def test_the_settings_file_names_other_headings() -> None:
    text = "## Bugs — fix first\n\n- **A.**\n\n## Security\n\n- **B.**\n\n## Play\n\n- **C.**\n"
    s = goal.Settings(defects=["Bugs"], next=["Play", "Security"])
    todo = goal.parse_todo(text, s)
    assert [e.title for e in todo.defects] == ["A"]
    assert [e.title for e in todo.next] == ["C", "B"]


def test_a_missing_heading_is_named() -> None:
    todo = goal.parse_todo("# Todo\n\n## Next\n\n- **A.**\n", goal.Settings())
    assert todo.missing == ["a `## Defects` (or `## Bugs`) heading"]


@pytest.mark.parametrize("use_tomllib", [True, False])
def test_the_settings_reader_works_with_and_without_tomllib(use_tomllib: bool) -> None:
    text = '# format details\ndefects = ["Bugs"]\nnext = ["Security", "Play"]  # in order\nskip = ["(owner)", "**Owned by**"]\nmerge_method = "rebase"\n'
    s = goal.parse_settings(text, use_tomllib=use_tomllib)
    assert s.defects == ["Bugs"]
    assert s.next == ["Security", "Play"]
    assert s.skip == ["(owner)", "**Owned by**"]
    assert s.merge_method == "rebase"


def test_an_unknown_setting_is_an_error_naming_it() -> None:
    with pytest.raises(ValueError, match="nxt"):
        goal.parse_settings('nxt = ["A"]\n', use_tomllib=False)


def test_a_slug_is_safe_for_a_branch_name() -> None:
    assert (
        goal.slug("Show the `time` left, in the title!")
        == "show-the-time-left-in-the-title"
    )


def test_a_mark_on_an_entry_ending_in_crlf_keeps_the_crlf() -> None:
    text = "## Defects\r\n\r\n## Next\r\n\r\n- **A.** x\r\n"
    marked = goal.mark(text, 5, goal.Claim("agent-a", "box1", "2026-11-22"))
    assert marked.endswith("- **A.** x [claimed by agent-a on box1, 2026-11-22]\r\n")
    assert goal.unmark(marked, 5) == text


def test_a_title_with_no_ascii_letters_still_gets_a_branch_name() -> None:
    assert goal.slug("日本語のタイトル")


def test_long_titles_that_share_a_start_get_different_slugs() -> None:
    a = goal.slug("Make the timer ring louder on every phone in the kitchen, first")
    b = goal.slug("Make the timer ring louder on every phone in the kitchen, second")
    assert a != b
    assert len(a) <= 60
