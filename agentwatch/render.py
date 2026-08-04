"""One event, one line, narrow enough to read while it scrolls.

The layout is fixed-width on purpose.  A live stream is read by glancing at it,
and a glance needs the marks in the same column every time — so the project
column never resizes, even when a longer name shows up later.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import unicodedata
from typing import Dict, Optional

PROJECT_WIDTH = 12
MIN_TEXT = 20

# Everything on the line that is not the project or the text: the clock, the two
# gaps around the project column, the mark, and the space after it.
_FIXED = 8 + 2 + 2 + 1 + 1

# The mark says what happened; it is the only thing scanned at speed.
MARKS = {
    "cmd": "$",
    "write": "✎",   # pencil
    "read": "·",    # middle dot
    "error": "✗",   # ballot X
    "turn": "»",    # right guillemet
}

ASCII_MARKS = {
    "cmd": "$",
    "write": "w",
    "read": ".",
    "error": "!",
    "turn": ">",
}

_COLORS = {
    "cmd": "\033[36m",      # cyan
    "write": "\033[32m",    # green
    "read": "\033[90m",     # grey
    "error": "\033[31m",    # red
    "turn": "\033[35m",     # magenta
}
_DIM = "\033[90m"
_RESET = "\033[0m"


def marks_for(stream) -> Dict[str, str]:
    """Unicode marks, unless this stream cannot carry them.

    Falling back is not cosmetic: on a terminal claiming ASCII, an unencodable
    glyph raises mid-write and takes the watcher down with it.
    """
    encoding = getattr(stream, "encoding", None) or ""
    try:
        "".join(MARKS.values()).encode(encoding or "ascii")
    except (LookupError, UnicodeEncodeError, TypeError):
        return dict(ASCII_MARKS)
    return dict(MARKS)


def use_color(stream, force: Optional[bool] = None) -> bool:
    """Colour only where it will be seen and is not unwelcome."""
    if force is not None:
        return force
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def terminal_width(default: int = 100) -> int:
    try:
        return max(40, min(200, os.get_terminal_size().columns))
    except OSError:
        pass
    try:
        return max(40, min(200, int(os.environ.get("COLUMNS", "") or default)))
    except ValueError:
        return default


def _clock(event: Dict) -> str:
    at = event.get("at")
    if at is None:
        return "  --:--"
    try:
        return at.astimezone().strftime("%H:%M:%S")
    except (ValueError, OSError):
        return "  --:--"


def _local_day(event: Dict):
    """The event's date in the reader's timezone, or None if it has no stamp."""
    at = event.get("at")
    if at is None:
        return None
    try:
        return at.astimezone().date()
    except (ValueError, OSError):
        return None


def day_rule(event: Dict, previous, width: int = 100, color: bool = False):
    """A dated rule when the day changes, and nothing when it does not.

    Returns ``(line_or_None, day_to_pass_back_next_time)``.

    Every event line carries a clock and no date, which is right for a tailer:
    everything on screen happened moments ago, and a date repeated down the page
    is a column of the same word.  Over history it is not right at all — a week
    of events under `--since 1w` printed four lines with the same `09:22:58` on
    them, three byte-identical, for things that happened days apart.

    So the date is printed when it changes, and only then.  Watching live never
    crosses a day, so the common case still prints exactly what it did before;
    the cost lands only on the runs that were ambiguous.  Midnight during a long
    follow crosses one, which is the other half of the same problem.

    The first event is a day change only if it is not today's.  Starting a live
    session with "today" written across the top is noise about the one date
    nobody has to be told.
    """
    day = _local_day(event)
    if day is None:
        # A stampless event is not evidence about what day it is, so it must not
        # move the marker on — the next real event would then not be announced.
        return None, previous
    if previous is None:
        try:
            today = _dt.datetime.now().astimezone().date()
        except (ValueError, OSError):
            today = None
        if day == today:
            return None, day
    elif day == previous:
        return None, day
    return _rule_line(day, width, color), day


def _rule_line(day, width: int, color: bool) -> str:
    # The year only when it is not this one: on the ordinary run it is four
    # characters of nothing, and on the odd one it is the whole point.
    try:
        this_year = _dt.datetime.now().astimezone().year
    except (ValueError, OSError):
        this_year = day.year
    label = day.strftime("%a %d %b").replace(" 0", " ")
    if day.year != this_year:
        label += day.strftime(" %Y")
    text = "── {} ".format(label)
    # Never wider than the terminal: a rule that wraps puts a stray line of
    # dashes under itself and breaks the fixed layout it exists to organise.
    if len(text) > width:
        text = text[:width]
    else:
        text += "─" * (width - len(text))
    return _DIM + text + _RESET if color else text


def _clean(text: str) -> str:
    """Text that cannot do anything to the terminal it is printed on.

    Everything here is text an agent chose, arriving from a file some other
    program wrote.  Printed as-is, an escape sequence in a command would clear
    the screen, retitle the window, or leave every later line coloured — and a
    right-to-left override would let a command read as something it is not.  So
    both control characters (Cc) and formatting characters (Cf, which is where
    the bidi overrides live) become spaces, and the caller's whitespace collapse
    then removes them.  Cf also holds the joiners inside some emoji, which is a
    price worth paying to make this whole class of problem impossible.
    """
    if not any(ord(c) < 32 or ord(c) == 127 or ord(c) > 126 for c in text):
        return text                     # the overwhelmingly common case
    return "".join(
        " " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c
        for c in text)


def _cells(char: str) -> int:
    """How many terminal cells one character is drawn in."""
    if unicodedata.category(char) in ("Mn", "Me"):
        # Drawn on top of the character before it; it occupies no cell of its own.
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def display_width(text: str) -> int:
    """The width of a string in terminal cells, which is not its length.

    Every column in this layout is measured in cells, and ``len`` counts
    characters.  On a Japanese codebase the two differ by a factor of two, so a
    line that counts characters runs past the edge and wraps — and a wrapped
    line costs the fixed layout far more than a truncated one does.
    """
    if text.isascii():
        return len(text)                    # the overwhelmingly common case
    return sum(_cells(c) for c in text)


def _pad(text: str, width: int) -> str:
    """``ljust`` in cells rather than characters."""
    return text + " " * max(0, width - display_width(text))


def _fit(text: str, width: int) -> str:
    """One line, at most ``width`` cells, with a visible cut."""
    text = " ".join(_clean(text).split())   # newlines in a stream ruin the layout
    if display_width(text) <= width:
        return text
    if width <= 1:
        return "…"[:max(0, width)]
    # Cut by cells too: stopping after ``width - 1`` characters would leave a
    # double-width line one cell over the edge, which is the whole bug.
    out, used = [], 0
    for char in text:
        cells = _cells(char)
        if used + cells > width - 1:
            break
        out.append(char)
        used += cells
    return "".join(out) + "…"


def _shorten_path(path: str, project: str) -> str:
    """A written file, said the way the person watching would say it."""
    if not path:
        return ""
    if project:
        marker = os.sep + project + os.sep
        idx = path.find(marker)
        if idx >= 0:
            return path[idx + len(marker):]
    home = os.path.expanduser("~")
    if home and home != os.sep and path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def format_event(event: Dict, marks: Dict[str, str], color: bool = False,
                 width: int = 100) -> str:
    """The whole line for one event."""
    kind = event.get("kind", "")
    mark = marks.get(kind, "?")
    # On a narrow terminal something has to give, and it is the project column:
    # holding it at full width would push the line past the edge and wrap it,
    # which costs the fixed layout far more than a truncated name does.
    room = max(4, min(PROJECT_WIDTH, width - _FIXED - MIN_TEXT))
    project = _pad(_fit(event.get("project") or "-", room), room)
    text = event.get("text") or ""
    if kind == "write":
        text = _shorten_path(text, (event.get("project") or ""))
    elif kind == "turn":
        text = "you"
    elif kind == "error" and not text:
        text = "(a call failed)"
    text = _fit(text, max(MIN_TEXT, width - _FIXED - room))
    stamp = _clock(event)
    if color:
        return "{}{}{}  {}  {}{}{} {}".format(
            _DIM, stamp, _RESET, project,
            _COLORS.get(kind, ""), mark, _RESET, text)
    return "{}  {}  {} {}".format(stamp, project, mark, text)


def format_json(event: Dict) -> str:
    """One JSON object per line, for anything downstream of this."""
    at = event.get("at")
    return _one_line(json.dumps({
        "at": at.isoformat() if at is not None else None,
        "kind": event.get("kind", ""),
        "text": event.get("text", ""),
        "project": event.get("project", ""),
        "session": event.get("session", ""),
        "source": event.get("source", ""),
    }, ensure_ascii=False, sort_keys=True))


def _one_line(row: str) -> str:
    """Escape the two characters that are a newline to a reader but not to us.

    ``json.dumps`` escapes every control character, but U+2028 and U+2029 are
    not control characters — they pass through, and JSON-lines output that is
    one object per line silently becomes two.  Both are legal inside a JSON
    string as an escape, so this stays valid JSON and round-trips unchanged.
    """
    if "\u2028" in row or "\u2029" in row:
        row = row.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return row


def write_line(line: str, stream=None) -> None:
    """Print a line now, not when the buffer feels like it.

    A watcher whose output is piped into ``less`` or ``tee`` and appears in
    4 KB bursts is not a live view of anything.
    """
    stream = stream or sys.stdout
    try:
        stream.write(line + "\n")
        stream.flush()
    except UnicodeEncodeError:
        stream.write(line.encode("ascii", "replace").decode("ascii") + "\n")
        stream.flush()
    except (BrokenPipeError, ValueError):
        raise
