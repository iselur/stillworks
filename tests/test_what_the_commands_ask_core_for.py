"""Four operations, and the answers they hand back.

`cmd_check` had always been one line of work -- `core.check(project)` -- and
then thirty lines of deciding how to print what came back.  `cmd_lock` was the
other shape entirely: it reached into `core` for sixteen separate names and did
the recording itself, in the command-line layer, three `print(..., stderr)`
calls deep inside the run.  `cmd_status` did its own counting off the raw
lockfile.  So two of the four commands could be tested by calling a function
and reading a dict, and two could only be tested by running a process and
reading its screen.

That is the difference this file is about.  `lock` and `status` now answer the
way `check` and `accept` already did, which means every fact either one works
out is reachable from Python:

  * what stopped a recording run short, and what got kept anyway;
  * which functions could not be fuzzed;
  * what lockfile is being replaced, and whether it was even readable;
  * how many records, calls, commands, flagged and skipped;
  * and, for `status`, what the file on disk says about itself.

None of it is printed here.  The wording is the tool's, the streams are the
caller's, and `tests/test_cli.py` still runs the real command to check that the
rendering of these answers is what a person sees.

The last class is the seam itself: the list of names `cli.py` is allowed to
reach for in `core`.  It is a cheap test and an easy one to argue with, but the
sixteen names did not arrive on purpose either -- they arrived one at a time,
each perfectly reasonable, over a year.
"""

from __future__ import annotations

import ast
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from stillworks import core  # noqa: E402

STEADY = '''\
def add(a: int, b: int) -> int:
    return a + b
'''

# No annotations, so there is nothing for `--fuzz` to make an input out of.
UNFUZZABLE = '''\
def add(a, b):
    return a + b
'''

DRIVER_THAT_DIES = '''\
import sys
import calc
calc.add(1, 2)
sys.exit(3)
'''

# `sys.exit(0)` raises SystemExit exactly the way `sys.exit(3)` does, and a
# `sys.exit(main())` at the bottom of a script is the ordinary way to end one.
DRIVER_THAT_EXITS_CLEAN = '''\
import sys
import calc
calc.add(1, 2)
sys.exit(0)
'''


class _InAProject(unittest.TestCase):

    def setUp(self):
        self.project = tempfile.mkdtemp(prefix="sw-core-lock-")
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def write(self, source, name="calc.py"):
        path = os.path.join(self.project, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source)
        return path

    def lock(self, **kw):
        kw.setdefault("target", self.write(STEADY))
        kw.setdefault("fuzz", 4)
        return core.lock(self.project, **kw)


class TestWhatItRefuses(_InAProject):
    """Every refusal comes back as `error`, and nothing is written."""

    def refusal(self, **kw):
        result = core.lock(self.project, **kw)
        self.assertIn("error", result, result)
        return result["error"]

    def test_nothing_to_lock_at_all(self):
        self.assertIn("nothing to lock", self.refusal())

    def test_nothing_to_lock_is_said_before_anything_more_specific(self):
        # `lock --run drive.py` with no target and no --cmd has two things
        # wrong with it, and the one worth saying is the one that names what a
        # lockfile is made of.  The CLI answered in this order before any of
        # this moved into core, and the order is part of the answer.
        self.assertIn("nothing to lock", self.refusal(run="x.py", fuzz=4))

    def test_a_run_with_nothing_to_record_it_into(self):
        self.assertIn("--run needs a TARGET",
                      self.refusal(run="x.py", cmds=["true"]))

    def test_fuzzing_with_nothing_to_fuzz(self):
        self.assertIn("--fuzz needs a TARGET",
                      self.refusal(fuzz=4, cmds=["true"]))

    def test_a_timeout_that_cannot_elapse(self):
        for bad in (0, -1):
            with self.subTest(bad):
                self.assertIn("greater than zero",
                              self.refusal(cmds=["true"], timeout=bad))

    def test_a_project_that_is_a_file(self):
        path = self.write(STEADY, "notadir")
        self.assertIn("must be a directory",
                      core.lock(path, cmds=["true"])["error"])

    def test_a_target_that_will_not_load(self):
        self.write("import nonexistent_module_xyz\n", "bad.py")
        self.assertIn("could not load",
                      self.refusal(target=os.path.join(self.project, "bad.py"),
                                   fuzz=2))

    def test_a_script_that_is_not_there(self):
        self.assertIn("no such script",
                      self.refusal(target=self.write(STEADY),
                                   run=os.path.join(self.project, "gone.py")))

    def test_a_target_that_yielded_nothing_says_what_to_try(self):
        error = self.refusal(target=self.write(STEADY))
        self.assertIn("no behavior captured", error)
        self.assertIn("--fuzz 8", error, "a refusal with no way forward in it")

    def test_a_refusal_leaves_no_lockfile_behind(self):
        core.lock(self.project)
        self.assertFalse(os.path.exists(core.lock_path(self.project)))


