"""`stillworks check | head` is a normal thing to do, and it used to be a crash.

A baseline of a few hundred records makes a long report, so it gets piped:
`| head -20` for the first few changes, `| less` and quit with `q`, `| grep -q
CHANGED` that stops the moment it has an answer.  Each closes the read end
while we are still writing.  The next write fails with EPIPE, Python raises
`BrokenPipeError`, and unhandled the interpreter prints

    Exception ignored in: <_io.TextIOWrapper name='<stdout>' ...>
    BrokenPipeError: [Errno 32] Broken pipe

over the output and exits 120 — or, when the error escapes `main()` rather than
the shutdown flush, a full traceback and exit **1**, which is this tool's code
for BEHAVIOR CHANGED.  That is the worst spelling available: `stillworks check
| head` on code that had not moved came back looking exactly like code that
had, and `stillworks check && deploy` stopped a deploy nothing was wrong with.

141 is 128 + SIGPIPE, the shell's own spelling of "the reader hung up", the
same way 130 spells ctrl-c.  A check that got cut off compared nothing, so it
must be neither 0 nor 1 nor 2.

The read end is closed before the command writes a byte, so none of this
depends on how much output there is or on the size of the pipe buffer.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_BEFORE = """
def add(a: int, b: int) -> int:
    return a + b


def scale(x: float, k: float) -> float:
    return x * k


def label(name: str, loud: bool) -> str:
    return name.upper() if loud else name
"""

_AFTER = _BEFORE.replace("return a + b", "return a + b + 1")


def _env():
    return dict(os.environ, PYTHONPATH=_ROOT)


def run_with_no_reader(args):
    """Run the CLI with a stdout pipe whose read end is already closed."""
    read_fd, write_fd = os.pipe()
    os.close(read_fd)                       # the reader went away
    proc = subprocess.Popen(
        [sys.executable, "-m", "stillworks"] + list(args),
        stdout=write_fd, stderr=subprocess.PIPE, cwd=_ROOT, env=_env())
    os.close(write_fd)
    _, err = proc.communicate(timeout=300)
    return proc.returncode, err.decode("utf-8", "replace")


def run_normally(args):
    proc = subprocess.Popen(
        [sys.executable, "-m", "stillworks"] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=_ROOT, env=_env())
    out, err = proc.communicate(timeout=300)
    return (proc.returncode,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"))


class TestTheReaderHungUp(unittest.TestCase):

    def setUp(self):
        self.project = tempfile.mkdtemp(prefix="stillworks_epipe_")
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        self.module = os.path.join(self.project, "mod.py")
        with open(self.module, "w", encoding="utf-8") as fh:
            fh.write(_BEFORE)
        code, out, err = run_normally(
            ["--project", self.project, "lock", "mod.py", "--fuzz", "12"])
        self.assertEqual(code, 0, out + err)
        # Move the behavior, so `check` has a real verdict of 1 to be confused
        # with a truncated run.
        with open(self.module, "w", encoding="utf-8") as fh:
            fh.write(_AFTER)

    def commands(self):
        p = ["--project", self.project]
        return [
            p + ["status"],
            p + ["check"],
            p + ["report"],
            ["tools"],
            ["--version"],
            ["--help"],
        ]

    def test_nothing_is_printed_about_a_broken_pipe(self):
        for args in self.commands():
            with self.subTest(args=args[-1:]):
                _, err = run_with_no_reader(args)
                self.assertNotIn("BrokenPipeError", err, err)
                self.assertNotIn("Exception ignored", err, err)

    def test_it_is_not_a_traceback(self):
        for args in self.commands():
            with self.subTest(args=args[-1:]):
                _, err = run_with_no_reader(args)
                self.assertNotIn("Traceback", err, err)

    def test_a_cut_off_check_is_neither_pass_nor_fail(self):
        # 0 is unchanged, 1 is changed, 2 is an unreadable lockfile.  A check
        # nobody read is none of those, and `check && deploy` had better agree.
        for args in self.commands():
            with self.subTest(args=args[-1:]):
                code, err = run_with_no_reader(args)
                self.assertEqual(code, 141,
                                 "{} -> {}\n{}".format(args[-1:], code, err))
                self.assertNotIn(code, (0, 1, 2))

    def test_help_and_version_are_covered_too(self):
        # argparse prints these and exits before any command body runs.
        for args in (["--version"], ["--help"]):
            with self.subTest(args=args):
                code, err = run_with_no_reader(args)
                self.assertEqual(code, 141, err)
                self.assertEqual(err, "", err)

    def test_the_gate_still_closes_when_anyone_is_reading(self):
        # The regression guard: the real verdict must survive all of the above.
        code, out, err = run_normally(["--project", self.project, "check"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("CHANGED", out, out)

    def test_an_unchanged_check_still_passes(self):
        with open(self.module, "w", encoding="utf-8") as fh:
            fh.write(_BEFORE)
        code, out, err = run_normally(["--project", self.project, "check"])
        self.assertEqual(code, 0, out + err)


if __name__ == "__main__":
    unittest.main()
