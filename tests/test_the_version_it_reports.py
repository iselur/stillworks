"""`--version` has to say the version that was installed.

There are two copies of this number in every repo in the family: the one in
`pyproject.toml`, which is what the wheel is named and what PyPI serves, and
`__version__` in the package, which is what `--version` prints.  A release bumps
the first one.  Nothing bumps the second one, and nothing noticed:

    unedit     pyproject 0.1.4   __init__ 0.1.3
    agentdiff  pyproject 0.1.3   __init__ 0.1.2

Both of those are published.  `pip install unedit==0.1.4` installs 0.1.4 and
then `unedit --version` says 0.1.3, so a bug report against it names a release
that does not contain the bug, and the maintainer looks in the wrong tree.

The existing version test in each repo compares `--version` against
`__version__` — the same number on both sides of the assertion, so it passes
however far the two copies have drifted.  This one reads `pyproject.toml` off
disk instead, which is the only side that is true by construction: it is what
the build actually ships under.

`tomllib` is 3.11 and this package supports 3.9, so the version is read with a
regex rather than a parser.  A key called `version` at the top level of
`[project]` is not a shape that needs parsing.
"""

from __future__ import annotations

import io
import os
import re
import sys
import unittest
from contextlib import redirect_stdout

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from stillworks import __version__
from stillworks import cli

PYPROJECT = os.path.join(_ROOT, "pyproject.toml")


def packaged_version() -> str:
    """The version the wheel is built with, read from pyproject.toml."""
    with io.open(PYPROJECT, encoding="utf-8") as handle:
        text = handle.read()
    # Only inside [project]: [build-system] has a `requires` line and a future
    # table could have a `version` of its own, and picking up the wrong one
    # would make this test pass for a reason that has nothing to do with the
    # release.
    section = text.split("[project]", 1)[1].split("\n[", 1)[0]
    found = re.search(r'^version\s*=\s*"([^"]+)"', section, re.MULTILINE)
    return found.group(1) if found else ""


class TestTheVersionItReports(unittest.TestCase):
    def test_pyproject_has_a_version_at_all(self):
        # If this fails the other two tests are comparing against "", which
        # would fail for the wrong reason and send somebody looking at
        # __init__.py when the problem is here.
        self.assertRegex(packaged_version(), r"^\d+\.\d+")

    def test_the_package_agrees_with_what_pip_installs(self):
        self.assertEqual(
            __version__, packaged_version(),
            "pyproject.toml and stillworks/__init__.py disagree; --version would "
            "name a release that is not the one installed")

    def test_the_flag_prints_the_installed_version(self):
        # End to end, because the two constants agreeing is not the promise —
        # the promise is that the string a user pastes into a bug report is the
        # release they have.
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--version"])
        # argparse ends `--version` by raising `SystemExit(0)` from inside the
        # standard library.  `main` catches it and hands the number back, so
        # this is the same 0 the shell is given -- see shell.run_as_a_command.
        self.assertEqual(code, 0)
        self.assertIn(packaged_version(), out.getvalue())


if __name__ == "__main__":
    unittest.main()
