# claude-guards

A [Claude Code](https://code.claude.com) plugin with hooks that stop risky git, GitHub and shell commands
before an agent runs them. Each refusal says why and what to do instead, so the agent can fix its own command
and carry on.

The guards were written for several agents working in one repository at once, each in its own git worktree.
They suit a single agent too. Note that a plain clone is its own main checkout, so the main-checkout guards
apply to it: work in a worktree (`git worktree add ../feature -b feature origin/main`), or opt the repo out
as described below.

## Install

```text
/plugin marketplace add kilotronic/claude-guards
/plugin install guards@claude-guards
```

Restart Claude Code after installing. The hooks need `python3` (3.9 or later) and `git` on `PATH`. They use
no other packages.

## What each guard does

Each guard has a rule id. It appears in its refusal as `[guards:<id>]`, and you use the id to turn the rule
off for a repo (see below). A plugin that bundles a copy of `guardlib.py` for hooks of its own gets labels
with its own name from its `plugin.json`.

| Hook                        | Rule id                    | Refuses                                                                                                                                                                                                                                                                       |
| --------------------------- | -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `guard-shared-stash.py`     | `no-commit-on-main`        | `git stash`. A repo has one stash, so agents working in parallel take each other's work.                                                                                                                                                                                      |
| `guard-hard-reset.py`       | `no-commit-on-main`        | `git reset --hard` on `main`/`master`, or over uncommitted changes.                                                                                                                                                                                                           |
| `guard-no-verify.py`        | `no-commit-on-main`        | `git commit --no-verify` with new content. Rewording or squashing commits that already passed the hooks is allowed.                                                                                                                                                           |
| `guard-commit-on-main.py`   | `no-commit-on-main`        | `git commit` in a worktree that is on `main`/`master`.                                                                                                                                                                                                                        |
| `block-main-edits.py`       | `main-checkout`            | Edit, Write, MultiEdit and NotebookEdit on files in the session's repo, in its main checkout, while it is on `main`/`master`.                                                                                                                                                 |
| `guard-branch-switch.py`    | `main-checkout`            | `git switch`, `git checkout <branch>` and `gh pr checkout` in a repo's main checkout. Going back to `main` and restoring files are allowed, and linked worktrees are not checked.                                                                                             |
| `guard-admin-merge.py`      | `no-admin-merge`           | `gh pr merge --admin`, which merges past branch protection. Plain and `--auto` merges are allowed.                                                                                                                                                                            |
| `guard-github-polling.py`   | `no-github-polling`        | `gh run watch` and `gh pr checks --watch` without `--interval 60` or more, shell loops that call `gh` again and again, and `watch gh ...`. Covers both the Bash and Monitor tools.                                                                                            |
| `guard-secret-print.py`     | `no-secret-print`          | Printing secrets into the transcript: reading `.env` files, `op read`, `gh auth token` or `security ... -w` with output to the terminal, bare `env`/`printenv`/`set`/`export`/`declare -p`, and echoing `*_TOKEN`, `*_KEY` and similar variables. Covers Bash, Read and Grep. |
| `guard-zsh-special-vars.py` | `no-zsh-special-vars`      | Setting `path`, `status`, `UID` or `GID`. In zsh, `path` is tied to `PATH`, and the others are read-only or change the user.                                                                                                                                                  |
| `guard-ours-theirs.py`      | `no-wholesale-ours-theirs` | `git checkout --ours/--theirs`, `-X ours/theirs` and `-s ours`. Each of these takes one side of every conflict at once.                                                                                                                                                       |

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
- One id can cover several guards: `no-commit-on-main` turns off the stash, hard-reset, `--no-verify` and
  commit-on-main guards together, and `main-checkout` the edit and branch-switch guards.
- A `.main-edit-ok` file at a repo's root also lets that repo edit, switch branches and commit in its main
  checkout (`block-main-edits.py`, `guard-branch-switch.py` and `guard-commit-on-main.py`).

The guards read the first of these files that is set:

1. `overrides.json` at the plugin's root. A plugin that bundles these hooks can ship its own file there.
2. The file named by `$CLAUDE_GUARDS_OVERRIDES`.
3. `~/.config/claude-guards/overrides.json`.

Only the first that is set is read: when `$CLAUDE_GUARDS_OVERRIDES` names a file that doesn't exist, the
file in `~/.config` is not read either. If no file is found, or the one found can't be read, every rule
stays on.

## Development

```sh
uvx pytest -q tests           # the tests
prek run --all-files          # format and lint (https://github.com/j178/prek)
claude plugin validate .      # the plugin and marketplace manifests
```

Each test runs the hooks the way Claude Code runs them, with a JSON event on stdin. A refusal exits with
code 2 and writes its reason to stderr. Add a test that fails before you change a guard.

## Licence

MIT. See [LICENSE](LICENSE).
