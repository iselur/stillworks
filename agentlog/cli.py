"""agentlog command-line interface.

Commands
--------
  agentlog                       # same as: agentlog today
  agentlog today | yesterday | week
  agentlog since DATE            # ISO date, or offset like 3d / 12h
  agentlog on DAY                # one whole day: 2026-07-31, or 3d
  agentlog show SESSION_ID       # one session in full detail
  agentlog list                  # recent sessions, compact table (default 50)
  agentlog list --all            # all sessions

View flags (time commands)
  --sessions                     # per-session view instead of the digest
  --project NAME                 # only projects matching NAME

Output flags (may be combined with any time command)
  --html FILE                    # write self-contained HTML digest
  --md [FILE]                    # Markdown (to FILE or stdout)
  --json                         # machine-readable JSON

Other flags
  --all                          # list: show all sessions (no row limit)
  --limit N                      # list: show at most N sessions (default 50)
  --verbose                      # show skipped-line counts and debug hints
  --home DIR                     # override home directory (useful for tests)

Exit codes
  0   normal
  2   usage or argument error

The tool never writes to or uploads the session logs.
"""

from __future__ import annotations

import argparse
import codecs
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import __version__
from .parser import active_spans, find_sessions
from .render import (
    render_digest,
    render_json,
    render_list,
    render_markdown,
    render_show,
    render_text,
    render_unusable,
)
from .html import render_html


# ---------------------------------------------------------------------------
# Date / time helpers
# ---------------------------------------------------------------------------

def _local_midnight(d: date) -> datetime:
    """Midnight at the start of `d`, in local time *on that date*.

    Not "midnight, plus whatever the offset happens to be today".  This used to
    capture `datetime.now().astimezone().tzinfo` once at import — a single fixed
    offset — and stamp it onto every date.  In a zone that observes daylight
    saving, asking in July about a day in January then opened the window an hour
    early and closed it an hour early: a session from 23:00 the night before was
    reported as belonging to the day you asked about, and the last hour of that
    day was missing.  Neither looks like an error.  It looks like the log.

    A naive datetime handed to `.astimezone()` is resolved against the
    platform's rules *for that date*, which is the question we actually mean.
    """
    return datetime(d.year, d.month, d.day).astimezone()


def _today_local() -> date:
    return datetime.now().astimezone().date()


def _parse_since(value: str) -> Optional[datetime]:
    """Parse a --since / 'since DATE' argument.

    Accepts:
      ISO date:  2026-07-15
      Offset:    3d, 12h, 2w
    Returns an aware datetime or None on failure.
    """
    value = value.strip().lower()

    # Offset form
    if value and value[-1] in "dhw":
        try:
            n = int(value[:-1])
        except ValueError:
            return None
        if n <= 0:
            # 'since 0d' is an empty window and 'since -3d' is the future;
            # neither is what anybody meant to type.
            return None
        unit = value[-1]
        try:
            delta = {"d": timedelta(days=n), "h": timedelta(hours=n),
                     "w": timedelta(weeks=n)}[unit]
            return datetime.now(timezone.utc) - delta
        except (OverflowError, OSError):
            # timedelta gives out long before int does.
            return None

    # ISO date
    try:
        d = date.fromisoformat(value)
        return _local_midnight(d)
    except ValueError:
        return None


def _parse_day(value: str) -> Optional[Tuple[datetime, datetime]]:
    """Parse an 'on DAY' argument into one whole local day.

    ``since`` answers "from then until now", which cannot express the commonest
    question a digest is for: *what did I do on Tuesday*.  Before this, the only
    two days that could be named were ``today`` and ``yesterday``.

    The argument is the one ``since`` takes, minus the forms that do not name a
    day.  ``12h`` and ``2w`` are durations, not dates — they would have to be
    rounded to a day and the rounding would be a guess, so they are refused.
    ``0d`` is the one place the two commands part company on purpose: ``since
    0d`` is a window from now until now and is refused as an obvious typo, while
    ``on 0d`` is today and is perfectly sensible.  See tests/test_a_named_day.py.
    """
    value = (value or "").strip().lower()

    if value.endswith("d"):
        try:
            n = int(value[:-1])
        except ValueError:
            return None
        if n < 0:
            return None
        try:
            day = _today_local() - timedelta(days=n)
        except (OverflowError, OSError):
            return None
    else:
        try:
            day = date.fromisoformat(value)
        except ValueError:
            return None

    # The day after is worked out as a *date* and then turned into a moment, so
    # that a day the clocks change is still a day and not twenty-three hours.
    try:
        return _local_midnight(day), _local_midnight(day + timedelta(days=1))
    except (OverflowError, OSError, ValueError):
        return None


