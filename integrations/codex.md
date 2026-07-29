# stillworks + Codex CLI

Two options: MCP server (tool-call interface) or plain shell commands via
`AGENTS.md`. Plain CLI works immediately with no config.

---

## Option 1 — Plain CLI via AGENTS.md (recommended)

Add to your project's `AGENTS.md`:

```markdown
## Behavior verification

Before editing any file where behavior must not change, run:
  stillworks lock <path/to/file.py> --fuzz 8
After editing, run:
  stillworks check
Exit 0 = behavior preserved. Exit 1 = behavior changed; fix or accept each diff.
Do not accept a change just to make the check pass.
```

Codex reads `AGENTS.md` at the project root and follows its instructions.
No other setup required.

---

## Option 2 — MCP server via config.toml

Add a stillworks server stanza to `~/.codex/config.toml`:

```toml
# verify this key name against current Codex CLI docs
[[mcp_servers]]
name    = "stillworks"
command = "stillworks"
args    = ["mcp"]
```

If `stillworks` is not on `PATH`:

```toml
[[mcp_servers]]
name    = "stillworks"
command = "/path/to/.venv/bin/python"
args    = ["-m", "stillworks", "mcp"]
```

> **Verify against current docs**: the exact TOML key (`mcp_servers` vs
> `mcpServers`) and array style may differ across Codex CLI versions. Check
> `codex --help` or the project README for the current config schema.

---

## Quick reference

```bash
stillworks lock src/billing.py --fuzz 8   # before editing
# ... Codex edits billing.py ...
stillworks check                          # after editing
stillworks check --json                   # machine-readable; gate on .ok == true
stillworks accept apply_discount#3        # bless an intentional change
stillworks report -o EVIDENCE.md          # attach to PR
```
