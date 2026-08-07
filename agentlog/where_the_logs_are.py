"""Where the agents put their session logs, said once.

Claude Code writes a session under ``~/.claude/projects`` and Codex writes one
under ``~/.codex/sessions``.  That is one fact about somebody else's software,
and it was written out four times: in the finder that reads the logs, in the
sentence printed when it finds none, in the guard that stops a report being
written on top of a log, and in the walk the live view does.

Four copies is four chances to be looking somewhere the logs are not, and they
fail differently, which is the part worth knowing.  The finder looking in the
wrong place prints "no sessions" -- wrong, but it says something.  The guard
looking in the wrong place says *nothing*: `agentlog` promises it never writes
to the logs it reads, and a guard holding a directory the logs left does not
refuse the write, it waves it through onto a day's work.  A promise kept by a
copy of a fact is kept until somebody updates the other copy.

So the roster is here, and it is the only thing that knows the layout.  Adding
an agent is one line in one file rather than a branch in each of two packages,
which is the honest test of whether this was worth doing.

Two decisions worth writing down.

A home is taken as an argument, never read from the environment here.  The two
commands disagree on purpose about how a home is named -- `AGENTLOG_HOME` and
`AGENTWATCH_HOME`, and one of them stops when a directory it was handed does
not exist, because a directory somebody typed is one they meant.  Folding that
in would mean a parameter for the variable's name and another for the error
policy: an interface as wide as the thing behind it, bought with nothing.
Where the logs are and what the user asked for are two questions.

The directory is returned unresolved.  `os.path.realpath` belongs at the one
call site that compares paths -- the write guard, which has to see through a
symlinked home to answer at all.  Applying it here would change the paths the
other three *print*, so a person on a box where ``/home`` is a link would be
shown a directory they do not recognise as theirs.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

#: Each agent these tools read: the name a log record carries, the name a
#: person calls it, and where it writes, relative to a home directory.
_AGENTS = (
    ("claude", "Claude Code", (".claude", "projects")),
    ("codex", "Codex", (".codex", "sessions")),
)

#: The agents, in the order they are looked in and listed.  Order is part of
#: this: a report that names its sources names them the same way every run.
SOURCES = tuple(source for source, _, _ in _AGENTS)


def log_dirs(home: Optional[str] = None) -> List[Tuple[str, str, str]]:
    """``(source, shown_as, directory)`` for every agent, under ``home``.

    ``home`` is a home directory; ``None`` means the one this process has.  The
    directories are returned whether or not they exist, because "no such
    directory" is the ordinary case -- a box with only one of the two agents on
    it -- and the caller is the one that knows what to say about it.
    """
    where = home if home else os.path.expanduser("~")
    return [(source, shown_as, os.path.join(where, *parts))
            for source, shown_as, parts in _AGENTS]
