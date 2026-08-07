"""Which project somebody meant.

Two of these tools take a project from whoever is running them.  `agentlog
--project relay` and `agentwatch --project relay` ask the same question — which
of the projects in the log directory did you mean? — and until now they answered
it with two rules written independently.

They had drifted, and neither side could see it.  `agentlog` matched the name or
the path, and said so in its help.  `agentwatch` matched the last component of
the path and nothing else, so `agentwatch --project /home/you/relay` printed

    nothing has happened in that window

on a project that had been busy all afternoon.  That sentence is about the
window, so it sends the reader to widen `--since`, which will not help however
far back they go; nothing on screen suggests the flag was the problem.  The same
string typed at the two commands meant "that project" in one and "no such
project" in the other.

Nobody decided that.  `agentwatch` follows a live tail, where the column shows a
name and not a path, so a name is what got compared; the other tool was never
opened.  The help text was a third copy of the rule and had drifted with it —
one tool offering a path the other would refuse.

So the rule is here, once, and the sentence both tools print about it is read off
this file.  Widening what a project can be called is one edit, and there is no
second place left to forget.

It has to stay copied: nothing in this family imports outside its own package —
the promise `pip install stillworks` makes, enforced by
`test_every_import_is_stdlib_or_the_packages_own` — so a shared module is not on
offer.  What is on offer is a copy that cannot drift, pinned byte-for-byte by
`test_a_project_is_asked_for_the_same_way_in_both.py` in the stillworks tree.
"""

from __future__ import annotations

#: The one sentence either tool's help prints about `--project`.  A separately
#: worded copy is a third thing that can come to disagree with the rule, and it
#: is the copy people read *before* typing the flag rather than after.
HOW_IT_MATCHES = "only projects whose name or path contains NAME"


def _plain(text: str) -> str:
    """A project string in the form the comparison happens in.

    Case goes, because `--project API` and `--project api` are one question.

    A trailing slash goes, because that is what tab completion types for you:
    `--project ~/relay/` is the same ask as `--project ~/relay`, and without
    this the slash is the whole difference between the project and an empty
    screen.  It is stripped from both sides — a path the tool found may have one
    too — so neither side has to be the tidy one.
    """
    return (text or "").strip().rstrip("/").lower()


def matches(needle: str, *names: str) -> bool:
    """Whether a project the tools found is one that was asked for.

    `names` is whatever the caller knows this project by — its path, its name,
    or one of the two on its own.  Callers hand over what they have rather than
    a canonical form, because the two tools know a project by different amounts
    at different moments: a live tail has the last component of a decoded
    directory before the log says which cwd it is in, and the full path
    afterwards.  Any one of them containing `needle` is a match, so a caller
    that learns more about a project later can only ever match more, never less.

    An empty `needle` — nobody asked — matches everything.  Every call site used
    to spell that case itself, as a `if self.project and ...` guard next to the
    comparison, which is a rule about the flag sitting in code about projects;
    the one time it is written the wrong way round, a tool shows nothing at all
    by default.
    """
    needle = _plain(needle)
    if not needle:
        return True
    return any(needle in _plain(name) for name in names)
