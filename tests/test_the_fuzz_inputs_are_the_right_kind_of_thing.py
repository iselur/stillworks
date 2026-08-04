"""Where fuzz arguments come from, and why the wrong ones are worse than none.

`stillworks lock --fuzz` calls a function with generated inputs and records
what came back. Two things shape those inputs. `_constants_of` mines the
literals out of the function's own bytecode, so that `if tier == "GOLD"` gets
tried with `"GOLD"` rather than with `"a"`. `_sweep_candidates` then builds one
argument tuple per mined literal, putting it at the parameter it fits and
leaving plain defaults everywhere else.

Both of those decide *types*, and a wrong type does not fail loudly. It gets
called, raises a TypeError, and the TypeError is recorded as the function's
behavior — a baseline that says "this function raises" for a function that does
nothing of the sort. The next `check` faithfully confirms it still raises. The
sweep put a mutant in the widening rule that does exactly this, and it lived,
because a lockfile full of recorded TypeErrors round-trips perfectly.

The other mutant is the cap on how big a mined integer may be. It is a bare
literal, nothing else in the tree names it, and off by one it stays invisible
until a function has a constant sitting exactly on it.
"""

from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from stillworks.core import _constants_of, _sweep_candidates  # noqa: E402

# The largest mined integer, one past. A copy of a bare literal in the source.
_INT_CAP = 10 ** 9


def tiered(tier: str, amount: int) -> str:
    if tier == "GOLD" and amount > 500:
        return "free"
    return "standard"


def sits_on_the_cap(n: int) -> int:
    return n + 1000000000          # exactly the cap


def sits_just_under_the_cap(n: int) -> int:
    return n + 999999999           # one below it


def flagged(on: bool, n: int) -> int:
    return n if on else 0


class TestTheMinedConstantsAreTheOnesWorthTrying(unittest.TestCase):

    def test_a_branch_literal_is_mined(self):
        # The reason any of this exists, and the vacuity guard for the rest:
        # a miner that returned nothing would pass every boundary test below.
        pools = _constants_of(tiered)
        self.assertIn(
            "GOLD", pools.get(str, []),
            "the literal a branch compares against was not mined, so fuzzing "
            "this function never takes the branch: {}".format(pools))
        self.assertIn(
            500, pools.get(int, []),
            "the number a branch compares against was not mined: {}".format(
                pools))

    def test_an_integer_just_under_the_cap_is_mined(self):
        self.assertIn(
            _INT_CAP - 1, _constants_of(sits_just_under_the_cap).get(int, []),
            "an integer inside the cap was dropped, so the branch it belongs "
            "to goes untried")

    def test_an_integer_on_the_cap_is_not(self):
        # The boundary. Wherever the cap is set, a mined constant becomes an
        # argument that gets pickled into the lockfile and replayed on every
        # check — so the cap is a real decision, and it has to be the number
        # the code names rather than one either side of it.
        self.assertNotIn(
            _INT_CAP, _constants_of(sits_on_the_cap).get(int, []),
            "an integer at the cap was mined, so the cap is one larger than "
            "the code says it is")

    def test_true_and_false_are_not_mined_as_numbers(self):
        # `bool` is a subclass of `int`, so an unguarded miner collects True
        # and False into the integer pool — where they come back out as
        # arguments to integer parameters, in a lockfile whose args_repr says
        # `True` where a reader expects a number.
        self.assertNotIn(
            True, _constants_of(flagged).get(int, []),
            "True was mined into the integer pool")


class TestTheGeneratedArgumentsFitTheParameterTheyGoTo(unittest.TestCase):

    def test_an_integer_literal_is_offered_to_a_float_parameter(self):
        # `f(1.5)` and `f(2)` both work, and a function annotated `float` that
        # branches on a whole number is common enough — `if rate == 0` — that
        # not widening would leave those branches untried.
        combos = _sweep_candidates([float], {int: [7]})
        self.assertIn(
            (7.0,), combos,
            "an integer literal was not offered to a float parameter, so "
            "`if rate == 0` never gets a 0: {}".format(combos))
        # `7 == 7.0`, so membership cannot tell these apart — the type can,
        # and the type is the whole point: an int reaching a float parameter
        # is recorded with `7` in its args_repr and replayed as one.
        for args in combos:
            self.assertIsInstance(
                args[0], float,
                "a {} was generated for a parameter annotated float: "
                "{}".format(type(args[0]).__name__, combos))

    def test_an_integer_literal_is_not_offered_to_a_string_parameter(self):
        # The mutant this exists for. Widening on every parameter *except*
        # float puts `7.0` where a `str` is annotated; the call raises
        # TypeError, and the TypeError is recorded as the behavior of a
        # function that has none of the sort.
        combos = _sweep_candidates([str], {str: ["GOLD"], int: [7]})
        self.assertIn(
            ("GOLD",), combos,
            "the string literal for a string parameter went missing: "
            "{}".format(combos))
        for args in combos:
            self.assertIsInstance(
                args[0], str,
                "a {} was generated for a parameter annotated str, so the "
                "recorded behavior for this function is a TypeError: "
                "{}".format(type(args[0]).__name__, combos))

    def test_each_literal_gets_its_own_call_with_defaults_elsewhere(self):
        # What the shape is for: one literal at a time, so a failure names the
        # literal that caused it instead of a tuple of six interesting values.
        combos = _sweep_candidates([str, int], {str: ["GOLD"], int: [500]})
        self.assertIn(
            ("GOLD", 1), combos,
            "the string literal was not tried with a plain default beside it: "
            "{}".format(combos))
        self.assertIn(
            ("a", 500), combos,
            "the integer literal was not tried with a plain default beside "
            "it: {}".format(combos))


if __name__ == "__main__":
    unittest.main()
