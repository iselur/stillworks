"""`lock.json` is committed, so it is a file other people and agents edit.

That is the point of it — `check` works in a reviewer's checkout because the
baseline travelled with the code. It is also what makes the file's shape an
input rather than an invariant: it gets merged, hand-edited, truncated by a
crashed write, and rewritten by an agent that was asked to "update the tests".

`load_lock` already refuses anything that is not a lockfile, and the suite
already covers a damaged one and a partial one. What none of that reaches is
the layer above: a file that *is* a lockfile, whose records are fine, and whose
`module` entry has lost a key. A sweep put four mutants there and all four
lived — one of them turning a missing module block into an `AttributeError` out
of `check`, which is the one command a person runs when they already suspect
something is wrong.

The other two are in the determinism guard. A record whose function cannot be
found, or whose arguments will not decode, is marked nondeterministic so that
`check` skips it rather than replaying something it does not have. Set those
marks to False and the record is replayed anyway — and a replay that cannot
happen is not a failure, it is silence.

Which is the thread through all of it: every status here is a way of saying "I
could not check this". None of them may quietly become "this is fine".
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

from stillworks.core import (  # noqa: E402
    check, encode_args, lock_path, load_module, mark_nondeterministic,
)

MODULE = '''\
def add(a: int, b: int) -> int:
    return a + b
'''


def lock_a_project(tmp, source=MODULE, name="calc.py"):
    """A project with a real lockfile, made the way a person makes one."""
    with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
        fh.write(source)
    proc = subprocess.run(
        [sys.executable, "-m", "stillworks", "lock", name, "--fuzz", "4"],
        cwd=tmp, capture_output=True, text=True,
        env=dict(os.environ, PYTHONPATH=_ROOT))
    assert proc.returncode == 0, "could not lock the fixture: {}{}".format(
        proc.stdout, proc.stderr)
    return proc


def read_lock(tmp):
    with open(lock_path(tmp), encoding="utf-8") as fh:
        return json.load(fh)


def write_lock(tmp, lock):
    with open(lock_path(tmp), "w", encoding="utf-8") as fh:
        json.dump(lock, fh, indent=1, default=str)


def statuses(result):
    return sorted({row["status"] for row in result["results"]})


class TestCheckSurvivesALockfileThatLostSomething(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        lock_a_project(self.tmp)

    def test_the_fixture_checks_clean_to_start_with(self):
        # Vacuity guard. Every test below asserts that a damaged lockfile does
        # not degrade into something worse than it should be; if the intact
        # one were already failing they would all pass while proving nothing.
        result = check(self.tmp)
        self.assertEqual(
            statuses(result), ["OK"],
            "the untouched fixture does not check clean, so nothing below "
            "means anything: {}".format(result.get("results")))

    def test_a_module_entry_with_only_a_path_still_replays(self):
        # `module` holds two ways of naming the same file — the path it was
        # locked from and the dotted name it imports as — and `check` uses
        # whichever it has. A lockfile carrying one of them is not damaged;
        # it is a lockfile from a version that wrote one, or one an editor
        # tidied. Either way there is nothing here that stops a replay.
        lock = read_lock(self.tmp)
        lock["module"] = {"path": lock["module"]["path"]}
        write_lock(self.tmp, lock)
        self.assertEqual(
            statuses(check(self.tmp)), ["OK"],
            "a lockfile naming its module by path alone could not replay, "
            "though the path is right there and the file has not moved")

    def test_a_module_entry_with_only_a_dotted_name_still_replays(self):
        lock = read_lock(self.tmp)
        lock["module"] = {"module": lock["module"]["module"]}
        write_lock(self.tmp, lock)
        self.assertEqual(
            statuses(check(self.tmp)), ["OK"],
            "a lockfile naming its module by its importable name alone could "
            "not replay")

    def test_call_records_with_no_module_at_all_are_reported_not_raised(self):
        # The mutant this exists for reads the module block without checking
        # there is one, so `check` comes apart with an AttributeError on a
        # lockfile that says `"module": null`. `check` is documented never to
        # raise for a behavior difference, and this is not even that — it is a
        # file that lost a field. It has to come back as a status.
        lock = read_lock(self.tmp)
        lock["module"] = None
        write_lock(self.tmp, lock)
        try:
            result = check(self.tmp)
        except Exception as exc:  # noqa: BLE001 - the failure is the raise
            self.fail(
                "check() raised {}: {} on a lockfile with no module entry, "
                "instead of reporting the records it could not "
                "replay".format(type(exc).__name__, exc))
        self.assertNotIn(
            "OK", statuses(result),
            "records were reported OK against a module the lockfile does not "
            "name: {}".format(result.get("results")))
        self.assertFalse(
            result.get("ok"),
            "a run that could not load the module at all still said the "
            "behavior was intact")


class TestARecordThatCannotBeReplayedIsMarkedNotAssumed(unittest.TestCase):
    """`mark_nondeterministic` runs at lock time and decides what `check` may
    later trust. Two of its branches are not about determinism at all: they are
    the cases where the record cannot be replayed even once."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        path = os.path.join(self.tmp, "calc.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(MODULE)
        self.mod, _ = load_module(path, self.tmp)

    def record(self, target="add", args=(2, 3)):
        return {"kind": "call", "target": target, "id": target + "#1",
                "args_b64": encode_args(list(args), {}),
                "args_repr": repr((args, {})),
                "out": {"kind": "value", "canon": 5, "repr": "5"}}

    def test_a_record_that_can_be_replayed_is_left_alone(self):
        # Vacuity guard: a marker stuck on True would pass both tests below.
        rec = self.record()
        mark_nondeterministic([rec], self.mod)
        self.assertFalse(
            rec["nondet"],
            "a plain, replayable record was flagged nondeterministic, which "
            "would exclude it from every check from here on")

    def test_a_record_for_a_function_that_is_not_there_is_flagged(self):
        rec = self.record(target="subtract")
        mark_nondeterministic([rec], self.mod)
        self.assertTrue(
            rec["nondet"],
            "a record naming a function the module does not have was left "
            "unflagged, so it goes on to be replayed against nothing")

    def test_a_record_whose_arguments_will_not_decode_is_flagged(self):
        rec = self.record()
        rec["args_b64"] = "bm90IGEgcGlja2xl"      # "not a pickle"
        mark_nondeterministic([rec], self.mod)
        self.assertTrue(
            rec["nondet"],
            "a record whose arguments cannot be decoded was left unflagged, "
            "so the replay it cannot do counts as one it did")

    def test_a_flagged_record_is_skipped_by_check_rather_than_passed(self):
        # What the flag buys, end to end. SKIP is the only status that is not
        # a verdict, and a record that reaches `check` unflagged and
        # unreplayable would land on one that is.
        lock_a_project(self.tmp, name="calc2.py")
        lock = read_lock(self.tmp)
        for rec in lock["records"]:
            rec["nondet"] = True
        write_lock(self.tmp, lock)
        result = check(self.tmp)
        self.assertEqual(
            statuses(result), ["SKIP"],
            "records flagged nondeterministic were replayed anyway: "
            "{}".format(result.get("results")))
        self.assertFalse(
            result.get("ok"),
            "a run in which every record was skipped still reported the "
            "behavior as intact, which is a gate that cannot go red")


if __name__ == "__main__":
    unittest.main()
