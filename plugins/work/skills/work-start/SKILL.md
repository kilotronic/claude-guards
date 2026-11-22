---
name: work-start
description:
  Start one todo item properly. Proves it is still open, claims it in the todo file with goal claim (merged
  before any code), makes the worktree and branch off origin/main, posts the standup step, and restates what
  done means. Use for "start <item>", "claim <item>", "pick up <item>", and at the end of the next skill.
---

# Start an item

Takes the item the user named, or `goal`'s next item (`goal --json`: `.next.item.title`). Do the steps in
order; none is optional.

## 1. Prove it is still open

The todo list lags reality; redoing shipped work is the costly mistake. Before claiming:

```bash
git fetch -q origin
git log --oneline -S'<a symbol or phrase from the entry>' origin/main -- <path>   # did a fix land?
git branch -r --list '*<a word from the title>*'                                  # related branches
goal merged <a related branch>                                                    # exit 0: on main already
```

If the fix already landed, don't claim: delete the entry in a small docs pull request, citing the fix, and go
back to `goal`. That is a complete, reportable outcome.

## 2. Claim it

```bash
goal claim '<the entry's title>'
```

It opens a pull request that only marks the entry's line in the todo file with who has it, and arms
auto-merge with the repo's merge method. Branch protection and the repo's review rules apply to it as to any
pull request. **The claim holds only once that pull request is merged**:

```bash
gh pr checks <n> --watch --interval 60
gh pr view <n> --json state -q .state        # MERGED: the item is yours
```

- Exit 3 from `goal claim`, or a claim pull request that conflicts and can't merge, means someone else took
  it. Say so, close your claim pull request if one is open, delete its branch, and go back to `goal`.
- `goal` now answers **finish** with this item: that is the check that the claim is yours.

## 3. Worktree and branch

`goal --json` gives the branch name (`.next.item.branch`, `work/<slug>` by default). Always a worktree, never
the main checkout, branched from the up-to-date `origin/main`:

```bash
git worktree add ../<repo>-<slug> -b <branch> origin/main
```

Then the repo's own set-up (its README or CLAUDE.md), in that worktree.

## 4. Post the standup step

From the worktree, so the branch is detected:

```bash
standup status --goal '<the session goal>' --step 'started: <title>'
```

A subagent posts its own child row with `--as-subagent '<label>'` instead.

## 5. Say what done means

Restate in plain words, to the person:

- the item, in one sentence;
- what done looks like: its finish line, or the test that will pass;
- what is out of scope.

If the entry doesn't make done clear, ask the person before writing code, and add the answer to the entry in
the work's own pull request, so the next reader has it.
