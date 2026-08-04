"""The whole tool comes down to two comparisons, and nothing was checking them.

`stillworks lock` records what the code did.  `stillworks check` runs it again
and asks whether it did the same thing.  That question is answered in exactly
two places — `outcomes_equal` for a function call and `cmd_outcomes_equal` for a
command — and everything else in the tool is plumbing around them.

A mutation sweep put eight mutants into those two functions and every one of
them lived.  `return False` became `return True`, `and` became `or`, `==`
became `!=`, and the suite stayed green, because the tests reach these
functions only through `check()` on fixtures where behavior did not change.
Code that says "these are the same" is exercised by every one of them; code
that says "these are different" by none.

That is the failure worth naming.  A comparison that answers "same" to
everything passes any test that only ever shows it identical inputs — and it is
the answer that makes the tool useless rather than noisy.  A stillworks that
cries wolf gets debugged on day one.  A stillworks that says "no change" to a
rewritten function is trusted right up until it matters.

So each of these tests is a pair that differs in one thing, and the thing they
differ in is the reason to keep them apart.
"""

from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from stillworks.core import (  # noqa: E402
    canon, cmd_outcomes_equal, outcomes_equal, run_call,
)


def outcome_of(fn, *args, **kwargs):
    """What the tool would record for one call."""
    return run_call(fn, list(args), dict(kwargs))


def cmd_outcome(exit_code=0, stdout="", stderr=""):
    """A command result shaped the way `run_cmd` returns one."""
    return {"exit": exit_code, "stdout": stdout, "stderr": stderr}


def boom(exc):
    def raiser(*_args, **_kwargs):
        raise exc
    return raiser


class TestOutcomesEqualSaysSameOnlyWhenItIs(unittest.TestCase):

    def test_the_same_call_twice_is_the_same(self):
        # The baseline the tool lives on: a function that has not changed must
        # not be reported as changed, or every run is a false alarm.
        first = outcome_of(lambda a, b: a + b, 2, 3)
        second = outcome_of(lambda a, b: a + b, 2, 3)
        self.assertTrue(
            outcomes_equal(first, second),
            "two runs of the same addition were reported as different "
            "behavior: {} vs {}".format(first, second))

    def test_a_different_answer_is_a_different_outcome(self):
        self.assertFalse(
            outcomes_equal(outcome_of(lambda: 41), outcome_of(lambda: 42)),
            "returning 41 and returning 42 were called the same behavior")

    def test_returning_a_value_is_not_the_same_as_raising(self):
        # The mutant this exists for: `if a.get("kind") != b.get("kind"):
        # return False` with the False changed to True.  A function that used
        # to return None and now raises is the loudest kind of change there
        # is, and it is the one that mutant waves through.
        returned = outcome_of(lambda: None)
        raised = outcome_of(boom(ValueError("no")))
        self.assertFalse(
            outcomes_equal(returned, raised),
            "a call that returned {!r} and a call that raised were called the "
            "same behavior".format(None))
        self.assertFalse(
            outcomes_equal(raised, returned),
            "the same two outcomes compared the other way round disagreed, so "
            "the comparison is not symmetric")

    def test_two_exceptions_with_the_same_message_are_still_two_exceptions(self):
        # `raise ValueError("bad input")` becoming `raise TypeError("bad
        # input")` changes what every caller's `except` clause does, and the
        # message — which is all the recorded canon holds for an exception —
        # is identical.  The type is the only thing left to tell them apart.
        value_error = outcome_of(boom(ValueError("bad input")))
        type_error = outcome_of(boom(TypeError("bad input")))
        self.assertEqual(
            value_error["canon"], type_error["canon"],
            "these two are meant to differ only in their type; the test is "
            "not testing what it says it is")
        self.assertFalse(
            outcomes_equal(value_error, type_error),
            "ValueError('bad input') and TypeError('bad input') were called "
            "the same behavior — the recorded type is being ignored")

    def test_the_same_exception_twice_is_the_same(self):
        # The other side of it, and the mutant that needs it: turning the
        # `and` on the type check into an `or` makes every exception differ
        # from itself.  A tool that reports raising code as changed on every
        # run is a tool nobody runs twice.
        first = outcome_of(boom(KeyError("missing")))
        second = outcome_of(boom(KeyError("missing")))
        self.assertTrue(
            outcomes_equal(first, second),
            "raising KeyError('missing') twice was reported as a behavior "
            "change: {} vs {}".format(first, second))

    def test_two_exceptions_of_one_type_with_different_messages_differ(self):
        self.assertFalse(
            outcomes_equal(outcome_of(boom(ValueError("a"))),
                           outcome_of(boom(ValueError("b")))),
            "ValueError('a') and ValueError('b') were called the same")

    def test_values_that_are_equal_but_not_identical_are_the_same(self):
        # canon exists so that two structurally equal results compare equal
        # without being the same object.  If this fails the tool reports a
        # change every time a dict is rebuilt.
        self.assertTrue(
            outcomes_equal(outcome_of(lambda: {"b": [1, 2], "a": 3}),
                           outcome_of(lambda: {"a": 3, "b": [1, 2]})),
            "the same dict built in a different key order was reported as a "
            "behavior change")

    def test_a_list_and_a_tuple_are_not_the_same_result(self):
        # Returning a tuple where a list used to come back breaks anything
        # that mutates the result.  canon keeps the distinction; this is what
        # holds it to using it.
        self.assertFalse(
            outcomes_equal(outcome_of(lambda: [1, 2]), outcome_of(lambda: (1, 2))),
            "returning [1, 2] and returning (1, 2) were called the same")

    def test_dict_key_order_is_normalised_before_the_comparison(self):
        # Two mutants in this function turn off `sort_keys` on the json dump
        # that ends it, and both survive — because canon has already sorted
        # everything by the time the dump happens, so the flag changes
        # nothing.  That is only true while canon keeps doing it, and nothing
        # else says it has to.  This is the property those two mutants are
        # redundant against, pinned on its own.
        import json
        one = json.dumps(canon({"b": 1, "a": 2, "c": 3}), default=str)
        other = json.dumps(canon({"c": 3, "a": 2, "b": 1}), default=str)
        self.assertEqual(
            one, other,
            "canon left two equal dicts with different key orders looking "
            "different, so every comparison downstream now depends on how a "
            "dict happened to be built")


