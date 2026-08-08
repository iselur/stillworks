"""The day, reported the way an engineer reports to whoever asked for the work.

The digest answers "what happened": files, commands, errors, the prompt you
typed.  That is an activity log, and an activity log is not a report.  Nobody
answers "what did you get done today?" by reading out their shell history, and
nobody wants their own question read back to them either -- they already know
what they asked for; what they do not know is whether it is finished.

So this view answers three questions and nothing else:

    what did you work on, what is done, what is not

Two kinds of work go into that, and the split between them is the whole design:

**Counting is done here, in code.**  Sessions, tokens, hours, projects,
commits, pushes, releases, what failed and stayed failed, what compaction cost.
Every number printed on the page is computed from the transcripts by the
functions below, and no number is ever taken from the model.  A report whose
figures were guessed is worse than no report, because it reads exactly the
same.

**Grouping and naming is done by a model.**  "These eleven sessions across four
directories were one piece of work, and the piece of work was "get the release
out"" is a judgement, and there is no arithmetic for it.  The model is given
the facts and asked for the headings, the done lines and the open lines, and it
is told in the prompt not to write figures at all.

Every figure on the page -- the headline, and the tally under each theme -- is
computed here from the sessions the model attached to that theme, so a model
that miscounts changes the wording of the page and never its arithmetic.  A
sentence that states a count anyway is dropped rather than printed, because a
made-up figure in prose sits an inch from a real one and reads the same.

The model attaches each theme to projects *by name*, and the numbers on a theme
are the sum of those projects' facts.  That is the join that keeps the two
halves honest: the model can be wrong about what to call a piece of work, and
the page is still arithmetically true.

If no model can be reached the report still prints -- headings and prose go
missing, the facts do not.  See ``asking_a_model.NoModel``.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import clock
from .asked import pick_ask
from .asking_a_model import NoModel
from .asking_a_model import ask as ask_a_model
from .render import (active_seconds, compaction_note, group_by_project,
                     shortened)
from .terminal import block as safe_for_terminal
from .which_file import as_shown

WIDTH = 80

#: How much of the day's evidence one project is allowed to contribute to the
#: prompt.  A brief is built from a day that can hold thousands of commands,
#: and the model needs the shape of the work rather than all of it.
_PER_PROJECT = 14

#: Prompts are read by the model to work out what the work was *for*, and are
#: never printed.  This is the line that used to be the digest's `asked` row,
#: and taking it off the page is the point of this view.
_ASKS_PER_PROJECT = 2


# ---------------------------------------------------------------------------
# Outcomes -- the things a session finished, rather than the things it did
# ---------------------------------------------------------------------------
#
# A transcript records activity, and activity is a poor witness: a thousand
# commands can leave nothing behind, and one can ship a release.  What survives
# a session is a commit, a push, a tag, a published package, a passing suite.
# Those are the events worth reporting, and unlike a count they are checkable.
#
# The patterns below are deliberately narrow.  A command that is *nearly* a
# release is not a release, and a timeline that includes near-misses is a
# timeline nobody can trust.  Anything unmatched is simply not an outcome; it
# is still counted among the commands, where a count belongs.

#: Where a program name has to sit for it to be a program that ran: the start
#: of the line, after a shell separator, or after one of the words that wrap a
#: command.  Optionally behind a path, because a virtualenv's build tool is
#: spelled `/home/you/.venv/bin/pyproject-build`.
#:
#: Without this, ``grep -n 'git commit' notes.md`` is a commit and
#: ``rg pytest`` is a test run: the words appear, in a command, and matching on
#: the words alone cannot tell the difference between running something and
#: talking about it.  A report that invents outcomes is worse than one that
#: misses them, so the match is anchored rather than widened.
_RUNS = (r"(?:^\s*|[\n;&|()]+\s*|\$\(\s*|`\s*"
         r"|\b(?:sudo|nohup|time|env|xargs|command)\s+)(?:\S*/)?")


def _when_run(pattern: str) -> "re.Pattern[str]":
    return re.compile(_RUNS + "(?:" + pattern + ")")


_OUTCOMES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("committed", _when_run(r"git\s+(?:-C\s+\S+\s+)?commit\b")),
    ("pushed", _when_run(r"git\s+(?:-C\s+\S+\s+)?push\b")),
    ("tagged", _when_run(r"git\s+(?:-C\s+\S+\s+)?tag\s+\S")),
    ("merged", _when_run(r"git\s+merge\b|gh\s+pr\s+merge\b")),
    ("opened a pull request", _when_run(r"gh\s+pr\s+create\b")),
    ("published", _when_run(
        r"twine\s+upload\b|npm\s+publish\b|cargo\s+publish\b|"
        r"gh\s+release\s+create\b")),
    ("built", _when_run(
        r"pyproject-build\b|python3?\s+-m\s+build\b|docker\s+build\b|"
        r"npm\s+run\s+build\b|make\s+build\b")),
    ("ran the tests", _when_run(
        r"pytest\b|python3?\s+-m\s+unittest\b|npm\s+(?:run\s+)?test\b|"
        r"cargo\s+test\b|go\s+test\b")),
    ("installed it", _when_run(
        r"pipx?\s+install\b|pipx\s+(?:re)?install\b|npm\s+i(?:nstall)?\b")),
)

#: The subject of a commit, when one was written on the command line.  This is
#: an engineer saying what they just finished, in their own words, already on
#: disk -- the single most useful sentence a transcript contains.
_COMMIT_SUBJECT = re.compile(
    r"""commit\b[^\n]*?-m\s*(['"])(?P<subject>.+?)\1""", re.DOTALL)


def _first_line(text: str) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return " ".join(line.split())


def outcomes_of(session: Dict) -> List[str]:
    """What this session finished, as short phrases, in the order they happened.

    Commands only.  A file written is not an outcome -- a session that edits
    forty files and commits none has finished nothing, and saying otherwise is
    the flattery that makes a report useless.
    """
    out: List[str] = []
    for cmd in session.get("commands") or []:
        # Read as written, newlines and all.  Flattening first would join a
        # commit body onto its subject line, and the subject is the sentence
        # worth printing; the patterns are whitespace-tolerant already.
        text = str(cmd)
        for label, pattern in _OUTCOMES:
            if not pattern.search(text):
                continue
            if label == "committed":
                found = _COMMIT_SUBJECT.search(text)
                subject = _first_line(found.group("subject")) if found else ""
                out.append("committed: " + subject if subject else "committed")
            else:
                out.append(label)
            break
    return out


def _stuck(session: Dict, limit: int = 3) -> List[str]:
    """Failures worth reporting: the last thing tried, not every attempt.

    A command that failed and was then run again successfully is an engineer
    working, not a problem, and a report that lists both wastes the reader on
    the one that no longer matters.  What is kept is the tail of the failures,
    which is the closest a transcript comes to "still broken when I stopped".
    """
    failures = [_first_line(str(f)) for f in (session.get("failed_cmds") or [])]
    seen = []
    for failure in reversed(failures):
        if failure and failure not in seen:
            seen.append(failure)
        if len(seen) >= limit:
            break
    return seen


# ---------------------------------------------------------------------------
# Facts -- the half of the page a model never touches
# ---------------------------------------------------------------------------


class Facts(object):
    """Everything countable about one project's day.

    A plain object rather than a dict because every field is read by name in
    two places, and a typo in a dict key is a silently missing number on a
    report -- which is the one failure this view cannot afford.
    """

    def __init__(self, name: str, sessions: List[Dict]):
        self.name = name
        self.sessions = sessions
        self.seconds = active_seconds(sessions)
        self.tokens_in = sum(s.get("tokens_in") or 0 for s in sessions)
        self.tokens_out = sum(s.get("tokens_out") or 0 for s in sessions)
        self.files = sorted({f for s in sessions
                             for f in (s.get("files_written") or [])})
        self.commands = sum(len(s.get("commands") or []) for s in sessions)
        self.errors = sum(s.get("errors") or 0 for s in sessions)
        self.outcomes = [o for s in sessions for o in outcomes_of(s)]
        self.stuck = [f for s in sessions for f in _stuck(s)]
        self.recaps = [text for s in sessions
                       for _at, text in (s.get("recaps") or [])]
        self.asks = [a for s in sessions
                     for a in ((s.get("asks") or [])[:_ASKS_PER_PROJECT])]

    def counted(self) -> str:
        """The one line of figures that hangs under a theme."""
        parts = ["{} session{}".format(len(self.sessions),
                                       "" if len(self.sessions) == 1 else "s"),
                 clock.duration(self.seconds)]
        if self.tokens_in or self.tokens_out:
            parts.append("{} tokens".format(
                _compact_number(self.tokens_in + self.tokens_out)))
        if self.files:
            parts.append("{} file{} edited".format(
                len(self.files), "" if len(self.files) == 1 else "s"))
        if self.errors:
            parts.append("{} error{}".format(
                self.errors, "" if self.errors == 1 else "s"))
        return " · ".join(parts)


def _compact_number(n: int) -> str:
    """`1.2M`, `84k`, `912`.

    A brief is read at a glance and `1,238,004` is not glanceable; the digest
    prints exact figures and this one prints readable ones, which is the
    difference between a report and a ledger.
    """
    if n >= 1_000_000:
        return "{:.1f}M".format(n / 1_000_000).replace(".0M", "M")
    if n >= 1_000:
        return "{:.0f}k".format(n / 1_000)
    return str(n)


def facts_by_project(sessions: List[Dict]) -> "Dict[str, Facts]":
    """One :class:`Facts` per project, keyed by the name shown in the digest.

    Two directories can share a display name -- a checkout and its worktree, or
    the same repository cloned twice -- and the model refers to a project by
    that name and nothing else.  So they are rolled together here rather than
    one of them quietly replacing the other in the dict, which would drop a
    project's whole day on the floor while the page went on looking complete.
    """
    merged: "Dict[str, List[Dict]]" = {}
    for group in group_by_project(sessions):
        name = group.get("name") or "(unknown)"
        merged.setdefault(name, []).extend(group.get("sessions") or [])
    return {name: Facts(name, members) for name, members in merged.items()}


# ---------------------------------------------------------------------------
# The question put to a model
# ---------------------------------------------------------------------------

_FORMAT = """\
Answer with nothing but lines in this format, one block per theme:

THEME: <what this piece of work was, 3-8 words, plain language>
PROJECTS: <comma-separated project names from the evidence, exactly as spelled>
DID: <something finished, one short sentence>
OPEN: <something started and not finished, one short sentence>

Rules:
- Two to five themes. Fewer is better. Group by the piece of work, not by
  directory: one theme may cover several projects.
- DID lines are for work that is actually finished, and the evidence for
  finished is a commit, a push, a release, a passing suite. Repeat as needed.
- OPEN lines are for what is not done: a failure still failing at the end, a
  thing built but not released, a stated next step. Repeat as needed. If a
  theme really has nothing open, leave the OPEN line out.
- Write no numbers at all. No counts, no times, no token figures. They are
  added afterwards from the transcripts and yours would be ignored.
- Do not quote or paraphrase the prompts back. The reader typed them. Say what
  came of them.
- Plain language, no jargon, no markdown, no preamble, no sign-off."""


def _evidence(facts: "Dict[str, Facts]") -> str:
    """The day, written down small enough to ask about."""
    lines = []
    for name, f in facts.items():
        lines.append("## project: {}".format(name))
        if f.asks:
            lines.append("  was asked for: "
                         + " | ".join(shortened(a, 160) for a in f.asks))
        if f.outcomes:
            lines.append("  finished: "
                         + "; ".join(f.outcomes[:_PER_PROJECT]))
        if f.recaps:
            lines.append("  the agent's own notes: "
                         + " | ".join(shortened(r, 300) for r in f.recaps[:4]))
        if f.files:
            lines.append("  edited: " + ", ".join(
                as_shown(p) for p in f.files[:_PER_PROJECT]))
        if f.stuck:
            lines.append("  still failing at the end: "
                         + "; ".join(shortened(s, 120) for s in f.stuck[:4]))
        if not (f.outcomes or f.files or f.stuck):
            lines.append("  nothing was written or committed here")
    return "\n".join(lines)


def the_question(facts: "Dict[str, Facts]", period_label: str) -> str:
    """The whole prompt, so a test can read it without a model running."""
    return (
        "You are writing a short status report for the person whose work this "
        "was. They know what they asked for; they want to know what came of "
        "it.\n\n"
        "Here is the evidence from their agent transcripts for {}.\n\n"
        "{}\n\n{}\n".format(period_label, _evidence(facts), _FORMAT))


# ---------------------------------------------------------------------------
# The answer, read back
# ---------------------------------------------------------------------------


#: The words this page computes for itself.  A model sentence that puts a
#: number in front of one of them is stating a tally, and a tally in prose sits
#: an inch from the real one and looks exactly as authoritative -- so the
#: sentence is dropped rather than printed beside a figure that contradicts it.
#:
#: Deliberately only these words.  "Released 0.3.0" and "fixed the 32-bit path"
#: are numbers a model read off the evidence and neither pretends to be a
#: count; cutting every digit would cost more than it saved.
_A_TALLY = re.compile(
    r"\b\d[\d,.]*\s*(?:\S+\s+){0,2}"
    r"(?:session|token|project|file|error|command|commit|"
    r"hour|minute|test|line|failure)s?\b", re.I)


def _states_a_tally(text: str) -> bool:
    return bool(_A_TALLY.search(text))


class Theme(object):
    """One piece of work: what it was called, what came of it, what did not."""

    def __init__(self, title: str):
        self.title = title
        self.projects: List[str] = []
        self.did: List[str] = []
        self.open: List[str] = []


def read_the_answer(text: str, known: Sequence[str]) -> List[Theme]:
    """Turn a model's reply into themes, ignoring anything else it said.

    Lenient on purpose.  A CLI that wraps its answer in a sentence of its own,
    or numbers the blocks, or uses a dash, should not cost a person their
    report; every line that is not one of the four keywords is dropped, and a
    project name that does not match one we counted is dropped too, so a made-up
    name can never carry made-up figures onto the page.

    Strict in exactly one place: a sentence that states a tally is dropped
    whole.  The prompt asks for no numbers, and a model that writes one anyway
    has written the one thing on this page nobody checked.
    """
    themes: List[Theme] = []
    lookup = {name.lower(): name for name in known}
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*0123456789. ").strip()
        head, _, rest = line.partition(":")
        key, value = head.strip().upper(), rest.strip()
        if key == "THEME" and value:
            themes.append(Theme(safe_for_terminal(value)))
        elif not themes:
            continue
        elif key == "PROJECTS":
            for part in value.split(","):
                match = lookup.get(part.strip().lower())
                if match and match not in themes[-1].projects:
                    themes[-1].projects.append(match)
        elif key == "DID" and value and not _states_a_tally(value):
            themes[-1].did.append(safe_for_terminal(value))
        elif key == "OPEN" and value and not _states_a_tally(value):
            themes[-1].open.append(safe_for_terminal(value))
    return [t for t in themes if t.did or t.open]


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def _wrapped(text: str, first: str, after: str) -> List[str]:
    """`text` under a label, wrapped to the page rather than cut.

    A done line is a sentence and cutting it at 80 cells loses the half that
    says what was done.  The digest cuts because its rows are a table; this is
    prose, so it wraps.
    """
    room = WIDTH - len(after)
    words, lines, current = text.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > room:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word).strip()
    if current:
        lines.append(current)
    if not lines:
        return []
    return [first + lines[0]] + [after + rest for rest in lines[1:]]


def _headline(sessions: List[Dict], period_label: str,
              facts: "Dict[str, Facts]") -> str:
    tokens = sum(f.tokens_in + f.tokens_out for f in facts.values())
    parts = [clock.duration(active_seconds(sessions)),
             "{} session{}".format(len(sessions),
                                   "" if len(sessions) == 1 else "s"),
             "{} project{}".format(len(facts),
                                   "" if len(facts) == 1 else "s")]
    if tokens:
        parts.append("{} tokens".format(_compact_number(tokens)))
    return "{} · {}".format(period_label, " · ".join(parts))


def _theme_facts(theme: Theme, facts: "Dict[str, Facts]", room: int) -> str:
    """The tally line under a theme: where the work was, then the figures.

    The figures are the reason the line exists, so the names give way first.  A
    theme covering seven sandbox directories once filled the whole line with
    their names and the count fell off the end -- a row of pure noise where the
    only checkable thing on the page should have been.
    """
    named = [facts[p] for p in theme.projects if p in facts]
    if not named:
        return ""
    rolled = Facts("", [s for f in named for s in f.sessions])
    figures = rolled.counted()
    names = [f.name for f in named]
    # Shed a whole name at a time until the row fits.  Truncating the joined
    # list instead leaves "relay-review-1, 5 othe…", which names nothing and
    # counts nothing; one whole name and an honest remainder is worth more
    # than a fragment of both.
    where, kept = ", ".join(names), len(names)
    while kept > 1 and len(where) + 3 + len(figures) > room:
        kept -= 1
        where = ", ".join(names[:kept] + ["{} others".format(len(names) - kept)])
    where = shortened(where, max(8, room - len(figures) - 3))
    return "{} · {}".format(where, figures)


def render_brief(sessions: List[Dict], period_label: str,
                 ask: Optional[Callable[[str], str]] = None) -> str:
    """The report, as text.

    ``ask`` is the seam.  It takes the prompt and returns what the model said;
    the default reaches the ``claude`` command through
    :mod:`agentlog.asking_a_model`, and the tests pass a function that returns
    a fixed string, so every line of this module is exercised without a model
    and without a network.
    """
    if not sessions:
        return "no sessions found for: {}".format(period_label)

    facts = facts_by_project(sessions)
    lines = [_headline(sessions, period_label, facts), ""]

    ask = ask or ask_a_model
    try:
        answer = ask(the_question(facts, period_label))
        themes = read_the_answer(answer, list(facts))
        trouble = ""
    except NoModel as why:
        themes, trouble = [], str(why)

    if themes:
        count = _in_words(len(themes))
        lines.append("{} thing{}:".format(
            count.capitalize(), "" if len(themes) == 1 else "s"))
        lines.append("")
        for n, theme in enumerate(themes, 1):
            lines.append("  {}. {}".format(n, theme.title))
            for did in theme.did:
                lines.extend(_wrapped(did, "     done      ",
                                      "               "))
            for still in theme.open:
                lines.extend(_wrapped(still, "     not done  ",
                                      "               "))
            counted = _theme_facts(theme, facts, WIDTH - 5)
            if counted:
                # Figures are a row, not a sentence: wrapping them puts half a
                # tally on a line of its own, which reads as a second fact.
                lines.append("     " + counted)
            lines.append("")
    else:
        lines.extend(_the_facts_alone(facts, trouble))

    cost = compaction_note(sessions)
    if cost:
        lines.append(cost)
    return "\n".join(lines).rstrip() + "\n"


_NUMBERS = ("no", "one", "two", "three", "four", "five", "six", "seven",
            "eight", "nine", "ten")


def _in_words(n: int) -> str:
    """`three`, not `3`.

    "Today I worked on three things" is how a person says it, and the whole
    point of this view is that it reads like a person saying it.
    """
    return _NUMBERS[n] if n < len(_NUMBERS) else str(n)


def _the_facts_alone(facts: "Dict[str, Facts]", trouble: str) -> List[str]:
    """What prints when no model answered.

    The report degrades rather than disappearing.  A person who wanted to know
    what happened today still finds out; what they lose is the sentence tying
    it together, and they are told that is what they lost and why.
    """
    lines = []
    if trouble:
        lines.append("(no summary: {})".format(trouble.splitlines()[0]))
        lines.append("")
    for name, f in sorted(facts.items(),
                          key=lambda kv: kv[1].seconds, reverse=True):
        lines.append("  {}".format(name))
        lines.append("     " + shortened(f.counted(), WIDTH - 5))
        if f.outcomes:
            lines.extend(_wrapped("; ".join(f.outcomes[:6]),
                                  "     done      ", "               "))
        if f.stuck:
            lines.extend(_wrapped("; ".join(f.stuck[:3]),
                                  "     failing   ", "               "))
        goal = pick_ask(f.asks)
        if goal and not f.outcomes:
            lines.extend(_wrapped(shortened(goal, 200),
                                  "     asked for ", "               "))
        lines.append("")
    return lines
