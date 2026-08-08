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
  --brief                        # a written report: what you worked on, what
                                 #   is done, what is not.  This one asks a
                                 #   model, so it sends your day off the box.
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
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import __version__
from .brief import render_brief
from .handover import handle as handle_hook
from .parser import find_sessions, read_one_session
from .render import (
    render_digest,
    render_json,
    render_list,
    render_markdown,
    render_show,
    render_text,
)
from .unusable import ALL, note_about
from .html import render_html
from .window import Unparseable, Window
from .when import HOW_TO_SPELL_IT
from .project import HOW_IT_MATCHES, matches
from .shell import as_typed, run_as_a_command
from .where_the_logs_are import log_dirs


def _filter_project(sessions: List[Dict], needle: str) -> List[Dict]:
    """Keep the sessions ``needle`` asked for.

    A session knows its project by two names -- the directory it ran in and the
    label shown in the digest -- and either one is worth matching, so both are
    handed over and ``matches`` decides.  Which is also how ``agentwatch``
    decides, now that there is one of these rules rather than two.
    """
    return [
        s
        for s in sessions
        if matches(needle, s.get("project_name"), s.get("project"))
    ]


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

# Commands that take a second word.  Everything else takes none, and a stray
# word after them is a typo the person deserves to be told about.
_COMMANDS_WITH_ARG = ("since", "on", "show")


