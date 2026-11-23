# claude-guards

Two [Claude Code](https://code.claude.com) plugins for agents working in a repository, often several at once:

- **guards**: hooks that stop risky git, GitHub and shell commands before an agent runs them. Each refusal says
  why and what to do instead, so the agent can fix its own command and carry on.
- **work**: the `goal` command, which answers "what should I work on next?" from what the repo states in
  writing, and the skills that claim an item, build it and land it. See [The work plugin](#the-work-plugin).

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
- **Claim before starting:** `goal claim '<the entry's title>'`
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
merge_method = "squash"               # how claim pull requests merge: squash, rebase or merge
branch_prefix = "work/"               # the work branch is <prefix><slug of the title>
```

### Claims live in the todo file

A claim marks the end of the entry's first line with who has it, merged to the default branch before the work
starts.
The entry still starts with its bold lead, so a repo's own checks of its entries read it as before:

```markdown
- **Add a snooze button.** Five more minutes. [claimed by session-1a2b3c4d on laptop, 2026-11-22]
```

- `goal claim '<title>'` opens a pull request touching only that line, on a `claim/<slug>` branch, and arms
  auto-merge. The repo's normal review rules and branch protection apply to it. The claim holds once it
  merges.
- **Git is the lock.** Two claims edit the same line, so once one merges the other conflicts and can't. A
  second claim while the first is open is refused at once, since `claim/<slug>` already exists. Git also
  treats edits on neighboring lines as a conflict, so a claim on the next entry can block yours: run
  `goal claim` again, and it rebuilds your claim on the new main while the item is still free there, opening
  a new pull request if the old one is gone.
- **Release:** finishing the work deletes the entry in the work's own pull request; abandoning it runs
  `goal release '<title>'`, which opens the pull request that removes the mark.
- **Claims are read from origin.** A mark is only real once merged, so `goal` reads marks from origin's default
  branch, found by asking GitHub (`gh repo view`), not from the local `origin/HEAD`, which can be stale. A mark
  that exists only in your working copy is reported as not yet merged and is not counted. If GitHub can't be
  reached, `goal` says claims weren't checked and answers from the local files.
- **In progress** is the number of claim marks, so it is stated in one place.
- **Stale:** a claim with no open pull request naming the item and no commit on a branch named for it for 3
  days is flagged by `goal`. It is never removed by itself.

Who you are comes from `--as`, else `$GOAL_AS`, else the Claude Code session, plus the machine's name
(`$GOAL_MACHINE` overrides it). With none of these, every session of the login shares one name, so
`goal claim` refuses and `goal` warns. A session label changes with each session, so to keep a claim yours across
sessions, set a stable `$GOAL_AS`. The mark is public wherever the todo file is: in a public repo, set
`$GOAL_MACHINE` and `$GOAL_AS` to names you are happy to publish.

`goal merged <branch>` says whether a branch's work is on main: every commit there as the same patch, or a
merged pull request ending at the branch's tip, as a squash merge leaves it.

### The skills

| Skill          | Does                                                                                                                  |
| -------------- | --------------------------------------------------------------------------------------------------------------------- |
| `orient`       | What changed on main, `goal --list`, who else is working, open pull requests, one recommendation. Reads only          |
| `next`         | Takes `goal`'s item and does it, start to merged, through `work-start` and `work-finish`                              |
| `work-start`   | Proves the item is still open, claims it, makes the worktree and branch, posts the standup step, says what done means |
| `work-finish`  | Removes the entry in the work's pull request, files follow-ups, runs the gates; after merge, cleans up                |
| `branch-prune` | Deletes branches and worktrees whose work is merged, by the same test as `goal merged`. Dry run by default            |

The skills post their progress with [standup](https://github.com/kilotronic/standup-board) when it is installed.

### The hooks

- **Agents may merge pull requests** (`allow-merge.py`). Claude Code's auto mode refuses `gh pr merge` even
  after the person said yes, so this hook allows a Bash call that is only `gh pr merge …`: nothing chained to
  it (`;`, `&&`, `|`), no redirection, substitution, wildcard or newline, and never `--admin` or `--body-file`
  (`-F`). Anything else gets no answer and the normal permission check runs. When to ask before merging is
  still each repo's rule; the guards plugin's `no-admin-merge` refuses `--admin` on its own.

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
