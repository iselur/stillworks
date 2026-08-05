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
import unicodedata

from . import __version__
from .git import find_repo_root, get_changes, GitError
from .rules import RULE_DOCS, SEVERITY, gating_findings, run_rules
from .shell import run_as_a_command


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _agentdiff_dir(repo_root):
    return os.path.join(repo_root, ".agentdiff")


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


def _load_file_lines(path):
    """Read a config file and return non-blank, non-comment lines.

    A config file that cannot be read is not a reason to refuse to review.  It
    is somebody's own directory: the file may be unreadable, a directory, or
    not text at all, and none of that says anything about the diff.
    """
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _load_scope(repo_root):
    return _load_file_lines(os.path.join(_agentdiff_dir(repo_root), "scope"))


def _load_ignore(repo_root):
    return _load_file_lines(os.path.join(_agentdiff_dir(repo_root), "ignore"))


# ---------------------------------------------------------------------------
# Output: text
# ---------------------------------------------------------------------------

_SEV_PAD = {"HIGH": "HIGH ", "MED": "MED  ", "LOW": "LOW  "}


_HIDDEN = ("Cc", "Cf", "Zl", "Zp")

_ESCAPES = {"\a": "\\a", "\b": "\\b", "\t": "\\t", "\n": "\\n",
            "\v": "\\v", "\f": "\\f", "\r": "\\r"}


def _escape(char):
    """One character that cannot be shown, written so it can be read."""
    if char in _ESCAPES:
        return _ESCAPES[char]
    n = ord(char)
    return "\\x{:02x}".format(n) if n < 0x100 else "\\u{:04x}".format(n)


def _safe(text):
    """A path that cannot do anything to the page it is printed on.

    Every line of a review exists to say which file to go and look at, and the
    path in it was put in the tree by whoever changed the tree.  An escape
    sequence there clears the screen or retitles the window as the review is
    read, and a right-to-left override makes the line name a different file
    from the one that changed — which is the one failure a review cannot
    afford.  A raw newline is worse still: `--name-status -z` hands paths over
    exactly as they are on disk, so a directory named with one would end the
    row and start another that looks just like a finding.

    These used to be deleted, which is safe and silent and leaves the line
    naming a file that is not there:

        HIGH   depsHIGH   forged.py   x/requirements.txt:1  dependency changed

    `deps` and `HIGH   forged.py   x` were two components of a real path.  On
    screen they are one word, there is no `depsHIGH` on disk, and nothing says
    anything was dropped — so the gate says review this file before merge and
    the file cannot be found.

    So they are escaped rather than dropped, and the path is quoted when any of
    them is, which is what git itself does and what `git status` shows.  The
    quoting is what makes the escaping mean something: without it a file named
    `a\\nb` and a file named `a<newline>b` print identically.  Escapes match
    git's for the ones anybody meets (`\\n`, `\\t`, `\\r`); rarer characters
    get `\\xNN` or `\\uNNNN` rather than git's octal, because this is a Python
    tool and a reader is likelier to know what those mean.

    A backslash or a double quote in an otherwise ordinary name gets the same
    treatment, for the same reason and again exactly as git does: a file named
    `a\\nb` on disk has to look different from a file named `a<newline>b`, and
    it is the quoting that tells them apart.

    Printable paths with neither are returned untouched — `café/naïve.py` is
    perfectly readable and quoting it would be noise.

    The JSON view is left alone: it is consumed by another program, which wants
    the path that is really on disk, and JSON's own escaping already makes it
    safe to print.
    """
    text = str(text)
    if text.isprintable() and '"' not in text and "\\" not in text:
        return text                     # the overwhelmingly common case
    if not any(c in '"\\' or unicodedata.category(c) in _HIDDEN for c in text):
        # Unprintable for some other reason — an unassigned or private-use
        # codepoint.  It cannot break the row or drive the terminal, so it is
        # left as it is, as it always was.
        return text
    out = []
    for c in text:
        if unicodedata.category(c) in _HIDDEN:
            out.append(_escape(c))
        elif c in '"\\':
            out.append("\\" + c)
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def _fmt_finding(f):
    loc = f"{_safe(f.file)}:{f.line}" if f.line else _safe(f.file)
    return f"  {_SEV_PAD.get(f.severity, f.severity)}  {loc}  {_safe(f.reason)}"


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
    return [head] + ["  {}  ({})".format(_safe(p), _safe(why))
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
            loc = f"{_safe(f.file)}:{f.line}" if f.line else _safe(f.file)
            lines.append(f"- **{loc}** — {_safe(f.reason)}")
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
        lines += ["- **{}** — could not be read ({})".format(_safe(p_), _safe(w))
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
    scope_globs = list(args.scope) if args.scope else _load_scope(repo_root)
    ignore_patterns = _load_ignore(repo_root)

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

    for g in args.globs:
        # The scope file is one glob per line, so a glob containing a newline
        # would be stored as two — and the second one is usually "**", which
        # quietly widens the scope every later review is checked against.
        if not g.strip():
            print("error: an empty scope glob matches nothing — give a pattern",
                  file=sys.stderr)
            return 2
        if "\n" in g or "\r" in g:
            print(f"error: a scope glob cannot contain a newline: {g!r}", file=sys.stderr)
            return 2

    d = _agentdiff_dir(repo_root)
    scope_path = os.path.join(d, "scope")
    try:
        os.makedirs(d, exist_ok=True)
        with open(scope_path, "w") as f:
            for g in args.globs:
                f.write(g + "\n")
    except OSError as e:
        print(f"error: could not save scope to {scope_path}: {e}", file=sys.stderr)
        return 2
    print(f"scope saved: {', '.join(args.globs)}")
    print(f"  stored in {scope_path}")
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