def _since_for_period(period: str) -> Optional[datetime]:
    """Return the start-of-window datetime for a named period."""
    today = _today_local()
    if period == "today":
        d = today
    elif period == "yesterday":
        d = today - timedelta(days=1)
    elif period == "week":
        d = today - timedelta(days=6)
    else:
        return None
    return _local_midnight(d)


def _until_for_period(period: str) -> Optional[datetime]:
    """The exclusive end of a named window, or None if it has no end.

    A named day ends when the day ends, not when you happened to run this.
    `today` runs to midnight tonight — the alternative is that a log written by
    a clock two minutes fast falls outside "today", which is not what anyone
    means by the word.  `since 3d` genuinely has no end, so it gets None.
    """
    today = _today_local()
    if period == "yesterday":
        end = today
    elif period in ("today", "week"):
        end = today + timedelta(days=1)
    else:
        return None
    return _local_midnight(end)


# ---------------------------------------------------------------------------
# Session filtering
# ---------------------------------------------------------------------------

def _inside(ts: Optional[datetime], start: datetime, end: datetime,
            end_open: bool) -> bool:
    """Whether an event at ``ts`` belongs to this window.

    The start is always inclusive.  The end is inclusive when it is a real
    event's timestamp — the window has been tightened onto something that
    happened, and that something is in it — and *exclusive* when it is an edge
    the caller asked for, which is what ``_until_for_period`` has always said it
    returns.  Both were inclusive, so an event at exactly local midnight was the
    last thing yesterday and the first thing today, and three turns across the
    two commands were reported as four.  See ``tests/test_day_partition.py``.
    """
    if ts is None or ts < start:
        return False
    return ts < end if end_open else ts <= end


def _first_and_last_inside(s: Dict, start: datetime, end: datetime,
                           end_open: bool = False):
    """When the session's first and last event inside the window happened.

    ``None`` when the session was parsed without per-event timestamps, or when
    none of them land inside — there is nothing to tighten to, so the caller
    keeps the window edge.  That is the same fallback ``_clip_counts`` takes,
    for the same reason: a lifetime total is a worse answer than a clipped one,
    but a made-up one is worse than both.
    """
    inside = [ts for ts, _kind, _value in (s.get("events") or [])
              if _inside(ts, start, end, end_open)]
    if not inside:
        return None
    return min(inside), max(inside)


def _clip_tokens(s: Dict, start: datetime, end: datetime,
                 end_open: bool = False) -> None:
    """Recount tokens from the turns that spent them inside the window.

    A week-long session was reporting its whole week's spend into every day it
    touched, on the line directly below a correctly clipped command count — on
    the real logs, 88.3M against the 14.2M actually spent that day.

    A session that spent nothing here spent nothing, and says zero: an empty
    window is an answer.  Only a session with no per-turn record at all keeps
    its lifetime total, because that is one we cannot see inside — the same
    distinction ``active_spans`` has to make.

    The window here is the one that was *asked for*, not the one tightened onto
    the session's first and last event.  Tightening exists so that a session
    left open overnight is not billed for the night, and it reckons on events —
    tool calls, turns, errors.  A reply that costs a thousand tokens and calls
    no tool is not an event, so anything spent after the day's last tool call
    sat outside the tightened edge and vanished.  That came to 3.4M tokens a
    week here, and showed as a week totalling more than its seven days did.
    See tests/test_window_tokens.py.
    """
    spent = s.get("token_events")
    if not spent:
        return
    tok_in = tok_out = 0
    for ts, a, b in spent:
        if _inside(ts, start, end, end_open):
            tok_in += a
            tok_out += b
    s["tokens_in"] = tok_in
    s["tokens_out"] = tok_out


