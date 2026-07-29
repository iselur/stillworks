---
name: stillworks
description: Verify that code behavior didn't change after edits. Use when refactoring, migrating, or making "should not change behavior" edits — especially in code without tests. Records real behavior before the edit (stillworks lock), replays it after (stillworks check), and produces an evidence report.
---

# stillworks — behavior lock for risky edits

You are editing code where behavior must not change (refactor, dependency
bump, migration, cleanup), or where there are no tests to catch a mistake.
Use the `stillworks` CLI to record behavior before you edit and verify it
after. It executes the code — the verdict is deterministic, not an opinion.

## When to use

- Refactoring or restructuring code that has no (or thin) test coverage
- "This change should not alter behavior" edits
- Before/after dependency or API migrations
- Any legacy/brownfield change the user is nervous about

Skip it for brand-new code with no existing behavior to preserve.

## Procedure

1. **Before editing**, lock the current behavior of the file(s) you will touch:

   ```bash
   stillworks lock path/to/module.py --fuzz 8
   ```

   - If the module's functions lack type annotations, or the logic is
     class-heavy, record command-level behavior instead (works for any
     language):

     ```bash
     stillworks lock --cmd "python path/to/entry.py --typical-args" --cmd "..."
     ```

   - If there is a script or entry point that exercises the module with real
     inputs, prefer recording a run:

     ```bash
     stillworks lock path/to/module.py --run scripts/real_usage.py
     ```

   - Confirm the lock output: `locked N records`. If it captured 0 or very
     few records, add `--cmd` probes before proceeding — a thin lock proves
     little.

2. **Make your edits** as usual.

3. **After editing**, verify:

   ```bash
   stillworks check
   ```

   - Exit 0 / `STILL WORKS`: behavior preserved. Say so and show the record
     count as evidence.
   - Exit 1 / `CHANGED` or `GONE` lines: behavior differs. For each diff,
     decide honestly:
     - **Unintended** → it's a bug in your edit. Fix the code, re-run
       `stillworks check`. Do NOT accept the change to make the gate green.
     - **Intended** (the user asked for this behavior change) → bless it:
       `stillworks accept <id>` (or `--all` if every diff is intended).

4. **Report.** Attach evidence to your summary or the PR:

   ```bash
   stillworks report -o STILLWORKS-EVIDENCE.md
   ```

5. **Cleanup.** If this was one-off scaffolding for a migration, you may
   delete `.stillworks/` after the merge. Keep it if more risky edits are
   coming.

## Rules

- Never `accept` a diff just to make the check pass — accepting is a claim
  that the user wanted that behavior change. When unsure, show the diff to
  the user and ask.
- Lock BEFORE editing. A lock taken after the edit records the new behavior
  and proves nothing.
- Records flagged `SKIP (nondeterministic)` are excluded from the gate by
  design — mention them in your summary, don't fight them.
- `stillworks check --json` gives machine-readable results if you need to
  parse statuses.
