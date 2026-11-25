---
name: next
description:
  Do the next piece of work in this repo, start to merged. Use for "do the next thing", "fix the next bug",
  "take the next item". To only ask what comes next, use orient. Takes next-item's item (your own claim, else the
  top free defect, else the top free item, else a cleanup), proves and claims it through start-work, builds it
  test first, lands it through finish-work, then does the follow-ups it filed.
---

# Do the next thing

The repo's standing work loop. `next-item` decides what comes next; this skill does it. The one exception is
your own follow-ups, which come first (step 6). Don't keep any other order of your own here or in the repo's docs: when the order is wrong, fix the README section or the todo file that
`next-item` reads.

## 1. Ask `next-item`

```bash
next-item
```

Take what it names, unless the user named an item; don't stop to offer it.

- **finish**: you already hold a claim. Finish that first (stop starting, start finishing).
- **fix** or **take**: an item nobody holds. A warning that it goes over the limit is advice, not a block: say
  it in one line and carry on, unless the owner has said otherwise.
- **cleanup**: nothing is free. Take a risk-free cleanup: delete dead code, merge a duplicate, fix a test or a
  doc. A cleanup needs no claim.
- **issues** (`record: issues`): the repo keeps its work in GitHub issues. Find, claim and finish one as
  [start-work's "Work kept in GitHub issues"](../start-work/SKILL.md#work-kept-in-github-issues) says; the
  steps below still apply, with that section in place of the todo-file steps.
- **Exit 2** (unclear): relay what `next-item` names as missing and ask the owner. Never pick something instead.
- **`warning: you hold the later claim`**: another pull request marked that item first. Stop that work: close
  your pull request, or move its work to another item (`/work:start-work` on it, after `next-item release`).

## 2. Start it

Run `/work:start-work` on the item. It proves the item is still open, makes the worktree and branch, commits
the claim mark and opens the work's pull request at once, as a draft (that open pull request is the claim; of two on one
item, the lower number wins), and restates what done means. If someone else took the
item, go back to step 1.

## 3. Red, then green

Follow the repo's own rules for tests (its CLAUDE.md). Write the test first and watch it fail for the right
reason; stub first so the red run reaches the logic. Put the test at the boundary the item lives at: empty,
the last one, the limit, a rollover. The comfortable middle is where these bugs hide.

## 4. Check it at the right height

- A change people see or press needs the real app, not only green tests.
- Read the diff against the repo's architecture, not just the test output. A one-line fix that churns many
  tests is at the wrong layer.
- Look for siblings: grep for the same wrong key, name or check elsewhere before calling it done.

## 5. Land it

Run `/work:finish-work`. It removes the entry in this work's own pull request (the claim ends when it merges), adds
follow-ups as new entries (your next work), runs the repo's gates and review, and cleans up once the pull request
shows MERGED.

## 6. Close out

Then do the follow-ups you filed, before anything `next-item` names: each one through `/work:start-work` by its
title, one pull request at a time, steps 3 to 5, until none is left. They are part of this item, also when the
user named it. Only one that waits on a person's decision or access, or on something that hasn't happened yet,
stays. Report what landed, each follow-up's pull request, and what still waits and on what. Then go back to
step 1, unless the user named one item.
