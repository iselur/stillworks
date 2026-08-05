# CONTEXT

The words this family uses for the things it deals with, and what each one
means here rather than in general. If a name in the code is not in this file,
either the name is wrong or this file is out of date; both are worth fixing.

This is the **domain** vocabulary. The **architecture** vocabulary — module,
interface, implementation, depth, seam, adapter, leverage, locality — is the
`/codebase-design` glossary and is not repeated here.

## The family

**Family** — the five packages that ship as one wheel: `stillworks`, `unedit`,
`agentdiff`, `agentlog`, `agentwatch`. `pip install stillworks` installs all
five console scripts. Each has its own repository, which is canonical; the copy
inside this repository is vendored and pinned byte-identical to it.

**Copied, not shared** — no package in the family imports another. That is the
promise the one wheel makes, and `test_every_import_is_stdlib_or_the_packages_own`
enforces it. So code two packages both need is not extracted into a shared
package; it is copied whole and pinned byte-identical by a contract test.
`shell.py` is in all five that way; `terminal.py` is in all five; `transcript.py`
is in two.

**The shell around a command** (`shell.py`) — the part of a command line that
is the same in every command line: reconfigure the streams if the locale
claimed ASCII, flush before leaving, turn ctrl-c into 130 and a closed pipe
into 141. `main()` returns its exit code rather than raising it.

**Text an agent wrote** — a filename, a command, a commit subject, a snapshot
message, a locked target. Every tool here prints some, and in four of the five
it comes out of a file the thing being audited is free to rewrite. It is never
assumed to be text: it is whatever was on disk.

**What a terminal obeys rather than shows** (`terminal.py`) — the characters a
terminal acts on instead of drawing, and what each tool does about them. Four
Unicode categories: `Cc`, where the escapes live; `Cf`, where the bidi overrides
live; `Zl` and `Zp`, the two separators that end a line for a reader and not for
`str.splitlines`. Six answers, and which one a column wants is that column's
business:

- **`one_line`** — every such character becomes a space. A space and not
  nothing: deleting fuses two components of a path into one word that is not on
  disk, and it takes back a cell the table already measured, so that row alone
  stands a cell left of every other.
- **`block`** — the same, but newlines and tabs stay. For a diff or a rendered
  table, which are many lines by definition.
- **`row`** — `one_line`, then bounded, and it says how much it is not showing.
  Blanking first, because 400 characters cut off the front of a value can still
  hold a newline.
- **`quoted`** — escaped rather than blanked, git's spelling, in quotes. For
  text naming something the reader has to go and find: a path with a space where
  a control character was is not a path on disk either.
- **`display_width`** and **`pad`** — a width in cells rather than characters,
  because a name in Japanese is drawn twice as wide as `len` says and `ljust`
  puts the next column somewhere else.


## stillworks — behaviour, before and after

**Record** — one thing the code was observed to do: a call with its arguments
and what came back, or a command with its output. Records are what a lockfile
is made of.

**Lockfile** — the file under `.stillworks/` holding the records, the module
they came from, and the history. Written by `lock`, read by `check` and
`status`, added to by `accept`.

**Baseline** — the records a `check` compares today's run against. A baseline
can be of a module, of commands, or of both.

**Fuzzing** — making up inputs for a function from the type annotations on its
parameters. A function with no annotations cannot be fuzzed, which is why
`--fuzz` can come back with nothing to record.

**Nondeterministic record** — a record flagged at lock time because running it
twice gave two answers. It is kept and marked, not discarded: the point is that
a later `check` should not call it a change.

**Partial** — a lock whose driver script (`--run`) did not finish. The records
made before it died are kept, and the lockfile says it is partial, because a
baseline that stops halfway is still a baseline as long as everyone knows.

**Accepting** — recording that a change was intended. The old answer moves into
the lockfile's history and the new one becomes the baseline.

## unedit — the working tree, before and after

**Snapshot** — a copy of the working tree at a moment, stored
content-addressed under `.unedit/`: each distinct file body kept once, with a
manifest per snapshot naming the paths.

**Aside** — where files that exist now but did not exist in the snapshot are
moved when you step back to it. Restoring never deletes; it sets aside.

## agentdiff — what an agent changed

**FileChange** — one file's worth of a diff, as the rules see it.

**Rule** — a deterministic check over a FileChange that returns findings. No
model, no network, no heuristics that guess.

**Finding** — one thing a rule wants a human to look at. When in doubt a rule
returns nothing: a false positive costs more than a miss for a tool people have
to trust before they will trust the agent.

## agentlog and agentwatch — what an agent did

**Session** — one run of a coding agent, as one JSONL file on disk. Claude Code
and Codex each write one, in different formats.

**Transcript** — the two session formats, and the facts of reading them:
how a stamp is written, which tool calls touch a file and where the path is,
which Codex calls are work rather than chatter, how an exec snippet carries its
commands and working directory and patch envelope, and how a call says it
failed. It knows nothing about what any of that means. It is one module,
copied into both packages that read a session.

**The two views** — `agentlog` reads a finished session and says what happened
in it; `agentwatch` tails a live one and says what is happening now. Same two
formats, two different questions, and they stay two.

**Event** (agentwatch) — one line of the live stream, of one **kind**: `turn`,
`cmd`, `write`, `read`, `error`. Reads are excluded from the default view, on
the grounds that a stream that is ninety per cent reads is a stream nobody
watches.

**Digest** (agentlog) — the report about a window: what ran, what was written,
what it cost.

**Window** — the stretch of time a digest is about: `today`, `week`,
`since 3d`, `on 2026-07-31`. A window parses an argument, labels itself, and
clips sessions down to what they did inside it.

**Unparseable** — what `Window.parse` returns instead of a window when the
argument was not one. A wrong date is an answer to be printed, not an
exception to be caught somewhere else.

## Words we do not use

- **"Test"** for what stillworks writes. It records behaviour; it does not
  assert intent. Calling a lockfile a test suite is how people end up keeping
  one.
- **"Baseline"** for a snapshot, or **"snapshot"** for a lockfile. One is
  behaviour, the other is file contents.
- **"Log"** for a session file when the reading of it is the subject. The file
  is a **session**; the format is the **transcript**.
- **"Event"** in agentlog. agentlog has records of a session; events are
  agentwatch's, and they have kinds.
