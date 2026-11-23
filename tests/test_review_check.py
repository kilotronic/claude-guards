# SPDX-License-Identifier: MIT

"""The fresh-review merge check: warns, never blocks, and never fails the merge when GitHub is out of reach."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "work"
HOOKS = PLUGIN / "hooks"

_spec = importlib.util.spec_from_file_location(
    "review_check", HOOKS / "review-check.py"
)
assert _spec and _spec.loader
review_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review_check)

AGENT_LINE = "🤖 **Agent update** — Claude Code, working for @someone · session `abcd1234` · host"
REVIEW = (
    AGENT_LINE
    + "\n\nReviewed commit 8c9fe4a6538fe057c7d3f8d6818efc6b0f2dfd1d.\n\nNo findings."
)


def bash(command: str, cwd: str = "/repo") -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd}


def pr(*bodies: str) -> dict:
    return {"comments": list(bodies)}


class Fetch:
    """A stand-in for the GitHub call: records what it was asked and answers with a fixed pull request."""

    def __init__(self, answer: dict | None):
        self.answer = answer
        self.calls: list[tuple[list[str], str | None]] = []
        self.timeouts: list[float] = []

    def __call__(self, selector: list[str], cwd: str | None, timeout: float):
        self.calls.append((selector, cwd))
        self.timeouts.append(timeout)
        return self.answer


def warning(answer: dict | None) -> str:
    """The warning text, after checking the answer warns and never refuses the merge."""
    assert answer is not None
    specific = answer.get("hookSpecificOutput", {})
    assert "permissionDecision" not in specific, answer
    assert answer["systemMessage"] == specific["additionalContext"]
    assert specific["hookEventName"] == "PreToolUse"
    return answer["systemMessage"]


class ReviewCheck(unittest.TestCase):
    def test_a_review_comment_means_nothing_is_said(self):
        for answer in (
            pr(REVIEW),
            pr("LGTM", AGENT_LINE + "\nReviewed 8c9fe4a: two findings."),
            pr("  " + AGENT_LINE + "\nNo findings."),
        ):
            fetch = Fetch(answer)
            self.assertIsNone(
                review_check.decide(bash("gh pr merge 12 --squash"), fetch)
            )
            self.assertEqual(fetch.calls, [(["12"], "/repo")])

    def test_no_review_comment_warns_and_lets_the_merge_go_ahead(self):
        for answer in (
            pr(),
            pr("LGTM"),
            pr("Reviewed. " + AGENT_LINE),
            pr("🤖 Agent update without the bold marker"),
        ):
            text = warning(
                review_check.decide(bash("gh pr merge 12 --squash"), Fetch(answer))
            )
            self.assertIn("no fresh-review comment", text, answer)
            self.assertIn("step 5", text)
            self.assertIn("12", text)

    def test_the_warning_names_this_plugin_and_its_pr_skill(self):
        for answer in (pr(), None):
            text = warning(
                review_check.decide(bash("gh pr merge 12 --squash"), Fetch(answer))
            )
            for line in text.splitlines():
                self.assertTrue(line.startswith("work: "), line)
                self.assertIn("`work:pr` skill's step 5", line)

    def test_the_pr_skills_review_line_counts(self):
        fetch = Fetch(
            pr("🤖 **Agent update** — Claude Code, pre-merge review\nNo findings.")
        )
        self.assertIsNone(review_check.decide(bash("gh pr merge 12"), fetch))

    def test_auto_merges_are_checked_too(self):
        fetch = Fetch(pr())
        text = warning(
            review_check.decide(bash("gh pr merge 7 --auto --squash"), fetch)
        )
        self.assertIn("no fresh-review comment", text)
        self.assertEqual(fetch.calls, [(["7"], "/repo")])

    def test_github_unreachable_warns_could_not_check_and_lets_the_merge_go_ahead(self):
        text = warning(
            review_check.decide(bash("gh pr merge 12 --squash"), Fetch(None))
        )
        self.assertIn("could not check", text.lower())
        self.assertIn("step 5", text)

    def test_other_commands_and_tools_are_untouched(self):
        for command in (
            "gh pr view 12",
            "git merge origin/main",
            "gh pr list --search merge",
            "gh pr checks 12 --watch --interval 60",
            'echo "gh pr merge 12"',
            "git commit -m 'gh pr merge 12'",
            "git commit -m 'line one\ngh pr merge 12'",
            "gh pr merge 12 --disable-auto",
            "gh pr merge 'unterminated",
            "",
        ):
            fetch = Fetch(pr())
            self.assertIsNone(review_check.decide(bash(command), fetch), command)
            self.assertEqual(fetch.calls, [], command)
        fetch = Fetch(pr())
        self.assertIsNone(
            review_check.decide(
                {"tool_name": "Edit", "tool_input": {"command": "gh pr merge 1"}}, fetch
            )
        )
        self.assertIsNone(review_check.decide({"tool_name": "Bash"}, fetch))
        self.assertEqual(fetch.calls, [])

    def test_the_pull_request_is_found_the_way_gh_finds_it(self):
        for command, selector, cwd in (
            (
                "gh pr merge 1043 --repo owner/repo --squash",
                ["1043", "--repo", "owner/repo"],
                "/repo",
            ),
            (
                "gh pr merge -R owner/other 12 --squash",
                ["12", "--repo", "owner/other"],
                "/repo",
            ),
            (
                "gh pr merge --repo=owner/other 12",
                ["12", "--repo", "owner/other"],
                "/repo",
            ),
            ("gh pr merge --auto --squash", [], "/repo"),
            ("gh pr merge -t 'a subject' -b 'a body' 9 -sd", ["9"], "/repo"),
            ("gh pr merge --subject=x 9", ["9"], "/repo"),
            (
                "gh pr merge https://github.com/owner/repo/pull/5",
                ["https://github.com/owner/repo/pull/5"],
                "/repo",
            ),
            ("gh pr merge 5 --squash; echo done", ["5"], "/repo"),
            # Lines, subshells, prefixes and redirections around the merge.
            ("echo hi\ngh pr merge 7 --squash", ["7"], "/repo"),
            ("(gh pr merge 5 --squash)", ["5"], "/repo"),
            ("env gh pr merge 5", ["5"], "/repo"),
            ("GH_REPO=a/b gh pr merge 7", ["7", "--repo", "a/b"], "/repo"),
            ("GH_REPO=a/b gh pr merge 7 -R c/d", ["7", "--repo", "c/d"], "/repo"),
            ("FOO=1 env BAR=2 gh pr merge 7", ["7"], "/repo"),
            ("gh pr merge --squash 2>&1 12", ["12"], "/repo"),
            ("gh pr merge 12 --squash > /tmp/out 2>/dev/null", ["12"], "/repo"),
            # A cd earlier in the command decides which branch's pull request gh merges.
            ("cd ../wt-b && gh pr merge --auto --squash", [], "/wt-b"),
            ("cd /elsewhere/wt; gh pr merge 5", ["5"], "/elsewhere/wt"),
            ("(cd wt && gh pr merge 5)", ["5"], "/repo/wt"),
            ("cd ~/repo && gh pr merge 5", ["5"], os.path.expanduser("~/repo")),
            # Quoted text is not a redirection; `12>` is a redirection of descriptor 12, as the shell reads it.
            ('gh pr merge 53 --squash --subject "a > b"', ["53"], "/repo"),
            ("gh pr merge 12 -b 'x < y' --squash", ["12"], "/repo"),
            ("gh pr merge 12>/dev/null", [], "/repo"),
            # Other ways of starting gh, and a command continued on the next line.
            ("/opt/homebrew/bin/gh pr merge 12", ["12"], "/repo"),
            ("command gh pr merge 12", ["12"], "/repo"),
            ("env -u X -i gh pr merge 12", ["12"], "/repo"),
            ("gh pr merge \\\n  12 --squash", ["12"], "/repo"),
        ):
            fetch = Fetch(pr())
            warning(review_check.decide(bash(command), fetch))
            self.assertEqual(fetch.calls, [(selector, cwd)], command)

    def test_every_merge_in_one_command_is_checked(self):
        answers = {"1": pr(REVIEW), "2": pr()}
        calls = []

        def fetch(selector, cwd, timeout):
            calls.append(selector)
            return answers[selector[0]]

        text = warning(
            review_check.decide(
                bash("gh pr merge 1 --squash && gh pr merge 2 --squash"), fetch
            )
        )
        self.assertIn("pull request 2 has no fresh-review comment", text)
        self.assertEqual(calls, [["1"], ["2"]])
        # One that can't be checked does not stop the next being checked, and both are reported.
        answers = {"1": None, "2": pr(), "3": pr()}
        calls.clear()
        text = warning(
            review_check.decide(
                bash("gh pr merge 1 && gh pr merge 2; gh pr merge 3"), fetch
            )
        )
        self.assertEqual(calls, [["1"], ["2"], ["3"]])
        for part in (
            "could not check pull request 1",
            "pull request 2 has no",
            "pull request 3 has no",
        ):
            self.assertIn(part, text.lower())
        with mock.patch.object(review_check, "BLOCKING", True):
            calls.clear()
            answer = review_check.decide(bash("gh pr merge 1 && gh pr merge 2"), fetch)
            self.assertEqual(answer["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertIn(
                "pull request 2 has no",
                answer["hookSpecificOutput"]["permissionDecisionReason"],
            )

    def test_all_lookups_together_stay_inside_the_hooks_time_limit(self):
        hooks = json.loads((HOOKS / "hooks.json").read_text())["hooks"]["PreToolUse"]
        limit = next(
            h["timeout"]
            for g in hooks
            for h in g["hooks"]
            if "review-check.py" in h["command"]
        )
        self.assertLess(review_check.BUDGET + 2, limit)
        self.assertLessEqual(review_check.GH_TIMEOUT, review_check.BUDGET)
        # The first lookup used up the budget: the second is not tried, and says it could not check.
        ticks = iter([0.0, 0.0, review_check.BUDGET + 1])
        fetch = Fetch(pr(REVIEW))
        text = warning(
            review_check.decide(
                bash("gh pr merge 1 && gh pr merge 2"), fetch, clock=lambda: next(ticks)
            )
        )
        self.assertIn("could not check pull request 2", text.lower())
        self.assertEqual([c[0] for c in fetch.calls], [["1"]])
        # A lookup never gets more time than is left.
        ticks = iter([0.0, review_check.BUDGET - 1])
        fetch = Fetch(pr(REVIEW))
        review_check.decide(bash("gh pr merge 1"), fetch, clock=lambda: next(ticks))
        self.assertEqual(fetch.timeouts, [1])

    def test_blocking_is_one_flag_and_never_blocks_on_a_failed_check(self):
        with mock.patch.object(review_check, "BLOCKING", True):
            answer = review_check.decide(bash("gh pr merge 12 --squash"), Fetch(pr()))
            self.assertEqual(answer["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertIn(
                "no fresh-review comment",
                answer["hookSpecificOutput"]["permissionDecisionReason"],
            )
            warning(review_check.decide(bash("gh pr merge 12 --squash"), Fetch(None)))
            self.assertIsNone(
                review_check.decide(bash("gh pr merge 12"), Fetch(pr(REVIEW)))
            )
        self.assertFalse(review_check.BLOCKING)


class FetchPr(unittest.TestCase):
    def test_asks_gh_for_the_comments_in_the_given_directory_with_the_given_time(self):
        out = json.dumps({"comments": [{"body": "a"}, {"body": REVIEW}]})
        done = subprocess.CompletedProcess([], 0, out, "")
        with mock.patch.object(
            review_check.subprocess, "run", return_value=done
        ) as run:
            self.assertEqual(
                review_check.fetch_pr(["12", "--repo", "o/r"], "/wt", 3.5),
                pr("a", REVIEW),
            )
        args, kwargs = run.call_args
        self.assertEqual(
            args[0], ["gh", "pr", "view", "12", "--repo", "o/r", "--json", "comments"]
        )
        self.assertEqual(kwargs["cwd"], "/wt")
        self.assertEqual(kwargs["timeout"], 3.5)

    def test_any_failure_is_none(self):
        good = json.dumps({"comments": []})
        failures = (
            subprocess.CompletedProcess(
                [], 1, "", "error connecting to api.github.com"
            ),
            subprocess.CompletedProcess([], 1, good, "partial answer"),
            subprocess.CompletedProcess([], 0, "not json", ""),
            subprocess.CompletedProcess([], 0, json.dumps({"other": []}), ""),
            subprocess.CompletedProcess([], 0, json.dumps({"comments": [{}]}), ""),
            FileNotFoundError("gh"),
            subprocess.TimeoutExpired("gh", 5),
            NotADirectoryError("/gone"),
        )
        for failure in failures:
            kind = (
                {"side_effect": failure}
                if isinstance(failure, BaseException)
                else {"return_value": failure}
            )
            with mock.patch.object(review_check.subprocess, "run", **kind):
                self.assertIsNone(review_check.fetch_pr(["12"], "/wt", 5), failure)


class AsClaudeCodeRunsIt(unittest.TestCase):
    """The command line in hooks.json, with a stand-in `gh` first on PATH."""

    def setUp(self):
        hooks = json.loads((HOOKS / "hooks.json").read_text())["hooks"]["PreToolUse"]
        commands = [
            h["command"]
            for group in hooks
            if group.get("matcher") == "Bash"
            for h in group["hooks"]
        ]
        found = [c for c in commands if "review-check.py" in c]
        self.assertEqual(len(found), 1, commands)
        self.command = found[0]
        self.bin = tempfile.TemporaryDirectory()
        self.addCleanup(self.bin.cleanup)
        # python3 on PATH without whatever else lives beside it (a real gh, often).
        (Path(self.bin.name) / "python3").symlink_to(sys.executable)

    def fake_gh(self, script: str) -> None:
        gh = Path(self.bin.name) / "gh"
        gh.write_text("#!/bin/sh\n" + script)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)

    def run_hook(
        self, command: str, path: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "CLAUDE_PLUGIN_ROOT": str(PLUGIN),
            "PATH": path if path is not None else f"{self.bin.name}:/usr/bin:/bin",
        }
        return subprocess.run(
            self.command,
            shell=True,
            input=json.dumps(bash(command, cwd=self.bin.name)),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def test_review_present_is_silent(self):
        self.fake_gh(
            "cat <<'EOF'\n" + json.dumps({"comments": [{"body": REVIEW}]}) + "\nEOF\n"
        )
        done = self.run_hook("gh pr merge 3 --squash")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))

    def test_review_missing_warns_and_exits_zero(self):
        self.fake_gh("echo '{\"comments\": []}'\n")
        done = self.run_hook("gh pr merge 3 --auto --squash")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("no fresh-review comment", warning(json.loads(done.stdout)))

    def test_gh_failing_warns_could_not_check_and_exits_zero(self):
        self.fake_gh("echo 'error connecting to api.github.com' >&2\nexit 1\n")
        done = self.run_hook("gh pr merge 3 --squash")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("could not check", warning(json.loads(done.stdout)).lower())

    def test_no_gh_at_all_warns_could_not_check_and_exits_zero(self):
        done = self.run_hook("gh pr merge 3 --squash", path=self.bin.name)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("could not check", warning(json.loads(done.stdout)).lower())

    def test_other_commands_and_bad_input_say_nothing(self):
        self.fake_gh("echo called >&2\nexit 1\n")
        done = self.run_hook("git status")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
        bad = subprocess.run(
            self.command,
            shell=True,
            input="not json",
            capture_output=True,
            text=True,
            env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(PLUGIN)},
            check=False,
        )
        self.assertEqual((bad.returncode, bad.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
