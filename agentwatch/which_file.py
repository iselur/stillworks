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
"""

from __future__ import annotations

import os
from typing import Optional

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