def _log_dirs(home_dir: Optional[str]) -> List[str]:
    """The log directories, resolved, for comparing a path against.

    `realpath` is here and not in `where_the_logs_are` because this is the one
    caller that compares rather than prints.  A home reached through a symlink
    -- ``/home`` on a box where it is a link to ``/mnt/home`` -- gives a target
    and a log directory that are the same place spelled two ways, and a guard
    that compares the spellings lets the write through.
    """
    home = home_dir or os.environ.get("AGENTLOG_HOME") or None
    return [os.path.realpath(directory) for _, _, directory in log_dirs(home)]


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
            "  agentlog handover        (run by an agent hook, not by you)\n"
        ),
    )
    p.add_argument("--version", action="version", version=f"agentlog {__version__}")
    p.add_argument(
        "command",
        nargs="?",
        default="today",
        metavar="COMMAND",
        help="today | yesterday | week | since DATE | on DAY | show ID | list "
             "| handover (default: today)",
    )
    p.add_argument(
        "arg",
        nargs="?",
        default=None,
        metavar="ARG",
        # What `since` takes is read off the table that parses it, so this
        # cannot go on advertising a spelling the parser dropped -- or stay
        # quiet about one it gained, which is what happened with `10m`.
        help="argument for 'since' ({}), 'on' (2026-07-15, 3d) "
             "or 'show' (session ID prefix)".format(HOW_TO_SPELL_IT),
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
        "--brief",
        action="store_true",
        help="a written report of what got done and what did not "
             "(asks a model; sends the day off this machine)",
    )
    p.add_argument(
        "--file",
        metavar="PATH",
        help="read only this transcript, all of it "
             "(the time command does not apply)",
    )
    p.add_argument(
        "--sessions",
        action="store_true",
        help="list every session instead of the per-project digest",
    )
    p.add_argument(
        "--project",
        metavar="NAME",
        help=HOW_IT_MATCHES,
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


def main(argv=None) -> int:
    """Entry point, and the one place ctrl-c is allowed to mean something.

    Reading a year of logs takes a moment, and a moment is long enough to
    change your mind in.  Interrupting a command that is taking longer than you
    expected is ordinary; answering it with a traceback is not, because a
    traceback reads as a crash and sends people looking for a bug they caused
    on purpose.  Answering an interrupted run with 130 also keeps `agentlog
    today > digest.md && mail-it` from mailing half a day.

    A closed pipe is the same shape of thing.  `agentlog today | head` and
    `| less` quit with `q` are ordinary too, and they leave us writing into a
    pipe nobody is reading.  A digest that got cut off short reported nothing
    about your day, so it must not come back as one of the answers a caller is
    looking for.

    Both of those are `shell.run_as_a_command`, which is where the mechanism
    lives and where the codes are named.  What is here is why this tool in
    particular cannot afford to get them wrong.
    """
    return run_as_a_command(_run, argv)


def _run(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.project = as_typed(args.project)

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

    # 'handover' is not a way of looking at the logs, so it is answered before
    # any of the flags that say how to look at them.  Its whole input is the
    # hook payload on standard input, and its exit code is 0 whatever happens:
    # an agent must not be stopped by a problem with its own note-taking.
    if args.command == "handover":
        # Typed at a terminal there is no payload coming, and a read on a
        # keyboard never returns: the command would look like it had hung,
        # which is the one impression a note-taker must not leave.  Say what
        # it is for instead.  Exit 2, like any other usage mistake -- a hook
        # never reaches this branch, so the promise above is untouched.
        if sys.stdin.isatty():
            print("agentlog handover is run by an agent hook, not by hand: it\n"
                  "reads a hook's JSON payload on standard input.  See 'The "
                  "note a\nsession leaves itself' in the README for the two "
                  "lines to add to\n~/.claude/settings.json.", file=sys.stderr)
            return 2
        out, err = handle_hook(sys.stdin.read(), home_dir)
        if out:
            sys.stdout.write(out)
        if err:
            sys.stderr.write(err)
        return 0

    # --brief is prose and the others are documents; asking for both means one
    # of them was a mistake, and guessing which would print the wrong one.
    if args.brief:
        also = [flag for flag, on in (("--html", bool(args.html)),
                                      ("--md", args.md is not None),
                                      ("--json", args.json),
                                      ("--sessions", args.sessions)) if on]
        if also:
            print(
                "agentlog: --brief cannot be combined with {}\n"
                "  --brief writes a report to read; the others write a "
                "document to keep.".format(" or ".join(also)),
                file=sys.stderr,
            )
            return 2
        if args.command in ("list", "show"):
            print(
                "agentlog: --brief is not supported for '{}'; use a time "
                "command (today, week, since ...) instead".format(args.command),
                file=sys.stderr,
            )
            return 2

    # --file names the one session to read.  'list' and 'show' are both ways of
    # finding a session among many, which is the job --file has already done.
    if args.file is not None and args.command in ("list", "show"):
        print(
            "agentlog: --file cannot be combined with '{}'\n"
            "  --file already names the session; '{}' is for finding "
            "one.".format(args.command, args.command),
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
    if args.file is not None:
        # A named file is the whole scope, so the window is not applied: a
        # session handed over by a hook may well have started yesterday, and
        # clipping it to today would answer an explicit request with silence.
        sessions, sources, unusable = read_one_session(args.file)
        if not sessions:
            print("agentlog: nothing to read in {}".format(args.file),
                  file=sys.stderr)
            _note_unusable(unusable, args.verbose, to_stderr=True)
            return 2
        period_label = "this session"
    else:
        sessions, sources, unusable = find_sessions(home_dir)
        if not sessions and not sources:
            _no_sessions_msg(home_dir)
            return 0

    filtered = sessions if args.file is not None else window.clip(sessions)
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
        elif args.brief:
            print(render_brief(filtered, period_label), end="")
            _note_unusable(unusable, args.verbose)
            return 0
        elif args.sessions:
            print(render_text(filtered, verbose=args.verbose))
        else:
            print(render_digest(filtered, period_label, verbose=args.verbose))
        _note_unusable(unusable, args.verbose)

    return 0


def _note_unusable(unusable, verbose: bool, to_stderr: bool = False) -> None:
    """Say that some session logs are missing from this, if any are.

    Goes to stderr under --json, where stdout is a published contract (a bare
    array of sessions) and a warning appended to it would break every reader.

    `--verbose` means name every one of them, not a sample: a report is
    re-runnable, and a reader who asked which files has asked for the list.
    That is the only part of the sentence this tool decides -- the rest of it
    is in `unusable.py`, which `agentwatch` prints from too.
    """
    note = note_about(unusable, ALL if verbose else 0)
    if note:
        print("\n" + note if not to_stderr else note,
              file=sys.stderr if to_stderr else sys.stdout)


def _no_sessions_msg(home_dir: Optional[str]) -> None:
    # The places named here are the places that were looked in, because they
    # come from the same list the looking did.  A sentence that tells you where
    # to go and start a session, naming a directory nothing read, is worse than
    # saying nothing: it is a wrong answer to "why is this empty".
    looked_in = "".join(
        "  {:<14}{}\n".format(shown_as + ":",
                              os.path.join(directory, "**", "*.jsonl"))
        for _, shown_as, directory in log_dirs(home_dir))
    print(
        "No agent session logs found.\n\n"
        "agentlog looks for:\n"
        f"{looked_in}\n"
        "Start a session with Claude Code (claude) or Codex (codex) and run agentlog again."
    )


if __name__ == "__main__":
    sys.exit(main())
