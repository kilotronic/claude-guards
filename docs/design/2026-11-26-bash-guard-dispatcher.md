# One dispatcher process per plugin for Bash calls

Status: built in the pull request that adds this note.

## The problem

Claude Code starts every hook that matches a tool call as its own process, all at once. A Bash call starts 10
Python interpreters for the guards plugin and 2 for the work plugin. Each interpreter starts, imports its
modules and reads the shared parser before it does any work, so on a loaded machine a dozen start at once and
the slowest runs out its limit. One day of transcripts had 38 `PreToolUse:Bash` timeouts at 5 s, each delaying
the agent's command by the full limit. Raising the limits to 10 s only moved the cliff.

## The standard pattern

One hook entry per event that imports every check and runs them in-process, in a fixed order: a dispatcher, as
pre-commit and lint runners do for their checks. Each plugin registers one `PreToolUse` entry for `Bash` that
runs `bash-guards.py` (guards) or `bash-hooks.py` (work). A Bash call then starts 2 interpreters, not 12.

## What stays the same

- **The guards stay separate modules.** Each keeps its file, its rule id, its `refusal` and its `__main__`, so
  each still runs alone (`guard-no-verify.py` reading an event on stdin works as before). The dispatcher lists
  them by file name in the order hooks.json ran them; a guard is added by adding it to that list.
- **Refusal text, labels and overrides.** The dispatcher calls each guard's own entry point (its `main()`, or
  `guardlib.run(RULE, refusal)` for those whose `__main__` is that line) with the event on a stand-in stdin and
  its stderr captured. Every refusal is written by the guard's own `guardlib.refuse`, so its text, its
  `[guards:<rule>]` label, the note on a locked or overridden rule, and each guard's per-command overrides and
  `.main-edit-ok` opt-out are the guard's own code, unchanged. All guards run on every call, as they did in
  parallel; when several refuse, their messages are joined in order, a blank line apart, and the dispatcher
  exits 2. Today Claude Code shows each hook's refusal separately; the agent now gets them in one message.
- **A guard that fails never blocks, and now fails alone.** Each guard runs inside its own `try`: an exception,
  or a `SystemExit` with a code other than 2, lets the command through for that guard only, and the others still
  run. Before, a crash also failed open for that guard only, since each was its own process. A crash stays
  visible: when no guard refuses, the dispatcher writes the traceback and exits 1, which Claude Code shows as a
  hook error and lets the call through, as it did for the guard alone.
- **Reading the event.** The dispatcher reads stdin once; unreadable input exits 0, as each guard did.

## What changes

- **The time budget is one limit for all.** The guards run one after another, so the dispatcher's limit is the
  sum of what they can take: 30 s for the guards (the git calls inside them have their own 5 to 10 s timeouts)
  and 15 s for the work hooks (`review-check.py`'s 9 s GitHub budget plus margin, as before). On an ordinary
  call they take tens of milliseconds together. A guard that hangs past the limit now fails open for all of them
  at once, where before it failed open for itself only; no guard has hung in the transcripts, and every
  subprocess a guard starts has a timeout except the ours-theirs guard's `git cat-file --batch` read, which the
  todo's ours-theirs entry already proposes to cap.
- **Other tools keep their own entries.** Only the Bash entry is merged. The polling guard keeps a `Monitor`
  entry and the secret guard a `Read|Grep` entry, each still one process for those tools, and the edit guard
  keeps its `Edit|Write|MultiEdit|NotebookEdit` entry.
- **The shared parser is read once per process**, since every guard imports the same `guardlib`. Its caches
  (the repo's `origin`, the overrides file) are now shared by the guards of one call. Each guard still parses
  the command itself; caching the parse is left out, since guards may change the commands they are given.

## The work plugin

The work plugin can be installed without the guards plugin, so it keeps a dispatcher of its own: `bash-hooks.py`
imports `allow-merge.py` and `review-check.py` and calls each one's `decide(event)`. Each answers with a hook
JSON object or nothing; the dispatcher merges them into one: the strictest `permissionDecision` (deny, then ask,
then allow) with its reason, and the `additionalContext` and `systemMessage` texts joined. Other top-level keys (`continue`, `stopReason`) are
not carried over when both answer, since neither hook writes them. A hook that crashes leaves its traceback on
stderr, and exits 1 when no hook answered. A `gh pr merge` in
auto mode with no review comment so gets both the allow and the warning, as it did from two processes. Each
`decide` runs in its own `try`, so a crash in one leaves the other's answer.

## Tests

The per-guard suites call each guard's own script or `main()`, which this does not change, so they keep
working as they are. The wiring tests (`test_hook_wiring.py`, and the hooks.json lookups in the work plugin's
tests) read the command lines from hooks.json; they now check that the dispatcher lists every guard, that
through its command line it refuses each guard's sample command with that guard's label and allows a harmless
one, and that a guard that raises fails open alone. New tests pin the work dispatcher's merging.

## Measured

How: every `PreToolUse` hook that matches Bash in both plugins, started at once through `sh -c` with the
event on stdin as Claude Code starts them; one call's time is until the last one exits. Sequential: calls one
after another. Under load: 8 calls at once, each timed. Numbers are in the pull request.
