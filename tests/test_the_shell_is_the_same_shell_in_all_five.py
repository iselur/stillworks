"""Five copies of one file, and the only thing making them one file.

`shell.py` is the part of a command line that is the same in every command
line: reconfigure the streams if the locale claimed ASCII, flush before
leaving, turn ctrl-c into 130, turn a closed pipe into 141.  Ninety lines, and
before it existed each of the five packages had its own.

They had drifted, in the quiet way copies do.  Three returned their exit code
and two raised `SystemExit` with it, so `main()` meant two different things
depending on which tool you imported.  Two had `as_typed` and three did not.
The same comment had been reworded three separate ways.  Nobody decided any of
that; it is just what four copies do when nothing is watching them.

It has to stay copied.  Nothing in this family imports anything outside its own
package -- that is the promise `pip install stillworks` makes, and
`test_every_import_is_stdlib_or_the_packages_own` enforces it -- so a shared
module is not on offer.  What is on offer is a copy that cannot drift, which is
this file.

Two things get checked, because pinning the bytes is not enough on its own:

  * the five `shell.py` are byte-identical, so a fix made in one is a fix
    everywhere or it is a failing test; and
  * every `main` actually goes through `run_as_a_command`, because the cheapest
    way to undo all of this is to leave the file sitting there unread.

The source repositories are covered by the same two facts arriving from the
other side: `test_the_vendored_packages_match_their_source_repos` pins each
vendored copy to the repository it came from, so five identical copies here
means five identical originals there.  Each repository also carries
`tests/test_the_shell_around_a_command.py`, which is what says the behaviour is
right; this file only says there is one of it.
"""

from __future__ import annotations

import ast
import hashlib
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from tests.test_family_claims import PACKAGES

# The name every command's entry point is expected to hand its work to.
THE_ENTRY_POINT = "run_as_a_command"


def _shell(package):
    return os.path.join(_ROOT, package, "shell.py")


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


class TestEveryPackageCarriesIt(unittest.TestCase):

    def test_there_are_five_of_them(self):
        # Read from pyproject, so a sixth package added to the wheel is asked
        # for its copy on the day it arrives.
        missing = [p for p in PACKAGES if not os.path.exists(_shell(p))]
        self.assertEqual(missing, [],
                         "packages with no shell.py: {}".format(missing))
        self.assertEqual(len(PACKAGES), 5)


class TestTheyAreOneFile(unittest.TestCase):

    def test_byte_for_byte(self):
        digests = {}
        for package in PACKAGES:
            digests.setdefault(
                hashlib.sha256(_read(_shell(package))).hexdigest(), []
            ).append(package)
        self.assertEqual(
            len(digests), 1,
            "shell.py has drifted into {} versions:\n  {}\nA fix belongs in "
            "all five: rsync the source repo you fixed, then the others.\n"
            .format(len(digests),
                    "\n  ".join(sorted(", ".join(sorted(group))
                                       for group in digests.values()))))

    def test_the_two_numbers_are_the_shells_numbers(self):
        # Named here as well as there, because these are the one part of the
        # interface a caller is allowed to compare against.
        import importlib
        for package in PACKAGES:
            with self.subTest(package):
                shell = importlib.import_module(package + ".shell")
                self.assertEqual(shell.INTERRUPTED, 130)
                self.assertEqual(shell.PIPE_CLOSED, 141)


class TestEveryMainGoesThroughIt(unittest.TestCase):
    """The file existing is not the same as the file being used."""

    def _main(self, package):
        path = os.path.join(_ROOT, package, "cli.py")
        tree = ast.parse(_read(path).decode("utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                return node
        self.fail("{}/cli.py has no main()".format(package))

    def test_main_hands_the_work_over(self):
        for package in PACKAGES:
            with self.subTest(package):
                called = {node.func.id
                          for node in ast.walk(self._main(package))
                          if isinstance(node, ast.Call)
                          and isinstance(node.func, ast.Name)}
                self.assertIn(THE_ENTRY_POINT, called,
                              "{}'s main() went back to rolling its own"
                              .format(package))

    def test_main_returns_the_code_rather_than_raising_it(self):
        # The protocol the console-script wrapper depends on: `sys.exit(main())`
        # only spells the right code if `main` returns one.  A `raise
        # SystemExit` here would still work from a terminal and silently stop
        # working for anyone calling `main` from Python.
        for package in PACKAGES:
            with self.subTest(package):
                main = self._main(package)
                raised = [node for node in ast.walk(main)
                          if isinstance(node, ast.Raise)]
                self.assertEqual(raised, [],
                                 "{}'s main() raises instead of returning"
                                 .format(package))
                self.assertTrue(
                    any(isinstance(node, ast.Return) and node.value is not None
                        for node in ast.walk(main)),
                    "{}'s main() returns nothing, so the process exits 0"
                    .format(package))


if __name__ == "__main__":
    unittest.main()
