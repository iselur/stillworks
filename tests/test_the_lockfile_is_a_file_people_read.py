"""Three places where the tool writes something a person is going to look at.

`lock.json` is committed and diffed. A `check` failure is read by whoever the
build woke up. Neither is machine-only output, and all three of the mutants
here survive because nothing in the suite reads the bytes — the tests go
through `load_lock`/`save_lock` as a round trip, and a round trip is happy with
any field order at all, and happy with an error message that says nothing.

  * the field order the lockfile is written in — a diff opens on it
  * the sentence `load_lock` produces when the file will not open
  * where the cap on recorded command output fires

The last one is the one that decides whether a report is trustworthy: output
cut short is announced, output that fits is not, and a cap that fires one
character early puts a truncation notice on a complete result.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import OrderedDict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from stillworks.core import (  # noqa: E402
    LockfileError, _scrub_output, load_lock, lock_path,
)

# The cap on recorded command output. It is a bare literal in the source, so
# this is a copy of it rather than an import — which is itself the reason the
# boundary is worth a test: nothing else in the tree names this number.
_MAX_OUTPUT = 20000

MODULE = '''\
def add(a: int, b: int) -> int:
    return a + b
'''


class TestTheCapOnRecordedOutputSaysWhenItFired(unittest.TestCase):

    def test_ordinary_output_comes_through_untouched(self):
        # Vacuity guard: a scrubber that truncated everything, or nothing,
        # would pass one of the two tests below on its own.
        self.assertEqual(
            _scrub_output("3 passed in 0.4s\n"), "3 passed in 0.4s\n",
            "a short, ordinary line of output did not survive being scrubbed")

    def test_output_of_exactly_the_cap_is_left_whole(self):
        text = "a" * _MAX_OUTPUT
        said = _scrub_output(text)
        self.assertEqual(
            said, text,
            "output of exactly {} characters — the length the cap allows — "
            "came back {} long".format(_MAX_OUTPUT, len(said)))
        self.assertNotIn(
            "truncated", said,
            "complete output was marked as truncated, which is the version of "
            "this failure a reader cannot tell from the real thing")

    def test_output_one_character_over_the_cap_says_so(self):
        said = _scrub_output("a" * (_MAX_OUTPUT + 1))
        self.assertIn(
            "truncated by stillworks", said,
            "output past the cap was cut without saying so, so a reader "
            "compares two results and never learns they are both partial")
        self.assertTrue(
            said.startswith("a" * _MAX_OUTPUT),
            "the cap kept {} characters and it allows {}".format(
                len(said.split("\n")[0]), _MAX_OUTPUT))


class TestTheMessageWhenTheLockfileWillNotOpen(unittest.TestCase):
    """`load_lock`'s whole reason for catching OSError is the sentence.

    Letting it through would already be safe — the caller catches it. What it
    would not be is legible: `[Errno 21] Is a directory: '/long/path/…'` names
    the path a second time inside a bracketed code, in the middle of a line
    that already names it.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_lockfile_that_is_a_directory_is_explained_in_words(self):
        path = lock_path(self.tmp)
        os.makedirs(path)
        with self.assertRaises(LockfileError) as caught:
            load_lock(self.tmp)
        message = str(caught.exception)
        self.assertIn(
            "directory", message.lower(),
            "the message does not say what was wrong with it: {!r}".format(
                message))
        self.assertNotIn(
            "[Errno", message,
            "the message hands the reader an errno code instead of the "
            "reason: {!r}".format(message))
        self.assertEqual(
            message.count(path), 1,
            "the path is named {} times in one sentence: {!r}".format(
                message.count(path), message))


class TestTheLockfileIsWrittenInTheOrderItWasDesignedIn(unittest.TestCase):
    """A lockfile is read as a diff, and a diff opens at the top of the file.

    The keys are written in the order they were chosen in — what format this
    is, when it was made, what it is about — and then the records. Sorting
    them alphabetically is not wrong so much as it is nobody's choice: it puts
    `created` first, `records` in the middle, and `schema` seventh, so the one
    field that tells a reader how to read the rest is buried under the one
    field that is thousands of lines long.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        with open(os.path.join(self.tmp, "calc.py"), "w", encoding="utf-8") as fh:
            fh.write(MODULE)
        proc = subprocess.run(
            [sys.executable, "-m", "stillworks", "lock", "calc.py", "--fuzz", "3"],
            cwd=self.tmp, capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=_ROOT))
        self.assertEqual(proc.returncode, 0,
                         "could not lock the fixture: " + proc.stdout + proc.stderr)
        with open(lock_path(self.tmp), encoding="utf-8") as fh:
            self.keys = list(json.load(fh, object_pairs_hook=OrderedDict))

    def test_the_format_version_is_the_first_thing_in_the_file(self):
        self.assertEqual(
            self.keys[0], "schema",
            "the lockfile opens on {!r}; a reader — or a future stillworks — "
            "needs to know what format this is before anything else in it "
            "means something".format(self.keys[0]))

    def test_the_records_come_after_everything_that_describes_them(self):
        # `records` is the whole file by volume. Anything written after it is
        # off the bottom of the first screen of the diff.
        for header in ("created", "tool", "module", "seed"):
            self.assertLess(
                self.keys.index(header), self.keys.index("records"),
                "{!r} is written after the records, so it is thousands of "
                "lines down: {}".format(header, self.keys))


if __name__ == "__main__":
    unittest.main()
