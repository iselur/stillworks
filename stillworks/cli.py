"""stillworks command-line interface.

Commands:
  stillworks lock [TARGET] [--run SCRIPT] [--fuzz N] [--cmd CMD]... [--seed N]
  stillworks check [--json]
  stillworks accept [ID ...] [--all]
  stillworks report [-o FILE]
  stillworks status
  stillworks mcp
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, core
from .shell import run_as_a_command
# What a terminal obeys rather than shows is a fact about terminals rather
# than about a behaviour lock: it lives in `terminal.py`, which is the same
# file in the five packages that print.  Every value on a `check` row comes
# out of a lockfile an agent working in the repo is free to rewrite, so this
# is the seam that most needs one answer rather than five.
from .terminal import row as _one_row


# The three numbers this tool promises, and the only part of its output a
# script reads.  `1` is the merge gate; the other two exist so that nothing
# can impersonate it.  See README.md and
# tests/test_the_exit_codes_the_readme_promises.py, which compares these
# against the prose.
OK = 0
BEHAVIOR_CHANGED = 1
COULD_NOT_CHECK = 2


def _err(msg):
    print("stillworks: {}".format(msg), file=sys.stderr)
    return COULD_NOT_CHECK


def _warn(msg):
    """Something went wrong beside the answer, not instead of it.

    Goes to stderr so it never lands in `--json`, and returns nothing, so it
    cannot be mistaken for an exit code on the way out.
    """
    print("stillworks: warning: {}".format(msg), file=sys.stderr)


def _say(note):
    """One of core's notes, on stderr, where it does not land in `--json`."""
    print("stillworks: {}".format(note), file=sys.stderr)


def cmd_lock(args):
    project = os.path.abspath(args.project)
    result = core.lock(
        project,
        target=args.target,
        run=args.run,
        script_args=args.script_args,
        fuzz=args.fuzz,
        seed=args.seed,
        cmds=args.cmd,
        timeout=getattr(args, "timeout", None),
        max_records=args.max,
    )
    # Said before the verdict either way: these are things that happened during
    # the run, and a note printed after "locked 12 records" reads as a caveat
    # about the lockfile rather than as part of making it.
    for note in result.get("notes", ()):
        _say(note)
    if "error" in result:
        return _err(result["error"])
    print("locked {} records ({} calls, {} commands) -> {}".format(
        result["records"], result["calls"], result["cmds"],
        os.path.relpath(result["path"])))
    if result["nondet"]:
        print("  {} nondeterministic (flagged, excluded from check)".format(
            result["nondet"]))
    if result["skipped"]:
        print("  {} calls skipped (arguments not picklable)".format(
            result["skipped"]))
    return OK


def cmd_check(args):
    project = os.path.abspath(args.project)
    result = core.check(project)
    if "error" in result:
        return _err(result["error"])
    if "not_saved" in result:
        # The comparison happened; only the receipt of it did not get written.
        # Say so, and let the records below decide the exit code.
        _warn(result["not_saved"] +
              " — `accept` and `report` will not see this run")
    # Asked once, here, rather than three times below: the verdict printed, the
    # advice printed under it and the code handed back are one answer to one
    # question, and a fourth reader of `verified` is how they come apart.
    nothing_verified = result["verified"] == 0
    if args.json:
        print(json.dumps(result, indent=1, default=str))
    else:
        counts = result["counts"]
        # Every value below was read back out of the lockfile, so every one of
        # them goes through _one_row: one record is one row, and the row count
        # on screen is what the closing summary counts.
        for e in result["results"]:
            if e["status"] == "OK":
                continue
            print("{:8s} {}  ({})".format(
                _one_row(e["status"]), _one_row(e["id"]), _one_row(e["target"])))
            if e["status"] == "CHANGED":
                if e["kind"] == "call":
                    print("         args: {}".format(_one_row(e.get("args", ""))))
                    w = e["was"].get("repr", e["was"])
                    n = e["now"].get("repr", e["now"])
                    if w == n:
                        # reprs identical (e.g. address-scrubbed objects) —
                        # the difference lives in the canonical projection
                        w = json.dumps(e["was"].get("canon"), sort_keys=True, default=str)
                        n = json.dumps(e["now"].get("canon"), sort_keys=True, default=str)
                    print("         was:  {}".format(_one_row(w)))
                    print("         now:  {}".format(_one_row(n)))
                else:
                    _print_cmd_diff(e)
            elif "note" in e:
                print("         {}".format(_one_row(e["note"])))
        total = sum(counts.values())
        summary = ", ".join("{} {}".format(v, k) for k, v in sorted(counts.items()))
        if nothing_verified:
            verdict = "NOTHING VERIFIED"
        elif result["ok"]:
            verdict = "STILL WORKS"
        else:
            verdict = "BEHAVIOR CHANGED"
        print("{}: {} records — {}".format(verdict, total, summary))
        if nothing_verified:
            # The one line that said this would happen was printed by `lock`,
            # days ago, and scrolled past.  Say it here, where the answer is
            # being read, and say what to do about it.
            print("         every record was flagged nondeterministic at lock "
                  "time, so nothing")
            print("         was compared.  Lock something that settles — a "
                  "seeded call, or an")
            print("         end-to-end run with `--cmd` — or this check cannot "
                  "fail.")
        if result.get("partial"):
            # Said on every check, not once at lock time, because the lockfile
            # outlives the terminal it was made in — and the verdict above is
            # about however much of the code the driver got to before it died.
            print("         {}.".format(_one_row(result["partial"])))
            print("         Whatever it would have exercised afterwards is "
                  "not covered here.")
            print("         Re-lock once the script runs to the end.")
    if nothing_verified:
        # Not 1: behavior did not change, because nothing looked.  2 is this
        # tool's word for "this did not work", and is what `lock` returns when
        # it declines to write a lockfile with nothing in it.
        return COULD_NOT_CHECK
    return OK if result["ok"] else BEHAVIOR_CHANGED


