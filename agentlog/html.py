"""Generate a self-contained, offline HTML digest.

No external requests.  All CSS is inline.  Works in light and dark mode.
"""

from __future__ import annotations

import html as _html
import json
from datetime import datetime
from typing import Dict, List, Optional

from . import __version__
from .asked import pick_ask
from .clock import duration, how_long, when
from .render import (
    _fmt_tokens,
    _dedup_merge,
    _shorten_cmd,
    group_by_project,
    left_out,
    unique_short_ids,
    summary_line,
    what_it_did,
)


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:         #ffffff;
  --surface:    #f7f7f8;
  --border:     #e4e4e7;
  --text:       #18181b;
  --muted:      #71717a;
  --accent:     #3b82f6;
  --tag-bg:     #eff6ff;
  --tag-text:   #1d4ed8;
  --code-bg:    #f4f4f5;
  --error-bg:   #fef2f2;
  --error-text: #991b1b;
  --ok-text:    #166534;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:         #0c0c0e;
    --surface:    #18181b;
    --border:     #27272a;
    --text:       #e4e4e7;
    --muted:      #71717a;
    --accent:     #60a5fa;
    --tag-bg:     #1e3a5f;
    --tag-text:   #93c5fd;
    --code-bg:    #1c1c1f;
    --error-bg:   #3b0f0f;
    --error-text: #fca5a5;
    --ok-text:    #86efac;
  }
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial,
               sans-serif;
  font-size: 14px;
  line-height: 1.6;
  background: var(--bg);
  color: var(--text);
  padding: 2rem 1rem;
}
.container { max-width: 860px; margin: 0 auto; }

/* ---- header ---- */
.header { margin-bottom: 2rem; }
.header h1 {
  font-size: 1.4rem;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--text);
}
.header h1 span { color: var(--accent); }
.summary-line {
  margin-top: 0.4rem;
  font-size: 0.95rem;
  font-weight: 500;
  color: var(--text);
}
.meta-line { font-size: 0.8rem; color: var(--muted); margin-top: 0.25rem; }

/* ---- project group ---- */
.project { margin-bottom: 2rem; }
.project-header {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
  flex-wrap: wrap;
  padding-bottom: 0.4rem;
  margin-bottom: 0.8rem;
  border-bottom: 2px solid var(--border);
}
.project-name {
  font-size: 1.05rem;
  font-weight: 600;
  color: var(--text);
}
.project-stats { font-size: 0.8rem; color: var(--muted); }
.project-path {
  font-size: 0.75rem;
  color: var(--muted);
  font-family: ui-monospace, "Cascadia Code", "Source Code Pro", monospace;
}
.project .card-project { display: none; }

/* ---- session card ---- */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1rem 1.2rem;
  margin-bottom: 1rem;
}
.card-header {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
  flex-wrap: wrap;
  margin-bottom: 0.5rem;
}
.card-project {
  font-weight: 600;
  font-size: 0.95rem;
}
.card-title {
  color: var(--muted);
  font-style: italic;
  font-size: 0.875rem;
}
.card-id {
  font-family: ui-monospace, "Cascadia Code", "Source Code Pro", monospace;
  font-size: 0.75rem;
  color: var(--muted);
  background: var(--code-bg);
  border-radius: 4px;
  padding: 1px 5px;
}
.tag {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  background: var(--tag-bg);
  color: var(--tag-text);
  border-radius: 4px;
  padding: 1px 6px;
}

.card-meta {
  font-size: 0.8rem;
  color: var(--muted);
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1.2rem;
  margin-bottom: 0.75rem;
}
.card-meta span { white-space: nowrap; }

/* ---- sections ---- */
.section { margin-top: 0.75rem; }
.section-label {
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  margin-bottom: 0.3rem;
}
/* What somebody typed, quoted.  Same rule-on-the-left as the recap because it
   is the same kind of thing — a sentence about the work rather than a list of
   it — but in the body ink rather than muted: on a card whose other rows are
   paths and counts, this is the row a reader is looking for. */
