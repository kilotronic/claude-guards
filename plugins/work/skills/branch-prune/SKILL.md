---
name: branch-prune
description:
  Delete remote branches, and local worktrees and branches, whose work is already merged
  into the default branch. Rebase-safe (the same patch or the same changed lines, not
  ahead counts). Dry run by default; "--apply" deletes. Use for "prune merged branches", "clean
  up stale worktrees".
allowed-tools: Bash(git *), Bash(gh pr *), Bash(gh repo view*), Bash(*/scripts/next-item" merged *), Bash(xargs *)
---

# Prune merged branches and worktrees

`next-item` is this plugin's command: run it as `"${CLAUDE_PLUGIN_ROOT}/scripts/next-item"`, which works
whether or not the plugin's SessionStart hook put it on `PATH`.

Delete remote branches, and local worktrees and branches, whose commits are **already on
the default branch**, using patch-equivalence rather than ancestry. With rebase or squash
merging, a merged branch keeps showing commits in `git rev-list main..branch` because the
merge rewrote its SHAs, so "ahead" counts lie. `next-item merged` compares the changes
instead: the **same patch** (`git cherry`), or, after a rebase onto a base that had moved,
the **same changed lines**. It cannot see work that landed **re-authored**; see step 2a
before trusting a "not merged".

## Arguments

`$ARGUMENTS` is optional:

- empty → **dry run**: list what would be deleted and what is kept; delete nothing.
- `--apply` → delete the merged remote branches, and remove the merged local worktrees and
  their branches.

## Prerequisites

- Run from inside the repo, **from the primary checkout** (the main, non-linked worktree).
  A worktree cannot be removed from inside itself.
- Deleting remote branches is outward-facing. Unless the project or user has durably
  authorized pruning, show the dry-run report and get a yes before `--apply`. `--apply`
  acts on the PRUNE and REMOVE lists only.
- If the project has its own `branch-prune` skill or pruning rules (protected ref
  namespaces, standing authorizations), those take precedence. Look for them first:
  `git grep -n -i -E 'prun|merged branch|standing authori' -- '*.md'`.

## Steps

### 1. Determine the default branch and the branches to protect

```bash
git fetch --prune origin
base=$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)   # usually main
gh pr list --state open --json headRefName --jq '.[].headRefName'           # never prune these
```

Without `gh`, use `git symbolic-ref --short refs/remotes/origin/HEAD` for the base and skip
the open-PR check only after telling the user.

Always protect:

- `$base`, and any `release*`, `production`, `develop` or similar long-lived branch.
- Every open PR's head branch.
- **Ref namespaces owned by automation** (sync daemons, snapshot or backup refs, bot
  branches such as `wip/*`, `gh-pages`, `dependabot/*` with open PRs). Such refs are
  force-pushed continuously; one can match `$base` at the moment you look and so read as
  "merged", but deleting it only makes the tool recreate it and can race whoever is
  writing it. If the repo has such a namespace, ask or check its docs before touching it.

### 2. Classify every remote branch by patch-equivalence

For each `origin/<branch>` that is not protected, run the shared merged-branch test (the one finish-work
uses too):

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/next-item" merged --no-fetch <branch>   # step 1 already fetched
```

- Exit `0` → **fully merged**, safe to delete; the output says which proof held:
  - every commit is on `$base` as the same patch (`git cherry` shows no `+`);
  - a merged pull request's head is the branch's tip. A squash merge shows this way: it rewrites the branch
    into one new commit, so `git cherry` shows every original commit as `+`;
  - every `+` commit has a commit of the same subject on `$base` with the same added and removed lines. A
    rebase merge onto a base that had moved shows this way: the lines around each change differ, so the patch
    id does too;
  - merging the branch into `$base` would change nothing (`git merge-tree`, git 2.38 or later; not tried in
    a repo with a merge driver configured).
- Exit `3` → none of those → **keep** (a hypothesis, not a verdict; step 2a). When a same-subject commit on
  `$base` shares only some of a commit's changed lines, the output names both commits and the count
  (`<commit> matches <base commit> on origin/$base in 9 of 11 changed lines`), and says `suspect` when every
  `+` commit matches on at least half (`"suspect": true` and `evidence` with `--json`): the work probably
  landed with edits.
- Exit `2` → `gh` couldn't be asked about merged pull requests → **keep**, and say the check was incomplete.
  It can say `suspect` too.

Build two lists: PRUNE (exit 0, no open PR, not protected) and KEEP (everything else, with
the reason: open PR #, unmerged commit count, or protected).

### 2a. Check the KEEP list for re-authored work

The test only recognizes work that landed as the same patch or the same changed lines. Work
that landed **re-authored** (a later PR rewrote the same idea) reports "not merged"
forever. The asymmetry matters:

- exit `0` is a **verdict**: the work is on `$base`. Safe to delete.
- exit `3` is a **hypothesis**: "no identical change found", not "this work is missing".

So the PRUNE test stays as written; the extra check goes on the KEEP list.

A branch the test called `suspect` is **KEEP-SUSPECT** at once, whatever its age, with the
test's printed lines (commits and counts) as its evidence.

For each other KEEP branch with **no open PR** whose last commit is **older than 7 days**
(`git log -1 --format=%cr <ref>`):

1. List what the branch touched:
   `git diff --name-only $(git merge-base origin/$base <ref>) <ref>`
2. Pick 2-4 **distinctive identifiers** from that diff: a new test name, a new symbol, a
   heading, a unique sentence.
3. Grep `$base`'s **current content** for each:
   `git show origin/$base:<path> | grep -n '<identifier>'`
   (or `git grep <identifier> origin/$base` if the file may have moved).

If every identifier is already on `$base`, report
**`KEEP-SUSPECT: possibly re-authored onto $base; verify before reviving or deleting`**,
with each identifier and where it was found. If any is missing, it stays a plain KEEP.

Seven days is a tripwire for a human read, not a deletion criterion; tune it freely.

**KEEP-SUSPECT never deletes.** A false positive would destroy the only copy of unmerged
work, so a human reads the evidence and deletes by hand.

**Trap: `git diff` does not answer this.** On a branch far behind, `git diff
origin/$base <ref>` shows the base branch's whole divergence. Three-dot
`git diff origin/$base...<ref>` also misleads: content both sides changed after the
merge-base reads as a pure addition even when `$base` already has it. Only comparing the
named files' current content on both sides answers the question.

### 3. Report

Print both lists with counts: how many will be deleted, and each kept branch with its
reason (KEEP-SUSPECT with its evidence). This makes over-deletion easy to spot and keeps a
re-authored branch from sitting in KEEP forever unexplained.

### 4. Delete remote branches (only with `--apply`)

Delete the PRUNE list in batches. zsh does **not** word-split unquoted variables, so use
`xargs`, not `git push origin --delete $list`:

```bash
printf '%s\n' "${prune[@]}" | xargs -n 20 git push origin --delete
```

Then `git fetch --prune origin` and list the remaining remote branches to confirm.

### 5. Classify local worktrees the same way

Linked worktrees pile up the same way: a PR merges (and the rebase rewrites its SHAs) but
the worktree and local branch stay. Classify them with the same patch-equivalence test.

```bash
git worktree list --porcelain
```

For every worktree except the primary checkout (never touch that one):

1. **Dirty check first, always.** If `git -C <path> status --porcelain` prints anything,
   the worktree is **KEEP: dirty**, whatever its merge status. Never discard uncommitted
   work.
2. **Open-PR check.** If its branch is an open PR head from step 1: **KEEP: open PR #N**.
3. **Patch-equivalence.** On a named branch: `next-item merged <branch>`. In **detached HEAD** (common once the
   PR branch was deleted upstream): `git merge-base --is-ancestor <sha> origin/$base`, then fall back to
   `git cherry origin/$base <sha>`. Merged → **MERGED**, safe to remove. Not merged →
   **KEEP: N outstanding commits**.
4. **Re-authored check.** "Not merged" here is the same hypothesis as in step 2. When the
   test said `suspect`, report **KEEP-SUSPECT** with its lines; else, with no open PR and a
   last commit older than 7 days, run the step 2a check and report **KEEP-SUSPECT** instead
   of a bare count. Still never removed by `--apply`.

Two worktrees can point at the same branch or SHA (one on the branch, one detached at the
same commit). Dedupe branch names so `git branch -D` runs once per name.

Local branches with no worktree can be classified the same way
(`git for-each-ref --format='%(refname:short)' refs/heads/`), skipping `$base` and the
current branch.

### 6. Report local worktrees and branches

Print REMOVE (path, branch or `(detached)`, "merged") and KEEP (path, reason: dirty / open
PR # / N outstanding commits / KEEP-SUSPECT with evidence), with counts, as in step 3.

### 7. Remove local worktrees and branches (only with `--apply`)

For each REMOVE entry, from the primary checkout:

```bash
git worktree remove <path>   # refuses if dirty; re-check rather than --force through it
git branch -D <branch>       # named branches only, once per name
```

`-D`, not `-d`: after a rebase or squash merge git does not see the rewritten branch as
merged. Then `git worktree prune` to clear stale entries, and `git worktree list` to
confirm the survivors match the KEEP list.

### 8. The durable fix

If the repo does not already have it, GitHub's "Automatically delete head branches"
(`delete_branch_on_merge`) removes a merged PR's remote branch by itself; suggest it, but
changing repo settings is the owner's call, not this skill's. Local worktrees and branches
still need steps 5-7.
