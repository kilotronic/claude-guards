---
name: finish-work
description:
  Finish one claimed todo item. In the work's own pull request, removes the entry (the claim ends when it
  merges), files follow-ups as new entries, runs the repo's gates, marks the pull request ready and arms
  auto-merge; after it shows MERGED, removes the worktree and branches. Also abandons an item cleanly by closing
  its pull request. Use for "finish this", "land it", "wrap up", "abandon this item".
---

# Finish an item

When `next-item` answers `record: issues`, the repo keeps its work in GitHub issues: skip the todo-file steps here
and follow [start-work's "Work kept in GitHub issues"](../start-work/SKILL.md#work-kept-in-github-issues) for
the issue; the gates, the merge and the clean-up below are the same.

## 1. Before merge, in the work's own pull request

- **Remove the entry from the todo file** in the last commit, citing the work by its commit subject (a squash
  or rebase merge rewrites SHAs). Delete it; never tick it. The pull request still holds the claim while it
  is open (`next-item` reads the mark from the branch's earlier commit), and the item is gone once it merges. A
  plan row (`rows` in `.claude/work.toml`) is removed by deleting its file. Keep any durable lesson in the repo's
  decisions doc first.
- **Follow-ups found on the way become new todo entries** in the same pull request, in the right section,
  with no claim mark.
- **Run the repo's gates**: its formatters, linters and tests (its CLAUDE.md or Makefile), and the review its
  rules require. Fix the findings before merge.
- The pull request has been open, as a draft, since start-work. Finish it with the `pr` skill, with its fresh
  review: once the work is done and pushed, mark it ready (`gh pr ready <n>`), since GitHub merges no draft.
  Arm auto-merge only after that, once its head is your pushed commit
  (`gh pr view --json headRefOid -q .headRefOid` equals `git rev-parse HEAD`).

## 2. After merge

Wait with one `gh pr checks <n> --watch --interval 60`, then confirm:

```bash
gh pr view <n> --json state -q .state    # must print MERGED
next-item merged <branch>                # exit 0: the branch's work is on main
```

A pull request the `pr-batch` skill closed and batched is done when the batch merged: its comment
"Landed in #<combined>" says where. Confirm that one shows `MERGED` and that the work is on main
(`git log origin/main`), then clean up below; `next-item merged` won't count a branch the batch landed.

`next-item merged` is the same test branch-prune uses: every commit on main as the same patch (`git cherry`), or a
merged pull request whose head is the branch's tip, which is how a squash merge shows. Exit 3 means
something on the branch is not on main: stop and look; don't delete.

Only then, from the main checkout:

```bash
git worktree remove ../<repo>-<slug>      # refuses if dirty; look rather than --force
git branch -D <branch>
git push origin --delete <branch>         # if the repo doesn't delete merged branches itself
```

## 3. If the work is abandoned instead

Close the work's pull request: the item is free again as soon as no open pull request carries its mark.

```bash
gh pr close <n>
```

If the work goes on under another item instead, move the claim on the branch, and push:

```bash
next-item release '<the old title>'    # commits the mark away
next-item claim '<the new title>'      # commits the new mark
```

An item held by a mark on the default branch, made before claims moved to pull requests, is freed only by a
pull request that removes the mark: `next-item release '<title>'` on a branch, pushed and merged. Closing someone
else's stale pull request, or removing their mark (`--holder <their label>`), is the owner's call.

Keep the worktree until its commits are safe: pushed to a branch on origin, or saved as a patch
(`git format-patch origin/main --stdout > <file>`) and the path given to the person. Uncommitted changes are
committed or saved first. Then remove it as in step 2.
