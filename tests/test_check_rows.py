"""A lockfile field wrote its own status rows.

`stillworks check` prints one row per record that is not OK:

    GONE     add#1  (add)

The id and the target on that row come out of `.stillworks/lock.json`, which
is committed and shared and is exactly the file an agent is free to edit.
Neither was flattened, so a target containing a newline printed as several
rows, and the extra ones look precisely like real ones:

    GONE     add#1  (add)
    OK       mul#1  (mul)
    OK       mul#2  (mul)
             function no longer exists (or is no longer public)

Two of those `OK` lines were never verified against anything.  They were a
string in a file, printed by the one command whose whole job is to say whether
behavior is still intact.  The trailing summary still counts truthfully, but
nothing on the forged rows says so.

Length went the same way: a 200,000-character target printed as a
200,000-character row, while `was:`/`now:` right beneath it have capped at 400
since the beginning.

So: one record is one row, and a row has a bound.  The Markdown report is the
same story — a newline in an id breaks out of its backtick span and starts a
new bullet under `### Differences`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from stillworks.cli import _one_row

MODULE = '''\
def add(a: int, b: int) -> int:
    return a + b


def mul(a: int, b: int) -> int:
    return a * b
'''


def _run(project, *argv):
    return subprocess.run(
        [sys.executable, "-m", "stillworks", *argv],
        cwd=project, capture_output=True, text=True,
        env=dict(os.environ, PYTHONPATH=_ROOT))


class TestOneRecordIsOneRow(unittest.TestCase):
    """The helper, on the values that reach it."""

    def test_ordinary_text_is_untouched(self):
        self.assertEqual(_one_row("add#1"), "add#1")
        self.assertEqual(_one_row("src/pay.py::total"), "src/pay.py::total")

    def test_a_newline_cannot_start_a_second_row(self):
        got = _one_row("add)\nOK       mul#1  (mul")
        self.assertEqual(len(got.splitlines()), 1, repr(got))

    def test_every_way_a_terminal_breaks_a_row(self):
        # A terminal moves to a new row on more than \n.
        for ch in ("\n", "\r", "\v", "\f", " ", " "):
            got = _one_row("a" + ch + "b")
            self.assertEqual(len(got.splitlines()), 1,
                             "{!r} still broke the row: {!r}".format(ch, got))

    def test_the_seam_stays_visible(self):
        # Deleting would fuse two values into one word that is not either.
        self.assertEqual(_one_row("a\nb"), "a b")

    def test_a_long_value_is_cut_and_says_so(self):
        got = _one_row("x" * 200_000)
        self.assertLess(len(got), 500, len(got))
        self.assertIn("more characters", got)

    def test_a_value_at_the_cap_is_left_alone(self):
        self.assertEqual(_one_row("x" * 400), "x" * 400)


class TestThroughTheRealGate(unittest.TestCase):
    """A real lockfile, edited the way an agent could edit it."""

    def setUp(self):
        self.project = tempfile.mkdtemp(prefix="sw-rows-")
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        with open(os.path.join(self.project, "m.py"), "w") as fh:
            fh.write(MODULE)
        p = _run(self.project, "lock", "m.py", "--fuzz", "6")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.lock = os.path.join(self.project, ".stillworks", "lock.json")

    def _forge(self, field, value):
        with open(self.lock) as fh:
            lock = json.load(fh)
        for rec in lock["records"]:
            if rec["kind"] == "call":
                rec[field] = value
                break
        with open(self.lock, "w") as fh:
            json.dump(lock, fh)

    def _break_the_code(self):
        with open(os.path.join(self.project, "m.py"), "w") as fh:
            fh.write(MODULE.replace("return a + b", "return a - b"))

    def _status_rows(self, out):
        """Rows that look like a verdict — a status word at column 0."""
        words = ("OK", "CHANGED", "GONE", "SKIP", "BROKEN")
        return [l for l in out.splitlines()
                if l.split(" ")[0] in words and not l.startswith(" ")]

    def test_a_forged_target_cannot_write_status_rows(self):
        self._forge("target", "add)\nOK       mul#9  (mul)\nOK       mul#8  (mul")
        self._break_the_code()
        p = _run(self.project, "check")
        self.assertNotIn("mul#9", self._status_rows(p.stdout),
                         "a lockfile field wrote its own rows:\n" + p.stdout)
        for row in self._status_rows(p.stdout):
            self.assertFalse(row.startswith("OK "),
                             "check never prints OK rows:\n" + p.stdout)

    def test_a_forged_id_cannot_write_status_rows(self):
        self._forge("id", "add#1  (add)\nOK       mul#9  (mul")
        self._break_the_code()
        p = _run(self.project, "check")
        for row in self._status_rows(p.stdout):
            self.assertFalse(row.startswith("OK "),
                             "a forged id wrote a row:\n" + p.stdout)

    def test_the_rows_agree_with_the_summary(self):
        self._forge("target", "add)\nOK       mul#9  (mul)\nGONE     mul#8  (mul")
        self._break_the_code()
        p = _run(self.project, "check")
        summary = [l for l in p.stdout.splitlines()
                   if l.startswith(("STILL WORKS:", "BEHAVIOR CHANGED:"))]
        self.assertEqual(len(summary), 1, p.stdout)
        counted = 0
        for part in summary[0].split("—")[1].split(","):
            n, status = part.split()
            if status != "OK":
                counted += int(n)
        self.assertEqual(len(self._status_rows(p.stdout)), counted,
                         "the rows and the count disagree:\n" + p.stdout)

    def test_a_huge_target_cannot_fill_the_screen(self):
        self._forge("target", "x" * 200_000)
        self._break_the_code()
        p = _run(self.project, "check")
        longest = max(len(l) for l in p.stdout.splitlines())
        self.assertLess(longest, 600, "longest row was {}".format(longest))

    def test_the_json_view_still_carries_the_whole_value(self):
        # Machine-readable output is not a page; nothing is flattened there.
        self._forge("target", "add)\nOK       mul#9  (mul")
        self._break_the_code()
        p = _run(self.project, "check", "--json")
        data = json.loads(p.stdout)
        targets = [e["target"] for e in data["results"]]
        self.assertIn("add)\nOK       mul#9  (mul", targets, targets)

    def test_the_exit_code_is_untouched(self):
        self._forge("target", "add)\nOK       mul#9  (mul")
        self._break_the_code()
        self.assertEqual(_run(self.project, "check").returncode, 1)

    def test_ordinary_output_is_unchanged(self):
        self._break_the_code()
        p = _run(self.project, "check")
        rows = self._status_rows(p.stdout)
        self.assertTrue(rows, p.stdout)
        for row in rows:
            self.assertRegex(row, r"^CHANGED  \S+  \(add\)$", p.stdout)

    def test_a_clean_check_still_passes(self):
        p = _run(self.project, "check")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("STILL WORKS", p.stdout)


class TestTheReportIsRowsToo(unittest.TestCase):
    """`stillworks report` is Markdown, and a newline starts a new bullet."""

    def setUp(self):
        self.project = tempfile.mkdtemp(prefix="sw-report-rows-")
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        with open(os.path.join(self.project, "m.py"), "w") as fh:
            fh.write(MODULE)
        _run(self.project, "lock", "m.py", "--fuzz", "6")
        lock_path = os.path.join(self.project, ".stillworks", "lock.json")
        with open(lock_path) as fh:
            lock = json.load(fh)
        for rec in lock["records"]:
            if rec["kind"] == "call":
                rec["id"] = "add#1`\n- **OK** `mul#9"
                break
        with open(lock_path, "w") as fh:
            json.dump(lock, fh)
        with open(os.path.join(self.project, "m.py"), "w") as fh:
            fh.write(MODULE.replace("return a + b", "return a - b"))
        _run(self.project, "check")

    def test_a_forged_id_does_not_become_a_second_bullet(self):
        p = _run(self.project, "report")
        bullets = [l for l in p.stdout.splitlines() if l.startswith("- **")]
        for b in bullets:
            self.assertFalse(b.startswith("- **OK**"),
                             "the report grew a finding:\n" + p.stdout)

    def test_the_difference_is_still_reported(self):
        p = _run(self.project, "report")
        self.assertIn("### Differences", p.stdout)
        self.assertIn("CHANGED", p.stdout)


if __name__ == "__main__":
    unittest.main()
