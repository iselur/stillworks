# stillworks

**Take a snapshot of what your code does. After you change it, see if anything
moved.**

You have to edit a file that has no tests. Afterwards, how do you know you only
changed the thing you meant to change?

`stillworks lock` runs your code and writes down what it gives back.
`stillworks check` runs it again after your edit and tells you if any answer
is different. That's the whole idea.

It's a safety net for one risky change, not a test suite you keep. Set it up in
under a minute, delete it when you're done.

Two verbs: `lock` and `check`. Zero dependencies. Plain CLI, so **every coding
agent can use it** (Claude Code, Codex, OpenCode, Cursor, aider — anything
that can run a shell command). Python ≥ 3.9, stdlib only, MIT license.

```bash
pip install 'stillworks[all]'   # all five agent tools (see below)
pip install stillworks          # or just this one, zero dependencies

stillworks lock src/pricing.py --fuzz 8   # before: record real behavior
# ... let your AI agent refactor pricing.py ...
stillworks check                          # after: did behavior change?
```

```
CHANGED  apply_discount#3  (apply_discount)
         args: ((100.0, 'GOLD'), {})
         was:  85.0
         now:  90.0
BEHAVIOR CHANGED: 24 records — 1 CHANGED, 23 OK
```

Exit code `1` — the merge gate closes. If the change was intentional:
`stillworks accept apply_discount#3`, and it becomes the new baseline.

## Should you just write tests instead?

Often, yes — and you should. A real test suite (`pytest`, plus `approvaltests`,
`syrupy` or `pytest-regressions` for snapshots) says what the code is *meant*
to do. That's more valuable than what it *happens* to do today, and it's worth
keeping around. If you can write those tests, or have an AI write them for
you — that works, and it beats this tool.

Use `stillworks` when you're not there yet:

- **The code has no tests and you're changing it today.** Snapshot it, make the
  change, check, delete the snapshot. Nothing left to maintain.
- **You don't actually know what it's supposed to do.** Nobody does; the person
  who wrote it left. What it does now is the only thing you can hold on to.
- **It's not Python, or you can't import it.** `--cmd "make report"` works on
  anything you can run from a terminal.
- **You want the check working in the next minute.** No test framework, no
  setup, one command.

**What it does not promise.** It records what your code *did*, not what it
*should* do. If the code has a bug today, the snapshot keeps the bug. A green
`check` means *nothing moved* — it never means *this is correct*. And it has no
special magic: anything can run your code and compare, including you and
including an AI. What you're buying here is that there's no test code to write.

## Three ways to capture behavior

| mode | command | best for |
|---|---|---|
| **Sampled inputs** | `stillworks lock src/mod.py --fuzz 8` | annotated Python functions — seeded inputs, including the literals your own branches compare against |
| **Record a run** | `stillworks lock src/mod.py --run scripts/daily.py` | real usage — records every call your script makes into the module |
| **Commands** | `stillworks lock --cmd "python report.py 2024" --cmd "make summary"` | **any language** — records exit code, stdout, stderr |

Modes combine — in a **single** `lock` invocation (`lock` replaces any
existing baseline and warns when it does):

```bash
stillworks lock src/mod.py --fuzz 8 --run scripts/daily.py --cmd "make summary"
```

Exceptions are recorded as behavior too: if `divide(1, 0)` raises
`ZeroDivisionError` today, a refactor that silently returns `0` is a
**CHANGED**, not a pass.

Nondeterministic functions (time, random, network) are detected at lock time —
each record is replayed immediately, and anything that doesn't reproduce is
flagged and excluded from gating rather than becoming a flaky test.

## The workflow with a coding agent

```bash
stillworks lock src/billing.py --run scripts/month_end.py   # 1. baseline
# 2. "hey Claude, refactor billing.py to use the new tax API"
stillworks check                                            # 3. gate
stillworks accept tax_total#2                               # 4. bless intended diffs
stillworks report -o EVIDENCE.md                            # 5. attach to the PR
```

The report is a human-readable evidence document: what was locked, what
reproduced, what changed and who accepted it — for the reviewer who has to
trust the merge. (`report` without `-o` prints to stdout.) All commands take
`--project DIR` to operate on another directory.

## For coding agents: CLI, skill, or MCP

- **CLI (recommended):** it's just a shell command — every agent already knows
  how to use it. Tell your agent: *"use `stillworks lock` before editing and
  `stillworks check` after."*
- **Claude Code skill:** copy `skill/` into `.claude/skills/stillworks/` and
  the agent locks/checks automatically around risky edits.
- **MCP server:** `stillworks mcp` serves the four operations over stdio for
  agents that prefer tools to shells. Zero-dependency, subprocess-isolated.

