---
name: pr
allowed-tools: Bash(git fetch *), Bash(git rebase origin/*), Bash(git status*), Bash(git log *), Bash(git rev-parse *), Bash(git push --force-with-lease origin HEAD), Bash(gh pr view *), Bash(gh pr create *), Bash(gh pr edit *), Bash(gh pr comment *), Bash(gh pr checks *), Bash(gh issue list *), Bash(gh run view *), Bash(gh run list *), Bash(gh run watch *), Bash(standup list*), Bash(goal merged *), Agent, Skill(code-review)
description:
  Open a pull request for the current branch (rebase, docs, checks, a fresh reviewer agent), or with "merge"
  arm auto-merge and confirm it landed. Use for "open a PR", "ship this branch", "merge it".
---

Open a pull request for the current branch, or with `merge`, merge it.

**The repo's own CLAUDE.md wins** over anything here: its test and lint commands, its review rules, what its
pull request must say, and who may merge.

`main` here means the repo's default branch; use its real name (`master`, `trunk`) where it differs.

This skill assumes squash merges: each pull request becomes one commit on `main`, titled and described from the
pull request, so the pull request's title and description are the commit message. A repo that merges another
way says so in `.claude/work.toml` (`merge_method`); use that method wherever this skill says `--squash`. Where a
repo checks commit subjects, each subject, and the title, reads `<type>: <imperative summary>` (feat, fix, docs,
ci, chore, refactor, test, build, perf, style, revert), e.g. `fix: keep the deadline on the map`. Reword a branch
commit that doesn't (`git rebase -i` is not available to agents: `git reset --soft origin/main` and commit
again).

## Arguments

`$ARGUMENTS` is optional: context for the title and description (e.g. "fixes the timer bug"), or `merge` for the
[Merge flow](#merge-flow).

## Before starting

- Not on `main`, and the working tree is clean: otherwise stop and say why.
- Work the repo tracks in an issue has its issue (the repo's rules say which).

## Steps

### 1. Rebase onto main

First check the branch isn't spent:

```bash
gh pr view --json number,url,state 2>/dev/null
```

- `MERGED`: the branch is spent. Start a new branch from `origin/main` and bring over only the commits made after
  it merged (`git switch -c <new-name> origin/main`, then `git cherry-pick <commits>`).
- `CLOSED` without merging: none of its work landed. Ask your person whether to reopen it
  (`gh pr reopen <n>`) or drop it; never move only some of its commits.

Otherwise:

```bash
git fetch origin main
git rebase origin/main
```

A rebase runs no git hooks, so step 2's checks run after it. On conflicts, stop and show them; don't resolve them
by taking one side wholesale.

### 2. Docs and local checks

- Update only the docs the change affects (the repo's CLAUDE.md says which pages follow the code).
- Run the test and lint commands the repo's CLAUDE.md lists. Fix and commit until they pass.

### 3. Push

`git push --force-with-lease origin HEAD` (your own branch; no need to ask). An `OPEN` pull request with
auto-merge armed: disarm it first (`gh pr merge <n> --disable-auto`), so new commits can't merge
before step 5 reviews them.

### 4. Open or update the pull request

Open it ready for review (not a draft), unless the repo's CLAUDE.md asks for a draft:

- **Title** (it becomes the commit subject): `<type>: <the outcome in plain words>`, under 70 characters, the
  summary in the imperative (`fix: keep the deadline on the map`, not `Fixed ...`).
- **Description**: what it must do and why; what changed; how it was checked (commands and results); anything
  not verified; `Part of #N` or `Closes #N` when the work has an issue; anything the repo's CLAUDE.md asks for.

An existing open pull request: update its title and description if they no longer match.

### 5. A fresh reviewer agent reviews it

Reading your own diff is not a review. Start a **new** general-purpose agent (not a fork: a fork carries your
conclusions) and give it only: the pull request's full URL, the worktree path, and the repo's review rules (its
CLAUDE.md, and any rules your plugins loaded at your session start: paste that text, since a new agent doesn't
load it), the review level you chose, and the pull-request checklist below, word for word. Size the level to the
risk: `low`/`medium` for one focused change, `high` when it is multi-file, adds a capability, or touches sign-in,
stored data, money or anything live; `ultra` is billed, only when your person asks. The reviewer is read-only: it
edits, commits and pushes nothing, and writes its comment file in its own scratchpad, never the worktree. Its
task:

1. Run `/code-review <level> <full pull request URL>` with the level it was given.
2. Report correctness bugs and broken repo rules, each with the file and line, what goes wrong, and how to show it.
3. Answer the pull-request checklist in its brief, a short answer per item ("n/a" when it doesn't apply). A
   clear break is a finding like any other; a doubt is a note, posted in the same comment, that holds nothing up.
4. Post its findings itself as one pull request comment (`gh pr comment <n> --body-file <file>`), starting with
   the agent line `🤖 **Agent update** — Claude Code, pre-merge review`, then the commit it reviewed; "no
   findings" is posted too. The record of the review then doesn't rest on your summary, and this plugin's merge
   hook finds it.

**Pull-request checklist.** Leave out of the brief an item turned off for the repo in the overrides file the
guards plugin reads (its README, "Turning a rule off for a repo"): `overrides.json` at the guards plugin's root if
it ships one (in the plugin cache, `~/.claude/plugins/cache/*/guards/*/overrides.json`), else `$CLAUDE_GUARDS_OVERRIDES` when it is set, else `~/.config/claude-guards/overrides.json`. An item is off when its id is listed with a non-empty `reason`
under the repo's `owner/name` in lower case, else, when the repo has no entry of its own, under the first group
pattern that matches it (`my-org/*`), and the id is not in the file's `_locked` list (which is not a repo).
With no file, or none of these, copy all five:

- `one-concern`: Is this one concern, and does the description say why it is needed? Read the title, the
  description and the list of changed files. Ask for a split only when the parts could ship and be undone on
  their own (two unrelated changes, or a rename mixed with new behavior).
- `docs-follow-code`: Which docs did this change make stale? Search the README, CLAUDE.md, `docs/`, rule and
  skill files for each command, flag, setting, path or behavior the diff changes or removes; name each stale
  line.
- `risky-change`: Does the diff add a route or endpoint with no sign-in check, a webhook with no signature
  check, personal data or a secret written to a log, or an upload or request body with no size limit? Read each
  new handler and what wraps it.
- `new-dependency`: For each new direct dependency (a package, a base image, or a workflow action from outside
  GitHub's own `actions/`; not a version bump): could a few lines replace it, is its license one we can ship,
  does it cost money, and is it maintained (last release, open issues)? Answer all four.
- `threat-model`: Does it add attack surface (a route, an input, a secret, a permission, an outside service, a
  data store) without updating the repo's threat model or risk register? Name the file that should change;
  "n/a" when the repo keeps neither.

Don't hand it your summary of the change, what you think is risky beyond the level, or an earlier review's
findings.

- **Fix every finding, test first** wherever a test can show the bug: a test that fails on it, then the fix.
  Wording in docs and rules has no test to write. Rerun step 2's checks, commit and push. A finding that is not
  a bug: reply on the pull request with the evidence.
- **If the fixes changed anything substantive** (behavior, not wording or a comment), have one more fresh
  reviewer agent review the updated pull request, and fix what it finds. Then stop reviewing.
- **A finding needs a person's decision or access you don't have:** comment on the pull request with the
  question, @-mentioning them; if they are not your own person (GitHub can't ask a pull request's author to
  review it), also add them as reviewer (`gh pr edit <n> --add-reviewer <login>`). Don't arm auto-merge.
  Apart from a person your person asked to review it, this is the only reason a person sees the pull request.

Add one line to the description that links each reviewer's comment and says what became of its findings (fixed,
not a bug, or waiting on whom).

Once every finding of the last review is fixed, shown not to be a bug, or decided by a person, arm auto-merge
([M1](#m1-arm-auto-merge)), unless a finding waits on a person, a reviewer is named on the pull request, your
person asked for someone's review, or the repo's CLAUDE.md says otherwise. Say in your report which it is: armed,
or waiting for whose decision.

### 6. Watch the checks

In the background (`run_in_background: true`), one call:

```bash
gh pr checks <number> --watch --interval 60
```

- A failure: read the failing job's log (`gh run view <run-id> --log-failed`), fix and commit. Push as in step 3
  (auto-merge disarmed first), then re-arm through M1 and watch again. A fix that changes behavior, not only what
  the check flagged, goes back through step 5 first.
- All passing: with auto-merge armed, confirm it landed ([M2](#m2-confirm-it-landed)); otherwise report whose
  decision or review it waits for.

Keep the worktree until the pull request merges: review may need follow-up commits.

---

## Merge flow

Runs after step 5 by default (see there), or when triggered by `merge`. Only your person's own pull requests,
or with the person's say-so; a repo's CLAUDE.md can restrict it.

### M1. Arm auto-merge

```bash
gh pr view --json number,url,state,mergeable
gh pr view --json headRefOid -q .headRefOid; git rev-parse HEAD
standup list    # if standup is installed
```

- Not `OPEN`: stop and say why.
- The two commit ids differ: push, or find out whose commit it is, before arming.
- `standup list` shows another session mid-rebase or merge on this repo: name it in one line and go on (advisory).
- No review line in the description linking a reviewer's comment on this pull request (step 5): run step 5 first.
  Nothing merges without a fresh review. This plugin's merge hook warns at `gh pr merge` when the pull request
  has no comment starting with the agent line. Commits after the reviewed one must be only fixes for its
  findings or for a failing check, or a rebase that replays the same change; anything else goes back through
  step 5.
- `CONFLICTING`: rebase onto `origin/main` (step 1), push (step 3), and come back here. Conflicts resolved by hand
  change code after the review: go back through step 5.
- Check the repo's open GitHub issues first (`gh issue list --state open`): if another issue covers the same work
  or the same files, name it in your report.

Then arm: `gh pr merge --auto --squash`. Run it on its own, with nothing chained to it: in auto mode, this
plugin's merge-permission hook lets only a plain `gh pr merge` through, never `--admin`.

### M2. Confirm it landed

Armed is not merged. Wait in the background with one call, then look:

```bash
gh pr checks <number> --watch --interval 60
gh pr view <number> --json state,mergeCommit -q '"\(.state) \(.mergeCommit.oid)"'
```

Only `MERGED` with a merge commit proves it landed. GitHub can take a minute after the last check to merge:
still `OPEN` with auto-merge armed, look once more a minute later. Still `OPEN` after that: read why
(`gh pr view <number> --json mergeStateStatus,reviewDecision`) and say so; don't loop.

### M3. Watch what the merge starts

If the repo deploys or tests on `main` (its CLAUDE.md says), watch that run in the background:

```bash
gh run list --branch main --limit 3 --json databaseId,name,headSha
gh run watch <run-id> --interval 60 --exit-status
```

Report the result; on failure, read the log (`gh run view <run-id> --log-failed`).

### M4. Clean up

First `goal merged <branch>`: exit 0 means every commit on the branch is on main. Any other answer: stop and
look before deleting anything (a local commit after the reviewed one is lost otherwise). Then, if the repo
doesn't delete merged branches on GitHub, delete it (`git push origin --delete <branch>`). Remove the local
worktree and branch from outside the worktree (`git worktree remove <path>`, `git branch -D <branch>`). If this session's working directory is that worktree, leave it and say it can be removed once the
session ends.
