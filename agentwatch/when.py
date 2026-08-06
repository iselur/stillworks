"""How a person spells a moment on the command line.

Two of these tools take a moment from whoever is running them.  `agentlog since
10m` and `agentwatch --since 10m` ask the same question — from when? — and until
now they answered it with two parsers written independently.

They had drifted, and neither side could see it.  `agentwatch` knew four units
and `agentlog` knew three, so `agentlog since 10m` was a usage error while
`agentwatch --since 10m` was the first example in its own README.  Nobody
decided that: minutes went into the live tail because a live tail is about the
last few minutes, and the other tool was never opened.  `agentlog` also refused
`2026-08-03T14:30`, which the other took, so one string typed at the two
commands meant "from half past two" in one and "that is not a date" in the
other.

The help text was a third copy of the same fact and had drifted too.  `agentlog
--help` offered `3d, 12h, 2w` — a list that agreed with its parser about the
units it had and said nothing about the one it was missing, and that would have
gone on offering `2w` for as long as it took anybody to notice weeks had been
dropped.  A tool whose help promises a spelling it does not accept is worse than
one that never offered it: the person who typed it has been told, by the
program, that it works.

So `_OFFSETS` is the one table and everything else is read off it.  The parser
matches against it, and the examples printed in help and in every error message
are built from it.  Adding a unit is one edit, and there is no second place left
to forget.

It has to stay copied: nothing in this family imports outside its own package —
the promise `pip install stillworks` makes, enforced by
`test_every_import_is_stdlib_or_the_packages_own` — so a shared module is not on
offer.  What is on offer is a copy that cannot drift, pinned byte-for-byte by
`test_a_moment_is_spelled_the_same_way_in_both.py` in the stillworks tree.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

#: The lengths of time a moment can be spelled as: the letter someone types,
#: the `timedelta` keyword it means, and the number the examples show it with.
#: One table, because a unit the parser knows and the help text does not — or
#: the other way round — is the whole of what this module exists to prevent.
_OFFSETS = (("m", "minutes", 10),
            ("h", "hours", 2),
            ("d", "days", 3),
            ("w", "weeks", 1))

_UNITS = {letter: keyword for letter, keyword, _ in _OFFSETS}

# A space before the unit because people type `3 d`, and any case because
# `--since 2H` is not a mistake worth an error message.
_OFFSET = re.compile(r"^(\d+)\s*([{}])$".format("".join(_UNITS)), re.IGNORECASE)

#: What to show someone who typed something else — the same sentence in both
#: tools' help and in every message either of them prints about a moment.
HOW_TO_SPELL_IT = "{} or a date like 2026-08-03".format(
    ", ".join("{}{}".format(number, letter)
              for letter, _, number in _OFFSETS))


def parse_moment(raw: Optional[str],
                 now: Optional[datetime] = None) -> datetime:
    """The moment `raw` names, as an aware datetime.

    Takes a length of time back from now — `10m`, `2h`, `3d`, `1w`, in any
    case, with or without a space before the unit — or an ISO date or datetime:
    `2026-08-03`, `2026-08-03T14:30`, `2026-08-03T14:30:00Z`.

    A bare date is midnight *on that date* in local time, resolved by the
    platform against the rules in force then rather than midnight plus today's
    offset.  The difference is an hour, for half the year, in any zone that
    observes daylight saving, and it does not look like an error — it looks
    like the log.  See test_day_boundaries_across_dst.py.

    Raises `ValueError` whose message is the whole of what to print for the
    person who typed it.  The message is in here rather than at the two call
    sites so that no caller can word it in a way that disagrees with what is
    actually accepted, which is exactly how the two parsers came apart.

    `now` is an argument because `10m` is a question about a moment, and a test
    that cannot name the moment asks a different question every time it runs.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty; try {}".format(HOW_TO_SPELL_IT))

    match = _OFFSET.match(text)
    if match:
        amount = int(match.group(1))
        if amount <= 0:
            # `0d` is a window from now until now, and the regex takes no sign
            # so `-3d` — the future — never gets this far.
            raise ValueError("{!r} is not a length of time; try {}"
                             .format(raw, HOW_TO_SPELL_IT))
        try:
            return ((now or datetime.now(timezone.utc))
                    - timedelta(**{_UNITS[match.group(2).lower()]: amount}))
        except OverflowError:
            # timedelta gives out long before int does.
            raise ValueError(
                "{!r} is further back than time goes".format(raw))

    if text[-1:] in ("Z", "z"):
        # `fromisoformat` learned to read a trailing Z in 3.11.  This package
        # supports 3.9, where it does not, so this line is load-bearing on the
        # floor and a no-op on anything newer.
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("{!r} is not a time; try {}"
                         .format(raw, HOW_TO_SPELL_IT))
    if moment.tzinfo is None:
        try:
            moment = moment.astimezone()
        except (OverflowError, OSError, ValueError):
            # Year 1 in a zone east of Greenwich is before datetime.min.
            raise ValueError(
                "{!r} is further back than time goes".format(raw))
    return moment


def is_a_length(raw: Optional[str],
                now: Optional[datetime] = None) -> bool:
    """Whether `raw` names a length of time back from now rather than a date.

    `agentlog on 12h` is not a typo, it is the wrong command for what was
    meant, and the useful thing to print is which command is right.  Saying so
    means knowing which spellings are lengths — and that list lives here, so a
    unit added to `_OFFSETS` starts being recognised without a second edit.

    False for a length that `since` would refuse anyway (`0d`, or a number of
    weeks larger than time), because pointing somebody at a second command that
    will also reject them is worse than saying nothing.
    """
    if not _OFFSET.match((raw or "").strip()):
        return False
    try:
        parse_moment(raw, now)
    except ValueError:
        return False
    return True
