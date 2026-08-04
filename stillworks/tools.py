"""`stillworks tools` — what of the family is installed, and what is missing.

The tools ship as independent distributions on purpose, so nothing here may
import a sibling: that would turn an optional extra into a real
dependency the first time someone forgot a try/except.  Detection is done from
the outside instead — find the command on PATH, ask it for its version — which
is also what the user would do by hand.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import List, Optional, Tuple

from . import __version__

# command, PyPI distribution, one-line pitch.  The distribution name is only
# shown when something is missing, because two of them had to take a
# suffixed name and the whole point of the `[all]` extra is that nobody has to
# learn them.
FAMILY: List[Tuple[str, str, str]] = [
    ("stillworks", "stillworks",
     "record what your code does now, catch when it changes"),
    ("unedit", "unedit",
     "a safety net for letting an agent loose on your files"),
    ("agentdiff", "agentdiff-cli",
     "see what the agent actually changed, before you merge"),
    ("agentlog", "agentlog-tool",
     "what did your coding agent actually do today?"),
    ("agentwatch", "agentwatch",
     "tail what your agent is doing, right now"),
]

# Spelled rather than printed as a digit, because the sentence reads better and
# because it is the one place the count appears: writing "five" into the strings
# below is how you end up shipping "all four installed" from a family of five.
_COUNTS = {2: "both", 3: "all three", 4: "all four", 5: "all five",
           6: "all six", 7: "all seven", 8: "all eight"}


def _count(n: int) -> str:
    return _COUNTS.get(n, "all {}".format(n))


_TIMEOUT_S = 5


def _neighbour(command: str) -> Optional[str]:
    """The sibling installed beside this stillworks, if there is one.

    `pip install 'stillworks[all]'` puts the whole family in one environment, so
    the copy next to our own interpreter is the copy that install produced.
    PATH may well hold an older one from somewhere else — a pipx install, a
    system package — and reporting that tells somebody who just installed the
    family that they still have the version they replaced.
    """
    bindir = os.path.dirname(os.path.abspath(sys.executable or ""))
    if not bindir:
        return None
    candidate = os.path.join(bindir, command)
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return None


def _version_of(command: str) -> Optional[str]:
    """The installed version of a sibling command, or None if it is absent.

    A command that exists but cannot answer `--version` still counts as
    installed — an old build or a wrapper script is not the user's problem to
    debug from here — so it reports an unknown version rather than absence.
    """
    if command == "stillworks":
        # We are stillworks.  Reporting the running version is both cheaper and
        # more honest than shelling out to whichever copy PATH happens to find.
        return __version__
    path = _neighbour(command) or shutil.which(command)
    if not path:
        return None
    try:
        out = subprocess.run(
            [path, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return "?"
    # `--version` exits 0.  Anything else means the command did not answer the
    # question — most often a sibling from before the flag existed, where
    # argparse prints a usage error and exits 2.  That error text carries
    # numbers of its own ("expected 1 argument"), and scraping one of those
    # used to put a confident `1` in the version column next to four correct
    # ones.  Nothing in the output said it was wrong.  Unknown is the honest
    # answer, and the command is still installed either way.
    if out.returncode != 0:
        return "?"
    text = (out.stdout or b"").decode("utf-8", "replace").strip()
    if not text:
        return "?"
    return _scrape_version(text)


# Punctuation a version can be wrapped in when a program dresses up its output:
# "agentdiff [0.1.2]", "agentdiff version 0.1.2.".  None of it is the number.
_LEFT = "([{<'\"vV"
_RIGHT = ")]}>'\",.;:"


def _scrape_version(text: str) -> str:
    """The tool's own version out of a line like "agentdiff 0.1.2"."""
    # Forwards, not backwards.  The first number after the program name is the
    # program's version; later ones belong to something else.  A `--version`
    # string of the common form "agentdiff 0.1.2 (python 3.11.4)" read
    # backwards reports the interpreter's version as the tool's, which looks
    # exactly like a correct answer.
    for token in text.split():
        token = token.lstrip(_LEFT).rstrip(_RIGHT)
        if token[:1].isdigit():
            return token
    return "?"


def render(rows: List[Tuple[str, str, str, Optional[str]]]) -> str:
    """The report, given already-detected (command, dist, pitch, version) rows."""
    lines = []
    cmd_w = max(len(r[0]) for r in rows)
    ver_w = max(len(r[3] or "—") for r in rows)
    for command, _dist, pitch, version in rows:
        lines.append("  {}  {}  {}".format(
            command.ljust(cmd_w), (version or "—").ljust(ver_w), pitch))

    missing = [(c, d) for c, d, _p, v in rows if v is None]
    lines.append("")
    if not missing:
        lines.append("  {} installed".format(_count(len(rows))))
    elif len(missing) == len(rows) - 1:
        # Everything but stillworks itself: the extra is the whole answer.
        lines.append("  missing: {}".format(", ".join(c for c, _ in missing)))
        lines.append("  install {}:  pip install 'stillworks[all]'"
                     .format(_count(len(rows))))
    else:
        lines.append("  missing: {}".format(", ".join(c for c, _ in missing)))
        lines.append("  install:  pip install {}".format(
            " ".join(d for _, d in missing)))
        lines.append("  or {}:  pip install 'stillworks[all]'"
                     .format(_count(len(rows))))
    return "\n".join(lines)


def cmd_tools(args) -> int:
    """Always exits 0: this reports a situation, it does not judge one."""
    rows = [(c, d, p, _version_of(c)) for c, d, p in FAMILY]
    if getattr(args, "json", False):
        import json
        print(json.dumps({
            "tools": [
                {"command": c, "distribution": d, "description": p,
                 "version": v, "installed": v is not None}
                for c, d, p, v in rows
            ],
        }, indent=2))
        return 0
    print(render(rows))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cmd_tools(None))
