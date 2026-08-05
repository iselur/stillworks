"""What happens to text an agent wrote when it is printed on a terminal.

Every tool in this family prints text it did not write: a filename, a command,
a commit subject, a snapshot message.  That is one seam -- the place where text
from outside meets a terminal -- and four packages had found it separately.

Left alone, an escape sequence in that text clears the screen, retitles the
window, or leaves every later line coloured; a right-to-left override makes a
path read as a different file from the one that will be opened; a raw newline
forges a row that looks exactly like a real one in a listing somebody is
reading to decide what to restore.  All four packages knew that, and all four
wrote out the same four Unicode categories -- ``Cc`` (control, where the
escapes live), ``Cf`` (format, where the bidi overrides live), and ``Zl`` /
``Zp`` (the two separators that are a line break to a reader but not to
``str.splitlines``).

Then they drifted, as copies do, and by the time this file was written the
four disagreed about what to *do* with such a character:

  agentdiff   escaped it and quoted the string
  agentwatch  turned it into a space
  unedit      turned it into a space
  agentlog    deleted it

Deleting is the one that is wrong, and agentdiff had already found out why and
written it down: two path components printed with nothing between them read as
one component that is not on disk, and nothing on screen says anything was
dropped.  agentlog had the same bug in a second form.  It lays a table out in
cells, and it measures the column *before* sanitising the assembled string at
the end -- so a hidden character was counted as one cell, given one cell, and
then removed, and that row's right-hand column stood one cell left of every
other row's.  Neither package could have learned it from the other.

So the fact lives here once, and the three answers to it are three functions
with the reason for each written down, rather than one answer per package
arrived at by nobody:

    one_line(text)   text that must not become two rows -- newlines go too
    block(text)      text that is allowed to have lines in it
    row(text, n)     one_line, and a bound, for a table cell
    quoted(text)     text naming something the reader has to find on disk
    display_width(t) how wide it is drawn, which is not how long it is
    pad(text, n)     ljust in cells rather than characters

Choosing between them is a decision about a particular column in a particular
tool, so it stays in that tool.  Which characters a terminal obeys, how many
cells a character is drawn in, and how a character that cannot be shown is
written so it can be read -- those are facts about terminals, and they are
here.

This file is byte-identical in the four packages that print, and a family test
says so.  It has to be copied rather than imported: nothing in this family
imports another package, which is the promise `pip install stillworks` makes.
"""

from __future__ import annotations

import unicodedata

# The categories a terminal acts on instead of showing.  Cc is the control
# characters, which is where the escapes live.  Cf is the formatting
# characters, which is where the bidi overrides live -- and also the joiners
# inside some emoji, which is a price worth paying to make the whole class of
# problem impossible.  Zl and Zp are the two separators that are a line break
# to a reader but not to `str.splitlines`.
_HIDDEN = ("Cc", "Cf", "Zl", "Zp")

# How a character that cannot be shown is written so it can be read.  These
# match git's for the ones anybody meets; rarer characters get `\xNN` or
# `\uNNNN` rather than git's octal, because this is a Python tool and a reader
# is likelier to know what those mean.
_ESCAPES = {"\a": "\\a", "\b": "\\b", "\t": "\\t", "\n": "\\n",
            "\v": "\\v", "\f": "\\f", "\r": "\\r"}


def _drives_terminal(char: str) -> bool:
    """True for a character a terminal acts on instead of showing."""
    return unicodedata.category(char) in _HIDDEN


def _escape(char: str) -> str:
    """One character that cannot be shown, written so it can be read."""
    if char in _ESCAPES:
        return _ESCAPES[char]
    n = ord(char)
    return "\\x{:02x}".format(n) if n < 0x100 else "\\u{:04x}".format(n)


def one_line(text) -> str:
    """Text that cannot become two rows, or drive the terminal it is on.

    A snapshot message, a filename and a command are all attacker-adjacent: a
    newline in any of them forges a row that looks exactly like a real one, and
    the listing is read by somebody deciding what to restore or what to trust.
    So the newline goes with the rest -- everything a terminal obeys becomes a
    space.

    A space, not nothing.  A cell was measured for that character and a cell is
    printed for it, so a table stays a table; and two components of a path
    stay two words rather than becoming one word that is not on disk.  Where
    the caller collapses whitespace afterwards the space costs nothing, and
    where it does not, a gap is the honest thing to show: something was there.

    Text that is not text at all answers the empty string.  These values come
    off disk, out of files another program wrote, and a manifest with a number
    where its message should be is not a reason to fail.
    """
    if not isinstance(text, str):
        return ''
    if text.isprintable():
        return text                     # the overwhelmingly common case
    return ''.join(' ' if _drives_terminal(ch) else ch for ch in text)


