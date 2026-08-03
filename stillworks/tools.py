"""`stillworks tools` — what of the family is installed, and what is missing.

The four tools ship as four independent distributions on purpose, so nothing
here may import a sibling: that would turn an optional extra into a real
dependency the first time someone forgot a try/except.  Detection is done from
the outside instead — find the command on PATH, ask it for its version — which
is also what the user would do by hand.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import List, Optional, Tuple

from . import __version__

# command, PyPI distribution, one-line pitch.  The distribution name is only
# shown when something is missing, because two of the four had to take a
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
]

_TIMEOUT_S = 5


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
    path = shutil.which(command)
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
    text = (out.stdout or b"").decode("utf-8", "replace").strip()
    if not text:
        return "?"
    # Conventional output is "agentlog 0.2.0"; take the last token that starts
    # with a digit so a prefixed program name does not end up in the column.
    for token in reversed(text.split()):
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
        lines.append("  all four installed")
    elif len(missing) == len(rows) - 1:
        # Everything but stillworks itself: the extra is the whole answer.
        lines.append("  missing: {}".format(", ".join(c for c, _ in missing)))
        lines.append("  install all four:  pip install 'stillworks[all]'")
    else:
        lines.append("  missing: {}".format(", ".join(c for c, _ in missing)))
        lines.append("  install:  pip install {}".format(
            " ".join(d for _, d in missing)))
        lines.append("  or all four:  pip install 'stillworks[all]'")
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
