"""
agentdiff.cli — command-line interface.

Commands:
  agentdiff review   analyse working-tree changes
  agentdiff scope    persist intended scope globs
  agentdiff rules    print every rule and what it flags

Exit codes: 0 = clean, 1 = findings at gating severity or a changed file
that could not be read, 2 = usage/error.
"""

import argparse
import json
import os
import sys

from . import __version__
from .git import find_repo_root, get_changes, GitError
from .rules import RULE_DOCS, SEVERITY, gating_findings, run_rules
from .shell import run_as_a_command
# What a scope file may hold, and where it is -- one file format read and
# written in one place, because the reader and the writer disagreeing about it
# is a scope that saves cleanly and reviews as no scope at all.
from . import scope as _scope
# What a terminal obeys rather than shows is a fact about terminals rather than
# about a review: it lives in `terminal.py`, which is the same file in the four
# tools that print.  A review names files the reader has to go and find, so it
# is `quoted` here rather than one of the blanking answers -- a path with a
# space where a control character was is not a path on disk either.
from .terminal import quoted


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _resolve_repo_root(args):
    """Find the repository, starting from ``--project`` if one was named.

    Asking git about the current directory is the right default and was for a
    while the only option, which made `cd` a required step: CI checks out into
    one directory and runs from another, a pre-commit wrapper runs from
    wherever the editor launched it, and an agent driving several checkouts had
    to shell out through `cd` for each one.

    ``--project`` is the word the rest of the family already uses for "that
    directory over there", so it is the word here too.  A directory somebody
    named and got wrong is worth stopping over by name — the alternative is
    git's own message about the *current* directory, which sends people looking
    at the wrong path entirely.
    """
    start = getattr(args, "project", None) or "."
    if not os.path.isdir(start):
        raise GitError("no such directory: {}".format(start))
    return find_repo_root(start)


# ---------------------------------------------------------------------------
# Output: text
# ---------------------------------------------------------------------------

_SEV_PAD = {"HIGH": "HIGH ", "MED": "MED  ", "LOW": "LOW  "}




def _fmt_finding(f):
    loc = f"{quoted(f.file)}:{f.line}" if f.line else quoted(f.file)
    return f"  {_SEV_PAD.get(f.severity, f.severity)}  {loc}  {quoted(f.reason)}"


def _nothing_reviewed_line(since_ref, staged_only):
    """What to say when the diff was empty, instead of calling it clean.

    `clean` means the changes were examined and none were flagged.  With no
    changes nothing was examined, and saying `clean: 0 file(s) changed` put
    this tool's verdict word on a review that never happened.  The two ways it
    happens are both mundane — a pre-commit hook that runs before `git add`,
    and CI pointed at a `--since` ref that is not the one the author meant —
    and both are silently, permanently green.

    So name the ref, since a ref that isn't what you think is the whole of the
    second failure, and say plainly that there was nothing to look at.
    """
    if staged_only:
        return "no changes: nothing is staged, so nothing was reviewed"
    return ("no changes against {}, so nothing was reviewed".format(since_ref))


def _unread_changes(changes):
    """The changed files whose contents never arrived, one entry per path.

    Deduplicated on the path because a rename produces two entries for what is
    one file on disk, and naming it twice would read as two problems.
    """
    seen = {}
    for c in changes:
        if c.unread and c.path not in seen:
            seen[c.path] = c.unread
    return sorted(seen.items())


def _unread_lines(unread):
    """The block that names files nothing could be read from.

    Always names them.  There are rarely more than one or two, and the fix is a
    chmod on a specific path or a line in `.agentdiff/ignore` — a count on its
    own is not something anybody can act on.
    """
    if not unread:
        return []
    n = len(unread)
    head = "{} changed file(s) could not be read, so {} not reviewed".format(
        n, "it was" if n == 1 else "they were")
    return [head] + ["  {}  ({})".format(quoted(p), quoted(why))
                     for p, why in unread]