def _print_cmd_diff(e):
    import difflib
    was, now = e["was"], e["now"]
    if was.get("exit") != now.get("exit"):
        print("         exit: {} -> {}".format(was.get("exit"), now.get("exit")))
    for stream in ("stdout", "stderr"):
        w, n = was.get(stream, ""), now.get(stream, "")
        if w == n:
            continue
        print("         {} changed:".format(stream))
        if "\n" in w or "\n" in n:
            diff = difflib.unified_diff(w.splitlines(), n.splitlines(),
                                        "was", "now", lineterm="")
            for i, line in enumerate(diff):
                if i >= 40:
                    print("           ...diff truncated...")
                    break
                print("           {}".format(line))
        else:
            print("           was: {!r}".format(_head(w)))
            print("           now: {!r}".format(_head(n)))


def _head(text, n=200):
    return text if len(text) <= n else text[:n] + "..."

def cmd_accept(args):
    project = os.path.abspath(args.project)
    if not args.ids and not args.all:
        return _err("say which records to accept: `stillworks accept ID ...` "
                    "or `stillworks accept --all`")
    result = core.accept(project, ids=None if args.all else args.ids)
    if "error" in result:
        return _err(result["error"])
    for rid in result["accepted"]:
        print("accepted new behavior: {}".format(rid))
    for rid in result["removed"]:
        print("removed (function gone): {}".format(rid))
    if not result["accepted"] and not result["removed"]:
        print("nothing to accept — baseline already matches current behavior")
    return OK


def cmd_report(args):
    from . import report
    project = os.path.abspath(args.project)
    text = report.build(project)
    if text is None:
        return _err("no lockfile — run `stillworks lock` first")
    if args.output:
        # A path somebody typed: a read-only directory, a folder an earlier
        # step was supposed to create, a typo.  Unhandled this came back as a
        # traceback and exit 1, and 1 is this tool's word for BEHAVIOR CHANGED
        # — so a report that wrote nothing looked exactly like a check that
        # caught a regression.  Name the file, say why, exit 2.
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as exc:
            return _err("could not write the report to {}: {}".format(
                args.output, exc))
        print("wrote {}".format(args.output))
    else:
        print(text)
    return OK


def cmd_status(args):
    result = core.status(os.path.abspath(args.project))
    if "none" in result:
        print("no lockfile in {}".format(result["none"]))
        print("start with: stillworks lock <module-or-file> --fuzz 8")
        return OK
    print("lockfile: {} records, created {}".format(
        result["records"], result["created"]))
    # Same rule as `check`: every value here was read back out of a file an
    # agent in the repo is free to rewrite, so every value goes through a row.
    for label, value in (("module", result["module"]),
                         ("flagged", "{} nondeterministic".format(result["nondet"])
                                     if result["nondet"] else ""),
                         ("history", "{} accepted changes".format(result["history"])
                                     if result["history"] else ""),
                         ("partial", result["partial"])):
        if value:
            print("{:9s} {}".format(label + ":", _one_row(value)))
    return OK


def cmd_mcp(args):
    from . import mcp_server
    return mcp_server.serve()


def cmd_tools(args):
    from .tools import cmd_tools as _tools
    return _tools(args)


