---
name: next
description:
  Do the next piece of work in this repo, start to merged. Use for "do the next thing", "what can I pick up
  now", "fix the next bug", "take the next item". Takes goal's item (your own claim, else the top free defect,
  else the top free item, else a cleanup), proves and claims it through work-start, builds it test first,
  and lands it through work-finish.
---

# Do the next thing

The repo's standing work loop. `goal` decides what comes next; this skill does it. Don't keep an order of
your own here or in the repo's docs: when the order is wrong, fix the README section or the todo file that
`goal` reads.

## 1. Ask `goal`

```bash
goal
```

Take what it names, unless the user named an item; don't stop to offer it.

- **finish**: you already hold a claim. Finish that first (stop starting, start finishing).
- **fix** or **take**: an item nobody holds. A warning that it goes over the limit is advice, not a block: say
  it in one line and carry on, unless the owner has said otherwise.
- **cleanup**: nothing is free. Take a risk-free cleanup: delete dead code, merge a duplicate, fix a test or a
  doc. A cleanup needs no claim.
- **Exit 2** (unclear): relay what `goal` names as missing and ask the owner. Never pick something instead.
- **`warning: you hold the later claim`**: another pull request marked that item first. Stop that work: close
  your pull request, or move its work to another item (`/work:work-start` on it, after `goal release`).

## 2. Start it

Run `/work:work-start` on the item. It proves the item is still open, makes the worktree and branch, commits
the claim mark and opens the work's pull request at once (that open pull request is the claim), posts the
standup step, and restates what done means. If someone else took the item, go back to step 1.

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

Run `/work:work-finish`. It removes the entry in this work's own pull request (the claim ends when it merges), adds
follow-ups as new entries, runs the repo's gates and review, and cleans up once the pull request shows MERGED.

## 6. Close out

Report what landed and what follow-ups you filed. Then go back to step 1, unless the user named one item.