def block(text) -> str:
    """The same, for text that is meant to have lines in it.

    A unified diff is many lines by definition, and a rendered table is one
    string with newlines in it, so ``\\n`` and ``\\t`` stay; what goes is
    everything else a terminal would obey rather than show.  This is display
    only -- nothing downstream reads the text back, so nothing depends on the
    characters being kept.
    """
    if not isinstance(text, str):
        return ''
    return ''.join(
        ch if ch in '\n\t' else (' ' if _drives_terminal(ch) else ch)
        for ch in text)


def row(text, width=400) -> str:
    """``one_line``, and a bound, for anything about to be printed as a row.

    ``one_line`` stops a value becoming two rows.  This stops it becoming a
    screenful.  `unedit save -m "$(cat NOTES.md)"` is an ordinary thing for a
    script to do, and it put a 200,000-character row in `list` and `show` --
    every other snapshot scrolled away by one of them.

    Separate from ``one_line`` because that one also runs where the value is
    about to be written to disk: cutting there would lose the text, not only
    the room to show it.  Here nothing is lost -- the manifest and ``--json``
    still have the whole value, and the row says how much it is not showing.
    """
    flat = one_line(text)
    if len(flat) <= width:
        return flat
    return "{}… (+{:,} more characters, see --json)".format(
        flat[:width], len(flat) - width)


def quoted(text) -> str:
    """Text naming something the reader has to go and find, made readable.

    Every line of a review exists to say which file to look at, and the path in
    it was put in the tree by whoever changed the tree.  Blanking is enough to
    make such a path harmless, and not enough to make it *findable*: a path
    with a space where a control character was is not a path on disk either,
    and nothing says which one it is.

    So here the character is escaped rather than replaced, and the whole string
    is quoted when any of them is -- which is what git itself does, and what
    `git status` shows.  The quoting is what makes the escaping mean something:
    without it a file named ``a\\nb`` and a file named ``a<newline>b`` print
    identically.  A backslash or a double quote in an otherwise ordinary name
    gets the same treatment, for the same reason and again exactly as git does.

    Printable text with neither is returned untouched -- ``café/naïve.py`` is
    perfectly readable and quoting it would be noise.  Machine-readable views
    are left alone by their callers: another program wants the path that is
    really on disk, and JSON's own escaping already makes it safe to print.
    """
    text = str(text)
    if text.isprintable() and '"' not in text and "\\" not in text:
        return text                     # the overwhelmingly common case
    if not any(c in '"\\' or _drives_terminal(c) for c in text):
        # Unprintable for some other reason -- an unassigned or private-use
        # codepoint.  It cannot break the row or drive the terminal, so it is
        # left as it is.
        return text
    out = []
    for c in text:
        if _drives_terminal(c):
            out.append(_escape(c))
        elif c in '"\\':
            out.append("\\" + c)
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def _cells(char: str) -> int:
    """How many terminal cells one character is drawn in."""
    if unicodedata.category(char) in ("Mn", "Me"):
        # Drawn on top of the character before it; it takes no cell of its own.
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def display_width(text: str) -> int:
    """The width of a string in terminal cells, which is not its length.

    Every column in every one of these tools is read by eye, and an eye reads
    cells.  A project named in Japanese is drawn twice as wide as ``len`` says
    it is, so a table padded with ``ljust`` puts the next column somewhere else
    and stops being a table -- and in a fixed live layout the line runs past
    the edge and wraps, which costs more than a truncation would.  Non-ASCII
    names are entirely ordinary.
    """
    if text.isascii():
        return len(text)                # the overwhelmingly common case
    return sum(_cells(ch) for ch in text)


def pad(text: str, width: int) -> str:
    """``ljust`` in cells rather than characters."""
    return text + ' ' * max(0, width - display_width(text))
