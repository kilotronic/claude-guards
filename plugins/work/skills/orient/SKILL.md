---
name: orient
description:
  Orient at the start of a session with no task given, or when asked "what should we do next", "where are
  we", "check the board and the todo list". Syncs with main, reads goal's answer and full list, checks the
  standup board and open pull requests, and ends with one recommendation. Reads only; it starts nothing.
---

# Orient: what should we do next?

Answer the session-start question in one pass, and end with one concrete recommendation, not a survey.
The order of work is `goal`'s, not this skill's: `goal` reads the repo's README section
`## How work runs here` and its todo file on `origin/main`, and answers.

## Steps

1. **What changed on main.** `git fetch -q origin`, then `git log --oneline` over the commits you haven't
   seen. Other agents land work all the time; note anything that closes or changes a todo entry.
2. **The answer and the list.**

   ```bash
   goal --list
   ```

   It prints the goal, the next item with its reason, the in-progress count against the limit, any stale
   claims, then every free item in order and every claim. Exit 2 means the repo doesn't say enough: relay
   what it names as missing and ask the owner those questions. Never pick an item yourself instead.

3. **Who else is working.** `standup list`, if standup is installed: other sessions on this repo, their branches and pull requests.
   A session working on the item `goal` names, without a claim mark, is a reason to coordinate first.
4. **Open pull requests.** `gh pr list`. One of ours that isn't merged comes before new work: drive it to
   merged. A pull request about to change the code an item touches is worth naming.
5. **Prove the top item is still open** before recommending it: todo lists lag reality.
   `git log --oneline -S'<a symbol from the entry>' -- <path>` finds a fix that already landed. If it did,
   say so; the entry is deleted, citing the fix, in a docs pull request.
6. **Recommend.** Lead with `goal`'s item, why (its reason), and the first concrete action, which is
   `/work:work-start` on that item. Name any warning `goal` gave (over the limit, a stale claim) in one line
   each. If asked to rank the open items, rank them all in `goal`'s order, still leading with the one.

Keep the output short: what changed, who else is active, the answer, the recommendation.
