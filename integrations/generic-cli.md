# stillworks — generic agent integration

Any agent that can run shell commands can use stillworks. No API key, no
network, no framework required.

---

## The two-line instruction

Add this to your agent's system prompt or project instruction file:

```
Before editing any file where behavior must not change, run:
  stillworks lock <path/to/file.py> --fuzz 8
After editing, run:
  stillworks check
```

That is the complete integration. Exit 0 = behavior preserved. Exit 1 =
behavior changed.

---

## When the file lacks type annotations (or isn't Python)

`--fuzz` needs type annotations on function parameters. When they're missing,
or when the code isn't Python, use command-level recording instead:

```
stillworks lock --cmd "python run.py --typical-args" --cmd "python run.py --edge-case"
```

Command records capture exit code, stdout, and stderr for any executable —
any language, any runtime.

---

## Programmatic gating with --json

For agents that parse output rather than read human text:

```bash
stillworks check --json
```

Output shape:

```json
{
  "ok": true,
  "counts": { "OK": 12 },
  "results": [
    { "id": "apply_discount#1", "kind": "call", "target": "apply_discount", "status": "OK" }
  ],
  "checked": "2024-11-05T12:00:00"
}
```

Gate on `.ok`:
- `true` → all records reproduced; exit code 0
- `false` → at least one CHANGED, GONE, or BROKEN record; exit code 1

A `CHANGED` result carries `was` and `now` fields showing exactly what
differed. A `SKIP` result means the record was flagged nondeterministic at lock
time and is excluded from gating by design.

---

## Full workflow

```bash
stillworks lock src/billing.py --fuzz 8   # 1. baseline before editing
# ... agent makes changes ...
stillworks check                          # 2. gate after editing
stillworks accept apply_discount#3        # 3. bless intentional changes (if any)
stillworks report -o EVIDENCE.md          # 4. attach evidence to the PR
```

Accepting a record is a claim that the behavior change was intentional. Never
accept to make the check pass — only accept when the user asked for that change.
