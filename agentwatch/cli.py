"""Command line for agentwatch.

    agentwatch                     # follow every active session, live
    agentwatch --since 10m         # replay the last ten minutes, then follow
    agentwatch --once              # print what is there and exit
    agentwatch --project relay     # one project only
    agentwatch --only cmd,error    # just the commands and the failures
    agentwatch --json              # one JSON object per line

Exit codes: 0 normal — including Ctrl-C while following, which is how you stop
a tailer rather than a failure.  2 usage error.  130 Ctrl-C anywhere else,
because `--once` promises finished output and a truncated file must not pass
for a complete one.  141 the reader closed the pipe.  There is deliberately no
exit 1 — agentwatch reports what an agent is doing, it does not judge it.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import List, Optional, Tuple

from . import __version__
from .events import KINDS
from .follow import DEFAULT_STALE_S, Watcher
from .printer import Printer
from .shell import as_typed, run_as_a_command
from .project import HOW_IT_MATCHES
from .when import HOW_TO_SPELL_IT, parse_moment

DEFAULT_KINDS = ("cmd", "write", "error", "turn")


def parse_kinds(raw: str) -> Tuple[str, ...]:
    """The ``--only`` list, validated against what actually exists."""
    wanted = [part.strip().lower() for part in (raw or "").split(",") if part.strip()]
    if not wanted:
        raise ValueError("empty; try --only cmd,error")
    bad = [k for k in wanted if k not in KINDS]
    if bad:
        raise ValueError("unknown: {}. Known kinds: {}".format(
            ", ".join(sorted(set(bad))), ", ".join(KINDS)))
    return tuple(dict.fromkeys(wanted))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agentwatch",
        description="Tail what your coding agent is doing, right now.",
        epilog="Reads Claude Code and Codex session logs. Never reads message "
               "text, never writes to them, sends nothing anywhere.",
    )
    p.add_argument("--version", action="version",
                   version="agentwatch {}".format(__version__))
    p.add_argument("--since", metavar="WHEN",
                   # Read off the table that parses it, so the help and the
                   # parser cannot come to disagree about the spellings.
                   help="replay activity since " + HOW_TO_SPELL_IT)
    p.add_argument("--once", action="store_true",
                   help="print what is there and exit, instead of following")
    p.add_argument("--project", metavar="NAME", default="",
                   # The sentence is in `project.py`, beside the rule it
                   # describes, so help that offers a path cannot outlive a
                   # matcher that takes one.
                   help=HOW_IT_MATCHES)
    p.add_argument("--only", metavar="KINDS",
                   help="comma-separated: {}".format(",".join(KINDS)))
    p.add_argument("--reads", action="store_true",
                   help="include file reads (an agent reads a great deal)")
    p.add_argument("--json", action="store_true",
                   help="one JSON object per line, for scripts")
    p.add_argument("--interval", metavar="SECONDS", type=float, default=1.0,
                   help="how often to look for new activity (default 1.0)")
    p.add_argument("--claude", action="store_true", help="Claude Code logs only")
    p.add_argument("--codex", action="store_true", help="Codex logs only")
    p.add_argument("--stale", metavar="SECONDS", type=float, default=DEFAULT_STALE_S,
                   help="ignore logs untouched for this long (default 900)")
    p.add_argument("--home", metavar="DIR",
                   help="override the home directory; used by tests and CI")
    p.add_argument("--no-color", action="store_true", help="never colourise")
    return p


def _sources(args) -> Tuple[str, ...]:
    if args.claude and not args.codex:
        return ("claude",)
    if args.codex and not args.claude:
        return ("codex",)
    return ("claude", "codex")


def _resolve_home(args, parser) -> str:
    """Where to look, and whether a bad answer is worth stopping over.

    A directory somebody named — typed, or exported as ``AGENTWATCH_HOME`` —
    is a directory they meant, so getting it wrong is worth saying out loud:
    staying quiet would look like a slow afternoon on a box that was never
    being watched.  A ``HOME`` that merely came with the process is a
    different thing.  Containers started with ``--user 1001`` and pods with
    ``runAsUser`` hand you a home nobody created, and this tool is exactly the
    sort of thing left running in one of those.  There being no sessions there
    is not an error; it is the empty case, which we already know how to say.
    """
    named = args.home or os.environ.get("AGENTWATCH_HOME")
    if named:
        if not os.path.isdir(named):
            parser.error("no such directory: {}".format(named))
        return named
    return os.path.expanduser("~")



def main(argv: Optional[List[str]] = None) -> int:
    """Entry point.  Ctrl-c and a closed pipe both end the run here.

    Ctrl-c is 130, the family's spelling of "you stopped it".  Following is the
    one exception and handles its own — ctrl-c is how you stop a tailer, not a
    failure — but it has to be the exception rather than the rule, because the
    rest of the modes were making a promise about their output.  `--once` is the
    scripting one: `agentwatch --once --json > events.json && process it` only
    works if exit 0 means the file is finished.  It used to be 0 whatever
    happened, so an interrupt handed back a truncated file, or an empty one,
    marked complete — and empty is also what a quiet day looks like.

    A closed pipe is different again — `agentwatch --once | head` means the
    reader stopped reading while we still had lines to write, so the tail did
    not finish, and it answers what every other tool in the family answers.

    Both live out here rather than around the polling loop, because argparse
    prints `--help` and `--version` and exits before the loop is ever built —
    and those were the two that still leaked.  Out here now means
    `shell.run_as_a_command`, which is where the mechanism lives and where the
    codes are named.
    """
    return run_as_a_command(_run, argv)


def _run(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    kinds = DEFAULT_KINDS
    if args.only:
        try:
            kinds = parse_kinds(args.only)
        except ValueError as exc:
            parser.error("--only {}".format(exc))
    elif args.reads:
        kinds = DEFAULT_KINDS + ("read",)

    since = None
    if args.since:
        try:
            since = parse_moment(args.since)
        except ValueError as exc:
            parser.error("--since {}".format(exc))

    if args.interval <= 0:
        parser.error("--interval must be greater than zero")
    if args.stale <= 0:
        parser.error("--stale must be greater than zero")

    home = _resolve_home(args, parser)

    watcher = Watcher(
        home=home,
        sources=_sources(args),
        since=since,
        stale_s=args.stale,
        project=as_typed(args.project),
    )

    printer = Printer(sys.stdout,
                      color=False if args.no_color else None,
                      as_json=args.json)
    wanted = set(kinds)

    def emit(events) -> int:
        shown = 0
        for event in events:
            if event["kind"] not in wanted:
                continue
            printer.write(event)
            shown += 1
        return shown

    if args.once:
        first = watcher.poll()
        shown = emit(first)
        if not args.json and shown == 0:
            _note(_nothing_message(watcher, since, as_typed(args.project)))
        # After the "nothing" message, which it qualifies: "nothing new yet" is
        # a claim about the agent, and a locked log makes it a claim about
        # permissions instead.
        if _unreadable_note(watcher):
            _note(_unreadable_note(watcher))
        return 0
    return _follow(watcher, args, emit)


def _nothing_message(watcher: Watcher, since, project: str) -> str:
    if watcher.watched() == 0:
        if project:
            return "no recent session logs for a project matching {!r}".format(project)
        # Logs exist, they are just all outside the window — which is a different
        # thing to be told than "you have never run an agent here".
        if watcher.found() and since is not None:
            return "nothing has happened in that window"
        return "no session logs have been written to recently"
    if since is not None:
        return "nothing has happened in that window"
    return "nothing new yet"


def _unreadable_note(watcher: Watcher) -> str:
    """What to say about logs that would not open, or '' if they all did.

    Said on every run, including `--json`, because the alternative is a quiet
    screen that reads as an idle agent.  Paths are always named — there are
    rarely more than one or two, and the fix is a chmod on a specific file.
    """
    paths = watcher.unreadable()
    if not paths:
        return ""
    n = len(paths)
    head = "{} session log{} could not be read — that activity is not shown".format(
        n, "" if n == 1 else "s")
    shown = paths[:3]
    lines = [head] + ["    " + p for p in shown]
    if len(paths) > len(shown):
        lines.append("    ... and {} more".format(len(paths) - len(shown)))
    return "\n  ".join(lines)


def _note(text: str) -> None:
    """Context goes to stderr, so `--json` on stdout stays machine-clean."""
    try:
        sys.stderr.write("  " + text + "\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def _follow(watcher: Watcher, args, emit) -> int:
    """Follow until stopped.  Ctrl-c is the stop key, so it is 0.

    This is the one mode where an interrupt is the expected ending rather than
    an interruption of anything, and it is caught here — in the only place that
    knows it is following — rather than out in `main`, where it used to be
    applied to `--once` as well and marked truncated output a success.
    """
    first = watcher.poll()          # adopts the files; also replays --since
    count = watcher.watched()
    # Sessions, not logs.  A sitting that fanned out to twenty subagents has
    # twenty-one files open and is one thing happening; see Watcher.watched.
    _note("watching {} session{} · Ctrl-C to stop".format(
        count, "" if count == 1 else "s")
        if count else "waiting for a session to start · Ctrl-C to stop")
    if _unreadable_note(watcher):
        _note(_unreadable_note(watcher))
    try:
        emit(first)
        while True:
            time.sleep(args.interval)
            emit(watcher.poll())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":       # pragma: no cover
    sys.exit(main())
