---
name: long-loops
description:
  Keep a long-running loop's finished work when it is interrupted - one model call per item, a batch of
  records, a download, a migration. Use when writing or reviewing code that loops for minutes over many
  independent items, or when a run lost its work after a crash, timeout or restart.
---

# Long loops keep their finished work

A loop that runs for minutes must not hold its results only in memory and write them once at the end. The case
that taught this: a 45-minute loop of one model call per item was killed before it finished and left nothing on
disk.

Some work is already kept for you: a cache that answers a repeated model call from storage, or a queue that
holds each item until it is handled. Check what the project already has before adding a checkpoint; a team's
own rules or plugin may say. Everything else needs its own.

## The four requirements

1. **No half-written records.** Write to a temporary file and move it into place (`os.replace`), or append one
   whole line at a time. Never leave a rewrite that an interruption could cut in the middle.
2. **Save at each finished item.** Write when one item's output is complete and valid on its own (one record,
   one case, one file).
3. **Same-shaped records, valid at any count.** The saved file is a growing list of records of one shape; a
   reader must find it valid after 1 record or 1,000.
4. **Resume.** On restart, read the ids already saved, skip them, and carry on from the first missing one.

## Size matters

Rewriting the whole file after each item costs time that grows with the square of the number of items: fine
for tens or hundreds, a real cost for thousands. For a loop that may reach thousands, append one JSON line per
item (JSONL) and, if one file is wanted at the end, combine them once when the loop finishes, keeping the same
resume-by-id rule.

## When one write at the end is fine

Short work (seconds, not minutes), or output that is one indivisible document. The need grows with how long the
loop runs and how independent its items are.

## Review checklist

```text
- [ ] Work already kept elsewhere (a stored-answer cache, a durable queue) is not checkpointed twice
- [ ] One item produces one complete, valid record
- [ ] Each record is saved whole (temp file + os.replace, or one appended line)
- [ ] The saved file is valid at any count
- [ ] A restart reads the saved ids and resumes from the first missing one
- [ ] Repeating an item is safe (a crash can land between doing it and saving it)
```
