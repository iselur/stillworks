"""`1` means behavior changed, and it is the only thing that means it.

    Exit code `1` — the merge gate closes.
    The other codes exist so nothing can impersonate that one: `0` nothing
    moved, `2` the check could not be made ... `130` stopped by ctrl-c,
    `141` the reader hung up. All of those mean the check never finished
    comparing, which is neither a pass nor a fail — and `stillworks check
    && deploy` needs to be able to tell.

That paragraph is a promise to every script that ever wrote `stillworks check
|| block-the-merge`, and it is a promise about a *number*, which is the only
part of the output a script reads.  A tool that answers 1 when it could not
find the lockfile is telling that script the code changed.  The merge gets
blocked, somebody goes looking for a diff that does not exist, and the next
time they see a 1 they stop believing it.

The failure direction that matters is one-way.  A check that wrongly answers
0 is caught the first time somebody notices an unreviewed change shipped.  A
check that answers 1 for something that is not a change is a false alarm, and
false alarms get routed around — the gate ends up disabled by the person it
was protecting.

So each way of not finishing gets its own run here, and each is asserted
against 1 by name rather than merely against "nonzero".  130 and 141 already
have tests, because producing them means sending signals (test_interrupt,
test_broken_pipe); what was uncovered is everything reachable without one.

The two extractors below are the other half: the README's numbers and the
source's numbers, compared to each other.  Neither the prose nor the code can
drift alone.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import re
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

README = os.path.join(_ROOT, "README.md")
CLI_SOURCE = os.path.join(_ROOT, "stillworks", "cli.py")
SHELL_SOURCE = os.path.join(_ROOT, "stillworks", "shell.py")

# "`0` nothing moved, `2` the check could not be made" — a backticked number
# and nothing else, so `stillworks accept apply_discount#3` in the same
# paragraph is not read as the code 3.
_DOCUMENTED = re.compile(r"`(\d{1,3})`")

STEADY = '''\
def add(a: int, b: int) -> int:
    return a + b
'''

CHANGED = '''\
def add(a: int, b: int) -> int:
    return a + b + 1
'''

# Nothing about this settles, so every record made from it is flagged at lock
# time and there is nothing left to compare on the way back.
#
# The obvious spelling of this — `random.random() * n` — does not work, and
# the reason is worth keeping: the fuzzer tries 0, and `random.random() * 0`
# is 0.0 every time.  One record settles, so one record is verified, and the
# check has something to answer about after all.  The tool was right and the
# fixture was wrong.  Formatting the number into a string leaves nothing for
# an argument to cancel.
RESTLESS = '''\
import random


def roll(n: int) -> str:
    return "{}-{}".format(n, random.random())
'''


def readme() -> str:
    with open(README, encoding="utf-8") as handle:
        return handle.read()


def documented_codes(text):
    """The codes the README's exit-code passage lists.

    It is two paragraphs rather than unedit's one sentence: the first gives 1
    on its own, because 1 is the answer the tool exists to give, and the
    second gives the codes that exist to stay out of its way.
    """
    start = text.find("Exit code `1`")
    if start < 0:
        return set()
    passage = "\n\n".join(text[start:].split("\n\n")[:2])
    return {int(code) for code in _DOCUMENTED.findall(passage)}


def shell_codes():
    """The codes `shell.py` chooses on its own -- the two it gives names to.

    Everything else that module returns is a number this command picked and
    handed back out, and those are counted in cli.py where they were picked.
    These two are picked nowhere else.  They are also the two the README
    documents that no longer appear in cli.py at all, so a reader that stops
    at cli.py sees them vanish and calls that agreement.
    """
    with open(SHELL_SOURCE, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    named = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, int)
                and not isinstance(node.value.value, bool)):
            named[node.targets[0].id] = node.value.value
    returned = {node.value.id for node in ast.walk(tree)
                if isinstance(node, ast.Return)
                and isinstance(node.value, ast.Name)}
    return {named[name] for name in returned & set(named)}


def source_codes():
    """Every constant exit code cli.py produces."""
    with open(CLI_SOURCE, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    codes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Return):
            values = [node.value]
        elif (isinstance(node, ast.Call)
              and getattr(node.func, "attr", None) == "exit"):
            values = [node.args[0]] if node.args else []
        else:
            continue
        # `return 0 if result["ok"] else 1` is the whole verdict, and it is
        # the only place 1 is written.  Reading returns as single constants
        # walks straight past it, which would leave the tool's own headline
        # answer out of the set being compared with the README.
        for value in list(values):
            if isinstance(value, ast.IfExp):
                values += [value.body, value.orelse]
        for value in values:
            if (isinstance(value, ast.Constant)
                    and isinstance(value.value, int)
                    and not isinstance(value.value, bool)):
                codes.add(value.value)
    return codes | shell_codes()


class TestTheExitCodesTheREADMEPromises(unittest.TestCase):
    def setUp(self):
        self.project = tempfile.mkdtemp(prefix="sw-exitcode-")
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def write(self, source, name="calc.py"):
        with open(os.path.join(self.project, name), "w", encoding="utf-8") as fh:
            fh.write(source)

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "stillworks", *argv],
            cwd=self.project, capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=_ROOT))

    def lock(self, source=STEADY, name="calc.py", *extra):
        self.write(source, name)
        proc = self.run_cli("lock", name, "--fuzz", "4", *extra)
        self.assertEqual(proc.returncode, 0,
                         "could not record a baseline to check against:\n"
                         + proc.stdout + proc.stderr)
        return proc

    # -- the prose and the code -------------------------------------------

    def test_the_readme_still_promises_exit_codes(self):
        # Without this the comparison below passes on an empty set, which is
        # what deleting the passage looks like.
        self.assertGreaterEqual(len(documented_codes(readme())), 4,
                                "no exit-code passage left in README.md")

    def test_the_documented_codes_are_the_ones_the_code_can_return(self):
        self.assertEqual(
            sorted(documented_codes(readme())), sorted(source_codes()),
            "README.md's exit codes and the ones stillworks/cli.py returns "
            "disagree")

    # -- the two answers the gate is allowed to give ----------------------

    def test_a_check_that_found_nothing_is_zero(self):
        self.lock()
        proc = self.run_cli("check")
        self.assertEqual(proc.returncode, 0,
                         "an unchanged check did not exit 0:\n"
                         + proc.stdout + proc.stderr)
        self.assertIn("STILL WORKS", proc.stdout)

    def test_a_check_that_found_a_change_is_one(self):
        # The vacuity guard for everything below: if nothing here can produce
        # a 1, then "not 1" is not evidence of anything.
        self.lock()
        self.write(CHANGED)
        proc = self.run_cli("check")
        self.assertEqual(proc.returncode, 1,
                         "a real behavior change did not exit 1:\n"
                         + proc.stdout + proc.stderr)
        self.assertIn("BEHAVIOR CHANGED", proc.stdout)

    # -- and every way of not answering at all ----------------------------

    def test_a_check_with_no_lockfile_is_not_a_verdict(self):
        proc = self.run_cli("check")
        self.assertEqual(proc.returncode, 2,
                         "a check with nothing recorded did not exit 2:\n"
                         + proc.stdout + proc.stderr)

    def test_a_lockfile_nobody_can_read_is_not_a_verdict(self):
        # The realistic version of this: lock.json is committed, so it goes
        # through merges, and a merge can leave conflict markers in it.
        self.lock()
        path = os.path.join(self.project, ".stillworks", "lock.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("<<<<<<< HEAD\n{}\n=======\n")
        proc = self.run_cli("check")
        self.assertEqual(proc.returncode, 2,
                         "an unreadable lockfile did not exit 2:\n"
                         + proc.stdout + proc.stderr)
        self.assertNotIn("BEHAVIOR CHANGED", proc.stdout)

    def test_a_check_that_compared_nothing_is_not_a_verdict(self):
        # A lockfile can be well-formed, readable, full of records, and still
        # answer nothing, because every record in it was flagged at lock time.
        # That is the case most likely to be mistaken for a pass, since the
        # command runs to the end and prints a summary line.
        self.lock(RESTLESS, "dice.py")
        proc = self.run_cli("check")
        self.assertEqual(proc.returncode, 2,
                         "a check that compared nothing did not exit 2:\n"
                         + proc.stdout + proc.stderr)
        self.assertIn("NOTHING VERIFIED", proc.stdout)

    def test_an_unknown_flag_is_two(self):
        proc = self.run_cli("check", "--not-a-flag")
        self.assertEqual(proc.returncode, 2,
                         "an unknown flag did not exit 2:\n"
                         + proc.stdout + proc.stderr)

    def test_nothing_that_failed_to_compare_ever_answers_one(self):
        # The same claim as the four tests above, made in one place and about
        # the number rather than about each command — this is the assertion a
        # reader of `stillworks check && deploy` actually needs.
        self.lock()
        path = os.path.join(self.project, ".stillworks", "lock.json")
        with open(path, encoding="utf-8") as fh:
            good = fh.read()

        ways_to_not_finish = []
        ways_to_not_finish.append(("an unknown flag", ("check", "--not-a-flag")))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("not json at all")
        ways_to_not_finish.append(("an unreadable lockfile", ("check",)))

        for described, argv in ways_to_not_finish:
            proc = self.run_cli(*argv)
            self.assertNotEqual(
                proc.returncode, 1,
                "{} answered 1, which is this tool's word for BEHAVIOR "
                "CHANGED — every gate reading it would block a merge over a "
                "diff that does not exist:\n{}"
                .format(described, proc.stdout + proc.stderr))

        with open(path, "w", encoding="utf-8") as fh:
            fh.write(good)
        self.assertEqual(self.run_cli("check").returncode, 0,
                         "the fixture never recovered, so the loop above was "
                         "not testing what it says")

    def test_a_baseline_it_could_not_save_is_not_reported_as_blessed(self):
        # README: `accept` goes the other way — writing the baseline is the
        # whole job, so a write it could not do is 2, not a quiet 0.
        self.lock()
        self.write(CHANGED)
        self.assertEqual(self.run_cli("check").returncode, 1)
        store = os.path.join(self.project, ".stillworks")
        os.chmod(store, 0o500)
        self.addCleanup(os.chmod, store, 0o700)
        proc = self.run_cli("accept", "--all")
        if proc.returncode == 0 and os.access(
                os.path.join(store, "lock.json"), os.W_OK):
            self.skipTest("this filesystem ignores the mode, so nothing was "
                          "stopped from writing")
        self.assertEqual(proc.returncode, 2,
                         "an accept that could not write the baseline did not "
                         "exit 2:\n" + proc.stdout + proc.stderr)

    def test_a_receipt_it_could_not_save_does_not_change_the_verdict(self):
        # And the same situation on `check` goes the other way, deliberately:
        # the comparison is the verdict, and saving a receipt of it is
        # bookkeeping.  A read-only store must not turn a pass into a failure.
        self.lock()
        store = os.path.join(self.project, ".stillworks")
        os.chmod(store, 0o500)
        self.addCleanup(os.chmod, store, 0o700)
        proc = self.run_cli("check")
        self.assertEqual(proc.returncode, 0,
                         "a read-only .stillworks failed a check that found "
                         "nothing:\n" + proc.stdout + proc.stderr)

    def test_the_json_verdict_and_the_exit_code_say_the_same_thing(self):
        # Two ways of reading one answer, and a wrapper may use either.
        self.lock()
        self.write(CHANGED)
        proc = self.run_cli("check", "--json")
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(json.loads(proc.stdout)["ok"],
                         "--json says the check passed and the exit code says "
                         "it failed")


if __name__ == "__main__":
    unittest.main()
