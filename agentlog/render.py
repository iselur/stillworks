"""Plain-text and JSON formatters for session digests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import clock
from .clock import how_long, when
from .parser import active_spans
from .terminal import block as safe_for_terminal
from .terminal import display_width, pad as _pad
from .which_file import as_shown


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
#
# What a terminal obeys rather than shows, and how wide a character is drawn,
# are facts about terminals rather than about digests: they live in
# `terminal.py`, which is the same file in the four tools that print.  This
# module used to answer both itself, and answered the first one wrong -- it
# *deleted* the character, having measured a cell for it a moment earlier in
# `_pad`, so a row holding one stood a cell to the left of every other row.
# `block` replaces it with a space instead, which is the cell that was already
# measured.  See tests/test_printing_what_an_agent_wrote.py.


#: The one mark that says text was cut.  There were three spellings of this in
#: this file -- ``...`` in front, ``...`` behind, and ``…`` -- so the same
#: event looked like three different ones depending on which row you read.
_CUT = "…"


def _fitting(text: str, cells: int, from_the_end: bool) -> str:
    """The longest run of `text` that fits in `cells`, from one end or other."""
    order = reversed(text) if from_the_end else text
    kept, used = [], 0
    for char in order:
        width = display_width(char)
        if used + width > cells:
            break
        kept.append(char)
        used += width
    return "".join(reversed(kept)) if from_the_end else "".join(kept)


def shortened(text: str, width: int, keep_the_end: bool = False) -> str:
    """`text` cut to fit `width` cells, saying so where it was cut.

    Three separate things a caller would otherwise have to get right, and this
    module got two of them wrong in three places out of four:

    *In cells, not characters.*  `display_width` is imported at the top of this
    file precisely because an eye reads cells, and then four helpers cut with
    `len` anyway.  A failed command written in Japanese was cut to 56
    characters, drawn in 121 cells, and ran off the side of an 80-cell digest
    -- the row directly under the one that carries a comment about having
    fixed this exact fault for *its* row.

    *The mark is inside the budget.*  A width is a promise about the row it
    goes in, so a mark added after the cut breaks the promise by the width of
    the mark, which is how a row that fits becomes a row that wraps.

    *One mark, one direction per kind of thing.*  A path is identified by its
    end, so ``keep_the_end`` keeps the basename and marks the front; a command
    is identified by its start.  One caller cut a command to its last 80
    characters and then to the first 72 of those, so the reader was shown the
    middle of the command and no part of either end.

    Not for `_one_row`, which is a different job: it bounds a detail view that
    has no column after it, and says how much it is not showing rather than
    fitting anything.
    """
    if display_width(text) <= width:
        return text
    room = width - display_width(_CUT)
    if room <= 0:
        # No room to say anything but "there was more" -- and if there is not
        # even room for that, the caller asked for a column no text fits in.
        return _CUT if width >= display_width(_CUT) else ""
    if keep_the_end:
        return _CUT + _fitting(text, room, from_the_end=True)
    return _fitting(text, room, from_the_end=False) + _CUT


def _first_few(items: List[str], limit: int = 6, indent: str = "  ") -> List[str]:
    """Up to `limit` items, and a line saying how many were left out.

    How many items to show and how long each may be are two questions, and
    answering both here meant answering the second one for callers that wanted
    different things -- the tail of a path, the head of a command.  Each caller
    now cuts its own with `shortened`, which is the only thing that knows how.

    `indent` is the only part that actually varies: inside a markdown fence the
    line sits flush, everywhere else it sits under its list.  What it says is
    `left_out`, which the views that are not lines of text ask for too.
    """
    out = list(items[:limit])
    tail = left_out(len(items), limit)
    if tail:
        out.append(indent + tail)
    return out


def left_out(total: int, shown: int) -> str:
    """What to say about the items a view did not show, or nothing to say.

    One situation, one sentence.  This was written out by hand at four places
    in this file and spelled two different ways, so the plain view said
    ``... and 5 more`` where the digest said ``… and 2 more`` about the same
    kind of thing in the same run.  Collecting them into `_first_few` fixed
    every view made of lines and left out the one that is not — so the HTML
    digest went on saying it the old way, and a run written to both still read
    as two tools rather than one.

    Which is why the sentence is here rather than in `_first_few`: a view that
    wraps each item in a tag cannot use a list of indented strings, and asking
    it to spell the sentence itself is asking for exactly this again.

    Empty when nothing was left out, so a caller can ask without first working
    out whether there is an answer.
    """
    missing = total - shown
    return "{} and {} more".format(_CUT, missing) if missing > 0 else ""


def _identifying_line(cmd: str) -> Tuple[str, bool]:
    """The line that says which command this is, and whether more follow.

    Heredocs and inline scripts are several lines long; flattening them into
    one run-on line is unreadable, and the first line is the identifying part.

    Not fitted to anything, because two rows in the same digest do not have the
    same room -- the one carrying a repeat count has less -- and because the
    digest collapses repeats on this.  Collapsing on the *fitted* text would
    make two commands that differ only past the cut read as one.
    """
    lines = [ln for ln in cmd.splitlines() if ln.strip()]
    return (lines[0].strip(), len(lines) > 1) if lines else ("", False)


def _fitted_headline(line: str, more_below: bool, width: int) -> str:
    """An identifying line in `width` cells, both of its marks included.

    The "and there is more of it" mark is taken out of the width before the
    line is cut, not added after: both marks are part of what has to fit.
    """
    if not line:
        return ""
    more = " " + _CUT if more_below else ""
    return shortened(line, width - display_width(more)) + more


def _cmd_headline(cmd: str, width: int = 56) -> str:
    """First line of a command, marked when more lines follow, in `width`."""
    line, more_below = _identifying_line(cmd)
    return _fitted_headline(line, more_below, width)


def _shorten_cmd(cmd: str, width: int = 72) -> str:
    """Shorten a shell command for display, keeping the end that names it."""
    return shortened(cmd.replace("\n", " ").strip(), width)


def _one_row(text: str, width: int = 400) -> str:
    """One value from a log file, on one row, whatever it has in it.

    `safe_for_terminal` keeps newlines because they are this module's own
    layout — but only if the untrusted text passed through it *first*.  Put a
    raw value into the layout and the two are no longer distinguishable, so a
    command containing a newline arrives on screen as extra rows:

        commands (1):
          $ echo harmless
          $ npm publish --access public

    One command ran; two are shown, and the second was written by the thing
    being audited in the exact shape of a real row.  `agentlog show` is where
    someone goes to find out what an agent did, so a row it cannot vouch for
    is the whole problem, and the header count disagreeing with the rows is
    the only tell.  Ordinary sessions hit this too — agents write heredocs and
    `python3 -c` all day, and every one of those became several rows.

    `str.splitlines` rather than a `\\n` replace, because a terminal also
    breaks on `\\r`, `\\v`, `\\f` and U+2028/9.  `safe_for_terminal` strips
    those later, but stripping them would join the two halves into one word
    and hide the seam.

    The width is generous — this is the detail view, and reading the whole
    command is what it is for — but not unbounded: one 5 MB heredoc used to
    print as a 5 MB row and take the rest of the session off the screen with
    it.  When it does cut, it says so and says where the whole one is, because
    a detail view quietly showing less than it has is the same fault pointing
    the other way.
    """
    flat = " ".join(text.splitlines())
    if len(flat) <= width:
        return flat
    return "{}{} (+{:,} more characters, see --json)".format(
        flat[:width], _CUT, len(flat) - width)


# ---------------------------------------------------------------------------
# Summary line
# ---------------------------------------------------------------------------

def unique_short_ids(sessions: List[Dict], minimum: int = 8) -> Dict[str, str]:
    """Map each session id to the shortest prefix that is unique in this set.

    Codex ids are UUIDv7: the first 8 hex characters encode only the top 32
    bits of a millisecond timestamp, so any two sessions started within ~65
    seconds of each other share them.  Truncating to a fixed 8 made distinct
    sessions look like duplicates.  One width is chosen for the whole set so
    the ``list`` table stays aligned.
    """
    ids = [s["id"] for s in sessions if s.get("id")]
    if not ids:
        return {}
    longest = max(len(i) for i in ids)
    width = minimum
    while width < longest and len({i[:width] for i in ids}) < len(set(ids)):
        width += 1
    return {i: i[:width] for i in ids}


def active_seconds(sessions: List[Dict]) -> float:
    """Wall-clock time during which at least one session was working.

    Sessions run concurrently — parallel Codex workers routinely overlap — so
    summing their durations reports more hours than the day contains.  This
    merges the intervals instead, clipped to the requested window when one was
    applied.

    The intervals are the stretches a session was *busy*, not the whole span it
    was open: two agents left running through the night are not sixteen hours of
    work, and neither is one.  See ``parser.active_spans``.
    """
    spans = []
    for s in sessions:
        for start, end in (s.get("active_spans")
                           if s.get("active_spans") is not None
                           else active_spans(s)):
            if start is None:
                continue
            if end is None or end < start:
                end = start
            spans.append((start, end))
    if not spans:
        return 0.0

    spans.sort()
    total = 0.0
    cur_start, cur_end = spans[0]
    for start, end in spans[1:]:
        if start > cur_end:
            total += (cur_end - cur_start).total_seconds()
            cur_start, cur_end = start, end
        elif end > cur_end:
            cur_end = end
    total += (cur_end - cur_start).total_seconds()
    return total


def what_it_did(files: int, commands: int, errors: int) -> List[str]:
    """What a run did, as phrases for a view to join with its own separator.

    `176 files edited · 874 commands · 75 errors`.  A count of zero is left out
    rather than printed: a project with no errors should not have to say so.

    This is one sentence, and it was typed out by hand in four views — the
    terminal digest, the markdown document, the HTML digest and the summary
    line — which is three chances to spell it differently, and two of them had
    been taken.  The markdown document said `1 commands` and `1 errors`
    because its plural guard had never been written; and only the summary line
    said what the files *were*, so everywhere else a reader was told `176
    files` and left to guess whether that meant read or written.  It means
    written.

    Phrases rather than a finished string because the views do not all want
    the same thing around them: the markdown document puts the time first, and
    the summary line puts sessions, time and projects first and compactions
    last.  Joining is the caller's business; the words are not.
    """
    out = []
    if files:
        out.append(f"{files} file{'' if files == 1 else 's'} edited")
    if commands:
        out.append(f"{commands} command{'' if commands == 1 else 's'}")
    if errors:
        out.append(f"{errors} error{'' if errors == 1 else 's'}")
    return out


def summary_line(sessions: List[Dict]) -> str:
    """Return a one-line digest summary, e.g. '4 sessions · 3h 12m · 3 projects'."""
    if not sessions:
        return "0 sessions"

    total_s = active_seconds(sessions)
    projects = len({s["project"] for s in sessions if s["project"]})
    files_edited = sum(len(s["files_written"]) for s in sessions)
    cmds = sum(len(s["commands"]) for s in sessions)
    errors = sum(s["errors"] for s in sessions)

    parts = [
        f"{len(sessions)} session{'s' if len(sessions) != 1 else ''}",
        clock.duration(total_s),
    ]
    if projects:
        parts.append(f"{projects} project{'s' if projects != 1 else ''}")
    parts.extend(what_it_did(files_edited, cmds, errors))
    compactions = sum(len(s.get("compactions") or []) for s in sessions)
    if compactions:
        parts.append(f"{compactions} compaction{'s' if compactions != 1 else ''}")

    return " · ".join(parts)


def what_compaction_cost(compactions: List[Dict]) -> str:
    """The price of compacting, in time and in thrown-away history.

    `6h 43m spent on it, 16,516,640 tokens dropped`.

    The time used to be reported as `6h 43m spent`, which does not say spent on
    what, and sits in a digest whose first line is `8h 44m active` — so the one
    reading it most naturally is the wrong one, that six of the day's eight
    hours are unaccounted for.  They are accounted for: the agent spent them
    summarising itself.  `on it` points back at the word the line opens
    with, which is the only thing it can be pointing at.
    """
    spent = clock.duration(sum(c.get("duration_s", 0.0) for c in compactions))
    dropped = sum(c.get("dropped", 0) for c in compactions)
    return f"{spent} spent on it, {dropped:,} tokens dropped"


def compaction_note(sessions: List[Dict]) -> str:
    """One line about what compaction cost, or '' if it did not happen.

    The count alone cannot be read: twelve compactions in one session is a
    session that should have been split, and twelve across twelve sessions is
    an ordinary week.  So the number of sessions it happened in goes in the
    same line as the count.
    """
    compactions = [c for s in sessions for c in (s.get("compactions") or [])]
    if not compactions:
        return ""
    in_sessions = sum(1 for s in sessions if s.get("compactions"))
    return (f"compacted {len(compactions)}x in {in_sessions} "
            f"session{'s' if in_sessions != 1 else ''}"
            f" · {what_compaction_cost(compactions)}")


# ---------------------------------------------------------------------------
# Digest — the default view.  Grouped by project, because "what did I work on"
# is the question; the session list answers "what sessions exist", which is
# not a question anybody has.
# ---------------------------------------------------------------------------

def group_by_project(sessions: List[Dict]) -> List[Dict]:
    """Aggregate sessions into per-project groups, busiest first."""
    groups: Dict[str, List[Dict]] = {}
    for s in sessions:
        key = s.get("project") or s.get("project_name") or "?"
        groups.setdefault(key, []).append(s)

    out = []
    for path, members in groups.items():
        writes: Dict[str, int] = {}
        for s in members:
            for f, n in (s.get("write_counts") or {}).items():
                writes[f] = writes.get(f, 0) + n
            # Sessions parsed before write counts existed still list the files.
            if not s.get("write_counts"):
                for f in s["files_written"]:
                    writes.setdefault(f, 1)
        failed: Dict[str, int] = {}
        for s in members:
            for cmd in s.get("failed_cmds") or []:
                if cmd:
                    failed[cmd] = failed.get(cmd, 0) + 1
        out.append(
            {
                "path": path,
                "name": members[0]["project_name"] or path or "?",
                "sessions": members,
                "seconds": active_seconds(members),
                "files": len({f for s in members for f in s["files_written"]}),
                "commands": sum(len(s["commands"]) for s in members),
                "errors": sum(s["errors"] for s in members),
                "top_files": sorted(writes, key=lambda f: (-writes[f], f)),
                "top_failed": sorted(failed.items(), key=lambda kv: (-kv[1], kv[0])),
            }
        )
    out.sort(key=lambda g: (-g["seconds"], g["name"]))
    return out


def _busiest_hour(sessions: List[Dict]) -> Optional[str]:
    """The local hour with the most recorded activity, as 'HH:00–HH:00'.

    Inside the window, when there is one.  Every other number in the digest is
    clipped to the period being reported; this one was counted from each
    session's whole event list, so a session that worked hard at 03:00 yesterday
    and ran two commands at 14:00 today had `today` report `busiest 03:00`.  The
    one line that says *when* was the one drawn from outside the window.
    ``win_start`` and ``win_end`` are both real events by the time they get
    here, so the ends are inclusive.  See ``tests/test_busiest_hour.py``.
    """
    buckets: Dict[int, int] = {}
    for s in sessions:
        start, end = s.get("win_start"), s.get("win_end")
        for ts, kind, _value in s.get("events") or []:
            if ts is None or kind == "turn":
                continue
            if start is not None and ts < start:
                continue
            if end is not None and ts > end:
                continue
            hour = ts.astimezone().hour
            buckets[hour] = buckets.get(hour, 0) + 1
    if not buckets:
        return None
    best = max(buckets, key=lambda h: (buckets[h], -h))
    return f"{best:02d}:00–{(best + 1) % 24:02d}:00"


_PERIOD_PHRASE = {
    "today": "today",
    "yesterday": "yesterday",
    "week": "the last 7 days",
}


def _period_phrase(period_label: str) -> str:
    phrase = _PERIOD_PHRASE.get(period_label, period_label)
    day = None
    if period_label == "today":
        day = datetime.now().astimezone()
    elif period_label == "yesterday":
        day = datetime.now().astimezone() - timedelta(days=1)
    if day is not None:
        # %-d is glibc-only; build the day number by hand so this works anywhere.
        phrase += day.strftime(", %a ") + str(day.day) + day.strftime(" %b")
    return phrase


#: The digest is a fixed layout, written for the narrowest terminal anybody
#: still has.  Every column in it is a share of this, so a row that ignores it
#: wraps on the reader's screen and breaks the shape of the whole thing.
_DIGEST_WIDTH = 80

#: The label the edited-files row hangs off, and therefore what it costs.
_EDITED = "      edited   "


def render_digest(
    sessions: List[Dict],
    period_label: str = "today",
    max_projects: int = 8,
    verbose: bool = False,
) -> str:
    """Render the answer-first, project-grouped digest."""
    if not sessions:
        return f"nothing recorded {_period_phrase(period_label)}"

    groups = group_by_project(sessions)
    total = clock.duration(active_seconds(sessions))
    n_proj = len(groups)
    lines = [
        f"{total} active across {n_proj} project{'s' if n_proj != 1 else ''}"
        f" · {_period_phrase(period_label)}",
        "",
    ]

    shown = groups[:max_projects]
    name_w = min(max(max(display_width(g["name"]) for g in shown), 10), 24)
    dur_w = max(len(clock.duration(g["seconds"])) for g in shown)

    for g in shown:
        stats = what_it_did(g["files"], g["commands"], g["errors"])
        if not stats:
            stats.append("no edits or commands recorded")
        lines.append(
            f"  {_pad(shortened(g['name'], name_w), name_w)}  "
            f"{clock.duration(g['seconds']).rjust(dur_w)}   " + " · ".join(stats)
        )

        if g["top_files"]:
            files = g["top_files"][:3]
            # Share the row out between them.  The names used to be allowed 34
            # cells each and then not held to it, so three long ones ran off
            # the edge of the terminal -- the one row in the digest that was
            # not measured, sitting directly under the one that is.  One file
            # gets the whole row; three split it.
            each = ((_DIGEST_WIDTH - display_width(_EDITED)
                     - 2 * (len(files) - 1)) // len(files))
            lines.append(_EDITED + ", ".join(
                as_shown(f, g["path"], each) for f in files))
        # Collapse on the headline: three heredocs that differ only below
        # their first line should read as one repeated failure, not three.
        by_head: Dict[Tuple[str, bool], int] = {}
        for cmd, n in g["top_failed"]:
            head = _identifying_line(cmd)
            by_head[head] = by_head.get(head, 0) + n
        ranked = sorted(by_head.items(), key=lambda kv: (-kv[1], kv[0]))
        for i, ((head, more_below), n) in enumerate(ranked[:3]):
            label = "      failed   " if i == 0 else "               "
            # Held to the digest's width like the row above it.  This row was
            # the one that was not, so a command with wide characters in it
            # ran off the side of the very layout the row above is measured
            # against -- and the count is part of the row, so it comes out of
            # the budget rather than being added once the cutting is over.
            #
            # Cut once, here, at the room this row has.  It used to be cut to a
            # fixed 56 cells first, which is narrower than the row ever is, so
            # the row's own width was decorative and widening the digest moved
            # nothing.
            times = f"  ({n}x)" if n > 1 else ""
            room = _DIGEST_WIDTH - display_width(label) - display_width(times)
            lines.append(label + _fitted_headline(head, more_below, room)
                         + times)

    if len(groups) > max_projects:
        rest = len(groups) - max_projects
        lines.append(f"  {_CUT} and {rest} more project{'s' if rest != 1 else ''}")

    lines.append("")

    by_source: Dict[str, int] = {}
    for s in sessions:
        by_source[s.get("source") or "?"] = by_source.get(s.get("source") or "?", 0) + 1
    tail = [f"{len(sessions)} session{'s' if len(sessions) != 1 else ''}"]
    if len(by_source) > 1:
        tail.append(", ".join(f"{n} {src}" for src, n in sorted(by_source.items())))
    busiest = _busiest_hour(sessions)
    if busiest:
        tail.append(f"busiest {busiest}")
    # Agents run in parallel, so per-project times can sum past the total.
    # Say so, or the two numbers read as a bug.
    spent = sum(g["seconds"] for g in groups)
    lines.append("  " + " · ".join(tail))
    if len(groups) > 1 and spent > active_seconds(sessions) * 1.15:
        lines.append("  projects overlap — agents ran in parallel, so their times sum past the total")
    # Part of where the day went, and the only part of it that leaves no other
    # trace: the work a session redid after forgetting it looks like new work.
    compacted = compaction_note(sessions)
    if compacted:
        lines.append("  " + compacted)
    lines.append("  more: agentlog list · agentlog show ID · agentlog --sessions")

    if verbose:
        skipped = sum(s["skipped_lines"] for s in sessions)
        if skipped:
            lines.append(f"  skipped {skipped} unparseable lines")

    return safe_for_terminal("\n".join(lines))


# ---------------------------------------------------------------------------
# Terminal text formatter
# ---------------------------------------------------------------------------

def render_text(sessions: List[Dict], verbose: bool = False) -> str:
    """Render a list of sessions as plain-text suitable for the terminal."""
    if not sessions:
        return ""

    lines: List[str] = []
    lines.append(summary_line(sessions))
    lines.append("")

    shorts = unique_short_ids(sessions)
    for s in sessions:
        _render_session_text(s, lines, verbose=verbose, shorts=shorts)
        lines.append("")

    return safe_for_terminal("\n".join(lines).rstrip())


def _render_session_text(
    s: Dict, lines: List[str], verbose: bool = False, shorts: Optional[Dict[str, str]] = None
) -> None:
    short_id = (shorts or {}).get(s["id"]) or (s["id"][:8] if s["id"] else "?")
    project = s["project_name"] or s["project"] or "?"
    source_tag = f"[{s['source']}]" if s.get("source") else ""

    # Header line
    time_range = when(s)
    duration = how_long(s)

    title = s.get("ai_title")
    header = f"  {short_id}  {project}  {source_tag}"
    if title:
        header += f'  "{title}"'
    lines.append(header)
    turns = s["user_turns"]
    lines.append(f"    {time_range}  ({duration})  "
                 f"{turns} turn{'' if turns == 1 else 's'}")

    if s["models"]:
        lines.append(f"    model: {', '.join(s['models'])}")

    files_all = _dedup_merge(s["files_read"], s["files_written"])
    if files_all:
        label = "files"
        lines.append(f"    {label}:")
        for f in _first_few(files_all):
            tag = " (r)" if f in s["files_read"] and f not in s["files_written"] else ""
            # A path is identified by its end, so that is the end kept.
            lines.append(f"      {shortened(f, 60, keep_the_end=True)}{tag}")

    if s["commands"]:
        lines.append(f"    commands ({len(s['commands'])}):")
        for cmd in _first_few(s["commands"], limit=5):
            # Cut once, at the width this row has.  It used to be cut twice in
            # opposite directions -- to the last 80 characters and then to the
            # first 72 of those -- so what was printed was the middle of the
            # command and neither end of it.
            lines.append(f"      $ {_shorten_cmd(cmd)}")

    tokens = _fmt_tokens(s)
    if tokens:
        lines.append(f"    {tokens}")

    if s["errors"]:
        lines.append(f"    errors: {s['errors']}")

    if verbose and s["skipped_lines"]:
        lines.append(f"    skipped lines: {s['skipped_lines']}")


def _dedup_merge(a: List[str], b: List[str]) -> List[str]:
    seen: set = set()
    out = []
    for x in a + b:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _fmt_compactions(s: Dict) -> str:
    """What the session spent keeping itself going, or '' if it never had to.

    A session that compacts is one that ran out of room and had to summarise
    itself to continue.  It costs real wall-clock — a median of over two
    minutes each on the developer's own logs — and it throws most of the
    context away, so a session that did it repeatedly looks from the outside
    like one that was merely slow.  This is the line that says otherwise.

    Manual compactions are named separately because they are a different
    event: `/compact` is a person deciding, and running out of room is not.  A
    count that mixed them would overstate how often the session hit a wall.
    """
    compactions = s.get("compactions") or []
    if not compactions:
        return ""
    manual = sum(1 for c in compactions if c.get("trigger") == "manual")
    how_many = f"compacted {len(compactions)}x"
    if manual:
        how_many += f" ({manual} manual)"
    return f"{how_many} — {what_compaction_cost(compactions)}"


def _fmt_tokens(s: Dict) -> str:
    parts = []
    if s.get("tokens_in") is not None:
        parts.append(f"in: {s['tokens_in']:,}")
    if s.get("tokens_out") is not None:
        parts.append(f"out: {s['tokens_out']:,}")
    if parts:
        return "tokens — " + "  ".join(parts)
    return ""


# ---------------------------------------------------------------------------
# List view (agentlog list)
# ---------------------------------------------------------------------------

def render_list(sessions: List[Dict]) -> str:
    """Render a compact table of all sessions."""
    if not sessions:
        return "no sessions found"

    rows = []
    shorts = unique_short_ids(sessions)
    for s in sessions:
        sid = shorts.get(s["id"]) or "?"
        project = shortened(s["project_name"] or "?", 24)
        # Started, not ran-from-to: the WHEN column is sixteen characters and a
        # range does not go in one.  And DUR is eight, so this is the one view
        # that takes the bare number without the phrase that explains it.
        started = clock.at(s["start"]) if s["start"] else "?"
        dur = how_long(s, qualified=False)
        src = s.get("source", "?")[:6]
        rows.append((sid, project, started, dur, src))

    # Column widths — the ID column grows with the prefix length needed here
    id_w = max([8] + [display_width(r[0]) for r in rows])
    w = [id_w, 24, 16, 8, 6]
    header = "  ".join(_pad(col, w[i]) for i, col in enumerate(("ID", "PROJECT", "WHEN", "DUR", "SRC")))
    sep = "  ".join("-" * width for width in w)
    lines = [header, sep]
    for row in rows:
        lines.append("  ".join(_pad(cell, w[i]) for i, cell in enumerate(row)))
    return safe_for_terminal("\n".join(lines))


# ---------------------------------------------------------------------------
# Single-session detail (agentlog show SESSION_ID)
# ---------------------------------------------------------------------------

def render_show(s: Dict) -> str:
    """Render a single session in full detail."""
    # Every value below came out of a log file written by the thing this view
    # exists to audit, so each one goes through `_one_row` before it is put
    # into the layout — a value that can start its own row can write whatever
    # it likes here.  The counts in the headers are what makes that visible,
    # and they are only true if one item is one row.
    lines: List[str] = []
    lines.append(f"session  {_one_row(str(s['id']))}")
    lines.append(f"source   {_one_row(str(s.get('source', '?')))}")
    lines.append(f"project  {_one_row(str(s['project'] or '?'))}")
    lines.append(f"start    {clock.at(s['start'])}")
    lines.append(f"end      {clock.at(s['end'])}")
    # The time it spent working, and -- when they differ -- the time it was
    # open.  This row used to be the open time alone, under the bare label
    # "duration", which made the most detailed view the only one disagreeing
    # with every other view about the same session.
    lines.append(f"duration {how_long(s)}")
    if s["models"]:
        lines.append(f"models   {_one_row(', '.join(s['models']))}")
    if s.get("version"):
        lines.append(f"version  {_one_row(str(s['version']))}")
    lines.append(f"turns    {s['user_turns']}")
    lines.append(f"errors   {s['errors']}")
    tokens = _fmt_tokens(s)
    if tokens:
        lines.append(f"tokens   {tokens.replace('tokens — ', '')}")
    # Only when it happened.  A row saying "0" is a row the reader has to read
    # before finding out it says nothing.
    compacted = _fmt_compactions(s)
    if compacted:
        lines.append(f"context  {compacted}")

    # Before the paths, because it is the only part of this view that answers
    # "what was this for?" and a reader who has to scroll past forty file
    # paths to reach it will not.  `_one_row` for the same reason as the
    # commands below: the text was written by the thing being audited, and a
    # recap containing a newline and a plausible heading would otherwise print
    # as extra rows in the exact shape of real ones.
    if s.get("recaps"):
        lines.append("")
        lines.append(f"recap ({len(s['recaps'])}):")
        for _at, text in s["recaps"]:
            lines.append(f"  {_one_row(str(text))}")

    if s["files_read"]:
        lines.append("")
        lines.append(f"files read ({len(s['files_read'])}):")
        for f in s["files_read"]:
            lines.append(f"  {_one_row(f)}")

    if s["files_written"]:
        lines.append("")
        lines.append(f"files written ({len(s['files_written'])}):")
        for f in s["files_written"]:
            lines.append(f"  {_one_row(f)}")

    if s["commands"]:
        lines.append("")
        lines.append(f"commands ({len(s['commands'])}):")
        for cmd in s["commands"]:
            lines.append(f"  $ {_one_row(cmd)}")

    return safe_for_terminal("\n".join(lines))


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def render_markdown(sessions: List[Dict]) -> str:
    """Render sessions as a Markdown document."""
    lines: List[str] = []
    lines.append("# agentlog digest")
    lines.append("")
    lines.append(summary_line(sessions))
    lines.append("")

    shorts = unique_short_ids(sessions)
    # Group by project, as the terminal and HTML views do: the reader wants
    # "what did it work on" before "which session ran when".
    for group in group_by_project(sessions):
        stats = [f"{clock.duration(group['seconds'])} active"]
        stats.extend(what_it_did(
            group["files"], group["commands"], group["errors"]))
        lines.append(f"## {group['name']}")
        lines.append("")
        lines.append(" · ".join(stats))
        lines.append("")

        for s in group["sessions"]:
            lines.extend(_markdown_session(s, shorts))

    return safe_for_terminal("\n".join(lines))


def _markdown_session(s: Dict, shorts: Dict[str, str]) -> List[str]:
    """The per-session block of the Markdown document."""
    lines: List[str] = []
    short_id = shorts.get(s["id"]) or "?"
    lines.append(f"### `{short_id}`")
    lines.append("")

    lines.append(f"- **when**: {when(s)}  ({how_long(s)})")
    lines.append(f"- **source**: {s.get('source', '?')}")
    if s["models"]:
        lines.append(f"- **model**: {', '.join(s['models'])}")
    lines.append(f"- **turns**: {s['user_turns']}  **errors**: {s['errors']}")
    tokens = _fmt_tokens(s)
    if tokens:
        lines.append(f"- **tokens**: {tokens.replace('tokens — ', '')}")
    lines.append("")

    # Flattened onto one line each, as in `show`: a blockquote ends at the
    # first line that is not one, and a stray ``` would take the rest of the
    # document into a code block with it.
    for _at, text in s.get("recaps") or []:
        lines.append(f"> {_one_row(str(text))}")
        lines.append("")

    files_all = _dedup_merge(s["files_read"], s["files_written"])
    if files_all:
        lines.append(f"**Files** ({len(files_all)}):")
        lines.append("```")
        # Flush against the fence rather than indented under a list, but the
        # same sentence in the same words as every other view.
        lines.extend(_first_few(
            [f + (" (read only)"
                  if f in s["files_read"] and f not in s["files_written"] else "")
             for f in files_all], limit=20, indent=""))
        lines.append("```")
        lines.append("")

    if s["commands"]:
        lines.append(f"**Commands** ({len(s['commands'])}):")
        lines.append("```sh")
        lines.extend(_first_few([f"$ {_shorten_cmd(c)}" for c in s["commands"]],
                                limit=20, indent=""))
        lines.append("```")
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def _session_for_json(s: Dict) -> Dict:
    out = dict(s)
    if s["start"]:
        out["start"] = s["start"].isoformat()
    if s["end"]:
        out["end"] = s["end"].isoformat()
    # `duration_s` is how long the session was open, and on its own it invites
    # exactly the mistake the text output stopped making.  Say the working time
    # too, under its own name, so a script does not have to add up the spans.
    out["active_s"] = clock.working_seconds(s)
    # A named pair rather than a bare tuple: a script reading the one field
    # here that is meant for a person should not have to know which end of a
    # list the sentence is on.
    if s.get("recaps"):
        out["recaps"] = [{"at": at.isoformat() if at else None, "text": text}
                         for at, text in s["recaps"]]
    # `at` is a datetime everywhere else in the session dict; json.dumps raises
    # on one, so the failure would land on whoever piped the output into jq.
    if s.get("compactions"):
        out["compactions"] = [
            dict(c, at=c["at"].isoformat() if c.get("at") else None)
            for c in s["compactions"]
        ]
    return out


def render_json(sessions: List[Dict]) -> str:
    return json.dumps([_session_for_json(s) for s in sessions], indent=2, default=str)
