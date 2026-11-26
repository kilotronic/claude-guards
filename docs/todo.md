# Things to do

Follow-ups for this repo's plugins, top first. An entry is deleted, not ticked, in the pull request that does it.

## Defects

- **The no-verify guard doesn't see the hooks path set through the environment.** `GIT_CONFIG_COUNT=1
GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null git commit -m x` and
  `git --config-env=core.hooksPath=HP commit` commit with no hooks and are allowed: `bypass()` reads only `SKIP=`
  in the environment and `-c` in git's options. Found reviewing #53; none seen in a real session. Shape: read
  `GIT_CONFIG_KEY_<n>` and `--config-env` as `-c` is read.

- **The stash guard calls `git rebase --autostash` safe, but a conflict puts the changes in the shared stash.**
  Its refusal says the autostash "lives in this worktree's own rebase dir, so it cannot race". That holds
  until the changes fail to re-apply after the rebase: git then keeps them as `stash@{0}: autostash` in
  `refs/stash`, the one ref every worktree shares, and tells you to `git stash pop` them or `git reset --hard`,
  both of which guards refuse. Seen 2026-11-25, rebasing uncommitted work over the claim commit, which changed
  the same `docs/todo.md` lines. Shape: advise committing first (a work-in-progress commit, or a patch) instead
  of `--autostash`.

- **The stash guard doesn't say why a `no-commit-on-main` override was ignored.** With `no-stash` locked, a
  repo's `no-commit-on-main` override no longer turns the guard off, but the refusal's note only reads
  `no-stash`, so it gives no reason. And a repo that turns `no-commit-on-main` off can keep the stash guard
  on only by locking `no-stash` for every repo. Shape: a note naming the old id.

- **The ours-theirs guard's binary exception has narrow gaps.** A merge driver set by `merge.default` in git
  config shows as `unspecified`, so the file is judged by its content; a text conflict whose markers were
  removed by hand and whose path was then marked `binary` passes; and the marker scan reads each named file
  to the end, so a multi-GB file on a slow disk could run the hook past its time limit, which lets the
  command through. Shape: read `merge.default`, and cap the scan.

- **The polling guard reads loop variables from text, not from the parser.** It follows a loop variable through
  assignments with a regex over quote-stripped text, so `q='$i'`, `q=\$i`, `read x <<< '$p'` and a literal
  `$1` in a single-quoted jq string count as using it; statement order is ignored (`n=$p; n=5`, or
  `set -- $p; set -- 5`); an assignment in a subshell or an uncalled function counts; `read -p prompt x`
  follows `prompt`. Loops that split an item with
  `$(echo $p | cut ...)`, `$((...))`, `export n=$p`, `GH_REPO=o/$p gh ...` or `read < <(...)` are still
  refused. Shape: have the parser record the variables each assignment and each `$1` refers to.

## Next

- **Park's loose ends.** A file the agent already staged still goes into the park commit, past the secret scan;
  `park resume` says "is yours" even when taking the label off fails (no write access); a new folder counts as one
  untracked file. Shape: commit only tracked paths (`git commit -- $(git diff --name-only HEAD)`), check the label
  edit's exit code, count with `git ls-files --others --exclude-standard`.
- **`next-item`'s row reading is quiet about slips.** A `rows` folder that doesn't exist shows no rows and no warning; a
  `Waits on` part that isn't a number or a span is skipped; a phase mapped to the defects heading goes last; a bad
  claim date on a row names the todo file, not the row file. Shape: name each as unclear or a warning.
- **The daily memory list can be used up by a session nobody reads.** A scripted `claude -p` run or a scheduled
  agent starts a session too, so it can take the day's list for about 20 hours. Shape: skip the list when the
  session isn't interactive, if the hook's input can tell.
- **The merge review check counts any agent comment, of any commit.** A review of an older commit, or a plain
  status update starting with the agent line, satisfies it, so a push after the review merges with no warning.
  Commits after the review are allowed when they only fix its findings, so requiring the head commit would
  warn on those too. Shape: give the reviewer's comment a mark of its own (a second line
  `**Fresh review** of <commit>`, written by the `pr` skill's step 5), and warn when commits after that one
  change more than the files its findings named.
- **The merge review check reads some commands wrongly.** It only sees a merge at the start of a command, so a
  merge after `for`/`while … do`, `if … then`/`else`/`elif`, `{` or `!`, or behind `time`, `timeout`, `sudo`,
  `bash -c` or `env -C DIR` (which checks the wrong pull request without saying so, so first) is missed;
  `export GH_REPO=` is ignored; `-Rowner/repo` (value attached) is not read as the repository, and in
  `-dt Release 9` the value of `-t` is taken as the pull request. A heredoc's body is read as commands (a false
  warning, or an apostrophe in it hides the real merge after it), and so is an apostrophe in a comment. A
  comment whose body is null stops the check without a word (`None.lstrip()`). Reviews posted with
  `gh pr review` are not read. Shape: read commands with the guards' shared parser, which knows loops,
  heredocs, comments and wrappers.
- **The merge-permission hook refuses some ordinary merges.** It looks for unsafe characters before reading
  quotes, so a subject like `--subject "feat: x (#17)"` gets no allow; and a short flag with its value
  attached that contains an F (`-RAcmeFoo/app`, `-tFix`) reads as `-F`. Both fail safe: auto mode blocks the
  merge as it would without the hook. Shape: read the words first, then check each unquoted part.
- **next-item follows the README's link to the goals file.** A `**Current milestone:**` linking a file is reported as a path only; the milestone
  and its finish line the linked file states are not read, so `next-item` can't say when a milestone looks done.
- **next-item reads a plan's ordered rows, not only todo entries.** Repos that keep their order in a plan's table
  still need a second lister and a second kind of claim. Shape: a `.claude/work.toml` setting naming the plan's
  format.
- **A warning hook on starting new work.** On a claim: warn when the repo is at its limit, when your own item
  is unfinished, or when a free defect is skipped. Warnings only.
- **A Stop check: did the session move the goal?** Compare `next-item`'s answer at the start and the end of a
  session (a claim closed, a pull request merged, an entry gone); one advisory line.
- **A reader for GitHub issues on a project board**, for repos that record work there instead of a todo file.
- **A `report` skill**: progress against the milestone's finish line, as `next-item` reads it, in plain words.
- **Stop reading claim marks on the default branch.** They hold only so that claims made before claims moved
  to pull requests survive the upgrade. Once none are left, drop them, their warning and `next-item release
--holder`, so open pull requests are the one place claims are read. `claims_from` in `--json` names the
  default branch, where it now means the branch the pull requests merge into.
- **Sessions sharing a name in the main checkout share their claims.** Off a work branch, `next-item` decides whose
  claim it is by name, so two subagents of one session (one `session-` label) starting there are each told to
  "finish" the first one's item. Shape: say in the README and the next skill that parallel sessions each set
  their own `--as` or `$GOAL_AS`; have the "finish" answer give the pull request's real branch (`Claim.head`),
  not one built from the title; test the detached-HEAD case; drop the comment in `claim` that repeats
  `Me.holds`.
- **Small gaps in next-item found in review.** `gh repo view` follows gh's default repo, which in a fork clone can be the
  upstream, not origin; a claimed entry whose title is edited locally reads as a mark no pull request carries;
  claims are read from the first 200 open pull requests only; a branch that deleted a claimed entry is read back
  only 20 commits; a pull request stacked on another work branch counts as a second claim on its item; on the
  work branch after finish-work deletes the entry, `next-item` no longer answers "finish" for it; a claim is matched to
  the local branch's name, so a local branch pushed under another name doesn't see its own pull request; `next-item release` compares the holder's name, not the machine; on
  Python 3.9 and 3.10 a settings value with an apostrophe is refused; a heading inside a code block is read as
  a section, in the todo file and in the README (so this repo's own README example reads as its section); bytes that aren't UTF-8 elsewhere in the todo file are rewritten by a claim; a prose formatter or
  line-length rule can move or reject the mark at the end of a long line.
