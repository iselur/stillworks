"""agentlog command-line interface.

Commands
--------
  agentlog                       # same as: agentlog today
  agentlog today | yesterday | week
  agentlog since DATE            # ISO date, or offset like 3d / 12h
  agentlog on DAY                # one whole day: 2026-07-31, or 3d
  agentlog show SESSION_ID       # one session in full detail
  agentlog list                  # recent sessions, compact table (default 50)
  agentlog list --all            # all sessions

View flags (time commands)
  --sessions                     # per-session view instead of the digest
  --project NAME                 # only projects matching NAME

Output flags (may be combined with any time command)
  --html FILE                    # write self-contained HTML digest
  --md [FILE]                    # Markdown (to FILE or stdout)
  --json                         # machine-readable JSON

Other flags
  --all                          # list: show all sessions (no row limit)
  --limit N                      # list: show at most N sessions (default 50)
  --verbose                      # show skipped-line counts and debug hints
  --home DIR                     # override home directory (useful for tests)

Exit codes
  0   normal
  2   usage or argument error

The tool never writes to or uploads the session logs.
"""

from __future__ import annotations

import argparse
import codecs
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import __version__
from .parser import find_sessions
from .render import (
    render_digest,
    render_json,
    render_list,
    render_markdown,
    render_show,
    render_text,
    render_unusable,
)
from .html import render_html
from .window import Unparseable, Window


def _filter_project(sessions: List[Dict], needle: str) -> List[Dict]:
    """Keep sessions whose project name or path contains ``needle``."""
    low = needle.lower()
    return [
        s
        for s in sessions
        if low in (s.get("project_name") or "").lower()
        or low in (s.get("project") or "").lower()
    ]


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

# Commands that take a second word.  Everything else takes none, and a stray
# word after them is a typo the person deserves to be told about.
_COMMANDS_WITH_ARG = ("since", "on", "show")


def _log_dirs(home_dir: Optional[str]) -> List[str]:
    home = home_dir or os.environ.get("AGENTLOG_HOME") or os.path.expanduser("~")
    return [
        os.path.realpath(os.path.join(home, ".claude", "projects")),
        os.path.realpath(os.path.join(home, ".codex", "sessions")),
    ]


