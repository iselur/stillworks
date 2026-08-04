"""`stillworks report -o` pointed somewhere it cannot write.

The report is evidence for a reviewer, so it gets written to a path somebody
typed: into a directory that turns out to be read-only, onto a mounted volume,
into a folder that was going to be created by an earlier step and wasn't, or
with a typo in it.  All of those are ordinary.

Unhandled, the `open()` came back as a `PermissionError` traceback and **exit
1** — which in this tool is the code for BEHAVIOR CHANGED.  So a `report` that
wrote nothing at all reported the same thing as a check that caught a real
regression, and `stillworks report -o out/EVIDENCE.md && gh pr comment` had a
verdict to act on that nobody had produced.

The right answer is the one the tool already gives for every other thing it
cannot do: name the file, say what went wrong, exit 2.  `agentlog --md` into
the same read-only directory has always done exactly that; this brings the two
into line.
"""

import io
import os
import shutil
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stillworks import cli  # noqa: E402

_MODULE = """
def add(a: int, b: int) -> int:
    return a + b


def scale(x: float, k: float) -> float:
    return x * k
"""


class TestAnUnwritableOutputPath(unittest.TestCase):

    def setUp(self):
        self.project = tempfile.mkdtemp(prefix="stillworks_report_out_")
        self.addCleanup(self._cleanup)
        with open(os.path.join(self.project, "mod.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(_MODULE)
        code, text = self._run(["--project", self.project, "lock", "mod.py",
                                "--fuzz", "6"])
        self.assertEqual(code, 0, text)
        self.locked = os.path.join(self.project, "out")
        os.makedirs(self.locked)
        os.chmod(self.locked, stat.S_IRUSR | stat.S_IXUSR)

    def _cleanup(self):
        try:
            os.chmod(self.locked, stat.S_IRWXU)
        except OSError:
            pass
        shutil.rmtree(self.project, ignore_errors=True)

    def _run(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(args)
        return code, out.getvalue() + err.getvalue()

    def test_a_read_only_directory_is_a_message_not_a_traceback(self):
        _, text = self._run(["--project", self.project, "report",
                             "-o", os.path.join(self.locked, "EVIDENCE.md")])
        self.assertNotIn("Traceback", text, text)
        self.assertNotIn("PermissionError", text, text)

    def test_it_says_which_file_and_why(self):
        target = os.path.join(self.locked, "EVIDENCE.md")
        _, text = self._run(["--project", self.project, "report", "-o", target])
        self.assertIn(target, text, text)
        self.assertIn("Permission denied", text, text)

    def test_a_report_that_wrote_nothing_is_not_BEHAVIOR_CHANGED(self):
        # 1 is the merge gate closing.  Nothing was compared here at all.
        code, text = self._run(["--project", self.project, "report",
                                "-o", os.path.join(self.locked, "EVIDENCE.md")])
        self.assertEqual(code, 2, text)
        self.assertNotEqual(code, 1, "an unwritable path read as a regression")

    def test_a_directory_that_is_not_there_says_so(self):
        code, text = self._run(
            ["--project", self.project, "report",
             "-o", os.path.join(self.project, "nope", "deeper", "EV.md")])
        self.assertEqual(code, 2, text)
        self.assertNotIn("Traceback", text, text)
        self.assertIn("No such file or directory", text, text)

    def test_the_output_path_being_a_directory_says_so(self):
        code, text = self._run(["--project", self.project, "report",
                                "-o", self.project])
        self.assertEqual(code, 2, text)
        self.assertNotIn("Traceback", text, text)

    def test_a_writable_path_still_gets_the_report(self):
        # The regression guard: none of the above may cost the working case.
        target = os.path.join(self.project, "EVIDENCE.md")
        code, text = self._run(["--project", self.project, "report",
                                "-o", target])
        self.assertEqual(code, 0, text)
        self.assertTrue(os.path.exists(target), "no report written")
        with open(target, encoding="utf-8") as fh:
            self.assertTrue(fh.read().strip(), "report is empty")
        self.assertIn(target, text, "did not say where it went")

    def test_printing_to_stdout_still_works(self):
        code, text = self._run(["--project", self.project, "report"])
        self.assertEqual(code, 0, text)
        self.assertTrue(text.strip(), "nothing printed")


if __name__ == "__main__":
    unittest.main()
