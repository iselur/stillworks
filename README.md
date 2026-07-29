# stillworks

**Prove your code still works after AI (or anyone) edits it.**

`stillworks` records what your code *actually does right now* — by running it —
and replays those recordings after changes. Same inputs, same outputs? It still
works. Different? You see exactly what changed, before it merges.

Two commands. Zero dependencies. Plain CLI, so **every coding agent can use it**
(Claude Code, Codex, OpenCode, Cursor, aider — anything that can run a shell
command). Python ≥ 3.9, stdlib only, MIT license.

```bash
pip install stillworks

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

## Why not just ask your AI to write tests?

Because the agent that wrote the change **can't grade its own homework** — and
an LLM writing tests *guesses* what the code should do. Four structural
differences, not preferences:

1. **Ground truth by execution.** `lock` runs your real code and records what
   it *does*. An LLM writing tests writes what it *believes* the code does —
   and when it hallucinates the expectation, the test passes for the wrong
   behavior. A recording can't hallucinate.
2. **Deterministic verdicts.** `check` executes and compares. Same code, same
   verdict, every run, as a CI exit code. Asking a model "does this look
   right?" gives you a different answer on different days. (In our benchmark
   of AI code review, open-ended LLM verdicts agreed with each other at
   Cohen's kappa −0.18 — coin-flip territory — while executed probes hit
   +0.65.)
3. **Independent of the agent.** The tool doesn't know which agent made the
   change, and the agent can't talk it out of a red verdict. Judge and
   defendant are different processes.
4. **Disposable scaffolding.** Legacy code without tests? Lock it, do the risky
   migration, check, delete the lockfile. No test suite to maintain forever —
   the lockfile is scaffolding you keep only as long as the renovation runs.

## Three ways to capture behavior

| mode | command | best for |
|---|---|---|
| **Fuzz** | `stillworks lock src/mod.py --fuzz 8` | annotated Python functions — generates seeded inputs automatically |
| **Record a run** | `stillworks lock src/mod.py --run scripts/daily.py` | real usage — records every call your script makes into the module |
| **Commands** | `stillworks lock --cmd "python report.py 2024" --cmd "make summary"` | **any language** — records exit code, stdout, stderr |

Modes combine. Exceptions are recorded as behavior too: if `divide(1, 0)`
raises `ZeroDivisionError` today, a refactor that silently returns `0` is a
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
trust the merge.

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

## Honest limits (v0.1)

- Function recording targets **module-level Python functions**. Methods and
  class-heavy code: use `--cmd` probes (they work for anything executable).
- `--fuzz` needs type annotations on parameters; unannotated functions are
  skipped (use `--run` or `--cmd` instead).
- Arguments are pickled for replay; exotic unpicklable inputs are counted and
  skipped, not silently dropped.
- stdout of recorded *function calls* isn't captured (command records capture
  it fully).

## What stillworks is not

Not a test framework (no assertions to write), not a security scanner, not an
LLM product (it never calls a model, needs no API key, sends nothing anywhere).
It does one thing: **prove behavior didn't change when it wasn't supposed to.**

## License

MIT. Contributions welcome — especially capture modes for more languages.