def _refuses_to_write(target: str, home_dir: Optional[str]) -> Optional[str]:
    """Why this path must not be written to, or None if it is fine.

    agentlog's one promise is that it never writes to the session logs.  A
    digest written over ``~/.claude/projects/.../session.jsonl`` destroys the
    only copy of a day's work, and a single mistyped path is all it takes.
    """
    if not target or target == "-":
        return None
    real = os.path.realpath(target)
    for root in _log_dirs(home_dir):
        if real == root or real.startswith(root + os.sep):
            return ("refusing to write inside the session log directory\n"
                    "  {}\n"
                    "  agentlog never writes to the logs it reads. "
                    "Choose a path outside them.".format(root))
    if real.endswith(".jsonl"):
        return ("refusing to write over {}\n"
                "  that is a session log, not an output file.".format(target))
    return None


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agentlog",
        description="What did your coding agent actually do today?",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  agentlog\n"
            "  agentlog yesterday\n"
            "  agentlog since 3d\n"
            "  agentlog on 2026-07-31\n"
            "  agentlog show 0224e6b8\n"
            "  agentlog list\n"
            "  agentlog today --html digest.html\n"
            "  agentlog week --json\n"
        ),
    )
    p.add_argument("--version", action="version", version=f"agentlog {__version__}")
    p.add_argument(
        "command",
        nargs="?",
        default="today",
        metavar="COMMAND",
        help="today | yesterday | week | since DATE | on DAY | show ID | list "
             "(default: today)",
    )
    p.add_argument(
        "arg",
        nargs="?",
        default=None,
        metavar="ARG",
        help="argument for 'since' (3d, 12h, 2026-07-15), 'on' (2026-07-15, 3d) "
             "or 'show' (session ID prefix)",
    )
    p.add_argument("--html", metavar="FILE", help="write self-contained HTML to FILE")
    p.add_argument(
        "--md",
        metavar="FILE",
        nargs="?",
        const="-",
        help="write Markdown to FILE (or stdout if FILE omitted)",
    )
    p.add_argument("--json", action="store_true", help="print JSON to stdout")
    p.add_argument(
        "--sessions",
        action="store_true",
        help="list every session instead of the per-project digest",
    )
    p.add_argument(
        "--project",
        metavar="NAME",
        help="only include projects whose name or path contains NAME",
    )
    p.add_argument("--all", action="store_true", help="list: show all sessions (no row limit)")
    p.add_argument("--limit", type=int, default=50, metavar="N", help="list: max rows to show (default 50)")
    p.add_argument("--verbose", action="store_true", help="show parsing diagnostics")
    p.add_argument(
        "--home",
        metavar="DIR",
        help="override home directory (default: ~); sets where logs are sought",
    )
    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _as_typed(text):
    """An argument in the form it was typed, not the form the locale allowed.

    Python decodes ``sys.argv`` with the filesystem encoding, and on a machine
    with no locale that encoding is ASCII — so ``--project 設定`` arrives as a
    run of surrogates and matches nothing.  A filter that silently matches
    nothing is the worst way for this to fail: it reads as a quiet day rather
    than as an error.  ``os.fsencode`` gives the bytes back untouched, and the
    shell that sent them was speaking UTF-8.
    """
    if text is None or text.isascii():
        return text                     # the overwhelmingly common case
    try:
        return os.fsencode(text).decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


