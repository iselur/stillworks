"""How a file path is written on a screen, in the two tools that write them.

The same file was spelled three ways depending on which command you happened to
run.  `agentwatch` said `src/app.py` if the project name appeared in the path,
`~/notes/todo.md` if it did not and the file was under home, and the whole
absolute path otherwise.  `agentlog`'s digest said `src/app.py` for a file under
the project root and a bare `todo.md` for everything else -- which is the one
that was wrong, because a bare basename in a list is a file you cannot find.
Two `cli.py` from two repositories are one line in that list, twice.

So the rule is here, once, and it is the honest one: say as much of the path as
is needed to know which file it is, and no more.  What the reader already knows
comes off the front -- the project they are looking at, or their own home -- and
what is left is what tells the files apart.

Two decisions worth writing down.

`project` is whatever the caller happens to know.  `agentlog` knows the root
directory, because it grouped the sessions by it; `agentwatch` knows only a
name, because an event carries one.  A root is absolute and a name is not, so
one parameter takes both and neither caller has to fake the other's knowledge.
The alternative -- two parameters, one always empty -- is a wider interface
bought with nothing.

Room is cut off the *front*, because the end of a path is the file.  `…/src/
app.py` is the answer to "which file"; `/home/you/very/deep/dir…` is not, and
that is what a column that clips from the right gives you.  Inside a single
name it goes the other way -- `test_the_note_about…` keeps its front, since the
front is what you recognise a name by, and that is what every other column in
these tools does with a string too long for it.

`paths_as_shown` is the same rule applied to a line that has paths *in* it
rather than being one.  Commands are mostly paths, and a command row was
getting the full absolute path -- the part this module exists to remove --
repeated inside it, and then the row was clipped from the right, so what fell
off the end was the flags saying what the command did.  On 15,834 real commands
68% were too wide for the row and 47% of those lost a path to the clip.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

from .terminal import display_width

#: One cell, unlike `...`, in a column that is already short of them.
_ELIDED = "…"


def as_shown(path: str, project: str = "", room: Optional[int] = None) -> str:
    """A file path, written the way the person reading it would say it.

    ``project`` is the project the reader is already looking at, given either
    as its root directory or as its name; that part comes off the front.  A
    file outside it keeps enough path to be found -- ``~/notes/todo.md``, not
    ``todo.md``.

    ``room`` is how many terminal cells there are for the answer.  ``None``
    means the caller will fit the line itself.
    """
    if not path:
        return ""
    shown = _minus_what_they_know(path, project)
    return shown if room is None else _fit_keeping_the_file(shown, room)


#: A path inside a line of other text.  Absolute only -- ``/x`` or ``~/x`` --
#: because a relative path has no leading part a reader already knows, so there
#: would be nothing to take off it, and matching one is all risk and no gain.
#:
#: The character before it is an allowlist rather than "not a word character",
#: which is what keeps the two things that look like paths and are not.  `:` is
#: absent on purpose: without it `https://host/a/b` would have `//host/a/b`
#: taken out of it and shortened, and mangling a URL is a visible bug where
#: leaving a `PATH=/a:/b` alone is merely a missed shortening.  `sed s/a/b/`
#: needs no case of its own -- `s` is a word character, so the run never starts.
#:
#: `:` ends the run as well as failing to start one, which is what stops
#: `PATH=/usr/local/bin:/usr/bin` being read as a single enormous path and
#: shortened through its own separator.  It costs nothing on a real filename
#: carrying a colon -- the run stops early, the rest of the name stays put as
#: ordinary text, and the two halves are written back out side by side.
_A_PATH_IN_A_LINE = re.compile(r"""(?:^|(?<=[\s"'=(<>|&;,]))(~?/[^\s"'`:]*)""")


def paths_as_shown(text: str, project: str = "", room: Optional[int] = None) -> str:
    """A line of text with the paths in it written the way a reader would say them.

    Same promise as `as_shown`, for a line that *contains* paths rather than
    being one: a command, in practice.  The front of the line is what
    identifies it -- `find`, `grep`, `bash` -- so unlike a path this is cut from
    the right; the paths inside it are still shortened from the front, because
    the end of a path is still the file.

    Shortening happens in two stages, and the order is the point.  What the
    reader already knows -- the project root, their own home -- comes off every
    path always, because dropping it costs nothing: they are looking at that
    project, on that machine.  Directories come off only under pressure, and
    only off the widest path still in the line, one component at a time, until
    the line fits.  That way a line with room to spare says everything, and a
    line without it gives up the least useful thing first.

    ``room`` of ``None`` does the free half and stops, leaving the fitting to a
    caller that has its own ideas about width.
    """
    if not text:
        return ""
    if room is not None and room <= 0:
        # A row with no cells left gets nothing, not the one cell the cut mark
        # costs.  `as_shown` has said this since it was written; a second rule
        # in the same file that overflowed by one instead would be the same bug
        # twice with one of them fixed.
        return ""
    pieces, paths = _split_around_paths(text)
    shown = [_minus_what_they_know(p, project) for p in paths]
    if room is None:
        return _rejoin(pieces, shown)
    while display_width(_rejoin(pieces, shown)) > room:
        widest = _the_widest_that_can_still_give(shown)
        if widest is None:
            break
        shown[widest] = _one_directory_less(shown[widest])
    line = _rejoin(pieces, shown)
    return line if display_width(line) <= room else _fit_keeping_the_front(line, room)


def _split_around_paths(text: str) -> Tuple[List[str], List[str]]:
    """The line as the bits between paths, and the paths, in order.

    Kept apart so the shortening never sees the rest of the line and the rest
    of the line never has a `/` read into it: rejoining is `pieces[0] + path[0]
    + pieces[1] + ...`, with one more piece than there are paths.
    """
    pieces: List[str] = []
    paths: List[str] = []
    at = 0
    for found in _A_PATH_IN_A_LINE.finditer(text):
        pieces.append(text[at:found.start(1)])
        paths.append(found.group(1))
        at = found.end(1)
    pieces.append(text[at:])
    return pieces, paths


def _rejoin(pieces: List[str], paths: List[str]) -> str:
    out = [pieces[0]]
    for i, path in enumerate(paths):
        out.append(path)
        out.append(pieces[i + 1])
    return "".join(out)


def _the_widest_that_can_still_give(shown: List[str]) -> Optional[int]:
    """Which path to take a directory off next, or None if none has one left.

    Widest first, so the line gives up its least useful cells first.

    Whether a path has anything left is asked of the shortener rather than
    worked out again here: a path down to `…/name` comes back unchanged, and
    two places deciding that separately is two places to get it wrong.

    Not measured in saved cells, which is the tempting version and is wrong.
    `a/b/c/app.py` gives up `a` for a mark and stays twelve cells wide, but the
    step after it is `…/c/app.py` and the one after that fits -- a rule that
    stopped at the first step buying nothing would freeze the path there and cut
    the line's tail off instead.  Equal-width swaps are a step on the way, and
    the one path where a swap would be pointless -- `~/name`, already an elision
    -- reports itself done rather than equal.
    """
    best, best_width = None, -1
    for i, path in enumerate(shown):
        if _one_directory_less(path) == path:
            continue
        width = display_width(path)
        if width > best_width:
            best, best_width = i, width
    return best


def _one_directory_less(path: str) -> str:
    """Drop the front-most directory, or return the path when it has none left.

    The leading `/` is not a directory, and neither is a `…` left by an earlier
    step, so they come off with the component they introduce -- otherwise the
    first step of `/home/you/a/b.py` would be `…/home/you/a/b.py`, a cell
    *wider* than what it replaced that drops nothing.  A `~` goes the same way
    for a different reason: it is already an elision of home, one cell exactly
    like the mark that would replace it, so `~/a/b.py` has nothing to gain
    until `a` goes with it.
    """
    parts = path.split("/")
    if parts[0] in ("", _ELIDED) or parts[0].startswith("~"):
        parts = parts[1:]
    if len(parts) <= 1:
        return path                 # only the file itself is left
    return _ELIDED + "/" + "/".join(parts[1:])


def _minus_what_they_know(path: str, project: str) -> str:
    """Drop the leading part the reader is not learning anything from."""
    if project:
        under = _under_the_project(path, project)
        if under is not None:
            return under
    home = os.path.expanduser("~")
    # The separator is required, and it is what makes the odd cases fall out
    # rather than being special-cased.  `/home/you` must not shorten
    # `/home/youngest/x`; a home of `/` -- a daemon account, a bare container --
    # asks for `//` and so shortens nothing at all, which is right, because a
    # `~` in front of every path on the disk says nothing.
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    # Home itself, with nothing after it.  `cd /home/you` is a real command and
    # the rule above cannot see it: there is no separator to require.  Left out,
    # it is the one path that gets *longer* under pressure -- `…/you` -- which
    # is a directory nobody can place standing in for the one everybody can.
    if path == home:
        return "~"
    return path


def _under_the_project(path: str, project: str) -> Optional[str]:
    """The path relative to the project, or ``None`` if it is not in it."""
    if project.startswith(os.sep):
        # A root directory: the caller knows exactly where the project is, so
        # this is an answer and not a guess.
        root = project.rstrip(os.sep)
        if path.startswith(root + os.sep):
            return path[len(root) + 1:]
        return None
    # A name: all that is known is what the directory is called, so look for it
    # as a whole component.  Bounded by separators on both sides, because
    # `relay` must not match `/home/you/relayed/x` -- a different project whose
    # name merely starts the same way.
    marker = os.sep + project + os.sep
    idx = path.find(marker)
    return path[idx + len(marker):] if idx >= 0 else None


def _fit_keeping_the_file(shown: str, room: int) -> str:
    """Cut directories off the front until what is left fits."""
    if room <= 0:
        return ""
    if display_width(shown) <= room:
        return shown
    parts = shown.split("/")
    for first_kept in range(1, len(parts)):
        candidate = _ELIDED + "/" + "/".join(parts[first_kept:])
        if display_width(candidate) <= room:
            return candidate
    return _fit_keeping_the_front(parts[-1], room)


def _fit_keeping_the_front(name: str, room: int) -> str:
    """A name too wide for the room it has, cut where every column cuts.

    A single cell needs no case of its own: the mark costs one, so the loop
    below keeps nothing and the answer is the mark, which is the answer.
    """
    kept, used = [], 0
    for char in name:
        cells = display_width(char)     # one character's own width, in cells
        if used + cells > room - 1:
            break
        kept.append(char)
        used += cells
    return "".join(kept) + _ELIDED
