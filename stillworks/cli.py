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
import codecs
import json
import os
import random
import runpy
import sys

from . import __version__, core


def _err(msg):
    print("stillworks: {}".format(msg), file=sys.stderr)
    return 2


def cmd_lock(args):
    project = os.path.abspath(args.project)
    if getattr(args, "timeout", None) is not None and args.timeout <= 0:
        return _err("--timeout must be greater than zero (got {})".format(args.timeout))
    if os.path.exists(project) and not os.path.isdir(project):
        return _err("--project must be a directory, and {} is a file".format(project))
    records = []
    module_info = None
    mod = None
    skipped = 0

    if not args.target and not args.cmd:
        return _err("nothing to lock — give a TARGET module/file, or --cmd")
    if args.run and not args.target:
        return _err("--run needs a TARGET module or file to record calls into, "
                    "e.g.: stillworks lock src/mod.py --run scripts/daily.py")
    if args.fuzz and not args.target:
        return _err("--fuzz needs a TARGET module or file, "
                    "e.g.: stillworks lock src/mod.py --fuzz 8")

    if args.target:
        try:
            mod, module_info = core.load_module(args.target, project)
        except Exception as exc:
            return _err("could not load {}: {}".format(args.target, exc))

    if args.run and mod is not None:
        script = os.path.abspath(args.run)
        if not os.path.exists(script):
            return _err("no such script: {}".format(script))
        with core.Recorder(mod) as rec:
            old_argv = sys.argv
            sys.argv = [script] + (args.script_args or [])
            try:
                runpy.run_path(script, run_name="__main__")
            except SystemExit:
                pass
            except Exception as exc:
                print("stillworks: script raised {}: {} (recorded calls up to "
                      "that point are kept)".format(type(exc).__name__, exc),
                      file=sys.stderr)
            finally:
                sys.argv = old_argv
        records.extend(rec.records)
        skipped += rec.skipped_unpicklable

    if args.fuzz and mod is not None:
        rng = random.Random(args.seed)
        per_fn = max(1, args.fuzz)
        fuzz_empty = []
        for name, fn in core.public_functions(mod):
            recs, sk = core.fuzz_function(name, fn, rng, per_fn)
            if not recs:
                fuzz_empty.append(name)
            records.extend(recs)
            skipped += sk
        if fuzz_empty:
            print("stillworks: could not generate inputs for: {}\n"
                  "  (--fuzz needs positional parameters annotated with "
                  "int/float/str/bool/list/dict\n   and no required "
                  "keyword-only parameters — capture these with --run or --cmd)"
                  .format(", ".join(fuzz_empty)), file=sys.stderr)

    timeout = getattr(args, "timeout", None) or core.DEFAULT_CMD_TIMEOUT
    for c in (args.cmd or []):
        out = core.run_cmd(c, cwd=project, timeout=timeout)
        records.append({"kind": "cmd", "cmd": c, "out": out, "source": "cmd"})

    if not records:
        hint = ""
        if args.target and not args.run and not args.fuzz:
            hint = " (try --fuzz 8, or --run your_script.py; fuzzing needs " \
                   "type annotations on function parameters)"
        return _err("no behavior captured{}".format(hint))

    if args.max and len(records) > args.max:
        records = records[:args.max]

    core.assign_ids(records)
    # Determinism guard: replay each record once; flag flaky ones.
    core.mark_nondeterministic(records, mod, project)

    # `lock` is the way out of a damaged lockfile, so it must not be blocked by
    # one.  It only reads the old file to say what it is about to replace.
    try:
        existing = core.load_lock(project)
    except core.LockfileError as exc:
        existing = None
        print("stillworks: replacing a lockfile that could not be read\n"
              "  {}".format(exc), file=sys.stderr)
    if existing is not None:
        n_hist = len(existing.get("history") or [])
        print("stillworks: replacing existing lockfile ({} records{})\n"
              "  (to capture several modes in one baseline, combine them in a "
              "single lock command)".format(
                  len(existing.get("records") or []),
                  ", {} accepted changes".format(n_hist) if n_hist else ""),
              file=sys.stderr)

    lock = core.new_lock(module_info, args.seed)
    lock["records"] = records
    try:
        core.save_lock(project, lock)
    except OSError as exc:
        return _err("could not write the lockfile into {}\n"
                    "  {}\n"
                    "  stillworks needs to create a {} directory in the project "
                    "it locks.".format(
                        os.path.join(project, core.LOCK_DIR), exc, core.LOCK_DIR))

    n_calls = sum(1 for r in records if r["kind"] == "call")
    n_cmds = sum(1 for r in records if r["kind"] == "cmd")
    n_nondet = sum(1 for r in records if r.get("nondet"))
    print("locked {} records ({} calls, {} commands) -> {}".format(
        len(records), n_calls, n_cmds,
        os.path.relpath(core.lock_path(project))))
    if n_nondet:
        print("  {} nondeterministic (flagged, excluded from check)".format(n_nondet))
    if skipped:
        print("  {} calls skipped (arguments not picklable)".format(skipped))
    return 0