class TestWhatItCounts(_InAProject):

    def test_the_records_it_made(self):
        result = self.lock()
        self.assertNotIn("error", result)
        self.assertEqual(result["calls"], result["records"])
        self.assertEqual(result["cmds"], 0)
        self.assertGreater(result["records"], 0)

    def test_commands_are_counted_apart_from_calls(self):
        result = self.lock(cmds=["echo hi"])
        self.assertEqual(result["cmds"], 1)
        self.assertEqual(result["calls"] + result["cmds"], result["records"])

    def test_the_path_it_wrote(self):
        result = self.lock()
        self.assertEqual(result["path"], core.lock_path(self.project))
        self.assertTrue(os.path.exists(result["path"]))

    def test_a_cap_is_a_cap(self):
        result = self.lock(cmds=["echo a", "echo b"], max_records=2)
        self.assertEqual(result["records"], 2)

    def test_what_settles_is_not_flagged(self):
        self.assertEqual(self.lock()["nondet"], 0)

    def test_what_does_not_settle_is_flagged_and_kept(self):
        result = self.lock(target=None, fuzz=0,
                           cmds=["python3 -c 'import time;print(time.time())'"])
        self.assertEqual(result["records"], 1)
        self.assertEqual(result["nondet"], 1,
                         "a clock-reading command was taken as a baseline")


class TestWhatItSaysBesideTheAnswer(_InAProject):
    """The three things that used to be `print(..., file=sys.stderr)`."""

    def test_a_quiet_run_says_nothing(self):
        self.assertEqual(self.lock()["notes"], [])

    def test_a_driver_that_died_says_so_and_keeps_what_it_got(self):
        self.write(STEADY)
        result = self.lock(fuzz=0, run=self.write(DRIVER_THAT_DIES, "drive.py"),
                           target=os.path.join(self.project, "calc.py"))
        self.assertIn("the script exited 3", result["partial"])
        self.assertEqual(result["records"], 1, "the call before it died")
        self.assertTrue(any("did not finish" in n for n in result["notes"]),
                        result["notes"])

    def test_the_reason_it_stopped_goes_into_the_lockfile_too(self):
        # A note is gone with the terminal it was printed in.  A lockfile is
        # committed, and `check` says this again every time it runs.
        self.write(STEADY)
        self.lock(fuzz=0, run=self.write(DRIVER_THAT_DIES, "drive.py"),
                  target=os.path.join(self.project, "calc.py"))
        self.assertIn("exited 3", core.load_lock(self.project)["partial"])

    def test_a_run_that_finished_leaves_partial_empty(self):
        self.assertEqual(self.lock()["partial"], "")

    def test_a_driver_that_exited_zero_finished(self):
        # The distinction the whole `partial` field is for.  Both scripts leave
        # by raising SystemExit; only one of them failed.
        self.write(STEADY)
        result = self.lock(
            fuzz=0, run=self.write(DRIVER_THAT_EXITS_CLEAN, "drive.py"),
            target=os.path.join(self.project, "calc.py"))
        self.assertEqual(result["partial"], "")
        self.assertEqual(result["notes"], [])
        self.assertEqual(result["records"], 1)

    def test_functions_it_could_not_fuzz_are_named(self):
        result = core.lock(self.project, cmds=["echo hi"], fuzz=4,
                           target=self.write(UNFUZZABLE))
        note = "\n".join(result["notes"])
        self.assertIn("could not generate inputs for: add", note)
        self.assertIn("--run", note, "named a problem with no way out of it")

    def test_replacing_a_lockfile_says_what_is_being_replaced(self):
        first = self.lock()
        note = "\n".join(self.lock()["notes"])
        self.assertIn("replacing existing lockfile", note)
        self.assertIn("{} records".format(first["records"]), note)

    def test_replacing_a_lockfile_nobody_could_read_still_works(self):
        # `lock` is the way out of a damaged lockfile, so it must not be the
        # thing a damaged lockfile stops.
        self.lock()
        with open(core.lock_path(self.project), "w", encoding="utf-8") as fh:
            fh.write("{not json at all")
        result = self.lock()
        self.assertNotIn("error", result)
        self.assertTrue(any("could not be read" in n for n in result["notes"]),
                        result["notes"])