.asked {
  margin-top: 0.75rem;
  padding-left: 0.7rem;
  border-left: 2px solid var(--tag-bg);
  font-size: 0.9rem;
  line-height: 1.55;
}
.project-asked {
  margin: 0.15rem 0 0.6rem;
  padding-left: 0.7rem;
  border-left: 2px solid var(--tag-bg);
  font-size: 0.9rem;
  line-height: 1.55;
}
.recap {
  margin-top: 0.75rem;
  padding-left: 0.7rem;
  border-left: 2px solid var(--code-bg);
  font-size: 0.85rem;
  line-height: 1.55;
}
.code-block {
  font-family: ui-monospace, "Cascadia Code", "Source Code Pro", monospace;
  font-size: 0.78rem;
  background: var(--code-bg);
  border-radius: 5px;
  padding: 0.5rem 0.7rem;
  overflow-x: auto;
  white-space: pre;
  line-height: 1.5;
}
.more { color: var(--muted); font-style: italic; }
.error-badge {
  display: inline-block;
  font-size: 0.72rem;
  font-weight: 600;
  background: var(--error-bg);
  color: var(--error-text);
  border-radius: 4px;
  padding: 1px 6px;
  margin-left: 0.5rem;
}

/* ---- footer ---- */
.footer {
  margin-top: 2.5rem;
  padding-top: 1rem;
  border-top: 1px solid var(--border);
  font-size: 0.75rem;
  color: var(--muted);
}
.footer a { color: var(--accent); text-decoration: none; }
.footer a:hover { text-decoration: underline; }

/* ---- responsive ---- */
@media (max-width: 500px) {
  body { padding: 1rem 0.75rem; }
  .card { padding: 0.75rem; }
}
"""


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _e(text: str) -> str:
    """HTML-escape a string."""
    return _html.escape(str(text), quote=True)


def _tag(name: str, content: str, **attrs) -> str:
    attr_str = ""
    for k, v in attrs.items():
        k = k.rstrip("_").replace("_", "-")
        attr_str += f' {_e(k)}="{_e(str(v))}"'
    return f"<{name}{attr_str}>{content}</{name}>"


# ---------------------------------------------------------------------------
# Per-session card
# ---------------------------------------------------------------------------

def _render_card(s: Dict, shorts: Optional[Dict[str, str]] = None) -> str:
    short_id = (shorts or {}).get(s["id"]) or (s["id"][:8] if s["id"] else "?")
    project = s["project_name"] or s["project"] or "?"
    source = s.get("source", "")
    title = s.get("ai_title") or ""

    # Card header
    header_parts = [
        _tag("span", _e(project), class_="card-project"),
    ]
    if title:
        header_parts.append(_tag("span", _e(f'"{title}"'), class_="card-title"))
    header_parts.append(_tag("span", _e(short_id), class_="card-id"))
    if source:
        header_parts.append(_tag("span", _e(source), class_="tag"))
    if s["errors"]:
        header_parts.append(
            _tag("span", f"{_e(str(s['errors']))} error{'s' if s['errors'] != 1 else ''}",
                 class_="error-badge")
        )
    card_header = _tag("div", " ".join(header_parts), class_="card-header")

    # Meta row
    meta_items = [
        _tag("span", f"⏱ {_e(when(s))}  ({_e(how_long(s))})"),
        _tag("span", f"{_e(str(s['user_turns']))} turn{'s' if s['user_turns'] != 1 else ''}"),
    ]
    if s["models"]:
        meta_items.append(_tag("span", _e(", ".join(s["models"]))))
    tok = _fmt_tokens(s)
    if tok:
        meta_items.append(_tag("span", _e(tok)))
    card_meta = _tag("div", "".join(meta_items), class_="card-meta")

    # Sections
    sections = ""

    # Above everything: what was asked for.  A card is a list of what happened;
    # this is the one row saying what it was all meant to do, so it goes first.
    ask = pick_ask(s.get("asks") or [])
    if ask:
        sections += _tag("div", _e(f'"{ask}"'), class_="asked")

    # Then the recap.  `_e` escapes it like everything else here — the text
    # was written by the thing being reported on.
    for _at, text in s.get("recaps") or []:
        sections += _tag("div", _e(str(text)), class_="recap")

    files_all = _dedup_merge(s["files_read"], s["files_written"])
    if files_all:
        limit = 12
        shown = files_all[:limit]
        code_lines = []
        for f in shown:
            tag_suffix = "  (read)" if f in s["files_read"] and f not in s["files_written"] else ""
            code_lines.append(_e(f) + _tag("span", _e(tag_suffix), class_="more"))
        tail = left_out(len(files_all), limit)
        if tail:
            code_lines.append(_tag("span", _e(tail), class_="more"))
        label = _tag("div", f"Files ({_e(str(len(files_all)))})", class_="section-label")
        block = _tag("div", "\n".join(code_lines), class_="code-block")
        sections += _tag("div", label + block, class_="section")

    if s["commands"]:
        limit = 8
        shown = s["commands"][:limit]
        code_lines = [_e("$ " + _shorten_cmd(cmd, 100)) for cmd in shown]
        tail = left_out(len(s["commands"]), limit)
        if tail:
            code_lines.append(_tag("span", _e(tail), class_="more"))
        label = _tag("div", f"Commands ({_e(str(len(s['commands'])))})", class_="section-label")
        block = _tag("div", "\n".join(code_lines), class_="code-block")
        sections += _tag("div", label + block, class_="section")

    body = card_header + card_meta + sections
    return _tag("div", body, class_="card")


def _render_project(group: Dict, shorts: Optional[Dict[str, str]] = None) -> str:
    """One project heading followed by that project's session cards."""
    stats = what_it_did(group["files"], group["commands"], group["errors"])
    n_sess = len(group["sessions"])
    stats.append(f"{n_sess} session{'s' if n_sess != 1 else ''}")

    header_parts = [
        _tag("span", _e(group["name"]), class_="project-name"),
        _tag("span", _e(duration(group["seconds"])), class_="project-stats"),
        _tag("span", _e(" · ".join(stats)), class_="project-stats"),
    ]
    if group["path"] and group["path"] != group["name"]:
        header_parts.append(_tag("span", _e(group["path"]), class_="project-path"))
    header = _tag("div", " ".join(header_parts), class_="project-header")
    if group["asked"]:
        header += _tag("div", _e(f'"{group["asked"]}"'), class_="project-asked")

    cards = "\n".join(_render_card(s, shorts) for s in group["sessions"])
    return _tag("div", header + cards, class_="project")


