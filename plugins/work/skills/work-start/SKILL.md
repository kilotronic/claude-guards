---
name: work-start
description:
  Start one todo item properly. Proves it is still open, makes the worktree and branch off origin/main, commits
  the claim mark with goal claim and opens the work's pull request at once (the open pull request is the
  claim), checks again that no lower-numbered pull request marks the same item, and restates what done
  means. Use for "start <item>", "claim <item>", "pick up <item>", and at the end of the
  next skill.
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
goal claim '<the entry's title>'        # checks the open pull requests, then commits the mark as the first commit
git push -u origin <branch>
gh pr create --title '<type>: <the work, in the imperative>' --body '<what and why; the item it claims>'
goal claim '<the entry's title>'        # again, now that the pull request is open: settles a tie
```

- **The first `goal claim` says `already yours`** for a mark already on the default branch: there is no mark
  commit to push, so make the work's first commit before opening the pull request.
- **The first `goal claim` exits 3**: an open pull request already marks the item (it names it), or the item
  needs the owner. Remove the worktree and branch, and go back to `goal`.
- **Open the pull request now**, ready (not draft), and **don't arm auto-merge**: work-finish does that
  once the work is done. No CI or review has to pass before the work starts.
- **The second `goal claim` settles a tie.** Two sessions can both pass the first check in the seconds before
  either pull request opens. The lower pull request number wins. Exit 0 (`already yours`) means yours holds
  the item. Exit 3 means another pull request marked it first: close yours with a comment naming the winner,
  as the message gives it (`gh pr close <n> --comment 'Closing: #<winner> marked this item first.'`), remove
  the worktree and branch, and go back to `goal` for another item. If the message gives no command, your
  pull request isn't listed on GitHub yet: close it all the same. Exit 1 means `goal` couldn't check (GitHub
  didn't answer, or another pull request's branch isn't fetched yet): run it again; don't start until it
  exits 0.
- `goal` now answers **finish** with this item and names your pull request.

Then the repo's own set-up (its README or CLAUDE.md), in that worktree.

## 4. Say what done means

Restate in plain words, to the person:

- the item, in one sentence;
- what done looks like: its finish line, or the test that will pass;
- what is out of scope.

If the entry doesn't make done clear, ask the person before writing code, and add the answer to the entry in
the work's own pull request, so the next reader has it.
