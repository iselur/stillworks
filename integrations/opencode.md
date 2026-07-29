# stillworks + OpenCode

Two options: MCP server (tool-call interface) or plain shell commands. Plain
CLI works immediately with no config.

---

## Option 1 — Plain CLI (recommended)

No config needed. Add these two instructions to your system prompt or project
context in OpenCode:

```
Before editing any file where behavior must not change:
  stillworks lock <path/to/file.py> --fuzz 8
After editing:
  stillworks check
```

Exit 0 = behavior preserved. Exit 1 = behavior changed.

---

## Option 2 — MCP server via opencode.json

Create or update `opencode.json` at the project root (or in
`~/.config/opencode/opencode.json` for global config):

```json
{
  "mcp": {
    "stillworks": {
      "command": "stillworks",
      "args": ["mcp"]
    }
  }
}
```

If `stillworks` is not on `PATH`:

```json
{
  "mcp": {
    "stillworks": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "stillworks", "mcp"]
    }
  }
}
```

> **Verify against current docs**: OpenCode's config schema (`mcp` key nesting
> and exact field names) may change across releases. Check
> `opencode.json` schema or the OpenCode README for the current format.

### Available MCP tools once connected

| Tool | What it does |
|---|---|
| `stillworks_lock` | Record current behavior as the baseline |
| `stillworks_check` | Replay baseline; returns JSON with `ok`, `counts`, `results` |
| `stillworks_accept` | Bless intentional behavior changes |
| `stillworks_report` | Generate a markdown evidence report |

---

## Quick reference

```bash
stillworks lock src/mod.py --fuzz 8       # before editing
# ... OpenCode edits mod.py ...
stillworks check                          # after editing
stillworks check --json                   # machine-readable output; gate on .ok
stillworks accept <id>                    # bless an intentional change
stillworks report -o EVIDENCE.md          # evidence for the PR
```