def build_parser():
    p = argparse.ArgumentParser(
        prog="stillworks",
        description="Record what your code does now, catch when it "
                    "changes later.")
    p.add_argument("--version", action="version",
                   version="stillworks {}".format(__version__))
    p.add_argument("--project", default=".", help="project directory "
                   "(default: current directory)")
    # Also accepted after the subcommand (`stillworks check --project X`);
    # SUPPRESS keeps the sub-level flag from clobbering the global default.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("lock", parents=[common],
                        help="record current behavior as the baseline")
    sp.add_argument("target", nargs="?", help="module name (pkg.mod) or file (src/mod.py)")
    sp.add_argument("--run", metavar="SCRIPT", help="run this script and record "
                    "every call into TARGET's public functions")
    sp.add_argument("--fuzz", type=int, metavar="N", help="generate up to N "
                    "inputs per annotated public function")
    sp.add_argument("--cmd", action="append", metavar="CMD", help="record a "
                    "shell command's exit/stdout/stderr (repeatable; works for "
                    "any language)")
    sp.add_argument("--seed", type=int, default=1234, help="fuzz seed (default 1234)")
    sp.add_argument("--timeout", type=float, metavar="SECONDS",
                    help="give up on a --cmd after this long (default {})".format(
                        core.DEFAULT_CMD_TIMEOUT))
    sp.add_argument("--max", type=int, help="cap total records")
    sp.add_argument("script_args", nargs="*", help="arguments passed to --run script")
    sp.set_defaults(func=cmd_lock)

    sp = sub.add_parser("check", parents=[common], help="replay the baseline against current code")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("accept", parents=[common], help="bless intentional behavior changes")
    sp.add_argument("ids", nargs="*", help="record ids to accept")
    sp.add_argument("--all", action="store_true", help="accept every change")
    sp.set_defaults(func=cmd_accept)

    sp = sub.add_parser("report", parents=[common], help="write a markdown evidence report")
    sp.add_argument("-o", "--output", help="output file (default: stdout)")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("status", parents=[common], help="show lockfile summary")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("mcp", help="serve the MCP interface on stdio")
    sp.set_defaults(func=cmd_mcp)

    # No count in the help text: it is one more place for "four" to survive a
    # family of five, and the command's own output says the number anyway.
    sp = sub.add_parser("tools", help="which of the agent tools you have")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_tools)

    return p



def main(argv=None):
    """Entry point, and the one place ctrl-c is allowed to mean something.

    Recording a baseline runs the commands being recorded, and those are test
    suites and builds — the longest-running thing anybody points this tool at.
    Interrupting one is ordinary; a traceback in reply reads as a crash.

    The code matters more here than elsewhere, because these codes are already
    load-bearing: `check` exits 0 for unchanged and 1 for changed, and gets
    written as `stillworks check && deploy`.  An abandoned check must be
    neither answer, so it is 130.

    A closed pipe is the same argument, twice over.  `stillworks check | head`
    and `stillworks report | less` quit with `q` are ordinary ways to read a
    long list of records, and unhandled the first of those ended in a traceback
    and exit 1 — the code that means BEHAVIOR CHANGED.  A check that got cut
    off compared nothing, so it must not answer either of those either.

    Both of those are `shell.run_as_a_command`, which is where the mechanism
    lives and where the codes are named.  What is here is why this tool in
    particular cannot afford to get them wrong.
    """
    return run_as_a_command(_run, argv)



def _run(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return OK

    problem = _whats_wrong_with_the_project_dir(args)
    if problem:
        print("stillworks: {}".format(problem), file=sys.stderr)
        return COULD_NOT_CHECK

    try:
        return args.func(args)
    except core.LockfileError as exc:
        # Not 0 and not 1: `check` gates a merge, where 0 means nothing moved
        # and 1 means something did.  A lockfile nobody can read is neither
        # answer, and a script would believe either one.
        print("stillworks: {}\n"
              "  a lockfile is a recording — re-record it with "
              "`stillworks lock`, or fix the file by hand."
              .format(exc), file=sys.stderr)
        return COULD_NOT_CHECK


# The commands that actually operate on a project directory — the ones built
# with the `common` parent, which is where `--project` comes from.  `tools`
# reports what is on your PATH and `mcp` takes a project per request, so
# neither has an opinion about this directory.
_COMMANDS_THAT_USE_THE_PROJECT = frozenset(
    ("lock", "check", "accept", "report", "status"))


def _whats_wrong_with_the_project_dir(args):
    """The reason to stop before running, or None.

    A `--project` that is not there used to be made rather than questioned:
    `stillworks --project ~/aap lock` created `~/aap/.stillworks/` and said
    `locked 1 records`, so the typo looked like a success while the project it
    was meant to guard stayed unlocked.  The reading commands had the mirror
    problem — `check` on a path that does not exist answered `no lockfile — run
    stillworks lock first`, which is what an un-locked project says.  Together
    those two make a loop that never mentions the real mistake.

    Only a named directory can be wrong this way; the default is the current
    directory, which exists by definition.
    """
    if args.command not in _COMMANDS_THAT_USE_THE_PROJECT:
        return None
    project = getattr(args, "project", ".")
    if os.path.isdir(project):
        return None
    if os.path.exists(project):
        return "not a directory: {}".format(project)
    return "no such directory: {}".format(project)


if __name__ == "__main__":
    sys.exit(main())