def _clip_recaps(s: Dict, start: datetime, end: datetime,
                 end_open: bool = False) -> None:
    """Keep the recaps written inside the window.

    Against the window that was *asked for*, like tokens and for the same
    reason: a recap is written when a turn ends, which on a busy day is after
    the last tool call, and the tightened window ends at the last tool call.
    Clipping against that one loses the recap that says how the day finished.

    A recap with no timestamp is kept.  It cannot be placed, and unlike a
    token or a command it can never be double-counted into a total — losing
    the one sentence that explains a session is the worse of the two mistakes.
    See tests/test_the_recap.py.
    """
    recaps = s.get("recaps")
    if not recaps:
        return
    s["recaps"] = [(ts, text) for ts, text in recaps
                   if ts is None or _inside(ts, start, end, end_open)]


def _clip_counts(s: Dict, start: datetime, end: datetime,
                 end_open: bool = False) -> None:
    """Recount files, commands, turns and errors from events inside the window.

    A session that ran for two weeks would otherwise contribute all of its
    edits to every single day's digest.  Sessions parsed before events were
    recorded (or with untimestamped records) keep their lifetime totals.

    Tokens are clipped separately, by ``_clip_tokens``, because they have to be
    measured against the window that was asked for rather than the tightened one
    this is given.
    """
    events = s.get("events") or []
    if not events:
        return

    seen: Dict[str, set] = {"read": set(), "write": set(), "cmd": set()}
    reads: List[str] = []
    writes: List[str] = []
    cmds: List[str] = []
    write_counts: Dict[str, int] = {}
    failed: List[str] = []
    turns = 0
    errors = 0
    for ts, kind, value in events:
        if not _inside(ts, start, end, end_open):
            continue
        if kind == "turn":
            turns += 1
        elif kind == "error":
            errors += 1
            failed.append(value)
        else:
            if kind == "write":
                write_counts[value] = write_counts.get(value, 0) + 1
            if kind in seen and value not in seen[kind]:
                seen[kind].add(value)
                {"read": reads, "write": writes, "cmd": cmds}[kind].append(value)

    s["files_read"] = reads
    s["files_written"] = writes
    s["commands"] = cmds
    s["write_counts"] = write_counts
    s["failed_cmds"] = failed
    s["user_turns"] = turns
    s["errors"] = errors


