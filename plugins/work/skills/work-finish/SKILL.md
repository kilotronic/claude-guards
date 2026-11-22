---
name: work-finish
description:
  Finish one claimed todo item. In the work's own pull request, removes the entry (ending the claim), files
  follow-ups as new entries and runs the repo's gates; after it shows MERGED, removes the worktree and branches
  and posts the last standup step. Also abandons an item cleanly with goal release. Use for "finish this",
  "land it", "wrap up", "abandon this item".
---

# Finish an item

## 1. Before merge, in the work's own pull request

- **Remove the entry from the todo file** in the last commit, citing the work by its commit subject (a squash
  or rebase merge rewrites SHAs). Delete it; never tick it. Removing the entry removes its claim mark, so
  the claim ends when this pull request merges. Keep any durable lesson in the repo's decisions doc first.
- **Follow-ups found on the way become new todo entries** in the same pull request, in the right section,
  with no claim mark.
- **Run the repo's gates**: its formatters, linters and tests (its CLAUDE.md or Makefile), and the review its
  rules require. Fix the findings before merge.
- Open the pull request (ready, not draft). Arm auto-merge only once its head is your pushed commit
  (`gh pr view --json headRefOid -q .headRefOid` equals `git rev-parse HEAD`), after `standup list`.

## 2. After merge

Wait with one `gh pr checks <n> --watch --interval 60`, then confirm:

```bash
gh pr view <n> --json state -q .state    # must print MERGED
goal merged <branch>                     # exit 0: the branch's work is on main
```

`goal merged` is the same test branch-prune uses: every commit on main as the same patch (`git cherry`), or a
merged pull request whose head is the branch's tip, which is how a squash merge shows. Exit 3 means
something on the branch is not on main: stop and look; don't delete.

Only then, from the main checkout:

```bash
git worktree remove ../<repo>-<slug>      # refuses if dirty; look rather than --force
git branch -D <branch>
git push origin --delete <branch>         # if the repo doesn't delete merged branches itself
```

If standup is installed, post the last step, saying what is true:
`standup status --step 'merged #<n>: <title>'`.

## 3. If the work is abandoned instead

```bash
goal release '<the entry's title>'
```

It opens the pull request that removes your claim mark, so the item is free again. Releasing someone else's
stale claim is the owner's call: `goal release '<title>' --holder <their label>` once the owner says so.

Keep the worktree until its commits are safe: pushed to a branch on origin, or saved as a patch
(`git format-patch origin/main --stdout > <file>`) and the path given to the person. Uncommitted changes are
committed or saved first. Then remove it as in step 2.
