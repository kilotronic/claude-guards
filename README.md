# claude-guards

Two [Claude Code](https://code.claude.com) plugins for agents working in a repository, often several at once:

- **guards**: hooks that stop risky git, GitHub and shell commands before an agent runs them. Each refusal says
  why and what to do instead, so the agent can fix its own command and carry on.
- **work**: the `goal` command, which answers "what should I work on next?" from what the repo states in
  writing; the skills that claim an item, build it and land it (`pr` opens the pull request and has a fresh
  agent review it); `long-loops` and `add-knowledge`; hooks around merging; and a hook that offers to share what
  Claude saves to memory. See [The work plugin](#the-work-plugin).

The guards were written for several agents working in one repository at once, each in its own git worktree.
They suit a single agent too. Note that a plain clone is its own main checkout, so the main-checkout guards
apply to it: work in a worktree (`git worktree add ../feature -b feature origin/main`), or opt the repo out
as described below.

## Install

```text
/plugin marketplace add kilotronic/claude-guards
/plugin install guards@claude-guards
/plugin install work@claude-guards
```

Restart Claude Code after installing. Both plugins need `python3` (3.9 or later) and `git` on `PATH`. They use
no other packages; `work` also uses `gh` to open pull requests.

## What each guard does

Each guard has a rule id. It appears in its refusal as `[guards:<id>]`, and you use the id to turn the rule
off for a repo (see below). A plugin that bundles a copy of `guardlib.py` for hooks of its own gets labels
with its own name from its `plugin.json`.

| Hook                        | Rule id                    | Refuses                                                                                                                                                                                                                                                                       |
| --------------------------- | -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `guard-shared-stash.py`     | `no-stash`                 | `git stash`. A repo has one stash, so agents working in parallel take each other's work.                                                                                                                                                                                      |
| `guard-hard-reset.py`       | `no-commit-on-main`        | `git reset --hard` on `main`/`master`, or over uncommitted changes.                                                                                                                                                                                                           |
| `guard-no-verify.py`        | `no-commit-on-main`        | `git commit --no-verify` with new content. Rewording or squashing commits that already passed the hooks is allowed.                                                                                                                                                           |
| `guard-commit-on-main.py`   | `no-commit-on-main`        | `git commit` in a worktree that is on `main`/`master`.                                                                                                                                                                                                                        |
| `block-main-edits.py`       | `main-checkout`            | Edit, Write, MultiEdit and NotebookEdit on files in the session's repo, in its main checkout, while it is on `main`/`master`.                                                                                                                                                 |
| `guard-branch-switch.py`    | `main-checkout`            | `git switch`, `git checkout <branch>` and `gh pr checkout` in a repo's main checkout. Going back to `main` and restoring files are allowed, and linked worktrees are not checked.                                                                                             |
| `guard-admin-merge.py`      | `no-admin-merge`           | `gh pr merge --admin`, which merges past branch protection. Plain and `--auto` merges are allowed.                                                                                                                                                                            |
| `guard-github-polling.py`   | `no-github-polling`        | `gh run watch` and `gh pr checks --watch` without `--interval 60` or more, shell loops that call `gh` again and again, and `watch gh ...`. Covers both the Bash and Monitor tools.                                                                                            |
| `guard-secret-print.py`     | `no-secret-print`          | Printing secrets into the transcript: reading `.env` files, `op read`, `gh auth token` or `security ... -w` with output to the terminal, bare `env`/`printenv`/`set`/`export`/`declare -p`, and echoing `*_TOKEN`, `*_KEY` and similar variables. Covers Bash, Read and Grep. |
| `guard-zsh-special-vars.py` | `no-zsh-special-vars`      | Setting `path`, `status`, `UID` or `GID`. In zsh, `path` is tied to `PATH`, and the others are read-only or change the user.                                                                                                                                                  |
| `guard-ours-theirs.py`      | `no-wholesale-ours-theirs` | `git checkout --ours/--theirs`, `-X ours/theirs` and `-s ours`. Each of these takes one side of every conflict at once. One side of a conflicted binary file, which has no hunks, is allowed.                                                                                 |

The guards read a command the way a shell would, closely enough to tell a command that runs from one that is
only quoted, for example in a commit message or a heredoc. They follow `cd`, `git -C`, `bash -c`, `$(...)`
and pipes. Each guard is meant to catch the mistakes agents actually make. None of them is a security
boundary: a determined agent, or a person, can get around any of them.

**A guard that fails never blocks.** If a hook hits an error or can't read its input, it lets the command
through.

## Turning a rule off for a repo

Some repos really do work on `main`, a dotfiles repo for example. You can turn a rule off for one repo, or
for a group of repos, in an overrides file:

```json
{
  "my-org/dotfiles": {
    "no-commit-on-main": { "reason": "this repo is worked on its main branch" },
    "main-checkout": { "reason": "this repo is worked on its main branch" }
  },
  "my-org/*": {
    "no-zsh-special-vars": { "reason": "our agents run bash, not zsh" }
  }
}
```

- The key is the `owner/name` that the repo's `origin` remote points to, in lower case. Any host counts, so an
  SSH alias such as `git@github-work:my-org/dotfiles.git` works. When a repo has no entry of its own, the
  first pattern that matches it (`*`, `?`, `[...]`) applies.
- An override counts only when it has a non-empty `reason`.
- The setting that applies is that of the repo the command acts in (after any `cd` or `git -C`), not the repo
  where the session started.
- `no-secret-print`, `no-admin-merge` and `no-github-polling` are never turned off: their guards ignore
  overrides. To stop overrides turning other rules off, list their ids under `"_locked": ["rule-id", ...]` in
  the same file.
- One id can cover several guards: `no-commit-on-main` turns off the hard-reset, `--no-verify` and
  commit-on-main guards together, and `main-checkout` the edit and branch-switch guards. The stash guard
  has had its own id, `no-stash`, since guards 1.1.1; `no-commit-on-main` off still turns it off too, unless
  `no-stash` is locked.
- A `.main-edit-ok` file at a repo's root also lets that repo edit, switch branches and commit in its main
  checkout (`block-main-edits.py`, `guard-branch-switch.py` and `guard-commit-on-main.py`).

The guards read the first of these files that is set:

1. `overrides.json` at the plugin's root. A plugin that bundles these hooks can ship its own file there.
2. The file named by `$CLAUDE_GUARDS_OVERRIDES`.
3. `~/.config/claude-guards/overrides.json`.

Only the first that is set is read: when `$CLAUDE_GUARDS_OVERRIDES` names a file that doesn't exist, the
file in `~/.config` is not read either. If no file is found, or the one found can't be read, every rule
stays on.

## The work plugin

`goal` reads the repo's working copy, so goals edited locally count right away, and answers in a fixed order:

1. **your own claimed item**: finish it before starting another;
2. **else the top free defect**, one defect fix at a time;
3. **else the top free item**, with a warning when taking it goes over the repo's limit on work in progress;
4. **else a cleanup**: delete code, fix a test or a doc.

Nothing blocks: the limit and the order are warnings and advice. When the repo doesn't say enough, `goal`
names what is missing and exits 2; it never guesses.

```text
$ goal
goal: M2, the phone app (done when: a timer rings on a locked phone)
next: fix "The timer skips a second" (docs/todo.md line 5): the top free defect; defects come before anything new
in progress: 0 of 2
```

`goal --list` adds every free item in order and every claim, and `--json` gives the same for scripts. Exit
codes: 0 an answer, 1 an error, 2 unclear, 3 no (the item is someone else's, or a branch is not merged). The
plugin puts `goal` on the Bash tool's `PATH`; outside Claude Code, run `plugins/work/bin/goal`.

### What the repo states

The facts live in the README, where people read them too, under one heading, as bold-label lines:

```markdown
## How work runs here

- **Mission:** <who this is for and what it does for them>
- **Current milestone:** <name>. **Done when:** <a finish line a person can check>. **By:** <date, or "no date">
- **Work is recorded in:** `docs/todo.md`
- **Claim before starting:** `goal claim '<the entry's title>'` as the work branch's first commit, with its pull request opened at once
- **Work in progress:** at most <N> at once; defect fixes one at a time
- **Defects first:** the Defects section is fixed, top first, before anything new
```

`goal` needs the todo file, the limit, and either the milestone or a `**Goals:**` line linking the file that
states it. A milestone whose By date has passed is unclear. A frozen repo has one line instead:
`- **Frozen:** new work goes to <repo>.`

The todo file has a `## Defects` (or `## Bugs`) heading and a `## Next` heading, each a list of entries,
top first, each a line starting with a dash. An entry's title is its bold lead (a leading `(size, tag)` is dropped), else its first sentence.
`###` subheadings belong to the section above them. An entry containing `(owner)` needs the owner: it is
listed apart, and `goal claim` refuses it.

An optional `.claude/work.toml` sets format details:

```toml
defects = ["Bugs"]                    # the defects heading(s)
next = ["Security", "Next", "Later"]  # the sections taken after defects, in order
skip = ["(owner)", "**Owned by**"]    # text marking an entry that isn't free
merge_method = "squash"               # how pr-skill pull requests merge: squash, rebase or merge
branch_prefix = "work/"               # the work branch is <prefix><slug of the title>
```

### The work's pull request is the claim

A claim is a mark at the end of the entry's first line saying who has it. The work's own branch commits it first,
and the work's pull request, open from the start, carries it. The entry still starts with its bold lead, so a
repo's own checks of its entries read it as before:

```markdown
- **Add a snooze button.** Five more minutes. [claimed by session-1a2b3c4d on laptop, 2026-11-22]
```

- **Starting:** on the work's new branch, `goal claim '<title>'` commits the mark, touching only that line.
  Push the branch and open the work's pull request at once, ready for review but with auto-merge not armed. No
  separate pull request has to merge first, and the work starts right away.
- **An item is in progress while an open pull request carries its mark.** `goal` asks GitHub for the open pull
  requests into the default branch, as GitHub names it (`gh repo view`), not the local `origin/HEAD`, which
  can be stale. It reads the todo file on each one's branch. The claim is that pull request's, named with
  its number, its author and the mark's holder. work-finish deletes the entry in the same pull request, and the
  claim holds until it merges.
- **Merged:** the entry is gone from the default branch, so the item is too. **Closed without merging:** the
  item is free again.
- **Ties are settled on GitHub.** `goal claim` refuses an item an open pull request already marks. Two
  sessions can still both pass that check in the seconds before either pull request opens, so work-start runs
  `goal claim` again once its pull request is open. The lower pull request number wins: the higher one gets
  exit 3 naming the winner, and closes itself with a comment pointing at it. `goal` names both pull requests
  while both are open, and tells the later one to stop.
- **Releasing:** to drop the work, close its pull request (a mark on the default branch needs a merged
  `goal release`, below). To go on under another item, run
  `goal release '<title>'`, which commits the mark away on the branch, then `goal claim` on the other one.
- A mark only in your working copy is not counted until its pull request opens. A mark on the default branch,
  made before claims moved to pull requests, still holds (and holds first) until the work deletes its entry, or
  its holder removes it; the owner can with `goal release '<title>' --holder <their label>`. It goes stale by
  its date. A pull request from a fork never holds an item.
- If GitHub can't be reached, `goal` says claims weren't checked and answers from the local files, taking
  their marks as they are. `goal claim` refuses until GitHub answers.
- **In progress** is the number of items claimed this way, so it is stated in one place.
- **Stale:** a claim whose pull request has had no activity for 3 days is flagged by `goal`. It is never
  removed by itself.

Who you are comes from `--as`, else `$GOAL_AS`, else the Claude Code session, plus the machine's name
(`$GOAL_MACHINE` overrides it). With none of these, every session of the login shares one name, so
`goal claim` refuses and `goal` warns. A session label changes with each session, so to keep a claim yours across
sessions, set a stable `$GOAL_AS`. The mark is public wherever the todo file is: in a public repo, set
`$GOAL_MACHINE` and `$GOAL_AS` to names you are happy to publish.

On a work branch, a pull request's claim is yours only when it comes from that branch: two sessions can share a
name (subagents of one session, or one stable `$GOAL_AS`), never a branch. On the default branch, and for a mark
on the default branch, the name decides.

`goal merged <branch>` says whether a branch's work is on main: every commit there as the same patch, or a
merged pull request ending at the branch's tip, as a squash merge leaves it.

### The skills

| Skill           | Does                                                                                                                                         |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `orient`        | What changed on main, `goal --list`, who else is working, open pull requests, one recommendation. Reads only                                 |
| `next`          | Takes `goal`'s item and does it, start to merged, through `work-start` and `work-finish`                                                     |
| `work-start`    | Proves the item is still open, makes the branch, commits the claim mark and opens the work's pull request, says what done means              |
| `work-finish`   | Removes the entry in the work's pull request, files follow-ups, runs the gates; after merge, cleans up                                       |
| `branch-prune`  | Deletes branches and worktrees whose work is merged, by the same test as `goal merged`. Dry run by default                                   |
| `long-loops`    | Keeps a long-running loop's finished work through a crash: whole records, saved per item, resumed by id                                      |
| `pr`            | Opens a pull request, has a fresh reviewer agent review it against a five-item checklist, arms auto-merge, confirms it landed                |
| `add-knowledge` | Moves a memory Claude saved into the file every session reads (a repo's CLAUDE.md or the team's rules) by pull request, or marks it personal |

The `pr` skill's checklist leaves out an item turned off for the repo in the guards' overrides file, by the item's
id (`one-concern`, `docs-follow-code`, `risky-change`, `new-dependency`, `threat-model`). The skills post their progress with [standup](https://github.com/kilotronic/standup-board) when it is installed.

### The hooks

- **Agents may merge pull requests in auto mode** (`allow-merge.py`). Claude Code's auto mode refuses
  `gh pr merge` even after the person said yes, so in auto mode this hook allows a Bash call that is only
  `gh pr merge …`: nothing chained to it (`;`, `&&`, `|`), no redirection, substitution, wildcard or newline,
  and never `--admin` or `--body-file` (`-F`). It allows every such merge, in any repo the agent can reach: it
  can't see whether the person said yes, so a repo that wants to be asked first says so in its written rules,
  and the agent asks. In other permission modes it says nothing, and the usual prompt asks the person. The
  guards plugin's `no-admin-merge` refuses `--admin` on its own.
- **Merges are checked for a fresh review** (`review-check.py`). Before `gh pr merge`, with or without
  `--auto`, it reads the pull request's comments once and looks for the reviewer's comment from the `pr`
  skill's step 5: one starting with the agent line, `🤖 **Agent update**` (any agent comment counts, even a status update or a review of an older commit). With
  none, or when GitHub can't be asked within a few seconds, it warns the agent; it does not stop the merge
  (`BLOCKING` in the hook turns that on).
- **Memories are offered to the team** (`memories.py`). After Claude writes a memory
  (`~/.claude/projects/<folder>/memory/<name>.md`) that is not `type: user` and not decided, Claude ends its reply
  with a one-line offer to share it (`/add-knowledge`). At most once a day (20 hours apart), the first session to start lists memories
  still waiting, and those whose sharing pull request may have merged. `shared: keep` in a memory's frontmatter
  marks it personal; `shared: <pull request URL>` records that it was sent. Set `$WORK_MEMORY_MATCH` (a regular
  expression, case ignored, for example your team's repo names) to offer only memories whose folder name or text
  matches; unset, every project memory is offered.

## Development

```sh
uvx pytest -q tests           # the tests
prek run --all-files          # format and lint (https://github.com/j178/prek)
claude plugin validate .      # the plugin and marketplace manifests
```

Each test runs the hooks the way Claude Code runs them, with a JSON event on stdin. A refusal exits with
code 2 and writes its reason to stderr. Add a test that fails before you change a guard. The `goal` tests
build a made-up project with a local `origin` and a stand-in `gh`, so none reaches GitHub.

## Licence

MIT. See [LICENSE](LICENSE).
