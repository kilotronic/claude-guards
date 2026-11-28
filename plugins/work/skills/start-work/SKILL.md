---
name: start-work
description:
  Start one todo item properly. Proves it is still open, makes the worktree and branch off origin/main, commits
  the claim mark with next-item claim and opens the work's pull request at once, as a draft (the open pull request is
  the claim), checks again that no lower-numbered pull request marks the same item, and restates what done
  means. Before claiming, checks that no open pull request changes the same files. Use for "start <item>", "claim <item>", "pick up <item>", and at the end of the
  next skill.
---

# Start an item

`next-item` is this plugin's command: run it as `"${CLAUDE_PLUGIN_ROOT}/scripts/next-item"`, which works
whether or not the plugin's SessionStart hook put it on `PATH`.

Takes the item the user named, or `next-item`'s next item (`next-item --json`: `.next.item.title`). Do the steps in
order; none is optional. When `next-item` answers `record: issues`, the repo keeps its work in GitHub issues instead:
follow [Work kept in GitHub issues](#work-kept-in-github-issues) below.

## 1. Prove it is still open

The todo list lags reality; redoing shipped work is the costly mistake. Before claiming:

```bash
git fetch -q origin
git log --oneline -S'<a symbol or phrase from the entry>' origin/main -- <path>   # did a fix land?
git branch -r --list '*<a word from the title>*'                                  # related branches
"${CLAUDE_PLUGIN_ROOT}/scripts/next-item" merged <a related branch>   # exit 0: on main already
```

If the fix already landed, don't claim: delete the entry (a plan row: its file) in a small docs pull request,
citing the fix, and go
back to `next-item`. That is a complete, reportable outcome.

**Then check it against the open pull requests**, so two branches don't change the same thing at once:

```bash
gh pr list --state open --json number,title,headRefName,mergeable
gh pr diff <n> --name-only   # for each open pull request near the item
```

If the item would change the same files or the same mechanism as an open pull request, don't start it: one
concern per pull request, and never build on a branch in review. Take the next item (a worker given this one item asks whoever gave it instead) and come back once that one
lands, or ask its author whether the item belongs in it.

## 2. Worktree and branch

`next-item --json` gives the branch name (`.next.item.branch`, `work/<slug>` by default). Always a worktree, never
the main checkout, branched from the up-to-date `origin/main`:

```bash
git worktree add ../<repo>-<slug> -b <branch> origin/main
```

Then the repo's own set-up (its README or CLAUDE.md), in that worktree, before claiming: `next-item claim` commits
there, so the repo's commit hooks need what the set-up installs.

## 3. Claim it: the work's pull request is the claim

In the worktree, in this order:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/next-item" claim '<the entry's title>'   # checks the open pull requests, then commits the mark as the first commit
git push -u origin <branch>
gh pr create --draft --title '<type>: <the work, in the imperative>' --body '<what and why; the item it claims>'
"${CLAUDE_PLUGIN_ROOT}/scripts/next-item" claim '<the entry's title>'   # again, now that the pull request is open: settles a tie
```

- **The first `next-item claim` says `already yours`** for a mark already on the default branch: there is no mark
  commit to push, so make the work's first commit before opening the pull request.
- **`gh pr create` is refused without the claim.** This plugin's hook (`claim-before-pr`) asks
  `next-item claimed`: a branch that changes code opens a pull request only when it carries the mark the first
  `next-item claim` committed, or a mark on the default branch is yours. Docs-only branches open freely. Work the
  owner asked for directly, with no todo entry to claim, says so on a line of its own in the pull request's
  body: `No backlog item: the owner asked for this directly.` Never add that line to skip a claim an entry exists for.
- **The first `next-item claim` exits 3**: an open pull request already marks the item (it names it), or the item
  needs the owner. Remove the worktree and branch, and go back to `next-item`.
- **Open the pull request now, as a draft** (`--draft`), and **don't arm auto-merge**: the draft says the work
  isn't ready yet. finish-work marks it ready (`gh pr ready`) and arms auto-merge once the work is done. A draft
  holds the item like any open pull request. No CI or review has to pass before the work starts.
- **The second `next-item claim` settles a tie.** Two sessions can both pass the first check in the seconds before
  either pull request opens. The lower pull request number wins. Exit 0 (`already yours`) means yours holds
  the item. Exit 3 means another pull request marked it first: close yours with a comment naming the winner,
  as the message gives it (`gh pr close <n> --comment 'Closing: #<winner> marked this item first.'`), remove
  the worktree and branch, and go back to `next-item` for another item. If the message gives no command, your
  pull request isn't listed on GitHub yet: close it all the same. Exit 1 means `next-item` couldn't check (GitHub
  didn't answer, or another pull request's branch isn't fetched yet): run it again; don't start until it
  exits 0.
- `next-item` now answers **finish** with this item and names your pull request.

## 4. Say what done means

Restate in plain words, to the person:

- the item, in one sentence;
- what done looks like: its finish line, or the test that will pass;
- what is out of scope.

If the entry doesn't make done clear, ask the person before writing code, and add the answer to the entry in
the work's own pull request, so the next reader has it.

## Work kept in GitHub issues

This is the one rule the work skills share for a repo whose README names GitHub issues as its record of work.
`next-item` reads the README's `**Work is recorded in:**` line and says which record the repo keeps (`next-item --json`:
`.record`):

- **`todo`**: the line names a todo file. Every step in these skills applies as written.
- **`issues`**: the line names GitHub issues. There is no todo file to read, mark or delete from (and the
  `claim-before-pr` hook checks nothing), so the
  todo-file steps (`next-item claim`, `next-item release`, removing the entry, follow-ups as entries) give way to the
  issue steps here. Everything else stays: the worktree and branch, tests first, the repo's gates, the `pr`
  skill's review, auto-merge and the clean-up.
- **Neither:** `next-item` exits 2 and says the line names neither a todo file nor GitHub issues. Say so to the
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
  request at once as a draft (`gh pr create --draft`) with `Closes #<n>` in its body, and `gh issue edit <n> --add-assignee @me`. An issue someone
  else is assigned to, or that another open pull request already names, is theirs: pick another.
- **Settle a tie:** two sessions can both pass that check in the same minute, so once yours is open, look
  again: `gh issue view <n> --json closedByPullRequestsReferences` lists the pull requests that close it. An
  open one with a lower number than yours holds it: close yours with a comment naming it, and go back to Find.
- **Finish:** finish-work as written, minus the todo entry. The pull request's `Closes #<n>` closes the issue
  when it merges. Follow-ups found on the way become new issues (`gh issue create`), named in the pull
  request.
- **Abandon:** close the pull request and `gh issue edit <n> --remove-assignee @me`.
