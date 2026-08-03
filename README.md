# stillworks

**Characterization testing with no test code to write.**

`stillworks` runs your code, records what it returns, and replays those
recordings after you change it. Same inputs, same outputs? It still works.
Different? You see exactly what changed, before it merges.

It is scaffolding, not a test suite — the point is that you can put a
behavior gate around untested code in about thirty seconds, and throw it away
when the risky change is done.

Two verbs: `lock` and `check`. Zero dependencies. Plain CLI, so **every coding
agent can use it** (Claude Code, Codex, OpenCode, Cursor, aider — anything
that can run a shell command). Python ≥ 3.9, stdlib only, MIT license.

```bash
pip install git+https://github.com/iselur/stillworks

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

Often, yes. If you know what the code is *supposed* to do, write `pytest` —
with `approvaltests`, `syrupy`, or `pytest-regressions` for the snapshot part.
Real tests express intent, target boundaries, mock dependencies, and
distinguish *changed* from *wrong*. A lockfile can't do any of that. And yes,
an LLM can write those tests for you, and can verify them by running the code
— that is a genuine option, not a trap.

`stillworks` is for the case *before* that:

- **The code has no tests and you're about to change it.** Lock it, do the
  migration, check, delete the lockfile. Nothing to maintain afterwards.
- **You don't yet know what it's supposed to do.** Pinning current behavior is
  the cheapest way to find out whether your change moved something.
- **It isn't Python, or isn't importable.** `--cmd "make report"` records exit
  code, stdout and stderr for anything you can run.
- **You want a gate right now.** No test framework, no fixtures, no
  dependencies, one command, and a CI exit code.

**What it does not claim.** A lockfile records what your code *did*, not what
it *should* do — bugs get pinned along with everything else, and `check` going
green never means "correct", only "unchanged". Running the code is available
to a test suite and to an agent with a shell just as much as it is to
`stillworks`; the only thing this tool really buys you is that there is no
test code to write and no harness to keep alive.

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
  lockfile like a fixture: don't lock functions whose arguments are secrets,
  and don't `check` lockfiles from sources you don't trust (unpickling runs
  code).
- stdout of recorded *function calls* isn't captured (command records capture
  it fully).

## What stillworks is not

Not a test framework and not a replacement for one — if the code is going to
live a long time, it deserves tests that say what it *should* do. Not a
security scanner. Not an LLM product (it never calls a model, needs no API
key, sends nothing anywhere). It does one thing: **catch behavior changes you
didn't intend, on code that has nothing else guarding it.**

## Part of a small family

Four tools for working with coding agents, same house style: zero dependencies,
MIT, no API key, nothing leaves your machine. None of them call a model — that is
the point, since the thing being checked already is one.

- [stillworks](https://github.com/iselur/stillworks) — record what your code does now, catch when it changes later  ← you are here
- [agentdiff](https://github.com/iselur/agentdiff) — see what the agent actually changed, before you merge
- [agentlog](https://github.com/iselur/agentlog) — what did your coding agent actually do today?
- [unedit](https://github.com/iselur/unedit) — a safety net for letting an agent loose on your files

## License

MIT. Contributions welcome — especially capture modes for more languages.
