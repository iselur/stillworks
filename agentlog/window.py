"""The stretch of time a digest is about, and what it does to a session.

Every command this tool has except `show` and `list` is a question about a
window: `today`, `week`, `since 3d`, `on 2026-07-31`.  Answering one takes two
steps that had been spread across a dozen module-level functions in `cli.py` —
work out which two moments were asked for, then cut every session down to what
it did between them — and a caller had to know all twelve to ask the question,
plus which of them applied to which command, plus how to word the four
different ways the argument can be wrong.

What a caller needs to know is smaller than that:

    window = Window.parse("on", "2026-07-31")      # or Unparseable
    for session in window.clip(sessions): ...
    print(window.label)

Everything below `Window` is the implementation, and it is where the hard-won
corrections live: midnight resolved against the date being asked about rather
than today's offset, a window edge that belongs to exactly one side of it,
tokens counted against the period asked for and commands against the period
tightened onto real events, and the difference between a session that spent
nothing here and one we cannot see inside.  Each is documented where it sits.

The moment is an argument.  `Window.parse` takes `now`, and passes it down to
everything that needs to know what day it is, because "what does `week` mean"
is a question about a moment, and a test that cannot name the moment is asking
a different question each time it runs — and a different one again after
midnight.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from .parser import active_spans

#: The commands that name a stretch of time.  `show` and `list` do not.
PERIODS = ("today", "yesterday", "week")
DATED = ("since", "on")


class Unparseable(ValueError):
    """The window could not be worked out, and this is what to tell the person.

    The message is the whole of what should be printed after `agentlog: `,
    newlines and all — some of these answers are two lines, because saying
    which command *is* right is more use than saying this one is wrong.
    """


class Window:
    """A stretch of time, and the sessions cut down to what happened in it.

    `since` is inclusive and `until` is exclusive; `until` is None for a window
    with no end, which is what `since 3d` asks for.  `label` is how the window
    should be named in a heading.
    """

    __slots__ = ("since", "until", "label")

    def __init__(self, since: Optional[datetime], until: Optional[datetime],
                 label: str) -> None:
        self.since = since
        self.until = until
        self.label = label

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Window({!r}, {!r}, {!r})".format(
            self.since, self.until, self.label)

    def __eq__(self, other) -> bool:
        if not isinstance(other, Window):
            return NotImplemented
        return ((self.since, self.until, self.label)
                == (other.since, other.until, other.label))

    @classmethod
    def parse(cls, command: str, arg: Optional[str] = None,
              now: Optional[datetime] = None) -> "Window":
        """The window a time command names, or `Unparseable` saying why not.

        `command` is one of today / yesterday / week / since / on.  Anything
        else raises, because a window is the only thing this module knows how
        to answer and pretending otherwise would hand the caller a window that
        means nothing.
        """
        if command in PERIODS:
            return cls(_since_for_period(command, now),
                       _until_for_period(command, now), command)

        if command == "since":
            if not arg:
                raise Unparseable(
                    "'since' requires a date or offset (e.g. since 3d)")
            since = _parse_since(arg, now)
            if since is None:
                raise Unparseable(
                    "could not parse '{}' \u2014 use an ISO date (2026-07-01) "
                    "or an offset (3d, 12h, 2w)".format(arg))
            return cls(since, None, "since {}".format(arg))

        if command == "on":
            if not arg:
                raise Unparseable(
                    "'on' requires a date or a day offset "
                    "(e.g. on 2026-07-31, on 3d)")
            day = _parse_day(arg, now)
            if day is None:
                message = ("'{}' does not name a day \u2014 use an ISO date "
                           "(2026-07-31) or a number of days ago (3d)"
                           .format(arg))
                # `12h` is not a mistake, it is the wrong command for it.  Say
                # which one is right, and say it only to the person who typed a
                # length.
                if (_parse_since(arg, now) is not None
                        and arg.strip()[-1:].lower() in "hw"):
                    message += ("\n  that is a length, not a day: "
                                "try 'agentlog since {}'".format(arg))
                raise Unparseable(message)
            since, until = day
            return cls(since, until, "on {}".format(since.date().isoformat()))

        raise Unparseable(
            "unknown command '{}'\n"
            "  try: agentlog today | yesterday | week | since DATE | on DAY\n"
            "       agentlog show ID | agentlog list".format(command))

    def clip(self, sessions: List[Dict]) -> List[Dict]:
        """The sessions that overlap this window, cut down to what they did in it.

        A copy per session; the originals are left alone, because `list` and
        `show` read the same parse and are not about a window at all.
        """
        return _filter_sessions(sessions, since=self.since, until=self.until)


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


def _today_local(now: Optional[datetime] = None) -> date:
    """Today's date where the person running this lives.

    `now` is an argument because a window is a question about a moment, and a
    test that cannot name the moment can only ask about this one -- which is a
    different question every time it runs, and a different one again at
    midnight.
    """
    return (now or datetime.now(timezone.utc)).astimezone().date()


def _parse_since(value: str,
                 now: Optional[datetime] = None) -> Optional[datetime]:
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
            return (now or datetime.now(timezone.utc)) - delta
        except (OverflowError, OSError):
            # timedelta gives out long before int does.
            return None

    # ISO date
    try:
        d = date.fromisoformat(value)
        return _local_midnight(d)
    except ValueError:
        return None


def _parse_day(value: str, now: Optional[datetime] = None
               ) -> Optional[Tuple[datetime, datetime]]:
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
            day = _today_local(now) - timedelta(days=n)
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


def _since_for_period(period: str,
                      now: Optional[datetime] = None) -> Optional[datetime]:
    """Return the start-of-window datetime for a named period."""
    today = _today_local(now)
    if period == "today":
        d = today
    elif period == "yesterday":
        d = today - timedelta(days=1)
    elif period == "week":
        d = today - timedelta(days=6)
    else:
        return None
    return _local_midnight(d)


def _until_for_period(period: str,
                      now: Optional[datetime] = None) -> Optional[datetime]:
    """The exclusive end of a named window, or None if it has no end.

    A named day ends when the day ends, not when you happened to run this.
    `today` runs to midnight tonight — the alternative is that a log written by
    a clock two minutes fast falls outside "today", which is not what anyone
    means by the word.  `since 3d` genuinely has no end, so it gets None.
    """
    today = _today_local(now)
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