def _filter_sessions(
    sessions: List[Dict],
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> List[Dict]:
    """Keep sessions that *overlap* the window, not just those that start in it.

    A session that began yesterday and is still running belongs in ``today``;
    filtering on the start timestamp alone made long-running sessions vanish.
    When a session extends past either edge of the window, a copy is returned
    carrying ``window_s`` — the time it spent *working* inside the window — so
    totals reflect the period asked for rather than the session's whole
    lifetime.  Working, not open: a session left running overnight did nothing
    at 3am, so every copy also carries ``active_spans``, the stretches with no
    silence longer than ``parser.IDLE_GAP_S`` in them, and that is what the
    reported figures are built from.  See ``tests/test_idle_gaps.py``.
    """
    out = []
    for s in sessions:
        start = s["start"]
        if start is None:
            continue
        end = s["end"] or start
        if since is not None and end < since:
            continue
        if until is not None and start >= until:
            continue

        # Only an asked-for edge clips.  `now` used to stand in when there was
        # no `until`, which is a no-op on every log written in the past and a
        # wrecking ball on one written by a clock that runs ahead: the window
        # collapsed to the instant the session started, so a full day reported
        # as `0s active` and one turn.  Two clocks are involved in reading
        # somebody else's log, and they do not agree.
        clipped_start = max(start, since) if since is not None else start
        clipped_end = max(end if until is None else min(end, until),
                          clipped_start)
        # An `until` that reaches back into the session is the *exclusive* end
        # of the window, so the clip has to happen even when it lands exactly on
        # the session's last event and nothing looks narrowed.  That is the case
        # a day boundary produces, and it is the one that was counted twice.
        end_open = until is not None and until <= end
        clip = clipped_start > start or clipped_end < end or end_open
        # Before the tightening below moves the edges: what a period cost is
        # measured against the period, not against the session's first and last
        # tool call inside it.  See _clip_tokens.
        asked = (clipped_start, clipped_end, end_open)
        if clip:
            # The window edge is where we started looking, not when anything
            # happened.  A session left open overnight began its day at local
            # midnight, so every hour spent asleep was counted as active and
            # one command at 09:16 headlined as `9h 16m active`.  The counts
            # beside it were already right, which is what made it look sound.
            tightened = _first_and_last_inside(s, clipped_start, clipped_end,
                                               end_open)
            if tightened is not None:
                # Now both edges are things that happened, and an event at one
                # of them is in the window by definition.
                clipped_start, clipped_end = tightened
                end_open = False
        s = dict(s)
        s["win_start"] = clipped_start
        s["win_end"] = clipped_end
        # The stretches it was actually busy, for every session and not just the
        # clipped ones: a session that sits wholly inside the day is exactly the
        # one that can sit idle inside it too, and that is the one that reported
        # `14h 21m active` against three hours of recorded turns.
        spans = active_spans(s, clipped_start, clipped_end, end_open)
        s["active_spans"] = spans
        if clip:
            _clip_tokens(s, *asked)
            _clip_recaps(s, *asked)
            s["window_s"] = sum((b - a).total_seconds() for a, b in spans)
            _clip_counts(s, clipped_start, clipped_end, end_open)
        out.append(s)
    return out


def _filter_project(sessions: List[Dict], needle: str) -> List[Dict]:
    """Keep sessions whose project name or path contains ``needle``."""
    low = needle.lower()
    return [
        s
        for s in sessions
        if low in (s.get("project_name") or "").lower()
        or low in (s.get("project") or "").lower()
    ]


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

# Commands that take a second word.  Everything else takes none, and a stray
# word after them is a typo the person deserves to be told about.
_COMMANDS_WITH_ARG = ("since", "on", "show")


def _log_dirs(home_dir: Optional[str]) -> List[str]:
    home = home_dir or os.environ.get("AGENTLOG_HOME") or os.path.expanduser("~")
    return [
        os.path.realpath(os.path.join(home, ".claude", "projects")),
        os.path.realpath(os.path.join(home, ".codex", "sessions")),
    ]


def _refuses_to_write(target: str, home_dir: Optional[str]) -> Optional[str]:
    """Why this path must not be written to, or None if it is fine.

    agentlog's one promise is that it never writes to the session logs.  A
    digest written over ``~/.claude/projects/.../session.jsonl`` destroys the
    only copy of a day's work, and a single mistyped path is all it takes.
    """
    if not target or target == "-":
        return None
    real = os.path.realpath(target)
    for root in _log_dirs(home_dir):
        if real == root or real.startswith(root + os.sep):
            return ("refusing to write inside the session log directory\n"
                    "  {}\n"
                    "  agentlog never writes to the logs it reads. "
                    "Choose a path outside them.".format(root))
    if real.endswith(".jsonl"):
        return ("refusing to write over {}\n"
                "  that is a session log, not an output file.".format(target))
    return None


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agentlog",
        description="What did your coding agent actually do today?",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  agentlog\n"
            "  agentlog yesterday\n"
            "  agentlog since 3d\n"
            "  agentlog on 2026-07-31\n"
            "  agentlog show 0224e6b8\n"
            "  agentlog list\n"
            "  agentlog today --html digest.html\n"
            "  agentlog week --json\n"
        ),
    )
    p.add_argument("--version", action="version", version=f"agentlog {__version__}")
    p.add_argument(
        "command",
        nargs="?",
        default="today",
        metavar="COMMAND",
        help="today | yesterday | week | since DATE | on DAY | show ID | list "
             "(default: today)",
    )
    p.add_argument(
        "arg",
        nargs="?",
        default=None,
        metavar="ARG",
        help="argument for 'since' (3d, 12h, 2026-07-15), 'on' (2026-07-15, 3d) "
             "or 'show' (session ID prefix)",
    )
    p.add_argument("--html", metavar="FILE", help="write self-contained HTML to FILE")
    p.add_argument(
        "--md",
        metavar="FILE",
        nargs="?",
        const="-",
        help="write Markdown to FILE (or stdout if FILE omitted)",
    )
    p.add_argument("--json", action="store_true", help="print JSON to stdout")
    p.add_argument(
        "--sessions",
        action="store_true",
        help="list every session instead of the per-project digest",
    )
    p.add_argument(
        "--project",
        metavar="NAME",
        help="only include projects whose name or path contains NAME",
    )
    p.add_argument("--all", action="store_true", help="list: show all sessions (no row limit)")
    p.add_argument("--limit", type=int, default=50, metavar="N", help="list: max rows to show (default 50)")
    p.add_argument("--verbose", action="store_true", help="show parsing diagnostics")
    p.add_argument(
        "--home",
        metavar="DIR",
        help="override home directory (default: ~); sets where logs are sought",
    )
    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _as_typed(text):
    """An argument in the form it was typed, not the form the locale allowed.

    Python decodes ``sys.argv`` with the filesystem encoding, and on a machine
    with no locale that encoding is ASCII — so ``--project 設定`` arrives as a
    run of surrogates and matches nothing.  A filter that silently matches
    nothing is the worst way for this to fail: it reads as a quiet day rather
    than as an error.  ``os.fsencode`` gives the bytes back untouched, and the
    shell that sent them was speaking UTF-8.
    """
    if text is None or text.isascii():
        return text                     # the overwhelmingly common case
    try:
        return os.fsencode(text).decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