```json
{ "mcpServers": { "stillworks": { "command": "stillworks", "args": ["mcp"] } } }
```

## What else is installed

```
$ stillworks tools
  stillworks  0.1.3  record what your code does now, catch when it changes
  unedit      0.1.3  a safety net for letting an agent loose on your files
  agentdiff   —      see what the agent actually changed, before you merge
  agentlog    0.2.2  what did your coding agent actually do today?
  agentwatch  0.1.0  tail what your agent is doing, right now

  missing: agentdiff
  install:  pip install agentdiff-cli
  or all five:  pip install 'stillworks[all]'
```

It finds the others on your PATH and asks each for its version — it never
imports them, so the extra stays genuinely optional and each tool keeps its own
release cycle. Always exits 0; it reports, it does not judge. `--json` for
scripts.

No pip available (managed environments, PEP 668)? It's stdlib-only, so a
checkout works as-is:

```bash
git clone https://github.com/iselur/stillworks && PYTHONPATH=stillworks python3 -m stillworks --help
# or: pipx install stillworks
```

## Prior art (and what's different)

The idea is **characterization testing** — Michael Feathers, *Working
Effectively with Legacy Code* (2004): when code has no tests, record what it
does and pin that. Snapshot-testing libraries like `approvaltests`, `syrupy`,
and `pytest-regressions` do this well *inside a test suite you write*, and
they are the better tool once that suite exists — they give you names,
fixtures, and intent alongside the snapshots.

`stillworks` differs in one deliberate way: **there is no test code to
write**. It captures behavior from annotations, from a real script run, or
from shell commands (any language), compares with one CLI verb, and needs no
test framework, no server, and no dependencies — which is exactly what a
coding agent, or a human mid-refactor, can use in thirty seconds. That is a
convenience difference, not a stronger guarantee: a snapshot test asserting
the same recorded values is worth exactly as much.

## Honest limits (v0.1)

- Function recording targets **module-level Python functions**. Methods and
  class-heavy code: use `--cmd` probes (they work for anything executable).
- `--fuzz` is seeded sampling, not coverage-guided fuzzing. It needs
  positional parameters annotated with `int`/`float`/`str`/`bool`/`list`/
  `dict`. Unannotated params, `Optional`/`Union`/`Literal`/`Enum`/custom
  types, and functions with required keyword-only params are skipped —
  and named in the output, with a hint to use `--run` or `--cmd`.
- Default parameter values are not exercised by `--fuzz`; a behavior change
  hiding behind a default only shows up via `--run` or `--cmd` capture.
- Functions returning generators/iterators are compared by materializing the
  first 200 items during `--fuzz`/`check`; during `--run` recording they are
  skipped (the iterator must reach your script unconsumed).
- `lock` and `check` **execute your code** — functions with side effects
  (writes, sends, charges) run once per record per verb. Point it at pure or
  read-only code paths, or use `--cmd` against a sandbox.
- Arguments are pickled into `.stillworks/lock.json` for replay; exotic
  unpicklable inputs are counted and skipped, not silently dropped. Treat the
  lockfile like a fixture: don't lock functions whose arguments are secrets.
- **A lockfile is executable, the same way a Makefile is.** It ships in the
  repo, and `check` re-runs what it names: `--cmd` records are shell commands
  stored verbatim, and unpickling arguments runs code too. So `stillworks
  check` on a repo you just cloned is `make` on a repo you just cloned — read
  `.stillworks/lock.json` first if you would not run its `Makefile`.
- stdout of recorded *function calls* isn't captured (command records capture
  it fully).

## What stillworks is not

Not a test framework and not a replacement for one — if the code is going to
live a long time, it deserves tests that say what it *should* do. Not a
security scanner. Not an LLM product (it never calls a model, needs no API
key, sends nothing anywhere). It does one thing: **catch behavior changes you
didn't intend, on code that has nothing else guarding it.**

## Part of a small family

Five tools for working with coding agents, same house style: zero
dependencies, MIT, no API key, nothing leaves your machine. None of them
call a model — that is the point, since the thing being checked already is
one.

- [stillworks](https://github.com/iselur/stillworks) — record what your code does now, catch when it changes later  ← you are here
- [agentdiff](https://github.com/iselur/agentdiff) — see what the agent actually changed, before you merge
- [agentlog](https://github.com/iselur/agentlog) — what did your coding agent actually do today?
- [agentwatch](https://github.com/iselur/agentwatch) — tail what your agent is doing, right now
- [unedit](https://github.com/iselur/unedit) — a safety net for letting an agent loose on your files

One install gets all five, and `stillworks tools` says which ones you have:

```sh
pip install 'stillworks[all]'
stillworks tools
```

## License

MIT. Contributions welcome — especially capture modes for more languages.
