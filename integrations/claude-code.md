# stillworks + Claude Code

Three options, lightest to deepest. Use whichever fits the project.

---

## Option 1 — Plain CLI (recommended)

No installation beyond `pip install stillworks`. Tell the agent in your prompt
or in `CLAUDE.md`:

```
Before editing any file where behavior must not change, run:
  stillworks lock <path/to/file.py> --fuzz 8
After editing, run:
  stillworks check
Exit 0 means behavior preserved. Exit 1 means something changed — fix or accept.
```

That is the entire integration. Every shell-capable agent already knows how to
run commands. The CLI is the primary interface.

---

## Option 2 — Skill (auto-invokes lock/check for risky edits)

Copy the skill into the project (or your global skills directory):

```bash
cp -r /path/to/stillworks/skill/ .claude/skills/stillworks/
# or globally:
cp -r /path/to/stillworks/skill/ ~/.claude/skills/stillworks/
```

Claude Code will pick up the skill automatically. When it recognizes a
refactor, migration, or "should not change behavior" edit, it will lock before
editing and check after.

The skill is in `skill/SKILL.md` in the stillworks repo. It has no other files.

---

## Option 3 — MCP server (tool-call interface)

### Add via the CLI

```bash
claude mcp add stillworks stillworks mcp
```

This writes the server into your local Claude Code MCP config.

### Or add via `.mcp.json` in the project root

```json
{
  "mcpServers": {
    "stillworks": {
      "command": "stillworks",
      "args": ["mcp"]
    }
  }
}
```

When `stillworks` is not on `PATH` (e.g., installed in a venv):

```json
{
  "mcpServers": {
    "stillworks": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "stillworks", "mcp"]
    }
  }
}
```

### Available MCP tools

| Tool | Equivalent CLI |
|---|---|
| `stillworks_lock` | `stillworks lock` |
| `stillworks_check` | `stillworks check --json` |
| `stillworks_accept` | `stillworks accept` |
| `stillworks_report` | `stillworks report` |

`stillworks_check` always returns JSON (machine-readable). `isError` is `false`
even when behavior changed — a CHANGED verdict is a result, not an error.
