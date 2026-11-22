# Things to do

Follow-ups for this repo's plugins, top first. An entry is deleted, not ticked, in the pull request that does it.

## Defects

- **The polling guard reads loop variables from text, not from the parser.** It follows a loop variable through
  assignments with a regex over quote-stripped text, so `q='$i'`, `q=\$i`, `read x <<< '$p'` and a literal
  `$1` in a single-quoted jq string count as using it; statement order is ignored (`n=$p; n=5`, or
  `set -- $p; set -- 5`); an assignment in a subshell or an uncalled function counts; `read -p prompt x`
  follows `prompt`. Loops that split an item with
  `$(echo $p | cut ...)`, `$((...))`, `export n=$p`, `GH_REPO=o/$p gh ...` or `read < <(...)` are still
  refused. Shape: have the parser record the variables each assignment and each `$1` refers to.

## Next

- **goal follows the README's link to the goals file.** `**Goals:**` is reported as a path only; the milestone
  and its finish line the linked file states are not read, so `goal` can't say when a milestone looks done.
- **goal reads a plan's ordered rows, not only todo entries.** Repos that keep their order in a plan's table
  still need a second lister and a second kind of claim. Shape: a `.claude/work.toml` setting naming the plan's
  format.
- **A warning hook on starting new work.** On a claim: warn when the repo is at its limit, when your own item
  is unfinished, or when a free defect is skipped. Warnings only.
- **A Stop check: did the session move the goal?** Compare `goal`'s answer at the start and the end of a
  session (a claim closed, a pull request merged, an entry gone); one advisory line.
- **A reader for GitHub issues on a project board**, for repos that record work there instead of a todo file.
- **A `report` skill**: progress against `goal`'s finish line, in plain words.
- **Small gaps in goal found in review.** `gh repo view` follows gh's default repo, which in a fork clone can be the
  upstream, not origin; a claimed entry whose title is edited locally reads as a mark not yet merged; The claims list ignores a custom branch prefix; the stale match finds
  a short title ("CI") inside any pull request; `goal release` compares the holder's name, not the machine; on
  Python 3.9 and 3.10 a settings value with an apostrophe is refused; a heading inside a code block is read as
  a section, in the todo file and in the README (so this repo's own README example reads as its section); bytes that aren't UTF-8 elsewhere in the todo file are rewritten by a claim; a prose formatter or
  line-length rule can move or reject the mark at the end of a long line.
