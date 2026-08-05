"""Everything that has to be true to print one event, in one place.

`render` knows how to draw things: a mark set, a colour decision, a width, a
dated rule, a line, a JSON object.  Knowing how to draw them is not the same as
knowing the order to do it in, and that order was the caller's problem —
`agentwatch --once` and `agentwatch --follow` each had to learn seven names,
call them in the right sequence, and carry three derived values and one piece of
mutable state between calls on the layout's behalf.

That is a lot to ask of a command whose job is deciding which events to show,
and it went wrong exactly where you would expect.  The width was read once,
before a loop that runs for hours, so a terminal resized during a follow was
never noticed: every line stayed fitted to the width the window had at startup,
for as long as the watch ran.  Nobody wrote that rule down anywhere, because it
was not a rule — it was a fact the caller happened to be holding, and facts go
stale.

So the whole sequence moved here, behind two things: make one of these from the
stream and the flags, then hand it events.  There is no order left to get wrong,
and the width is read for the line it is about to fit rather than for a line
printed some hours ago.
"""

from __future__ import annotations

import sys
from typing import Dict, Optional

from .render import (
    day_rule, format_event, format_json, marks_for, terminal_width, use_color,
    write_line,
)


class Printer:
    """One event in, one line out — or two, when the day turns over.

    ``color`` is the ``--no-color`` answer: ``True`` or ``False`` to insist,
    ``None`` to let the stream and ``NO_COLOR`` decide.  ``as_json`` picks the
    machine stream, which has no marks, no colour, no width and no day rule —
    every one of those is a fact about a terminal, and nothing downstream of a
    pipe is one.

    The dated rule is not an event.  It is printed on the way past and is not
    reported, so a caller counting what it showed cannot accidentally count it:
    the count is of events handed in, which is a number it already has.
    """

    def __init__(self, stream=None, color: Optional[bool] = None,
                 as_json: bool = False) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._marks = marks_for(self._stream)
        self._color = use_color(self._stream, color)
        self._as_json = as_json
        # The day of the last event printed, so the rule knows when it moved.
        # It lives for as long as the printer does, because a follow crosses
        # midnight in the middle of a run rather than between two of them.
        self._day = None

    def write(self, event: Dict) -> None:
        """Print one event, with its dated rule if the day just changed."""
        if self._as_json:
            write_line(format_json(event), self._stream)
            return
        # Read per line, not once per printer.  A follow runs for hours and the
        # window it runs in can be resized at any point in them; a width taken
        # at startup is a promise about the terminal that nobody made.  The cost
        # is one ioctl on a line that is already making a write and a flush, so
        # the syscall this adds is not the one worth counting.
        width = terminal_width()
        rule, self._day = day_rule(event, self._day, width, self._color)
        if rule:
            write_line(rule, self._stream)
        write_line(format_event(event, self._marks, self._color, width),
                   self._stream)
