---
name: pr-batch
description:
  Clear a pull request backlog by putting several of your own open pull requests (default 10) into one new
  pull request, one commit per original, resolving conflicts, closing the originals once they are batched,
  having a fresh reviewer agent review the combined pull request, landing it with auto-merge in the repo's
  merge method, then noting on each original where it landed. Batches only pull requests you opened unless
  your person names another. One repair attempt on failure, then it reopens the originals and says so. Use
  for "batch the PR backlog", "clear the PRs", "combine these pull requests".
allowed-tools: Bash(git *), Bash(gh pr *), Bash(gh run *), Bash(gh repo view*), Bash(gh api *), Bash(gh issue *), Bash(*/scripts/next-item" *), Agent, Skill(code-review)
---

# Batch pull requests into one

`next-item` is this plugin's command: run it as `"${CLAUDE_PLUGIN_ROOT}/scripts/next-item"`, which works
whether or not the plugin's SessionStart hook put it on `PATH`.

Each pull request runs the repo's checks, again on every push, and often a deploy when it merges. Ten pull
requests landing one by one means at least ten check runs; one combined pull request means one. Batch when the
queue is long or the runners are busy; a green pull request with no conflict with `main` can just merge on its
own.

**The repo's own CLAUDE.md wins** over anything here: when to batch, paths never to batch, its conflict rules,
its quick checks, and who may merge.

`main` here means the repo's default branch; use its real name where it differs.

## Whose pull requests

**Only your person's own pull requests**, by default: those whose author is the current GitHub user
(`gh pr list --author @me`). Someone else's pull request is batched only when your person names it, by number,
in this run's arguments or in what they asked. Without that, never close, merge or batch it, even when it is
green and in the way. Say which pull requests you left out for this reason.

Every session of one person is the same `@me`, so a pull request of yours may be another session's work in
progress: see "not being changed" in step 1.

## Arguments

`$ARGUMENTS`: `[N | <pr> <pr> ...] [--dry-run]`

- `N` (default **10**, at most **15**): take the N oldest eligible pull requests. (`next-item` looks back 20
  commits on a branch for a claim mark, so a larger batch would lose the claims its first commit carries.) A list of numbers takes exactly those, still
  checked for eligibility; a number in the list whose author is someone else counts as named. Say which were
  refused and why.
- `--dry-run`: build and check the combined branch on this machine. Push nothing, change nothing on GitHub, and
  report what a real run would do.

## 0. Before you start

- Follow the repo's CLAUDE.md. This skill exempts nothing: a worktree off a fresh `origin/main`, its gates and
  the `pr` skill's review all still apply.
- **The merge method.** `merge_method` in `.claude/work.toml` (`squash`, `rebase` or `merge`; see the README's
  "What the repo states") when it is set. Otherwise the first of squash, rebase and merge that the repo allows:
  `gh repo view --json squashMergeAllowed,rebaseMergeAllowed,mergeCommitAllowed`. Call it `--<method>` below.
- **The record of work.** `next-item --json` says which the repo keeps (`.record`): a todo file (`todo`) or
  GitHub issues (`issues`). For issues, the repo's own issue-tracking skill or rules come first, as in
  [start-work's "Work kept in GitHub issues"](../start-work/SKILL.md#work-kept-in-github-issues). Step 2 says
  how the combined pull request carries each original's item.
- **Never `git stash`** or `--autostash`: the stash is shared by every worktree of the repo. Commit, or drop the
  change, instead. **Never `git reset --hard`** over a half-applied change either (the guards refuse it): undo
  one with `git reset --merge`.
- **Only one session batches a repository at a time.** The claim is the batch's own open pull request, on a
  branch starting `batch/`. Before starting, look for one in flight:
  `gh pr list --state open --json number,headRefName,author,updatedAt --jq '.[]|select(.headRefName|startswith("batch/"))'`.
  If one is open, stop, unless it is abandoned: whatever its title, no push for 2 hours and its session is not
  running (check, or ask your person). Release an abandoned one of yours as below, which reopens the originals
  it closed (its description lists them), then go on; someone else's is theirs to release.
- **Release the claim** whenever you stop after opening the batch pull request (lost the tie, fewer than 2 left,
  gave up, or abandoned): reopen each original it closed, as step 4 says, then
  `gh pr close <combined> --delete-branch --comment "<why>"`, remove the worktree and `git branch -D` the branch.
- See whether CI is running at all:
  `gh run list --limit 30 --json status --jq 'group_by(.status)|map("\(.[0].status)=\(length)")'`. Queued runs
  with some `in_progress` is a backlog, and this skill helps. Everything queued for a long time with nothing in
  progress may mean the Actions minutes or runners are out: build and check locally, and tell your person
  before relying on CI.

## 1. Pick the pull requests

Read only; change nothing on GitHub in this step.

```bash
gh pr list --state open --author @me --limit 100 \
  --json number,title,author,baseRefName,headRefName,headRefOid,isDraft,createdAt,autoMergeRequest,labels,isCrossRepository,mergeable,closingIssuesReferences \
  --jq 'sort_by(.createdAt)'
gh pr checks <n> --required   # per candidate: which required checks passed, failed or are pending
```

Add each pull request your person named by number (`gh pr view <n> --json ...` with the same fields). Eligible:

- yours, or named by your person (above);
- not a draft (a draft says its work isn't done), not from a fork, no `do-not-batch` label;
- its base is `main`, or it is **stacked**: its base is the head branch of another eligible pull request, which
  is itself on `main` or stacked the same way. Any other base: leave it out;
- **not being changed.** Its head commit is older than 5 minutes
  (`gh api repos/{owner}/{repo}/commits/<sha> --jq .commit.committer.date`), the pull request wasn't updated in
  the last 5 minutes either (`updatedAt`: a push of an older commit), and no recent comment says someone is
  working on it. Another session rebasing, fixing or landing it would fight you. Name each one skipped this
  way;
- **finished.** In a todo-file repo, an original that claims an entry deletes it in its last commit
  (finish-work). One whose branch still has its marked entry isn't done: leave it out;
- if auto-merge is armed, **no conflict with `main`** (`mergeable` is `MERGEABLE`; `UNKNOWN` means GitHub hasn't
  worked it out yet: `gh pr view <n> --json mergeable` once more, and still unknown counts as a conflict). An armed pull request that conflicts stays with its owner:
  tell the owning session, or comment on the pull request. Never resolve an armed pull request's conflicts with
  `main` yourself;
- no **required** check failed on its head (a red pull request poisons the batch; leave it to its owner).
  Queued, pending or cancelled checks are fine;
- nothing the repo's CLAUDE.md says must land on its own (for example changes to permissions a person applies
  by hand).

Leave to merge alone a pull request whose required checks are **all green** with no conflict with `main`: it
can land now, and batching only delays it.

Take a stack in its order, all of it or a bottom part; never one without those below it. Fewer than 2 eligible:
stop and say so.

Record for each chosen pull request its number, title, author login, base branch, head branch, head SHA, the
authors (name and email) of its commits, whether auto-merge was armed, and the issues it closes, in a scratchpad
file. Putting back and closing both depend on it.

## 2. Build the combined branch

Build it all on this machine first, then claim the batch by opening its pull request (end of this step):

```bash
git fetch origin
git worktree add ../<repo>-batch-<date> -b batch/<date>-<n1>-<n2>-... origin/main
```

Then the repo's own set-up in that worktree (its README or CLAUDE.md), so its commit hooks run.

**In a todo-file repo, the first commit carries the claims.** An original's claim is the mark on its entry, and
its squash deletes the entry outright, so once the originals close no open pull request would carry the mark,
and `next-item` would offer the item as free while the batch is open. So first put each original's claim mark on
its entry (`next-item --list` shows the claims and their pull requests) and commit that as
`chore: carry the batched pull requests' claims`. Each original's squash then conflicts on its entry's line:
that conflict is expected, and resolves by deleting that entry, as the original did, while keeping every other
line the original changed in the same place (a follow-up entry it added, say).

**One commit per original**, oldest first (a stack bottom first), the way a squash merge would have made it:

```bash
git merge --squash <head sha>
git commit --author="<original author>" -m "<pull request title> (#<n>)" -m "<one-paragraph summary from its body>" \
  -m "Co-authored-by: <each other author of its commits>"
```

Keep the original author, so the history and any author check stay true. **A stacked pull request** is applied
as **only its own change**: the difference from the head of the pull request below it to its own head. Squashing
its whole head brings in the one below a second time, and every line it changes there turns into a false
conflict:

```bash
git merge-base --is-ancestor <head of the one below> <head sha>   # the stack holds together
git diff --binary <head of the one below> <head sha> | git apply --3way --index
```

If the stack doesn't hold together (the one below was pushed again since), drop that pull request and those
stacked on it: its diff would undo the newer changes below it.

An empty squash means the pull request already landed meanwhile: drop it.

**On a conflict**, resolve each hunk by **both pull requests' intent** (read both diffs and bodies), never by
picking a side:

- Two that each add something (a route, a field, a function, a test, a list entry): keep both.
- Documents: merge both updates into one consistent text; renumber nothing a reader may cite.
- A generated file (a lockfile, generated docs) is regenerated with its command, never merged by hand.
- The repo's CLAUDE.md may give more rules; they win.

A conflict you cannot resolve with confidence (two designs that contradict): undo it with `git reset --merge`
(`git merge --abort` doesn't work after `--squash`), **drop that pull request from the batch** with those
stacked on it, and say why.

**Let CI run the full suites.** Once the conflicts are resolved, run only the repo's quick checks (its linter,
and a parse or compile of each changed file), which take seconds and catch a broken merge before a CI round trip.
When CI fails, read the log and fix it in a commit named for what it fixes, authored by you.

Fewer than 2 left at any point: stop. `--dry-run` stops here too: report the chosen pull requests, the skipped
ones and why, each conflict and how it was resolved, and the check results. Remove the worktree and
`git branch -D` the branch.

**Claim the batch:** look once more for an open `batch/` pull request (step 0); none, so push the branch and open
the combined pull request, ready and not armed, with its real title and description, in plain words:

```text
Lands #a, #b, #c together, one commit each, so the checks run once instead of N times.
Where two of them changed the same lines: <file: how both were kept>.
The originals are closed while this runs, and reopened if it has to give one back.
Closes #<issue> ...   (for each issue an original closed)
```

Then look again: if another `batch/` pull request is open, the lower number wins; if yours is the higher one,
release the claim (step 0) and stop.

**In an issues repo**, the issues keep their assignees, and the `Closes` lines above close them when the batch
merges. Where the repo's issue rules ask for an update on the issue, say there that its work moved to the
combined pull request.

## 3. Close the originals, review it, arm it

**Close the originals right away**, before the review: while an original and the batch are both open, each item
has two claims, and `next-item` names the lower number as the holder. For each original still open with its
recorded head SHA (one that merged or moved meanwhile: rebuild without it):

```bash
gh pr close <n> --comment "Closed: batched into #<combined>, so its own checks stop. Reopened if the batch has to give it back. Auto-merge was <on|off>."
gh run list --branch <head> --json databaseId,status --jq '.[]|select(.status!="completed")|.databaseId'
gh run cancel <id>   # each id the line above printed
```

Close them, don't park them as drafts: a draft keeps its checks running and keeps showing as work to do.
Closing also turns auto-merge off, so the comment notes whether it was on. Never delete their branches or change
their bases (stacked pull requests above would close, and a reopened one must be as it was). Do all of this
before arming: an armed original that merges first makes the batch out of date.

**A fresh review, as the `pr` skill's step 5 says** ([pr skill](../pr/SKILL.md#5-a-fresh-reviewer-agent-reviews-it)):
a new general-purpose agent, not a fork, with the combined pull request's URL, the worktree path, the repo's
review rules and the pull-request checklist, and none of your conclusions. Size the level to the conflict
resolutions and fix commits: those are new. In its brief, list which commits are an original's squash, each with
a link to that original's own review comment if it has one; an original without one is reviewed in full like
any new change. Fix its findings as that step says, and if a fix is substantive, have one more fresh reviewer
review it. Add the review line to the description.

**Arm auto-merge only once the head is your pushed commit**, as the `pr` skill's M1 says:

```bash
gh pr view <combined> --json headRefOid -q .headRefOid; git rev-parse HEAD   # must match
gh pr merge <combined> --auto --<method>
```

Run it on its own, with nothing chained to it, and never with `--admin`: a check or rule that blocks the merge
is for a person to lift, not for you to skip. With `--squash` the batch lands on `main` as one commit, with the
originals' authors as co-authors; its pull request keeps one commit per original for reading. With `--rebase` or
`--merge`, `main` reads as if each had merged alone.

**Do not poll.** Every session on the account shares one GitHub API limit. Wait in the background with one
`gh pr checks <combined> --watch --interval 60`, then look once:
`gh pr view <combined> --json state,mergeStateStatus`. Armed is not merged. If it reads `BEHIND` (another pull
request landed first) or conflicts, disarm (`gh pr merge <combined> --disable-auto`), `git rebase origin/main`,
rerun the quick checks, `git push --force-with-lease`, re-arm as above and wait once more; conflicts resolved by
hand go back through the review. At each look, read each original
(`gh pr view <n> --json state,headRefOid,autoMergeRequest`): reopened, re-armed, or a head that moved from the
recorded one means its owner took it back. Say so on the combined pull request, and leave that original out of step 5.

## 4. On failure: one repair, then give up

A failure is a required check failing, a conflict a rebase cannot clear, or about 30 minutes with no progress
**while runners were free**. Waiting behind other queued runs is not a failure.

**The one repair:** read the failing log (`gh run view <id> --log-failed`), never just the check's name. Find which
original's commit or which resolution caused it; with one commit per original, reproduce locally and bisect by
commit. Fix it in a commit, or rebuild without the guilty pull request (and those stacked on it) and put it back
on its own (reopen it, as below). Disarm, push with `--force-with-lease`, re-arm once the head is the pushed
commit, and wait again. A fix that changes behavior goes back through the review first.

**Second failure: give up**, leaving things as they were:

1. Disarm and release the claim (step 0), saying in the closing comment what failed and both diagnoses.
2. For each original: `gh pr reopen <n>` (a reopened pull request runs its checks again by itself); re-arm it
   (`gh pr merge <n> --auto --<method>`) only if it was armed before; comment "Batch #<combined> failed; back in
   the queue on its own."
3. Tell your person in plain words: which pull requests, what failed, what you tried, and that the originals are
   back as they were.

## 5. After it merged: say where each original landed

Confirm it: `gh pr view <combined> --json state,mergeCommit` must show `MERGED` with a merge commit. Then
`git fetch origin`. With `--squash`, every original landed in that one commit. With `--rebase` or `--merge`, find
each original's own commit (the merge rewrites SHAs; look for its `(#<n>)` subject):

```bash
git log origin/main --oneline --grep "(#<n>)"
gh pr comment <n> --body "Landed in #<combined> as <sha>."
```

Reopen (and comment on) any original whose work is not on `main`; one its owner took back is theirs. A pull request dropped from
the batch after it was closed is reopened as soon as it is dropped, with the reason. Re-point each open pull
request stacked above the batch's top onto `main` (`gh pr edit <n> --base main`). Other sessions may still have a
worktree on an original's branch; leave those to their owners. A closed original's branch never counts as merged
for `next-item merged` or branch-prune, so delete the branch on GitHub of each landed original of your own with no
open pull request based on it (`git push origin --delete <head>`); someone else's is theirs to delete.

Clean up as the `pr` skill's M4 says: `next-item merged <batch branch>`, then remove the batch worktree and
`git branch -D` its branch. Report the merged commit, each original's fate, and anything the merge starts on
`main` (a deploy, if the repo's CLAUDE.md says so). If eligible pull requests remain, offer the next batch.
