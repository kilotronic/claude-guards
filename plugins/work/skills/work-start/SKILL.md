---
name: work-start
description:
  Start one todo item properly. Proves it is still open, makes the worktree and branch off origin/main, takes
  it with standup claim, commits the claim mark with goal claim and opens the work's pull request at once (the
  open pull request is the claim), posts the standup step, and restates what done means. Use for "start
  <item>", "claim <item>", "pick up <item>", and at the end of the next skill.
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

## 2. Worktree and branch

`goal --json` gives the branch name (`.next.item.branch`, `work/<slug>` by default). Always a worktree, never
the main checkout, branched from the up-to-date `origin/main`:

```bash
git worktree add ../<repo>-<slug> -b <branch> origin/main
```

## 3. Claim it: the work's pull request is the claim

In the worktree, in this order:

```bash
standup claim '<the entry's title>'     # if standup is installed: first come, first served
goal claim '<the entry's title>'        # commits the mark on the entry's line, the branch's first commit
git push -u origin <branch>
gh pr create --title '<type>: <the work, in the imperative>' --body '<what and why; the item it claims>'
```

- **`standup claim` refuses**: another session is starting this item right now. Pick another item; don't
  wait. It covers only the few seconds before the pull request exists: once that is open, the pull request is
  the claim.
- **`goal claim` exits 3**: an open pull request already holds the item (it names it), or the item needs the
  owner. Remove the worktree and branch, and go back to `goal`.
- **Open the pull request now**, ready (not draft), and **don't arm auto-merge**: work-finish does that
  once the work is done. No CI or review has to pass before the work starts.
- `goal` now answers **finish** with this item and names your pull request: that is the check that the claim
  is yours. If it instead warns that **you hold the later claim**, another pull request marked the item first:
  stop, close yours, and go back to `goal`.

Then the repo's own set-up (its README or CLAUDE.md), in that worktree.

## 4. Post the standup step

If standup is installed, from the worktree, so the branch is detected:

```bash
standup status --goal '<the session goal>' --step 'started: <title>, #<n>'
```

A subagent posts its own child row with `--as-subagent '<label>'` instead.

## 5. Say what done means

Restate in plain words, to the person:

- the item, in one sentence;
- what done looks like: its finish line, or the test that will pass;
- what is out of scope.

If the entry doesn't make done clear, ask the person before writing code, and add the answer to the entry in
the work's own pull request, so the next reader has it.
