"""The gate went green having checked nothing.

`lock` replays every record once and flags the ones that do not reproduce, so
that a function reading the clock or the RNG does not fail the gate forever.
Flagged records are excluded from `check`.  That is right.

What was wrong is what happens when *every* record is flagged:

    $ stillworks lock m.py --fuzz 4
    locked 8 records (8 calls, 0 commands) -> .stillworks/lock.json
      8 nondeterministic (flagged, excluded from check)

    $ stillworks check
    SKIP     roll#1  (roll)
    ...
    STILL WORKS: 8 records — 8 SKIP
    $ echo $?
    0

`STILL WORKS` is this tool's word for "behavior is intact", and it was said
about eight records none of which were run.  Rewriting the module so one
function raises and the other returns a string changed nothing: still SKIP,
still STILL WORKS, still exit 0.  In CI that is a gate that is permanently
green and permanently empty, and the one line that said so scrolled past at
lock time, days earlier.

The project already took this position once — `lock` refuses to write an
empty lockfile, exit 2, rather than leave a check that passes by having
nothing to check.  A lockfile whose records are all excluded is the same
thing arriving by a different road, so it gets the same answer: say nothing
was verified, and exit 2, which is this tool's word for "this did not work"
and is not the 1 that means behavior changed.

A check that verified even one record is a real check and keeps its verdict.
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

NONDET = '''\
import random


def roll(a: int) -> int:
    return random.randint(0, 10_000_000) + a


def toss(a: int) -> int:
    return random.randint(0, 10_000_000) + a
'''

MIXED = '''\
import random


def roll(a: int) -> int:
    return random.randint(0, 10_000_000) + a


def double(a: int) -> int:
    return a * 2
'''

STEADY = '''\
def double(a: int) -> int:
    return a * 2
'''


class Case(unittest.TestCase):
    def setUp(self):
        self.project = tempfile.mkdtemp(prefix="sw-vacuous-")
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def write(self, text):
        with open(os.path.join(self.project, "m.py"), "w") as fh:
            fh.write(text)

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "stillworks", *argv],
            cwd=self.project, capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=_ROOT))

    def lock(self, source):
        self.write(source)
        p = self.run_cli("lock", "m.py", "--fuzz", "4")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p


class TestEveryRecordExcluded(Case):
    """Nothing was compared, so there is no verdict to give."""

    def setUp(self):
        super().setUp()
        self.lock(NONDET)

    def test_it_does_not_say_the_behavior_is_intact(self):
        p = self.run_cli("check")
        self.assertNotIn("STILL WORKS", p.stdout,
                         "verified nothing and said behavior is intact:\n" + p.stdout)

    def test_it_says_what_actually_happened(self):
        p = self.run_cli("check")
        self.assertIn("NOTHING VERIFIED", p.stdout, p.stdout)

    def test_the_gate_does_not_go_green(self):
        p = self.run_cli("check")
        self.assertEqual(p.returncode, 2,
                         "exit {} — CI reads 0 as safe to merge:\n{}".format(
                             p.returncode, p.stdout + p.stderr))

    def test_it_stays_shut_after_the_code_is_rewritten(self):
        # The demonstration from the docstring: nothing about the module is
        # what it was, and the old answer was STILL WORKS / 0.
        self.write("def roll(a: int) -> int:\n    raise RuntimeError('broken')\n")
        p = self.run_cli("check")
        self.assertNotIn("STILL WORKS", p.stdout, p.stdout)
        self.assertNotEqual(p.returncode, 0, p.stdout)

    def test_it_says_how_to_get_out_of_it(self):
        p = self.run_cli("check")
        said = p.stdout + p.stderr
        self.assertIn("--cmd", said,
                      "nothing pointed at a way to capture these:\n" + said)

    def test_the_json_view_says_so_too(self):
        p = self.run_cli("check", "--json")
        data = json.loads(p.stdout)
        self.assertEqual(data["verified"], 0, data.get("counts"))
        self.assertFalse(data["ok"], "ok is the field a script reads")

    def test_the_report_does_not_call_it_a_pass(self):
        self.run_cli("check")
        p = self.run_cli("report")
        self.assertNotIn("PASS", p.stdout, p.stdout)


class TestARealCheckIsUnaffected(Case):
    """One verified record is a check.  Nothing here may change."""

    def test_a_steady_module_still_passes(self):
        self.lock(STEADY)
        p = self.run_cli("check")
        self.assertIn("STILL WORKS", p.stdout, p.stdout)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_one_verified_record_among_skips_is_still_a_check(self):
        self.lock(MIXED)
        p = self.run_cli("check")
        self.assertIn("STILL WORKS", p.stdout, p.stdout)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_a_real_regression_still_exits_one(self):
        self.lock(STEADY)
        self.write(STEADY.replace("a * 2", "a * 3"))
        p = self.run_cli("check")
        self.assertIn("BEHAVIOR CHANGED", p.stdout, p.stdout)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_the_json_view_counts_what_was_verified(self):
        self.lock(STEADY)
        p = self.run_cli("check", "--json")
        data = json.loads(p.stdout)
        self.assertEqual(data["verified"], data["counts"].get("OK", 0))
        self.assertTrue(data["ok"])

    def test_a_gone_function_is_a_finding_not_an_empty_check(self):
        # GONE is a verdict the tool reached, not a record it skipped.
        self.lock(STEADY)
        self.write("def other(a: int) -> int:\n    return a\n")
        p = self.run_cli("check")
        self.assertIn("BEHAVIOR CHANGED", p.stdout, p.stdout)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
