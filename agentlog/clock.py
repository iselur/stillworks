"""What a session's clock says, in the words a reader gets.

Four views print how long a session ran — the text digest, the HTML digest,
the Markdown document and the `list` table — and a fifth, `show`, prints it in
the most detail of any of them.  Each of them worked the answer out for itself,
inline, in three or four lines that looked the same at a glance and were not.
They disagreed in two ways that a reader could see:

A session that started at 23:40 and finished at 00:15 rendered in the text
digest as ``2026-08-04 23:40 – 2026-08-05 00:15`` and in the HTML digest as
``2026-08-04 23:40 – 00:15``, which reads as thirty-five minutes of running
backwards.  The text view had the rule and a comment explaining it; the HTML
view was written later and never got either, because the rule was not anywhere
it could be got from — it was four lines inside one renderer.

And `show`, the view you open when you want the truth about one session,
printed ``duration`` as the time the session was *open*.  Every other view
reports the time it was *working*, which is the distinction the digest exists
to make: a session left open over lunch was not working through it.  So the
same session answered ``40m 00s`` in the table and ``6h 12m`` in the detail,
and the detail was the one that was wrong.

Neither was a bug in anybody's formatter.  Both were rules that lived in a
caller, and a rule in a caller is a rule the next caller does not have.  So
they live here: ask this module what a session's time says and there is one
answer, which every view prints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from .parser import active_spans

# How much longer a session may sit open than it worked before that is worth
# saying out loud.  Under a minute is rounding, and a second number that merely
# repeats the first is a number the reader has to read to find that out.
IDLE_SLACK_S = 60


def duration(seconds: Optional[float]) -> str:
    """A length of time, at the coarsest unit that still says something."""
    if seconds is None or seconds < 0:
        return "?"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def time_of_day(dt: Optional[datetime]) -> str:
    """``HH:MM`` in the reader's timezone, not the one it was logged in."""
    if dt is None:
        return "?"
    return dt.astimezone().strftime("%H:%M")


def at(dt: Optional[datetime]) -> str:
    """A date and a time, in the reader's timezone."""
    if dt is None:
        return "?"
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def working_seconds(s: Dict) -> Optional[float]:
    """Seconds to report for a session: the time it spent working.

    Not the time it was open.  A session sitting idle from lunch until evening
    was not working through the afternoon, and counting from its first event to
    its last said it was.  ``duration_s`` still answers "how long was this
    open", which is a real and different question, and the views that have room
    for both print both.
    """
    spans = s.get("active_spans")
    if spans is None:
        # `list` and `show` ask for no window, so nothing has worked the spans
        # out for them.  The column is headed DUR in both places and it should
        # mean the same thing there as in the digest.
        spans = active_spans(s)
    if spans:
        return sum((b - a).total_seconds() for a, b in spans)
    if s.get("window_s") is not None:
        return s["window_s"]
    return s["duration_s"]


def idled(s: Dict) -> bool:
    """Whether this session was open appreciably longer than it was busy."""
    active = working_seconds(s)
    whole = s.get("duration_s")
    return (active is not None and whole is not None
            and active + IDLE_SLACK_S < whole)


def when(s: Dict) -> str:
    """When a session ran, as one phrase.

    The far end of the range is a bare ``HH:MM`` while the session stayed
    inside one day, because the date beside it already said which day that is.
    Once it crosses midnight the bare time is a lie of omission — ``23:40 –
    00:15`` reads as running backwards — so the far end says its own date.
    """
    if not s.get("start"):
        return "?"
    phrase = at(s["start"])
    end = s.get("end")
    if not end or end == s["start"]:
        return phrase
    same_day = end.astimezone().date() == s["start"].astimezone().date()
    return phrase + " – " + (time_of_day(end) if same_day else at(end))


def how_long(s: Dict, qualified: bool = True) -> str:
    """How long a session worked, and — unless told not to — why that differs.

    The plain number on its own is the one that misleads, so saying why is the
    default and a caller opts out of it.  There is one caller that does: the
    ``DUR`` column of `list` is eight characters wide and a phrase does not go
    in it, and a column of bare numbers is not making the claim a sentence
    would.

    Two things make the working time differ from the wall clock, and they are
    different things to be told.  A reporting window means we only looked at
    part of the session and the rest is outside the question asked.  Idling
    means we looked at all of it and it was not busy for most of it.
    """
    phrase = duration(working_seconds(s))
    if not qualified:
        return phrase
    if s.get("window_s") is not None:
        return phrase + f" in window, {duration(s['duration_s'])} total"
    if idled(s):
        # It was open longer than it was busy, and the time range printed
        # beside this says so -- without the second number the two look like
        # they disagree.
        return phrase + f" active, {duration(s['duration_s'])} open"
    return phrase
