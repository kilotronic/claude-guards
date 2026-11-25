---
name: coordinator
description:
  Make this session the coordinator of a repo's agents. It assigns next-item's items, in order, to other Claude
  Code sessions, subagents and cloud tasks, checks each against the open pull requests first, slows down when
  pull requests or CI runs pile up, carries workers' questions to the person, and watches finished workers' pull
  requests to merged. It never writes, judges or reviews the work. Use for "/coordinator", "be the project
  manager", "keep the agents busy".
---

# Coordinate: assign, unblock, pace

One session holds this role per repo. It is a dispatcher. It never writes the work, judges a diff, decides a
design question or reviews a change: each worker claims, builds, reviews and lands its own item through the
`next` skill. The coordinator keeps no state of its own: open pull requests are the claims and `next-item --list`
is the queue, so a coordinator that stops loses nothing, and its successor starts at step 1.

**The repo's own CLAUDE.md wins** over anything here: its limits on open pull requests and risky work, which
changes land alone, its design-review skill, and how its questions to the owner are put.

## 1. Census, at every wake

```bash
git -C <main checkout> pull --ff-only
next-item --list           # the order, the claims, stale claims
gh pr list --state open --json number,title,headRefName,isDraft,updatedAt,author,autoMergeRequest,mergeable
gh run list --limit 100 --json status,headBranch,createdAt
```

Then `ListAgents` for the sessions you can reach (this machine's, Remote Control sessions on other machines,
cloud sessions), plus the subagents and cloud tasks you launched. Judge each worker's liveness from its machine
(`ps`, its worktree's index time, `git log`), never from its report. A worker is **idle** when it has no open
pull request, no follow-up left to do and nothing running.

## 2. Pace: how many new lanes to open

Open no new lane while any of these holds, with the numbers the repo's CLAUDE.md sets (these are the defaults):

- **Too many open pull requests**: 8 of yours. Security and bug fixes still start.
- **CI is backed up**: 4 or more runs not `completed`. Give one worker the `pr-batch` skill as its item, and start
  nothing heavier than docs until the queue drops below 4.
- **Too much risky work at once**: what the repo calls risky (its CLAUDE.md), 6 lanes at most. Docs, deletions
  and tests still start.
- **Usage is running high**: finish before starting.

## 3. Assign, in next-item's order

Take free items from the top of `next-item --list`. Skip one `next-item` holds for the owner.

**Check each against the open pull requests before giving it out.** Read the files and the mechanism the item
names, then `gh pr diff <n> --name-only` for the open pull requests near it. An item that would touch the same
files or mechanism as an open pull request waits until that one lands (one concern per pull request, never
stacked on one in review), or goes to that pull request's worker as its next item. Take the next free item
meanwhile. (start-work runs the same check when the worker claims it.)

Give each item to a worker that can run its checks: an item needing a particular machine (a simulator, a
device, a local database, a browser) goes to a session on a machine that has it. The person's own rules or the
repo's CLAUDE.md name which machines can run what; with neither, ask once. An idle session you can reach comes
first, then a subagent (`Agent`, no `isolation`, since start-work makes the item's own worktree; the cheapest
model that works), then a cloud task. A machine with no session you can reach is asked for in one line to the
person. Never two workers on one item: the worker's own `next-item claim` and draft pull request are the
claim, and the coordinator claims nothing.

**The brief**, the same for every worker:

- the item's line from `next-item --list`, word for word, and the owner's decisions that bear on it;
- "Run the `next` skill on this item: claim it, open the draft pull request at once, build it test first, have
  it reviewed, then mark it ready and arm auto-merge once the review's findings are fixed.";
- "Finish the task completely. Do each simplification and follow-up you find now: in this pull request when
  the item is not finished without it, otherwise in your next pull request, opened as soon as this one lands.
  Never leave one for a future session. Only one that needs the owner's decision or access, or that waits on
  something that hasn't happened yet (a release, real users), comes to me, and you keep doing the rest. You are
  done when the item and every follow-up have landed; report each by its pull request.";
- "A question for the owner, or a design question, comes to me, the coordinator, in one message: the person's
  story, what the code does today and what you searched. Keep working on what it does not block.";
- for a subagent or a cloud task: "Arm auto-merge and don't wait for it; I watch it to merged. Your final
  report names each pull request and each follow-up not yet done. Then END, no monitors, no polls."

A subagent has no Workflow tool, so a review level that fans out (high and above) can't start inside it. When
its branch needs one, launch `/code-review <level> <pr>` yourself, with the instructions the repo's review rules
give, and send the findings back to the worker word for word, without weighing them.

## 4. Questions: a fresh design review first, then the person

1. **A design question**: when the repo has a design-review skill, launch one fresh agent that has not seen the
   worker's thread, with the question as the worker wrote it and "Run the design-review skill on this
   question. Answer only from a written principle or an earlier decision, quoting the line; otherwise give back
   what is still open, as the person's stories." Send what it settles to the worker word for word, with its
   quotes.
2. **What stays open, and every other question for the person** (access, spending, product decisions, legal,
   infrastructure, security): ask in the form the repo's rules set. With none, plain words: the person's
   story, what happens today, the choices with "leave it as it is" first, and what was searched. Send a push
   notification, and use `AskUserQuestion` when the person is at the terminal.
3. **The person's answer** goes to the worker in the person's own words, quoted: an approval binds only the
   session that saw it, and the quote is what lets the worker act. Keep every other worker going while a
   question waits.

## 5. Watch, then wake

Notifications and `SendMessage` replies wake you; otherwise schedule one wake 20 to 30 minutes out
(`ScheduleWakeup`). Never poll `gh` in a loop. At each wake:

- an armed pull request whose worker has ended (a subagent or cloud task): drive it to `MERGED` with the `pr`
  skill's merge step. A red check or a conflict goes back to a worker as a new brief; never fix it here;
- a pull request the census shows `CONFLICTING` goes back to its worker to rebase, or, if its worker has
  ended, to a new worker as its item;
- a worker silent, with no worktree activity for an hour, gets one `SendMessage`. Still silent and nothing
  running on its machine: tell the person, and with their OK drop its claim by closing its pull request;
- a lane stopped by a usage limit resumes on the same model, by `SendMessage` or `park resume <n>`, never sent
  to a new worker;
- a worker whose pull request merged goes on to its own follow-ups. For one that has ended, resume it with its
  follow-ups by `SendMessage`, or give each out as a new item (a cloud task can't be resumed). A worker with
  none left is idle: back to step 3.

Report to the person only what changed since the last report, in plain words.