# ---------------------------------------------------------------------------
# Full page
# ---------------------------------------------------------------------------

def render_html(
    sessions: List[Dict],
    sources: List[str],
    period_label: str,
    generated_at: Optional[datetime] = None,
) -> str:
    """Return a complete, self-contained HTML page as a string."""
    if generated_at is None:
        generated_at = datetime.now().astimezone()

    gen_str = generated_at.strftime("%Y-%m-%d %H:%M")
    source_str = " and ".join(sources) if sources else "no agent logs found"
    summary = summary_line(sessions)

    # Cards are grouped under the project they belong to: the first question a
    # reader has is "what did it work on", not "what ran at 14:07".
    if sessions:
        _shorts = unique_short_ids(sessions)
        cards_html = "\n".join(
            _render_project(g, _shorts) for g in group_by_project(sessions)
        )
    else:
        cards_html = _tag("p", "No sessions found for this time range.", class_="muted")

    header = _tag(
        "div",
        _tag("h1", 'agent<span>log</span> digest') +
        _tag("p", _e(summary), class_="summary-line") +
        _tag("p", _e(f"{period_label}  ·  {source_str}  ·  generated {gen_str}"),
             class_="meta-line"),
        class_="header",
    )

    privacy_note = (
        "<p>This digest may contain file paths, shell commands, the recaps "
        "the agent wrote, and what you typed to ask for the work. "
        "Nothing the agent replied and nothing a command printed is in here. "
        "Review before sharing.</p>"
    )
    footer = _tag(
        "div",
        f"{privacy_note}"
        f'<p>Generated by <a href="https://github.com/iselur/agentlog">agentlog</a> {_e(__version__)} '
        f"— local, offline, no network — "
        f'<a href="https://github.com/iselur/stillworks">stillworks family</a></p>',
        class_="footer",
    )

    body_content = (
        _tag("div", header + cards_html + footer, class_="container")
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>agentlog — {_e(period_label)}</title>
<style>{_CSS}</style>
</head>
<body>
{body_content}
</body>
</html>"""
