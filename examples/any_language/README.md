# Example: --cmd mode (any language)

`--cmd` records the exit code, stdout, and stderr of any shell command.
No Python required in the target code: bash scripts, awk/sed pipelines,
Go binaries, Rust CLIs — anything the shell can run.

This example uses a small bash log-summariser to show the full
lock → edit → catch workflow.

---

## The files

| file | what it is |
|---|---|
| `log_summary.sh` | shell script: counts log lines by level, shows top messages |
| `sample.log` | 21-line application log used as input |

Run the script directly to see what it produces:

```
bash log_summary.sh sample.log
```

Output:

```
=== Log summary: sample.log ===
Total lines: 21

Lines by level:
  INFO     14
  WARN     4
  ERROR    3

Top 3 messages:
  (1) request accepted: GET /api/status
  (2) connection refused: db host=db01 port=5432
  (3) disk at 80% capacity
```

---

## Step 1 — lock the command output

```
PYTHONPATH=/path/to/stillworks python3 -m stillworks lock \
    --cmd "bash log_summary.sh sample.log"
```

Real output:

```
locked 1 records (0 calls, 1 commands) -> .stillworks/lock.json
```

Confirm it is green:

```
PYTHONPATH=/path/to/stillworks python3 -m stillworks check
```

Real output:

```
STILL WORKS: 1 records — 1 OK
```

---

## Step 2 — edit the script

A colleague changes the script to show the top 5 messages instead of
top 3 (editing both the header string and the `head -3` call).

The new script output is:

```
=== Log summary: sample.log ===
Total lines: 21

Lines by level:
  INFO     14
  WARN     4
  ERROR    3

Top 5 messages:
  (1) request accepted: GET /api/status
  (2) connection refused: db host=db01 port=5432
  (3) disk at 80% capacity
  (4) worker pool ready (4 workers)
  (5) server started on port 8080
```

---

## Step 3 — check catches the output change

```
PYTHONPATH=/path/to/stillworks python3 -m stillworks check
```

Real output (exit code 1):

```
CHANGED  cmd#1  (bash log_summary.sh sample.log)
         stdout changed:
           was: '=== Log summary: sample.log ===\nTotal lines: 21\n\nLines by level:\n  INFO     14\n  WARN     4\n  ERROR    3\n\nTop 3 messages:\n  (1) request accepted: GET /api/status\n  (2) connection refused: db host=db01...'
           now: '=== Log summary: sample.log ===\nTotal lines: 21\n\nLines by level:\n  INFO     14\n  WARN     4\n  ERROR    3\n\nTop 5 messages:\n  (1) request accepted: GET /api/status\n  (2) connection refused: db host=db01...'
BEHAVIOR CHANGED: 1 records — 1 CHANGED
```

The diff is truncated at 200 characters; the key signal is
`Top 3 messages:` → `Top 5 messages:`.

---

## Step 4 — accept the change (if intentional)

If the Top-5 change was deliberate:

```
PYTHONPATH=/path/to/stillworks python3 -m stillworks accept cmd#1
```

Real output:

```
accepted new behavior: cmd#1
```

```
PYTHONPATH=/path/to/stillworks python3 -m stillworks check
```

Real output:

```
STILL WORKS: 1 records — 1 OK
```

If the change was a mistake, revert `log_summary.sh` and check again
— no accept needed.

---

## Adding more commands

Lock multiple commands in a single pass with repeated `--cmd` flags:

```
PYTHONPATH=/path/to/stillworks python3 -m stillworks lock \
    --cmd "bash log_summary.sh sample.log" \
    --cmd "bash log_summary.sh sample.log 2>&1 | wc -l"
```

Each command gets its own record (`cmd#1`, `cmd#2`, …) and is checked
independently.  Exit codes and stderr are gated on the same footing as
stdout — a script that silently starts failing with a non-zero exit will
be caught even if the output looks the same.
