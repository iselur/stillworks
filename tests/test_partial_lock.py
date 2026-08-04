"""A baseline recorded from a run that died partway was presented as whole.

`stillworks lock m.py --run drive.py` records whatever the driver script
exercises.  If the driver stops early, the calls it made first are kept — that
is deliberate and worth keeping.  What was wrong is that nothing said it had
stopped early:

    $ cat drive.py
    import sys, m
    m.add(1, 2)
    sys.exit(1)          # the argparse error, the failed step, the test run

    $ stillworks lock m.py --run drive.py
    locked 1 records (1 calls, 0 commands) -> .stillworks/lock.json
    $ echo $?
    0

Empty stderr.  Byte-for-byte the output of a driver that ran to the end and
had one call in it.  The nine calls after the exit are simply not there.

A driver that *raised* did print a line — so this was an asymmetry rather than
a decision, and `sys.exit(nonzero)` is the ordinary way a script fails: a
`sys.exit(main())` entry point, an argparse error, a test runner.

And the line the raising case printed went to a terminal that is now gone.
The lockfile is committed and read for months afterwards:

    $ stillworks check
    STILL WORKS: 1 records — 1 OK
    $ echo $?
    0

Green, forever, on a baseline that was known-incomplete the moment it was
written.  This is the variant peculiar to stillworks: unlike the other tools
in the family, it writes a file that outlives the run, so saying it once is
not saying it.

So: both endings are reported, both are written into the lockfile, and
`check`, `status` and the report say the baseline came from a run that did
not finish.

`lock` still exits 0 and `check` still gives its verdict.  The records that
are there were really recorded and really replayed — that verdict is true, it
is only narrower than it was meant to be.  Making `check` fail instead would
turn one bad afternoon into a red gate nobody can turn green except by
re-locking, and the note in the file is the durable signal that the one-shot
exit code was never going to be.

A driver that exits 0, or falls off the end, is the ordinary case and is
silent.
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

MODULE = '''\
def add(a: int, b: int) -> int:
    return a + b


def shout(s: str) -> str:
    return s.upper()
'''

# Each driver records exactly one call and then ends in a different way, so
# the lockfiles they produce are identical apart from how the run stopped.
DRIVER_EXIT_1 = '''\
import sys
import m

m.add(1, 2)
sys.exit(1)
'''

DRIVER_RAISED = '''\
import m

m.add(1, 2)
raise ValueError("driver blew up before it reached shout")
'''

DRIVER_EXIT_0 = '''\
import sys
import m

m.add(1, 2)
sys.exit(0)
'''

DRIVER_FELL_OFF_THE_END = '''\
import m

m.add(1, 2)
'''


class Case(unittest.TestCase):
    def setUp(self):
        self.project = tempfile.mkdtemp(prefix="sw-partial-")
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        self.write("m.py", MODULE)

    def write(self, name, text):
        path = os.path.join(self.project, name)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "stillworks", *argv],
            cwd=self.project, capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=_ROOT))

    def lock_with(self, driver):
        self.write("drive.py", driver)
        p = self.run_cli("lock", "m.py", "--run", "drive.py")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def lockfile(self):
        with open(os.path.join(self.project, ".stillworks", "lock.json")) as fh:
            return json.load(fh)


class TestADriverThatExitedNonZero(Case):

    def setUp(self):
        super().setUp()
        self.locked = self.lock_with(DRIVER_EXIT_1)

    def test_lock_says_the_run_did_not_finish(self):
        said = self.locked.stdout + self.locked.stderr
        self.assertIn("did not finish", said.lower(),
                      "silent about a driver that died partway:\n" + said)

    def test_lock_names_the_exit_code(self):
        # Which exit code it was is the thread back to the failing step.
        said = self.locked.stdout + self.locked.stderr
        self.assertIn("exited 1", said, said)

    def test_the_calls_it_did_record_are_still_kept(self):
        # Half a baseline beats none: this is why the run is not just failed.
        self.assertEqual(len(self.lockfile()["records"]), 1, self.lockfile())

    def test_the_lockfile_records_that_it_did_not_finish(self):
        # The line on stderr is gone by tomorrow.  This file is committed.
        self.assertTrue(self.lockfile().get("partial"),
                        "nothing in the lockfile says the run died:\n"
                        + json.dumps(sorted(self.lockfile()), indent=1))

    def test_check_does_not_present_it_as_a_whole_baseline(self):
        p = self.run_cli("check")
        self.assertIn("did not finish", p.stdout.lower(),
                      "months of green on a baseline known to be partial:\n"
                      + p.stdout)

    def test_check_still_gives_its_verdict(self):
        # The one record that is there was really replayed and really passed.
        p = self.run_cli("check")
        self.assertIn("STILL WORKS", p.stdout, p.stdout)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_check_json_carries_it(self):
        p = self.run_cli("check", "--json")
        data = json.loads(p.stdout)
        self.assertTrue(data.get("partial"), data)

    def test_status_says_so(self):
        p = self.run_cli("status")
        self.assertIn("did not finish", p.stdout.lower(), p.stdout)

    def test_the_report_says_so(self):
        out = os.path.join(self.project, "r.md")
        self.run_cli("report", "-o", out)
        with open(out) as fh:
            body = fh.read()
        self.assertIn("did not finish", body.lower(), body)

    def test_it_is_told_apart_from_a_driver_that_finished(self):
        # The reproduction, both halves: these two produced identical output.
        partial = self.locked.stdout + self.locked.stderr
        whole = self.lock_with(DRIVER_FELL_OFF_THE_END)
        self.assertNotEqual(partial.strip(),
                            (whole.stdout + whole.stderr).strip(),
                            "a driver that died and one that finished said "
                            "exactly the same thing:\n" + partial)


class TestADriverThatRaised(Case):

    def setUp(self):
        super().setUp()
        self.locked = self.lock_with(DRIVER_RAISED)

    def test_lock_still_names_the_exception(self):
        # This half already worked; it is pinned so the two endings stay level.
        said = self.locked.stdout + self.locked.stderr
        self.assertIn("ValueError", said, said)

    def test_the_lockfile_records_it_too(self):
        partial = self.lockfile().get("partial") or ""
        self.assertIn("ValueError", partial,
                      "the warning went to a terminal and nowhere else: "
                      + repr(partial))

    def test_check_says_the_baseline_did_not_finish(self):
        p = self.run_cli("check")
        self.assertIn("did not finish", p.stdout.lower(), p.stdout)


class TestAnOrdinaryRecordingRunIsUnaffected(Case):

    def test_a_driver_that_falls_off_the_end_is_silent(self):
        p = self.lock_with(DRIVER_FELL_OFF_THE_END)
        self.assertNotIn("did not finish", (p.stdout + p.stderr).lower(),
                         p.stdout + p.stderr)

    def test_a_driver_that_exits_zero_is_silent(self):
        # `sys.exit(main())` with main returning 0 — success, spelled the long
        # way, and by far the most common shape of a driver script.
        p = self.lock_with(DRIVER_EXIT_0)
        self.assertNotIn("did not finish", (p.stdout + p.stderr).lower(),
                         p.stdout + p.stderr)

    def test_the_lockfile_is_not_marked(self):
        self.lock_with(DRIVER_EXIT_0)
        self.assertFalse(self.lockfile().get("partial"), self.lockfile())

    def test_check_says_nothing_about_it(self):
        self.lock_with(DRIVER_FELL_OFF_THE_END)
        p = self.run_cli("check")
        self.assertIn("STILL WORKS", p.stdout, p.stdout)
        self.assertNotIn("did not finish", p.stdout.lower(), p.stdout)

    def test_status_says_nothing_about_it(self):
        self.lock_with(DRIVER_FELL_OFF_THE_END)
        p = self.run_cli("status")
        self.assertNotIn("did not finish", p.stdout.lower(), p.stdout)

    def test_a_fuzz_lock_has_no_recording_run_to_report_on(self):
        p = self.run_cli("lock", "m.py", "--fuzz", "4")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(self.lockfile().get("partial"), self.lockfile())

    def test_an_older_lockfile_without_the_field_still_checks(self):
        # `partial` is new, and lockfiles are committed: one written by the
        # version before this must not start erroring or claiming anything.
        self.lock_with(DRIVER_FELL_OFF_THE_END)
        path = os.path.join(self.project, ".stillworks", "lock.json")
        with open(path) as fh:
            lock = json.load(fh)
        lock.pop("partial", None)
        with open(path, "w") as fh:
            json.dump(lock, fh)
        p = self.run_cli("check")
        self.assertIn("STILL WORKS", p.stdout, p.stdout + p.stderr)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