class TestCmdOutcomesEqualSaysSameOnlyWhenItIs(unittest.TestCase):
    """The same job for a recorded command, and the same gap.

    `cmd_outcomes_equal` is one expression joining three comparisons with
    `and`.  Turning that into `or` leaves it green under every test the suite
    had, because all of them compare a command with itself — and it means any
    command that still exits 0 is "unchanged" no matter what it printed.
    """

    def test_the_same_result_twice_is_the_same(self):
        self.assertTrue(
            cmd_outcomes_equal(cmd_outcome(0, "ok\n", ""),
                               cmd_outcome(0, "ok\n", "")),
            "a command with an identical result was reported as changed")

    def test_a_different_exit_code_is_a_change(self):
        self.assertFalse(
            cmd_outcomes_equal(cmd_outcome(0, "ok\n", ""),
                               cmd_outcome(1, "ok\n", "")),
            "a command that started failing was called unchanged because it "
            "still printed the same thing")

    def test_different_output_is_a_change_even_when_it_still_succeeds(self):
        # The one most likely to be missed by a person: exit 0 both times,
        # and the tool is being asked precisely because exit codes are not
        # the whole story.
        self.assertFalse(
            cmd_outcomes_equal(cmd_outcome(0, "3 passed\n", ""),
                               cmd_outcome(0, "2 passed\n", "")),
            "a command that printed something different but still exited 0 "
            "was called unchanged")

    def test_different_stderr_is_a_change(self):
        self.assertFalse(
            cmd_outcomes_equal(cmd_outcome(0, "ok\n", ""),
                               cmd_outcome(0, "ok\n", "DeprecationWarning\n")),
            "a command that started writing to stderr was called unchanged")

    def test_a_missing_field_is_not_quietly_equal_to_a_present_one(self):
        # Records written by an older version, or half-written ones, arrive
        # here with keys absent.  `.get` makes that not crash; it must not
        # also make it match.
        self.assertFalse(
            cmd_outcomes_equal({"exit": 0}, cmd_outcome(0, "ok\n", "")),
            "a record with no recorded output matched one that printed "
            "something")


if __name__ == "__main__":
    unittest.main()