def _print_review(findings, changes, strict, out=None,
                  since_ref="HEAD", staged_only=False):
    """Print human-readable review output. Returns the appropriate exit code."""
    if out is None:
        out = sys.stdout

    n_files = len(set(c.path for c in changes))
    unread = _unread_changes(changes)
    n_reviewed = n_files - len(unread)
    gating = gating_findings(findings, strict=strict)

    if not changes:
        # Still 0, deliberately: an empty diff is an ordinary state of a
        # repository and every pre-commit hook in the world runs this.  What
        # changes is the word, so a green hook is readable.  See the README on
        # why stillworks' equivalent does exit 2 and this one does not.
        print(_nothing_reviewed_line(since_ref, staged_only), file=out)
        return 0

    if not findings:
        if unread:
            # Not `clean`, and not exit 0.  Nothing was flagged, but something
            # was never looked at, and exit 0 is what `agentdiff review && git
            # commit` acts on — the README's own reasoning about interrupted
            # runs, applied one file at a time.
            print("{} of {} changed file(s) reviewed, nothing flagged".format(
                n_reviewed, n_files), file=out)
            for line in _unread_lines(unread):
                print(line, file=out)
            return 1
        print(f"clean: {n_files} file(s) changed, nothing flagged", file=out)
        return 0

    # Group by severity and print most severe first
    by_sev = {s: [f for f in findings if f.severity == s] for s in SEVERITY}
    for sev in SEVERITY:
        group = by_sev[sev]
        if group:
            print(f"\n{sev} ({len(group)})", file=out)
            for f in group:
                print(_fmt_finding(f), file=out)

    counts = {s: len(by_sev[s]) for s in SEVERITY if by_sev[s]}
    count_str = ", ".join(f"{v} {k}" for k, v in counts.items())
    total = len(findings)

    if gating:
        print(f"\n{total} finding(s): {count_str} — review before merge", file=out)
    else:
        print(f"\n{total} finding(s): {count_str} — LOW only, pass --strict to gate on LOW", file=out)

    if unread:
        # After the findings, because the findings are what was asked for.  It
        # still turns a LOW-only run into a gating one: the file nobody could
        # open is the one place a HIGH could be hiding.
        for line in _unread_lines(unread):
            print(line, file=out)
        return 1
    return 1 if gating else 0


# ---------------------------------------------------------------------------
# Output: JSON
# ---------------------------------------------------------------------------

def _print_review_json(findings, changes, strict):
    gating = gating_findings(findings, strict=strict)
    unread = _unread_changes(changes)
    data = {
        "findings": [
            {
                "severity": f.severity,
                "file": f.file,
                "line": f.line,
                "reason": f.reason,
                "rule": f.rule,
            }
            for f in findings
        ],
        "files_changed": len(set(c.path for c in changes)),
        # How many files this run actually looked at.  `clean` says only that
        # nothing was flagged, which is also true of a review that examined
        # nothing, and `clean` is the field a CI script reads to decide whether
        # to merge.  A script can now tell the two apart.
        "reviewed": len(set(c.path for c in changes)) - len(unread),
        # `clean` is false while any changed file went unread, because a
        # verdict on contents nobody saw is not a verdict.
        "clean": len(findings) == 0 and not unread,
        "unread": [{"file": p, "reason": why} for p, why in unread],
        "gate_triggered": len(gating) > 0,
        "counts": {s: sum(1 for f in findings if f.severity == s) for s in SEVERITY},
    }
    print(json.dumps(data, indent=2))
    return 1 if (gating or unread) else 0


# ---------------------------------------------------------------------------
# Output: markdown report
# ---------------------------------------------------------------------------

