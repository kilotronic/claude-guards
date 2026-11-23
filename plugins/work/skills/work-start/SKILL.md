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
order; none is optional. When `goal` answers `record: issues`, the repo keeps its work in GitHub issues instead:
follow [Work kept in GitHub issues](#work-kept-in-github-issues) below.

## 1. Prove it is still open

The todo list lags reality; redoing shipped work is the costly mistake. Before claiming:

```bash
git fetch -q origin
git log --oneline -S'<a symbol or phrase from the entry>' origin/main -- <path>   # did a fix land?
git branch -r --list '*<a word from the title>*'                                  # related branches
goal merged <a related branch>                                                    # exit 0: on main already
```

If the fix already landed, don't claim: delete the entry (a plan row: its file) in a small docs pull request,
citing the fix, and go
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

## Work kept in GitHub issues

This is the one rule the work skills share for a repo whose README names GitHub issues as its record of work.
`goal` reads the README's `**Work is recorded in:**` line and says which record the repo keeps (`goal --json`:
`.record`):

- **`todo`**: the line names a todo file. Every step in these skills applies as written.
- **`issues`**: the line names GitHub issues. There is no todo file to read, mark or delete from, so the
  todo-file steps (`goal claim`, `goal release`, removing the entry, follow-ups as entries) give way to the
  issue steps here. Everything else stays: the worktree and branch, tests first, the repo's gates, the `pr`
  skill's review, auto-merge and the clean-up.
- **Neither:** `goal` exits 2 and says the line names neither a todo file nor GitHub issues. Say so to the
  person and treat it as today's unclear answer: ask the owner, and pick nothing yourself.

For `issues`, **use the repo's own issue-tracking skill or rules first**: if the repo has a skill for its
issues, or rules for them in its README or CLAUDE.md, follow those to find, claim, update and finish the work,
and use the steps below only where they say nothing. With none, the minimal steps with `gh`:

- **Find:** first the work you already hold: an open pull request from the branch you are on (`gh pr view`).
  Other open pull requests of yours (`gh pr list --author @me`) may be another session's: every session of
  one person is the same `@me`, so leave them unless the person says otherwise. Else the free issues, `gh issue list --state open --search 'no:assignee sort:created-asc' --limit 200`:
  defects (a `bug` label) first, then the rest, oldest first, unless the repo states another order. Prove it
  is still open as in step 1.
- **Claim:** the work's open pull request is the claim, as with a todo file; the assignee only shows it on
  the issue. Make the worktree and a branch `work/<n>-<slug of the title>` as in step 2, make the work's first
  commit (GitHub opens no pull request without one), push, open the work's pull
  request at once with `Closes #<n>` in its body, and `gh issue edit <n> --add-assignee @me`. An issue someone
  else is assigned to, or that another open pull request already names, is theirs: pick another.
- **Settle a tie:** two sessions can both pass that check in the same minute, so once yours is open, look
  again: `gh issue view <n> --json closedByPullRequestsReferences` lists the pull requests that close it. An
  open one with a lower number than yours holds it: close yours with a comment naming it, and go back to Find.
- **Finish:** work-finish as written, minus the todo entry. The pull request's `Closes #<n>` closes the issue
  when it merges. Follow-ups found on the way become new issues (`gh issue create`), named in the pull
  request.
- **Abandon:** close the pull request and `gh issue edit <n> --remove-assignee @me`.
