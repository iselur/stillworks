"""The one sentence about log files that exist and are not in what you see.

Both tools reach this situation and both said something different about it:

    agentlog:   note: 2 log files were not counted — could not be read
                      (run with --verbose to see which)

    agentwatch: 2 session logs could not be read — that activity is not shown
                    /home/val/a.jsonl
                    /home/val/b.jsonl

Same fact, two vocabularies, in two commands that arrive in one `pip install`.
And the halves were split the wrong way round: `agentlog` knew *why* each file
was skipped and would not tell you unless you asked twice, while `agentwatch`
named the files and threw the reason away at the `except OSError:` that produced
it -- so the tool that showed you the path did not tell you what to do to it,
and a chmod is nearly always what to do to it.

So this holds all of it: the word for the thing, the plural, how several
reasons read as one sentence, when a path is worth printing, and how many.  A
caller says only how many names it has room for.

Deliberately not "there is nothing here".  A tool with nothing to report says so
in its own words, because that sentence is about the question that was asked.
This one is about the answer being short a few files, which is true of the whole
run and reads the same in every view of it.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

#: The file would not open.  Permissions, a broken link, or something that is
#: not a regular file at all -- from the caller's side they are one problem with
#: one fix, and naming the file is what makes the fix findable.
UNREADABLE = "could not be read"

#: The file opened and held nothing this tool could use: truncated mid-write, a
#: different format, or a name that only happens to end .jsonl.  Distinct from
#: UNREADABLE because there is nothing to chmod -- the bytes are the problem.
NO_RECORDS = "had no readable records"

#: Pass as ``name_at_most`` to print every one of them.  A reader who has asked
#: to see which files (`--verbose`) has asked for the list, not a sample of it.
ALL = None

#: What a caller prints when it named none of them.  One flag name, so a tool
#: that passes 0 is promising it has a ``--verbose`` -- which is the whole of
#: what this sentence is for.
_HOW_TO_SEE_THEM = "(run with --verbose to see which)"

#: Under the sentence, not beside it: the note is one thing, and a path is a
#: detail of it.
_INDENT = "      "


def note_about(entries: Sequence[Tuple[str, str]],
               name_at_most: Optional[int] = 0) -> str:
    """One note about log files that exist and are not counted in what you see.

    ``entries`` is ``(path, reason)`` pairs, where the reason is ``UNREADABLE``
    or ``NO_RECORDS``.  Pairs and not paths: the reason is known where the
    failure happened and nowhere else, and a caller that drops it there cannot
    get it back.

    ``name_at_most`` is how many paths the caller has room to print -- ``0`` for
    a report that offers ``--verbose`` instead, a small number for a live view
    that has a screen to share, ``ALL`` for a reader who asked to see them.
    One number and not a flag plus a limit, because the three are one question.

    Returns ``''`` when there is nothing to say, which is the ordinary case, so
    a caller can print it unconditionally.
    """
    entries = sorted(entries)
    if not entries:
        return ""
    return "\n".join([_headline(entries)] + _detail(entries, name_at_most))


def _headline(entries: Sequence[Tuple[str, str]]) -> str:
    """`note: 2 session logs are not shown — could not be read`.

    "session log" and not "log file": it is the word the rest of both tools
    uses, and the thing missing from the report is a session, not a file.

    "are not shown" and not "were not counted": one of these tools counts and
    the other follows, and the reader of either wants the same fact -- what is
    on screen is short of what is on disk.
    """
    total = len(entries)
    return "note: {} session log{} {} not shown — {}".format(
        total, "" if total == 1 else "s", "is" if total == 1 else "are",
        _why(entries))


def _why(entries: Sequence[Tuple[str, str]]) -> str:
    """The reasons, as one clause.

    One kind of problem reads better without a count in front of it -- the
    count is already in the first half of the sentence and repeating it there
    says `2 session logs are not shown — 2 could not be read`.
    """
    counts = {}
    for _path, reason in entries:
        counts[reason] = counts.get(reason, 0) + 1
    if len(counts) == 1:
        return next(iter(counts))
    return ", ".join("{} {}".format(count, reason)
                     for reason, count in sorted(counts.items()))


def _detail(entries: Sequence[Tuple[str, str]],
            name_at_most: Optional[int]) -> List[str]:
    """The lines under the headline: some paths, or how to ask for them."""
    if name_at_most == 0:
        return [_INDENT + _HOW_TO_SEE_THEM]
    shown = entries if name_at_most is ALL else entries[:name_at_most]
    mixed = len({reason for _path, reason in entries}) > 1
    lines = [_INDENT + (
        "{}  ({})".format(path, reason) if mixed else path)
        for path, reason in shown]
    # Only when some were held back.  "and 0 more" is a line that says a file
    # is missing from a list of files that are missing, which is nobody's day.
    if len(entries) > len(shown):
        lines.append(_INDENT + "... and {} more".format(
            len(entries) - len(shown)))
    return lines