def _write_report(findings, changes, since_ref, report_path):
    n_files = len(set(c.path for c in changes))
    unread = _unread_changes(changes)
    by_sev = {s: [f for f in findings if f.severity == s] for s in SEVERITY}

    lines = [
        "# agentdiff report",
        "",
        f"**ref:** `{since_ref}`  ",
        f"**files changed:** {n_files}  ",
        f"**total findings:** {len(findings)}",
        "",
    ]

    for sev in SEVERITY:
        group = by_sev[sev]
        if not group:
            continue
        lines += [f"## {sev} ({len(group)})", ""]
        for f in group:
            loc = f"{quoted(f.file)}:{f.line}" if f.line else quoted(f.file)
            lines.append(f"- **{loc}** — {quoted(f.reason)}")
        lines.append("")

    if not changes:
        # Same distinction as the review line: this report is the evidence
        # somebody keeps, and "nothing flagged" reads as a review that passed.
        lines += ["_No changes to review — nothing was examined._", ""]
    elif not findings and not unread:
        lines += ["_Nothing flagged._", ""]

    if unread:
        # The report outlives the terminal it was made in, so the gap in it has
        # to be written down next to the findings rather than only shouted once.
        lines += ["## Not reviewed ({})".format(len(unread)), ""]
        lines += ["- **{}** — could not be read ({})".format(quoted(p_), quoted(w))
                  for p_, w in unread]
        lines.append("")

    with open(report_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _json_error(message):
    """Print a JSON error document to stdout and return 2."""
    print(json.dumps({
        "error": message,
        "findings": [],
        "files_changed": 0,
        "clean": False,
        "gate_triggered": False,
        "counts": {"HIGH": 0, "MED": 0, "LOW": 0},
    }))
    return 2


def cmd_review(args):
    """agentdiff review — analyse working-tree changes."""
    use_json = getattr(args, "json", False)
    try:
        repo_root = _resolve_repo_root(args)
    except GitError as e:
        if use_json:
            return _json_error(str(e))
        print(f"error: {e}", file=sys.stderr)
        return 2

    since_ref = args.since or "HEAD"
    scope_globs = list(args.scope) if args.scope else _scope.read(repo_root)
    ignore_patterns = _scope.read_ignore(repo_root)

    try:
        changes = get_changes(
            repo_root,
            since_ref=since_ref,
            staged_only=getattr(args, "staged_only", False),
        )
    except GitError as e:
        if use_json:
            return _json_error(str(e))
        print(f"error: {e}", file=sys.stderr)
        return 2

    findings = run_rules(changes, scope_globs=scope_globs, ignore_patterns=ignore_patterns)

    if getattr(args, "report", None):
        try:
            _write_report(findings, changes, since_ref, args.report)
        except OSError as e:
            # The report is the evidence.  Printing the review as if it had been
            # written leaves somebody looking for a file that is not there.
            msg = "could not write report to {}: {}".format(args.report, e)
            if use_json:
                return _json_error(msg)
            print("error: {}".format(msg), file=sys.stderr)
            return 2

    if use_json:
        return _print_review_json(findings, changes, args.strict)
    return _print_review(findings, changes, args.strict, since_ref=since_ref,
                         staged_only=getattr(args, "staged_only", False))


def cmd_scope(args):
    """agentdiff scope GLOB... — persist intended scope to .agentdiff/scope."""
    try:
        repo_root = _resolve_repo_root(args)
    except GitError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    try:
        stored = _scope.write(repo_root, args.globs)
    except _scope.ScopeError as e:
        # Which globs may be stored is the format's business, and the format
        # says why.  This command's job is the exit code and the `error:`.
        print(f"error: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"error: could not save scope to {_scope.path(repo_root)}: {e}",
              file=sys.stderr)
        return 2
    # What is printed is what went into the file, not what was typed.  Today
    # those are the same bytes even where the two are different strings -- on a
    # machine with no locale the argument arrives as surrogates, and `shell`
    # has already set stdout to write surrogates back out as the bytes they
    # came from, so `args.globs` here would look identical.  It is still the
    # wrong source: the sentence says what was saved, so it has to come from
    # the save.  The day `write` normalises something the terminal can see, a
    # confirmation that quotes the argument back is one that cannot notice.
    print(f"scope saved: {', '.join(stored)}")
    print(f"  stored in {_scope.path(repo_root)}")
    return 0


def cmd_rules(args):
    """agentdiff rules — print every rule and what it flags."""
    print("Rules run by 'agentdiff review':\n")
    for sev, name, doc in RULE_DOCS:
        print(f"  {sev:<4}  {name}")
        # Wrap doc text at ~76 chars
        words = doc.split()
        line = "        "
        for word in words:
            if line.strip() and len(line) + len(word) + 1 > 80:
                print(line)
                line = "        " + word
            else:
                line += (" " if line.strip() else "") + word
        if line.strip():
            print(line)
        print()
    print("Note: LOW findings only affect the exit code under --strict.")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser():
    p = argparse.ArgumentParser(
        prog="agentdiff",
        description="See what the agent actually changed — before you merge.",
    )
    p.add_argument("--version", action="version", version=f"agentdiff {__version__}")
    p.add_argument("--project", default=".", metavar="DIR",
                   help="project directory (default: current directory)")

    # Accepted on either side of the subcommand.  SUPPRESS matters: without it
    # the subparser's own default would overwrite a --project given before the
    # subcommand with None, and the flag would silently do nothing there.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", default=argparse.SUPPRESS, metavar="DIR",
                        help="project directory (default: current directory)")

    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    # review
    rev = sub.add_parser("review", parents=[common],
                         help="analyse working-tree changes against HEAD (or --since REF)")
    rev.add_argument(
        "--since", metavar="GIT_REF",
        help="compare against this ref instead of HEAD",
    )
    rev.add_argument(
        "--scope", metavar="GLOB", action="append",
        help="intended scope glob (repeatable); overrides .agentdiff/scope",
    )
    rev.add_argument("--json", action="store_true", help="machine-readable JSON output")
    rev.add_argument("--report", metavar="FILE", help="write markdown evidence document to FILE")
    rev.add_argument(
        "--strict", action="store_true",
        help="LOW findings also trigger exit 1",
    )
    rev.add_argument(
        "--staged-only", "--pre-commit",
        action="store_true", dest="staged_only",
        help="only staged changes (for use as a pre-commit hook)",
    )

    # scope
    sc = sub.add_parser("scope", parents=[common],
                        help="persist intended scope globs to .agentdiff/scope")
    sc.add_argument("globs", nargs="+", metavar="GLOB")

    # rules
    sub.add_parser("rules", parents=[common],
                   help="print every rule and what it flags")

    return p



def main(argv=None):
    """Entry point.  ``argv`` defaults to the real command line.

    Taking it as an argument is what lets the tests drive the whole CLI in
    process, rather than only the pieces underneath it.

    What comes back is the code the process should exit with.  Reviewing a
    large repository takes a moment, and interrupting a command that is taking
    longer than you expected is ordinary; answering it with a traceback reads
    as a crash and sends people looking for a bug they caused on purpose.
    Answering an interrupted review with 130 also keeps `agentdiff review &&
    git commit` from committing on a review nobody finished.

    A closed pipe matters as much.  `agentdiff review | head` is an ordinary
    way to skim a large review, and unhandled it ended in a traceback and exit
    1 — which is this tool's code for *the gate triggered*.  A review that was
    cut off found nothing and cleared nothing, so it must not answer with
    either of the two codes anyone is testing for.

    Both of those are `shell.run_as_a_command`, which is where the mechanism
    lives and where the codes are named.  What is here is why this tool in
    particular cannot afford to get them wrong.
    """
    return run_as_a_command(_run, argv)



def _run(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "review":
        sys.exit(cmd_review(args))
    elif args.command == "scope":
        sys.exit(cmd_scope(args))
    elif args.command == "rules":
        sys.exit(cmd_rules(args))
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