class TestWhatStatusSays(_InAProject):

    def test_a_project_nobody_has_locked(self):
        result = core.status(self.project)
        self.assertEqual(result["none"],
                         os.path.join(self.project, core.LOCK_DIR))

    def test_a_project_that_has_been_locked(self):
        locked = self.lock()
        result = core.status(self.project)
        self.assertNotIn("none", result)
        self.assertEqual(result["records"], locked["records"])
        self.assertEqual(result["nondet"], locked["nondet"])
        self.assertEqual(result["partial"], "")
        self.assertEqual(result["history"], 0)
        self.assertTrue(result["created"])

    def test_it_names_the_module_the_baseline_is_of(self):
        self.lock()
        self.assertIn("calc", core.status(self.project)["module"])

    def test_commands_only_means_no_module_rather_than_a_missing_key(self):
        core.lock(self.project, cmds=["echo hi"])
        self.assertEqual(core.status(self.project)["module"], "")

    def test_accepted_changes_are_counted(self):
        self.write(STEADY)
        self.lock(target=os.path.join(self.project, "calc.py"))
        self.write("def add(a: int, b: int) -> int:\n    return a + b + 1\n")
        accepted = core.accept(self.project)
        self.assertTrue(accepted["accepted"], accepted)
        self.assertEqual(core.status(self.project)["history"],
                         len(accepted["accepted"]))

    def test_what_was_flagged_at_lock_time_is_still_flagged(self):
        # Checked against a count that is not zero, because `0 == 0` is also
        # what a status that never looks at the flag would say.
        locked = core.lock(
            self.project,
            cmds=["python3 -c 'import time;print(time.time())'"])
        self.assertEqual(locked["nondet"], 1)
        self.assertEqual(core.status(self.project)["nondet"], 1)

    def test_a_partial_baseline_still_says_so_days_later(self):
        self.write(STEADY)
        self.lock(fuzz=0, run=self.write(DRIVER_THAT_DIES, "drive.py"),
                  target=os.path.join(self.project, "calc.py"))
        self.assertIn("exited 3", core.status(self.project)["partial"])


class TestTheSeamItself(unittest.TestCase):
    """What `cli.py` is allowed to know about `core`.

    Four operations and two facts.  The four each answer one question and hand
    back a dict; the two are things a caller has to know to talk about the
    answers -- the error a damaged lockfile arrives as, and the default this
    tool's `--help` quotes out loud.

    A name added here is not automatically wrong.  It is a decision, and this
    is the test that makes somebody make it.
    """

    THE_OPERATIONS = {"lock", "check", "accept", "status"}
    THE_FACTS = {"LockfileError", "DEFAULT_CMD_TIMEOUT"}

    def reached_for(self, module):
        path = os.path.join(_ROOT, "stillworks", module)
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        return {node.attr for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "core"}

    def test_the_command_line_reaches_for_four_operations_and_two_facts(self):
        self.assertEqual(
            self.reached_for("cli.py"), self.THE_OPERATIONS | self.THE_FACTS,
            "cli.py's reach into core changed.  Every name here is a thing the "
            "command line has to know about the inside of this tool; the four "
            "operations are things it only has to ask for.")

    def test_each_operation_is_really_there(self):
        for name in sorted(self.THE_OPERATIONS | self.THE_FACTS):
            with self.subTest(name):
                self.assertTrue(hasattr(core, name))

    def test_the_operations_hand_back_an_answer_rather_than_printing_one(self):
        # The property that makes them testable from here at all.
        path = os.path.join(_ROOT, "stillworks", "core.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in tree.body:
            if not (isinstance(node, ast.FunctionDef)
                    and node.name in self.THE_OPERATIONS):
                continue
            with self.subTest(node.name):
                printed = [n for n in ast.walk(node)
                           if isinstance(n, ast.Call)
                           and isinstance(n.func, ast.Name)
                           and n.func.id == "print"]
                self.assertEqual(printed, [],
                                 "core.{} prints; it is supposed to answer"
                                 .format(node.name))


if __name__ == "__main__":
    unittest.main()