def cmd_check(args):
    project = os.path.abspath(args.project)
    result = core.check(project)
    if "error" in result:
        return _err(result["error"])
    if args.json:
        print(json.dumps(result, indent=1, default=str))
    else:
        counts = result["counts"]
        for e in result["results"]:
            if e["status"] == "OK":
                continue
            print("{:8s} {}  ({})".format(e["status"], e["id"], e["target"]))
            if e["status"] == "CHANGED":
                if e["kind"] == "call":
                    print("         args: {}".format(e.get("args", "")))
                    w = e["was"].get("repr", e["was"])
                    n = e["now"].get("repr", e["now"])
                    if w == n:
                        # reprs identical (e.g. address-scrubbed objects) —
                        # the difference lives in the canonical projection
                        w = json.dumps(e["was"].get("canon"), sort_keys=True, default=str)
                        n = json.dumps(e["now"].get("canon"), sort_keys=True, default=str)
                    print("         was:  {}".format(_head(w, 400)))
                    print("         now:  {}".format(_head(n, 400)))
                else:
                    _print_cmd_diff(e)
            elif "note" in e:
                print("         {}".format(e["note"]))
        total = sum(counts.values())
        summary = ", ".join("{} {}".format(v, k) for k, v in sorted(counts.items()))
        verdict = "STILL WORKS" if result["ok"] else "BEHAVIOR CHANGED"
        print("{}: {} records — {}".format(verdict, total, summary))
    return 0 if result["ok"] else 1


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
    return 0


def cmd_report(args):
    from . import report
    project = os.path.abspath(args.project)
    text = report.build(project)
    if text is None:
        return _err("no lockfile — run `stillworks lock` first")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        print("wrote {}".format(args.output))
    else:
        print(text)
    return 0


def cmd_status(args):
    project = os.path.abspath(args.project)
    lock = core.load_lock(project)
    if lock is None:
        print("no lockfile in {}".format(os.path.join(project, core.LOCK_DIR)))
        print("start with: stillworks lock <module-or-file> --fuzz 8")
        return 0
    records = lock["records"]
    n_nondet = sum(1 for r in records if r.get("nondet"))
    print("lockfile: {} records, created {}".format(len(records), lock["created"]))
    if lock.get("module"):
        print("module:   {}".format(lock["module"].get("path") or lock["module"].get("module")))
    if n_nondet:
        print("flagged:  {} nondeterministic".format(n_nondet))
    if lock.get("history"):
        print("history:  {} accepted changes".format(len(lock["history"])))
    return 0


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


def _write_utf8_if_the_locale_said_nothing():
    """Write UTF-8 when the machine claims it can only take ASCII.

    A container with no locale set — a Dockerfile without ``ENV LANG``, cron,
    most of CI — leaves Python believing stdout is ASCII, and then a single em
    dash of our own raises ``UnicodeEncodeError`` halfway through a listing:
    a traceback and half a screen, over a character no one chose.

    An ASCII claim is not a claim about the terminal, though.  It is the
    absence of one, and the terminal on the other end is virtually always
    UTF-8.  So we write UTF-8 and keep ``surrogateescape``, which hands back
    unchanged the bytes of any filename this machine could not decode — that is
    what makes a name it cannot spell come out spelled right anyway.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if codecs.lookup(stream.encoding or "").name == "ascii":
                stream.reconfigure(encoding="utf-8", errors="surrogateescape")
        except (AttributeError, LookupError, OSError, ValueError):
            pass                        # not a real stream, or already written to


def main(argv=None):
    """Entry point, and the one place ctrl-c is allowed to mean something.

    Recording a baseline runs the commands being recorded, and those are test
    suites and builds — the longest-running thing anybody points this tool at.
    Interrupting one is ordinary; a traceback in reply reads as a crash.

    The code matters more here than elsewhere, because these codes are already
    load-bearing: `check` exits 0 for unchanged and 1 for changed, and gets
    written as `stillworks check && deploy`.  An abandoned check must be
    neither answer, so it is 130 — the shell's own spelling of "stopped by
    ctrl-c".

    A closed pipe is the same argument, twice over.  `stillworks check | head`
    and `stillworks report | less` quit with `q` are ordinary ways to read a
    long list of records, and unhandled the first of those ended in a traceback
    and exit 1 — the code that means BEHAVIOR CHANGED.  A check that got cut
    off compared nothing, so it answers 141: 128 + SIGPIPE, the shell's own
    spelling of "the reader hung up".  The flush is in a `finally` because
    argparse prints `--help` and `--version` and exits before `_run` runs.
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


def _stop_writing_down_a_closed_pipe():
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



def _run(argv=None):
    _write_utf8_if_the_locale_said_nothing()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    problem = _whats_wrong_with_the_project_dir(args)
    if problem:
        print("stillworks: {}".format(problem), file=sys.stderr)
        return 2

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
        return 2


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
