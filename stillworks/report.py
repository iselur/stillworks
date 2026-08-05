"""Markdown evidence report: what was locked, what was verified, what changed."""

from __future__ import annotations

import json
import os
import platform
import sys

from . import core
from .terminal import row as _one_row


def build(project_dir):
    lock = core.load_lock(project_dir)
    if lock is None:
        return None
    last = _load_last_check(project_dir)

    lines = []
    lines.append("# stillworks evidence report")
    lines.append("")
    target = ""
    if lock.get("module"):
        target = lock["module"].get("path") or lock["module"].get("module") or ""
    if target:
        lines.append("**Target:** `{}`".format(_one_row(target)))
    lines.append("**Baseline locked:** {}".format(_one_row(lock["created"])))
    records = lock["records"]
    n_calls = sum(1 for r in records if r["kind"] == "call")
    n_cmds = sum(1 for r in records if r["kind"] == "cmd")
    n_nondet = sum(1 for r in records if r.get("nondet"))
    lines.append("**Records:** {} total — {} function calls, {} commands"
                 .format(len(records), n_calls, n_cmds))
    if n_nondet:
        lines.append("**Flagged nondeterministic (excluded from gating):** {}"
                     .format(n_nondet))
    if lock.get("partial"):
        # Next to the record count, which is the number a reader would
        # otherwise take as the size of what was covered.
        lines.append("**Baseline is partial:** {} — whatever it would have "
                     "exercised afterwards is not covered by any verdict "
                     "below.".format(_one_row(lock["partial"])))
    lines.append("")

    if last:
        lines.append("## Last verification ({})".format(last.get("checked", "?")))
        lines.append("")
        counts = last.get("counts", {})
        # Derived from the counts rather than read from the receipt, so that a
        # receipt written by an older version — one that had no `verified`
        # field and would happily call an all-SKIP run a PASS — is described
        # honestly by this report.  SKIP is the only status that is not a
        # verdict; see the note in core.check.
        verified = sum(v for k, v in counts.items() if k != "SKIP")
        if verified == 0:
            verdict = ("NOT A RESULT — every record was excluded, nothing was "
                       "compared")
        elif last.get("ok"):
            verdict = "PASS — behavior unchanged"
        else:
            verdict = "FAIL — behavior differs from baseline"
        lines.append("**Verdict:** {}".format(verdict))
        lines.append("")
        lines.append("| status | count | meaning |")
        lines.append("|---|---|---|")
        meanings = {
            "OK": "reproduced exactly",
            "CHANGED": "same input, different output",
            "GONE": "recorded function no longer exists",
            "SKIP": "nondeterministic at lock time, not gated",
            "BROKEN": "record could not be replayed",
        }
        for status in ("OK", "CHANGED", "GONE", "SKIP", "BROKEN"):
            if counts.get(status):
                lines.append("| {} | {} | {} |".format(
                    status, counts[status], meanings[status]))
        lines.append("")
        diffs = [e for e in last.get("results", [])
                 if e["status"] in ("CHANGED", "GONE", "BROKEN")]
        if diffs:
            lines.append("### Differences")
            lines.append("")
            # Same rule as the `check` rows, for the same reason: these values
            # come out of the lockfile, and in Markdown a newline inside a
            # backtick span ends the span and starts a new bullet — one that
            # reads exactly like a difference this report found.
            for e in diffs:
                lines.append("- **{}** `{}`".format(
                    _one_row(e["status"]), _one_row(e["id"])))
                if e["status"] == "CHANGED" and e.get("kind") == "call":
                    lines.append("  - args: `{}`".format(_one_row(e.get("args", ""))))
                    lines.append("  - was: `{}`".format(_one_row(e["was"].get("repr"))))
                    lines.append("  - now: `{}`".format(_one_row(e["now"].get("repr"))))
                elif e["status"] == "CHANGED":
                    was, now = e.get("was", {}), e.get("now", {})
                    if was.get("exit") != now.get("exit"):
                        lines.append("  - exit: {} -> {}".format(
                            was.get("exit"), now.get("exit")))
                    for stream in ("stdout", "stderr"):
                        if was.get(stream) != now.get(stream):
                            lines.append("  - {} changed".format(stream))
                elif e.get("note"):
                    lines.append("  - {}".format(_one_row(e["note"])))
            lines.append("")
    else:
        lines.append("_No check has been run yet — run `stillworks check`._")
        lines.append("")

    if lock.get("history"):
        lines.append("## Accepted changes")
        lines.append("")
        for h in lock["history"]:
            lines.append("- {} — `{}`: {}".format(
                _one_row(h.get("when", "?")), _one_row(h.get("id", "?")),
                _one_row(h.get("action", ""))))
            if "was" in h and "now" in h:
                lines.append("  - was: `{}`".format(_one_row(h["was"].get("repr"))))
                lines.append("  - now: `{}`".format(_one_row(h["now"].get("repr"))))
        lines.append("")

    lines.append("## Environment")
    lines.append("")
    lines.append("- Python {}".format(sys.version.split()[0]))
    lines.append("- {} {}".format(platform.system(), platform.release()))
    lines.append("- stillworks lockfile schema v{}".format(lock.get("schema")))
    lines.append("")
    lines.append("_Generated by [stillworks](https://github.com/iselur/stillworks) — "
                 "records are executed, not estimated: every verdict above "
                 "comes from running the code._")
    return "\n".join(lines) + "\n"


def _load_last_check(project_dir):
    path = os.path.join(project_dir, core.LOCK_DIR, core.LAST_CHECK_FILE)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