def _write_utf8_if_the_locale_said_nothing() -> None:
    """Write UTF-8 when the machine claims it can only take ASCII.

    A container with no locale set — a Dockerfile without ``ENV LANG``, cron,
    most of CI — leaves Python believing stdout is ASCII, and then a single em
    dash of our own raises ``UnicodeEncodeError`` halfway through the digest:
    a traceback and half a report, over a character no one chose.

    An ASCII claim is not a claim about the terminal, though.  It is the
    absence of one, and the terminal on the other end is virtually always
    UTF-8.  So we write UTF-8 and keep ``surrogateescape``, which hands back
    unchanged the bytes of any path this machine could not decode — that is
    what makes a name it cannot spell come out spelled right anyway.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if codecs.lookup(stream.encoding or "").name == "ascii":
                stream.reconfigure(encoding="utf-8", errors="surrogateescape")
        except (AttributeError, LookupError, OSError, ValueError):
            pass                        # not a real stream, or already written to


def _stop_writing_down_a_closed_pipe() -> None:
    """Point stdout at nowhere, so nothing is left to fail on the way out.

    Catching the `BrokenPipeError` is only half of it: whatever is still in the
    buffer gets flushed again when the interpreter shuts down, too late for any
    `except` of ours, and that second failure is what prints `Exception ignored
    in: <_io.TextIOWrapper ...>` and turns the exit code into 120.  Redirecting
    the file descriptor gives that flush somewhere harmless to go.
    """
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
    except (AttributeError, OSError, ValueError):
        pass                            # not a real stream; nothing to protect


def main(argv=None) -> int:
    """Entry point, and the one place ctrl-c is allowed to mean something.

    Reading a year of logs takes a moment, and a moment is long enough to
    change your mind in.  Interrupting a command that is taking longer than you
    expected is ordinary; answering it with a traceback is not, because a
    traceback reads as a crash and sends people looking for a bug they caused
    on purpose.  130 is the shell's own spelling of "stopped by ctrl-c", and it
    keeps `agentlog today > digest.md && mail-it` from mailing half a day.

    A closed pipe is the same shape of thing.  `agentlog today | head` and
    `| less` quit with `q` are ordinary too, and they leave us writing into a
    pipe nobody is reading.  141 is 128 + SIGPIPE, the shell's spelling of
    "the reader hung up", and like 130 it is deliberately not one of the
    answers a caller is looking for: a digest that got cut off short reported
    nothing about your day.  The flush lives in a `finally` because argparse
    prints `--help` and `--version` and then exits, so the write that fails is
    one nothing inside `_run` would ever see.
    """
    try:
        try:
            return _run(argv)
        finally:
            sys.stdout.flush()
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        _stop_writing_down_a_closed_pipe()
        return 141


def _run(argv=None) -> int:
    _write_utf8_if_the_locale_said_nothing()
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.project = _as_typed(args.project)

    home_dir = args.home or os.environ.get("AGENTLOG_HOME") or None

    # A word after a command that takes none is a typo, not a no-op.
    if args.arg is not None and args.command not in _COMMANDS_WITH_ARG:
        print(
            "agentlog: '{}' accepts no extra argument (got '{}')\n"
            "  try: agentlog {} | agentlog on {} | agentlog show ID".format(
                args.command, args.arg, args.command, args.arg),
            file=sys.stderr,
        )
        return 2

    if args.limit < 1:
        print(
            "agentlog: --limit must be 1 or more (got {})\n"
            "  use --all to show every session".format(args.limit),
            file=sys.stderr,
        )
        return 2

    for flag, target in (("--html", args.html), ("--md", args.md)):
        if target:
            reason = _refuses_to_write(target, home_dir)
            if reason:
                print("agentlog: {} {}".format(flag, reason), file=sys.stderr)
                return 2

    # ---- 'list' command ----
    if args.command == "list":
        sessions, sources, unusable = find_sessions(home_dir)
        if not sessions:
            _no_sessions_msg(home_dir)
            # "No agent session logs found" is wrong if there are log files and
            # none of them could be used, which is the case this rescues.
            _note_unusable(unusable, args.verbose)
            return 0
        if args.html or args.md is not None:
            print(
                "agentlog: --html and --md are not supported for 'list'; "
                "use a time command (today, week, since ...) instead",
                file=sys.stderr,
            )
            return 2
        # The row limit is a property of the answer, not of how it is printed:
        # --json used to quietly return everything.
        limit = None if getattr(args, "all", False) else args.limit
        truncated = sessions if limit is None else sessions[:limit]
        if args.json:
            print(render_json(truncated))
            _note_unusable(unusable, args.verbose, to_stderr=True)
            return 0
        print(render_list(truncated))
        if limit is not None and len(sessions) > limit:
            print(f"... and {len(sessions) - limit} more  (use --all to see everything)")
        _note_unusable(unusable, args.verbose)
        return 0

    # ---- 'show SESSION_ID' command ----
    if args.command == "show":
        if not args.arg:
            print("agentlog: 'show' requires a session ID", file=sys.stderr)
            return 2
        sessions, sources, unusable = find_sessions(home_dir)
        prefix = args.arg.lower()
        matches = [s for s in sessions if s["id"].lower().startswith(prefix)]
        if not matches:
            print(f"agentlog: no session found matching '{args.arg}'", file=sys.stderr)
            # The session being asked for by name may be one of the files that
            # could not be read, which makes this the most useful place to say.
            _note_unusable(unusable, args.verbose, to_stderr=True)
            return 2
        if len(matches) > 1:
            print(
                f"agentlog: {len(matches)} sessions match '{args.arg}'; "
                "showing the first. Use more characters to disambiguate:",
                file=sys.stderr,
            )
            for m in matches:
                print(f"  {m['id']}", file=sys.stderr)
        if args.html or args.md is not None:
            print(
                "agentlog: --html and --md are not supported for 'show'; "
                "use a time command (today, week, since ...) instead",
                file=sys.stderr,
            )
            return 2
        if args.json:
            print(render_json([matches[0]]))
            _note_unusable(unusable, args.verbose, to_stderr=True)
            return 0
        print(render_show(matches[0]))
        _note_unusable(unusable, args.verbose)
        return 0

    # ---- time-range commands ----
    if args.command == "since":
        if not args.arg:
            print("agentlog: 'since' requires a date or offset (e.g. since 3d)", file=sys.stderr)
            return 2
        since_dt = _parse_since(args.arg)
        if since_dt is None:
            print(
                f"agentlog: could not parse '{args.arg}' — "
                "use an ISO date (2026-07-01) or an offset (3d, 12h, 2w)",
                file=sys.stderr,
            )
            return 2
        until_dt = None
        period_label = f"since {args.arg}"

    elif args.command == "on":
        if not args.arg:
            print("agentlog: 'on' requires a date or a day offset "
                  "(e.g. on 2026-07-31, on 3d)", file=sys.stderr)
            return 2
        window = _parse_day(args.arg)
        if window is None:
            msg = (f"agentlog: '{args.arg}' does not name a day — "
                   "use an ISO date (2026-07-31) or a number of days ago (3d)")
            # `12h` is not a mistake, it is the wrong command for it.  Say which
            # one is right, and say it only to the person who typed a length.
            if _parse_since(args.arg) is not None and args.arg.strip()[-1:].lower() in "hw":
                msg += ("\n  that is a length, not a day: "
                        f"try 'agentlog since {args.arg}'")
            print(msg, file=sys.stderr)
            return 2
        since_dt, until_dt = window
        period_label = f"on {since_dt.date().isoformat()}"

    elif args.command in ("today", "yesterday", "week"):
        since_dt = _since_for_period(args.command)
        until_dt = _until_for_period(args.command)
        period_label = args.command

    else:
        print(
            f"agentlog: unknown command '{args.command}'\n"
            "  try: agentlog today | yesterday | week | since DATE | on DAY\n"
            "       agentlog show ID | agentlog list",
            file=sys.stderr,
        )
        return 2

    # Load and filter
    sessions, sources, unusable = find_sessions(home_dir)
    if not sessions and not sources:
        _no_sessions_msg(home_dir)
        return 0

    filtered = _filter_sessions(sessions, since=since_dt, until=until_dt)
    if args.project:
        filtered = _filter_project(filtered, args.project)

    # ---- HTML output ----
    if args.html:
        html_str = render_html(filtered, sources, period_label)
        try:
            with open(args.html, "w", encoding="utf-8") as fh:
                fh.write(html_str)
            print(f"wrote {args.html}")
            # A report saved to a file outlives the terminal it was made in, so
            # this is the last chance to say it was built from part of the logs.
            _note_unusable(unusable, args.verbose)
        except OSError as exc:
            print(f"agentlog: could not write HTML: {exc}", file=sys.stderr)
            return 2

    # ---- Markdown output ----
    if args.md is not None:
        md_str = render_markdown(filtered)
        if args.md == "-":
            print(md_str)
            # `--md -` is piped into a file or a paste buffer often enough that
            # the note has to stay out of stdout, same as --json.
            _note_unusable(unusable, args.verbose, to_stderr=True)
        else:
            try:
                with open(args.md, "w", encoding="utf-8") as fh:
                    fh.write(md_str)
                print(f"wrote {args.md}")
                _note_unusable(unusable, args.verbose)
            except OSError as exc:
                print(f"agentlog: could not write Markdown: {exc}", file=sys.stderr)
                return 2

    # ---- JSON output ----
    if args.json:
        print(render_json(filtered))
        _note_unusable(unusable, args.verbose, to_stderr=True)
        return 0

    # ---- Default: plain text ----
    if not args.html and args.md is None:
        if not filtered:
            when = period_label
            # Naming the filter matters: an empty result with a --project flag
            # usually means the name was misspelled, not that nothing happened.
            if args.project:
                when += f" · project matching '{args.project}'"
            print(f"no sessions found for: {when}")
            if args.verbose:
                print(f"  searched {len(sessions)} total sessions")
        elif args.sessions:
            print(render_text(filtered, verbose=args.verbose))
        else:
            print(render_digest(filtered, period_label, verbose=args.verbose))
        _note_unusable(unusable, args.verbose)

    return 0


def _note_unusable(unusable, verbose: bool, to_stderr: bool = False) -> None:
    """Say that some log files were not counted, if any were not.

    Goes to stderr under --json, where stdout is a published contract (a bare
    array of sessions) and a warning appended to it would break every reader.
    """
    note = render_unusable(unusable, verbose)
    if note:
        print("\n" + note if not to_stderr else note,
              file=sys.stderr if to_stderr else sys.stdout)


def _no_sessions_msg(home_dir: Optional[str]) -> None:
    home = home_dir or os.path.expanduser("~")
    print(
        "No agent session logs found.\n\n"
        "agentlog looks for:\n"
        f"  Claude Code:  {os.path.join(home, '.claude', 'projects', '**', '*.jsonl')}\n"
        f"  Codex:        {os.path.join(home, '.codex', 'sessions', '**', '*.jsonl')}\n\n"
        "Start a session with Claude Code (claude) or Codex (codex) and run agentlog again."
    )


if __name__ == "__main__":
    sys.exit(main())
