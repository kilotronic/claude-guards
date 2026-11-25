---
name: orient
description:
  Orient at the start of a session with no task given, or when asked "what should we do next", "what can I
  pick up now", "where are we", "check the todo list". Syncs with main, reads next-item's answer and full list,
  checks open pull requests, and ends with one recommendation. Reads only; it starts nothing.
---

# Orient: what should we do next?

Answer the session-start question in one pass, and end with one concrete recommendation, not a survey.
The order of work is `next-item`'s, not this skill's: `next-item` reads the repo's README section
`## How work runs here` and its todo file in the working copy (claims from the open pull requests whose
branches carry a claim mark), and answers.

## Steps

1. **What changed on main.** `git fetch -q origin`, then `git log --oneline` over the commits you haven't
   seen. Other agents land work all the time; note anything that closes or changes a todo entry.
2. **The answer and the list.**

   ```bash
   next-item --list
   ```

   It prints the milestone, the next item with its reason, the in-progress count against the limit, any stale
   claims and any item two pull requests both claim, then every free item in order and every claim with its
   pull request. Exit 2 means the repo doesn't say enough: relay
   what it names as missing and ask the owner those questions. Never pick an item yourself instead.
   `record: issues` means the repo keeps its work in GitHub issues: list them as
   [start-work's "Work kept in GitHub issues"](../start-work/SKILL.md#work-kept-in-github-issues) says, and
   recommend from that list.

3. **Open pull requests.** `gh pr list`: who else is working on this repo, on which branches, since the
   work's own pull request opens when it starts. One of ours that isn't merged comes before new work: drive it to
   merged. A pull request about to change the code an item touches is worth naming.
4. **Prove the top item is still open** before recommending it: todo lists lag reality.
   `git log --oneline -S'<a symbol from the entry>' -- <path>` finds a fix that already landed. If it did,
   say so; the entry is deleted, citing the fix, in a docs pull request.
5. **Recommend.** Lead with `next-item`'s item, why (its reason), and the first concrete action, which is
   `/work:start-work` on that item. Name any warning `next-item` gave (over the limit, a stale claim) in one line
   each. If asked to rank the open items, rank them all in `next-item`'s order, still leading with the one.

Keep the output short: what changed, who else is active, the answer, the recommendation.
