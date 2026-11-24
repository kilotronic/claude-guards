---
name: add-knowledge
description: >-
  Share something Claude saved to its memory with the whole team, by pull request to the right file every
  session reads (one repo's CLAUDE.md, the team's shared rules, or a how-to page); or mark it personal with
  `keep`. Use for "/add-knowledge" (no arguments: list what waits, then share what was just offered),
  "/add-knowledge list" or "show" (just the list), "/add-knowledge keep", and "/add-knowledge <memory>
  [repo|team|keep]".
---

# Add knowledge: from one laptop's memory to the team's files

Claude's memory lives on one machine; a teammate's Claude never sees it. What the team should all know goes into
files every session reads. This skill moves one memory there, or marks it personal.

Arguments are all optional.

**The list.** Whenever you show the person memories to consider, show them one per line, each with a short
summary of what it says. Get the list from the hook script, which reads the same memories the daily check does
(undecided, not `type: user`, matching `$WORK_MEMORY_MATCH` or the plugin lists in
`~/.config/claude-guards/memory-match/`, sorted by folder and name):

```bash
python3 "${CLAUDE_SKILL_DIR}/../../hooks/memories.py" list
```

Print its lines as they are (a dash, the name in backticks, a colon and the summary), with a count above them. Mark any that are clearly personal
with "(personal? `keep`)" at the end of its line rather than dropping it.

- **`list` or `show`:** print the list, then one line: "`/add-knowledge` to share them, `/add-knowledge <name>`
  for one, `/add-knowledge keep` to keep what was offered personal." Change nothing; stop.
- **None:** print the list first. Then do the memories offered in this conversation: the one(s) you offered
  with "Worth sharing with the team?", or the daily list's "Memories waiting to be shared". Several: do each in
  turn, one pull request per repo (several memories for the same file go in one pull request). Nothing was
  offered in this conversation: do the first 9 on the list, skipping ones that are clearly personal (offer
  `keep` for those in your report). Say how many are left for the next run.
- **`keep`:** mark what was offered (as above) personal.
- **`<memory name>`** (the file name in `~/.claude/projects/*/memory/`, without `.md`), then optionally `keep`, a
  repo name or `team`: just that memory.

## 1. Find and read the memory

`ls ~/.claude/projects/*/memory/<name>.md`. Several match: list them with their folder and ask which. Read each
whole.

## 2. `keep`: mark it personal and stop

Add `shared: keep` to its frontmatter, as a line of its own just before the closing `---`. The memory hooks never offer it again. Done.

## 3. Pick the home (one per fact)

- **One repo's code, tests, data or way of shipping** → that repo's `CLAUDE.md`, or the page it points to for
  that topic (how-to pages follow the code).
- **How the team works in every repo** (git, reviews, posting, safety) → the team's shared rules file, when the
  team keeps one: the rules a team plugin loads at every session start say where they live. With none, ask
  whether it belongs in each repo's CLAUDE.md or stays personal.
- **Personal preference** (tone, the person's own habits) → it is personal: offer `keep`.

Which repo: the argument if given; else the memory folder's name (`-Users-x-Code-gh-widgets` → widgets); else
repo names or paths in its text. Still unclear, or it holds a repo fact and a team rule together: ask once,
naming the choices; split a mixed memory into one pull request per home.

## 4. Check it is not already there, and that it is true

- Read the target file on `origin/main` and grep for the rule's key words. Said, but less well: the change
  sharpens that line rather than adding a second one. Already said fully: delete the memory and its `MEMORY.md`
  line (the file already holds it), tell the person, and stop.
- Check the claim against the code or git history before writing it down (a memory can be stale). Drop what you
  cannot confirm, or ask.

## 5. Write it the team's way

In the target file's own style: plain words that the least technical reader of the file can follow, one bullet,
the rule first and the reason after. Don't tag it with who decided and when, or add dated history: that goes in
the commit message. Put it in the section where a reader would look. Keep it short.

A rules file is read by every session, so a line must earn its place:

- **A lesson lands as a check, a skill or a doc; a rules line comes last.** What a machine can verify (a test,
  a lint rule, a hook) becomes that check, with at most one line saying why. Prose in a rules file is only for
  what needs judgment every session.
- **One fact, one place.** If another file already owns the fact, link to it rather than repeat it. When a check
  or a skill takes over a line, delete the line.

## 6. Open the pull request

Follow the target repo's rules (worktree on a branch, any issue it tracks work in, review); the `pr` skill does
the rest. The pull request description quotes the memory it came from. If the target file ships in a plugin,
bump that plugin's version so installs pick it up.

Then add `shared: <pull request URL>` to the memory's frontmatter. Once a day the memory check lists these; when
the pull request has merged, delete the memory and its line in `MEMORY.md` (the fact now lives in one place); if
it closed unmerged, remove the `shared:` line so it is offered again.

## 7. Report

One line: the pull request, the file and section it changes, and whether it waits on a person's decision or
review.