def _write_utf8_if_the_locale_said_nothing() -> None:
    """Write UTF-8 when the machine claims it can only take ASCII.

    A container with no locale set — a Dockerfile without ``ENV LANG``, cron,
    most of CI — leaves Python believing stdout is ASCII, and then a single em
    dash of our own raises ``UnicodeEncodeError`` halfway through the digest:
    a traceback and half a report, over a character no one chose.

    An ASCII claim is not a claim about the terminal, though.  It is the
    absence of one, and the terminal on the other end is virtually always
    UTF-8.  So we write UTF-8 and keep ``surrogateescape``, which hands back
    unchanged the bytes of any path this machine could not decode — that is
    what makes a name it cannot spell come out spelled right anyway.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if codecs.lookup(stream.encoding or "").name == "ascii":
                stream.reconfigure(encoding="utf-8", errors="surrogateescape")
        except (AttributeError, LookupError, OSError, ValueError):
            pass                        # not a real stream, or already written to


def _stop_writing_down_a_closed_pipe() -> None:
    """Point stdout at nowhere, so nothing is left to fail on the way out.

    Catching the `BrokenPipeError` is only half of it: whatever is still in the
    buffer gets flushed again when the interpreter shuts down, too late for any
    `except` of ours, and that second failure is what prints `Exception ignored
    in: <_io.TextIOWrapper ...>` and turns the exit code into 120.  Redirecting
    the file descriptor gives that flush somewhere harmless to go.
    """
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
    except (AttributeError, OSError, ValueError):
        pass                            # not a real stream; nothing to protect


def main(argv=None) -> int:
    """Entry point, and the one place ctrl-c is allowed to mean something.

    Reading a year of logs takes a moment, and a moment is long enough to
    change your mind in.  Interrupting a command that is taking longer than you
    expected is ordinary; answering it with a traceback is not, because a
    traceback reads as a crash and sends people looking for a bug they caused
    on purpose.  130 is the shell's own spelling of "stopped by ctrl-c", and it
    keeps `agentlog today > digest.md && mail-it` from mailing half a day.

    A closed pipe is the same shape of thing.  `agentlog today | head` and
    `| less` quit with `q` are ordinary too, and they leave us writing into a
    pipe nobody is reading.  141 is 128 + SIGPIPE, the shell's spelling of
    "the reader hung up", and like 130 it is deliberately not one of the
    answers a caller is looking for: a digest that got cut off short reported
    nothing about your day.  The flush lives in a `finally` because argparse
    prints `--help` and `--version` and then exits, so the write that fails is
    one nothing inside `_run` would ever see.
    """
    try:
        try:
            return _run(argv)
        finally:
            sys.stdout.flush()
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        _stop_writing_down_a_closed_pipe()
        return 141


def _run(argv=None) -> int:
    _write_utf8_if_the_locale_said_nothing()
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.project = _as_typed(args.project)

    home_dir = args.home or os.environ.get("AGENTLOG_HOME") or None

    # A word after a command that takes none is a typo, not a no-op.
    if args.arg is not None and args.command not in _COMMANDS_WITH_ARG:
        print(
            "agentlog: '{}' accepts no extra argument (got '{}')\n"
            "  try: agentlog {} | agentlog on {} | agentlog show ID".format(
                args.command, args.arg, args.command, args.arg),
            file=sys.stderr,
        )
        return 2

    if args.limit < 1:
        print(
            "agentlog: --limit must be 1 or more (got {})\n"
            "  use --all to show every session".format(args.limit),
            file=sys.stderr,
        )
        return 2

    for flag, target in (("--html", args.html), ("--md", args.md)):
        if target:
            reason = _refuses_to_write(target, home_dir)
            if reason:
                print("agentlog: {} {}".format(flag, reason), file=sys.stderr)
                return 2

    # ---- 'list' command ----
    if args.command == "list":
        sessions, sources, unusable = find_sessions(home_dir)
        if not sessions:
            _no_sessions_msg(home_dir)
            # "No agent session logs found" is wrong if there are log files and
            # none of them could be used, which is the case this rescues.
            _note_unusable(unusable, args.verbose)
            return 0
        if args.html or args.md is not None:
            print(
                "agentlog: --html and --md are not supported for 'list'; "
                "use a time command (today, week, since ...) instead",
                file=sys.stderr,
            )
            return 2
        # The row limit is a property of the answer, not of how it is printed:
        # --json used to quietly return everything.
        limit = None if getattr(args, "all", False) else args.limit
        truncated = sessions if limit is None else sessions[:limit]
        if args.json:
            print(render_json(truncated))
            _note_unusable(unusable, args.verbose, to_stderr=True)
            return 0
        print(render_list(truncated))
        if limit is not None and len(sessions) > limit:
            print(f"... and {len(sessions) - limit} more  (use --all to see everything)")
        _note_unusable(unusable, args.verbose)
        return 0

    # ---- 'show SESSION_ID' command ----
    if args.command == "show":
        if not args.arg:
            print("agentlog: 'show' requires a session ID", file=sys.stderr)
            return 2
        sessions, sources, unusable = find_sessions(home_dir)
        prefix = args.arg.lower()
        matches = [s for s in sessions if s["id"].lower().startswith(prefix)]
        if not matches:
            print(f"agentlog: no session found matching '{args.arg}'", file=sys.stderr)
            # The session being asked for by name may be one of the files that
            # could not be read, which makes this the most useful place to say.
            _note_unusable(unusable, args.verbose, to_stderr=True)
            return 2
        if len(matches) > 1:
            print(
                f"agentlog: {len(matches)} sessions match '{args.arg}'; "
                "showing the first. Use more characters to disambiguate:",
                file=sys.stderr,
            )
            for m in matches:
                print(f"  {m['id']}", file=sys.stderr)
        if args.html or args.md is not None:
            print(
                "agentlog: --html and --md are not supported for 'show'; "
                "use a time command (today, week, since ...) instead",
                file=sys.stderr,
            )
            return 2
        if args.json:
            print(render_json([matches[0]]))
            _note_unusable(unusable, args.verbose, to_stderr=True)
            return 0
        print(render_show(matches[0]))
        _note_unusable(unusable, args.verbose)
        return 0

    # ---- time-range commands ----
    # Everything a time command means -- which two moments it names, how to say
    # the argument is wrong, and what each session did between them -- lives in
    # window.py.  What is left here is the part that is genuinely a command
    # line's job: print the complaint, and pick the exit code.
    try:
        window = Window.parse(args.command, args.arg)
    except Unparseable as bad:
        print("agentlog: {}".format(bad), file=sys.stderr)
        return 2
    period_label = window.label

    # Load and filter
    sessions, sources, unusable = find_sessions(home_dir)
    if not sessions and not sources:
        _no_sessions_msg(home_dir)
        return 0

    filtered = window.clip(sessions)
    if args.project:
        filtered = _filter_project(filtered, args.project)

    # ---- HTML output ----
    if args.html:
        html_str = render_html(filtered, sources, period_label)
        try:
            with open(args.html, "w", encoding="utf-8") as fh:
                fh.write(html_str)
            print(f"wrote {args.html}")
            # A report saved to a file outlives the terminal it was made in, so
            # this is the last chance to say it was built from part of the logs.
            _note_unusable(unusable, args.verbose)
        except OSError as exc:
            print(f"agentlog: could not write HTML: {exc}", file=sys.stderr)
            return 2

    # ---- Markdown output ----
    if args.md is not None:
        md_str = render_markdown(filtered)
        if args.md == "-":
            print(md_str)
            # `--md -` is piped into a file or a paste buffer often enough that
            # the note has to stay out of stdout, same as --json.
            _note_unusable(unusable, args.verbose, to_stderr=True)
        else:
            try:
                with open(args.md, "w", encoding="utf-8") as fh:
                    fh.write(md_str)
                print(f"wrote {args.md}")
                _note_unusable(unusable, args.verbose)
            except OSError as exc:
                print(f"agentlog: could not write Markdown: {exc}", file=sys.stderr)
                return 2

    # ---- JSON output ----
    if args.json:
        print(render_json(filtered))
        _note_unusable(unusable, args.verbose, to_stderr=True)
        return 0

    # ---- Default: plain text ----
    if not args.html and args.md is None:
        if not filtered:
            when = period_label
            # Naming the filter matters: an empty result with a --project flag
            # usually means the name was misspelled, not that nothing happened.
            if args.project:
                when += f" · project matching '{args.project}'"
            print(f"no sessions found for: {when}")
            if args.verbose:
                print(f"  searched {len(sessions)} total sessions")
        elif args.sessions:
            print(render_text(filtered, verbose=args.verbose))
        else:
            print(render_digest(filtered, period_label, verbose=args.verbose))
        _note_unusable(unusable, args.verbose)

    return 0


def _note_unusable(unusable, verbose: bool, to_stderr: bool = False) -> None:
    """Say that some log files were not counted, if any were not.

    Goes to stderr under --json, where stdout is a published contract (a bare
    array of sessions) and a warning appended to it would break every reader.
    """
    note = render_unusable(unusable, verbose)
    if note:
        print("\n" + note if not to_stderr else note,
              file=sys.stderr if to_stderr else sys.stdout)


def _no_sessions_msg(home_dir: Optional[str]) -> None:
    home = home_dir or os.path.expanduser("~")
    print(
        "No agent session logs found.\n\n"
        "agentlog looks for:\n"
        f"  Claude Code:  {os.path.join(home, '.claude', 'projects', '**', '*.jsonl')}\n"
        f"  Codex:        {os.path.join(home, '.codex', 'sessions', '**', '*.jsonl')}\n\n"
        "Start a session with Claude Code (claude) or Codex (codex) and run agentlog again."
    )


if __name__ == "__main__":
    sys.exit(main())
