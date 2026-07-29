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
import random
import runpy
import sys

from . import core


def _err(msg):
    print("stillworks: {}".format(msg), file=sys.stderr)
    return 2


def cmd_lock(args):
    project = os.path.abspath(args.project)
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

    for c in (args.cmd or []):
        out = core.run_cmd(c, cwd=project)
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

    existing = core.load_lock(project)
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
    core.save_lock(project, lock)

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


def build_parser():
    p = argparse.ArgumentParser(
        prog="stillworks",
        description="Record what your code does now, catch when it "
                    "changes later.")
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

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
