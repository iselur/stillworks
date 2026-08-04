"""A `.stillworks` directory that will not take a write.

`check` compares behavior and then saves a receipt of the run, so `accept`
and `report` can see what the last check found.  The saving is bookkeeping.
The comparison is the verdict.

Unhandled, a read-only `.stillworks` — a checked-out CI tree, a container
volume mounted read-only, a directory owned by another user — made the receipt
raise a `PermissionError` traceback that escaped `main()` and came back as
**exit 1**.  1 is this tool's word for BEHAVIOR CHANGED.  So a project whose
behavior had not moved at all failed the gate, and `stillworks check && deploy`
stopped on a regression nobody had.

Writing the receipt must not be able to decide the verdict.  If it fails, say
so on stderr and answer with what the comparison actually found.

`accept` is the other half and goes the other way: it *only* exists to write
the lockfile.  If that write fails nothing was accepted, so it must not report
success — it names the file and exits 2.
"""

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stillworks import cli, core  # noqa: E402

_SAME = """
def add(a: int, b: int) -> int:
    return a + b
"""

_MOVED = """
def add(a: int, b: int) -> int:
    return a + b + 1
"""


class _ReadOnlyLockDir(unittest.TestCase):

    def setUp(self):
        self.project = tempfile.mkdtemp(prefix="stillworks_ro_lockdir_")
        self.addCleanup(self._cleanup)
        self._write(_SAME)
        code, text = self._run(["--project", self.project, "lock", "mod.py",
                                "--fuzz", "6"])
        self.assertEqual(code, 0, text)
        self.lockdir = os.path.join(self.project, core.LOCK_DIR)

    def _cleanup(self):
        try:
            os.chmod(self.lockdir, stat.S_IRWXU)
        except OSError:
            pass
        shutil.rmtree(self.project, ignore_errors=True)

    def _write(self, source):
        with open(os.path.join(self.project, "mod.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(source)

    def _seal(self):
        os.chmod(self.lockdir, stat.S_IRUSR | stat.S_IXUSR)

    def _run(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(args)
        return code, out.getvalue() + err.getvalue()


class TestCheckKeepsItsVerdict(_ReadOnlyLockDir):

    def test_unchanged_behavior_still_passes(self):
        # The whole point.  Nothing moved, so nothing may block the merge.
        self._seal()
        code, text = self._run(["--project", self.project, "check"])
        self.assertNotIn("Traceback", text, text)
        self.assertEqual(code, 0, text)
        self.assertIn("STILL WORKS", text, text)

    def test_it_is_not_silent_about_the_unsaved_receipt(self):
        self._seal()
        _, text = self._run(["--project", self.project, "check"])
        self.assertIn("last-check.json", text, text)
        self.assertIn("Permission denied", text, text)

    def test_a_real_change_is_still_caught(self):
        # The regression guard on the other side: the gate must still close.
        self._write(_MOVED)
        self._seal()
        code, text = self._run(["--project", self.project, "check"])
        self.assertNotIn("Traceback", text, text)
        self.assertEqual(code, 1, text)
        self.assertIn("BEHAVIOR CHANGED", text, text)

    def test_json_stays_parseable(self):
        self._seal()
        code, text = self._run(["--project", self.project, "check", "--json"])
        self.assertEqual(code, 0, text)
        start = text.index("{")
        payload = json.loads(text[start:text.rindex("}") + 1])
        self.assertTrue(payload["ok"], payload)

    def test_a_writable_directory_still_leaves_the_receipt(self):
        code, text = self._run(["--project", self.project, "check"])
        self.assertEqual(code, 0, text)
        self.assertTrue(
            os.path.exists(os.path.join(self.lockdir, core.LAST_CHECK_FILE)),
            "the receipt stopped being written at all")
        self.assertNotIn("Permission denied", text, text)


class TestAcceptWillNotClaimItSaved(_ReadOnlyLockDir):

    def test_it_says_what_it_could_not_write(self):
        self._write(_MOVED)
        self._seal()
        code, text = self._run(["--project", self.project, "accept", "--all"])
        self.assertNotIn("Traceback", text, text)
        self.assertEqual(code, 2, text)
        self.assertIn("Permission denied", text, text)

    def test_it_does_not_print_accepted(self):
        # "accepted new behavior: ..." with nothing written is the lie.
        self._write(_MOVED)
        self._seal()
        _, text = self._run(["--project", self.project, "accept", "--all"])
        self.assertNotIn("accepted new behavior", text, text)

    def test_the_baseline_is_untouched(self):
        self._write(_MOVED)
        self._seal()
        self._run(["--project", self.project, "accept", "--all"])
        os.chmod(self.lockdir, stat.S_IRWXU)
        code, text = self._run(["--project", self.project, "check"])
        self.assertEqual(code, 1, "the change was quietly blessed anyway")
        self.assertIn("BEHAVIOR CHANGED", text, text)

    def test_a_writable_directory_still_accepts(self):
        self._write(_MOVED)
        code, text = self._run(["--project", self.project, "accept", "--all"])
        self.assertEqual(code, 0, text)
        self.assertIn("accepted new behavior", text, text)
        code, text = self._run(["--project", self.project, "check"])
        self.assertEqual(code, 0, text)


class TestTheOtherReadersDoNotTraceback(_ReadOnlyLockDir):

    def test_status(self):
        self._seal()
        code, text = self._run(["--project", self.project, "status"])
        self.assertNotIn("Traceback", text, text)
        self.assertEqual(code, 0, text)

    def test_report(self):
        self._seal()
        code, text = self._run(["--project", self.project, "report"])
        self.assertNotIn("Traceback", text, text)
        self.assertEqual(code, 0, text)


if __name__ == "__main__":
    unittest.main()
