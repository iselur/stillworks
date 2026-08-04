"""canon is what the comparison actually compares, and it has one hard job.

`outcomes_equal` never sees a Python value.  It sees whatever `canon` made of
one, dumped to JSON.  So canon decides two things and the rest of the tool
inherits both: that two equal results look equal, and that two different
results do not.

The second is the one that rots quietly.  Every fixture in this suite compares
a value with itself, so the paths that make things look *the same* are walked
constantly and the paths that keep them apart are walked by nothing.  A sweep
found ten survivors in here saying as much — the depth cut-off, the repr cap,
the fallback for objects with no `__repr__` of their own, and the flag on a
drained iterator that says whether it was cut short.

The object fallback is the sharpest of them.  Anything with the default repr
scrubs to `<Thing object at 0x...>` — the address is removed on purpose, so two
completely different objects end up with byte-identical reprs.  canon reaches
past that to their attributes.  Take that away and stillworks reports "no
change" for a class whose every field is different, which is the exact failure
the tool exists to catch.

The rest are boundaries.  A cap that fires one item early or one item late does
nothing visible until a value lands on it, and then it either truncates
something whole or claims something truncated is complete.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from stillworks.core import (  # noqa: E402
    _MAX_REPR, _Drained, canon, canon_hash, outcomes_equal, run_call, safe_repr,
)

# The depth canon stops descending at, spelled the way the code spells it.
_MAX_DEPTH = 20


class PlainObject:
    """No __repr__ of its own, so it reprs as `<PlainObject object at 0x...>`."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


class NoDict:
    """__slots__ means vars() raises, so canon has nothing to reach for."""

    __slots__ = ("only",)

    def __init__(self, only):
        self.only = only


def nest(depth):
    """A list nested `depth` deep with a 1 at the bottom."""
    value = 1
    for _ in range(depth):
        value = [value]
    return value


def counting_up_to(n):
    return iter(range(n))


class TestCanonReachesPastTheDefaultRepr(unittest.TestCase):

    def test_two_objects_with_different_attributes_are_different(self):
        # The failure this exists for.  Both of these repr as
        # `<PlainObject object at 0x...>` — identical, because the address is
        # scrubbed on purpose so that two runs of the same code match.  If
        # canon does not look at their attributes, a rewritten class compares
        # equal to the one it replaced and stillworks reports no change.
        one = PlainObject(name="alice", age=30)
        other = PlainObject(name="bob", age=41)
        self.assertEqual(
            safe_repr(one), safe_repr(other),
            "these are meant to have identical scrubbed reprs; if they do not "
            "the test is not testing what it says it is")
        self.assertNotEqual(
            canon(one), canon(other),
            "two objects with entirely different attributes canon'd to the "
            "same thing, so stillworks would report no change between them")

    def test_the_same_attributes_still_compare_equal(self):
        # The other direction, and the reason the address is scrubbed at all:
        # rebuilding an identical object across two runs is not a change.
        self.assertEqual(
            canon(PlainObject(name="alice", age=30)),
            canon(PlainObject(name="alice", age=30)),
            "two objects built with the same attributes were reported as "
            "different, which would make every run a false alarm")

    def test_it_says_what_the_object_was(self):
        shape = canon(PlainObject(x=1))
        self.assertEqual(
            shape.get("__obj__"), "PlainObject",
            "canon dropped the class name, so a report cannot say what "
            "changed: {!r}".format(shape))

    def test_an_object_with_no_attributes_to_read_still_canons(self):
        # __slots__ makes vars() raise.  There is nothing to reach for, so the
        # scrubbed repr is all there is — but it has to come back as something
        # rather than blow up, and the whole point of that branch is that it
        # is reached only when the attributes are genuinely unavailable.
        shape = canon(NoDict(only=1))
        self.assertIn(
            "__repr__", shape,
            "an object with __slots__ canon'd to {!r}; it has no attributes "
            "to read, so the repr is the only honest answer".format(shape))

    def test_a_class_is_described_by_what_it_is_not_what_is_in_it(self):
        # A class object is not an instance, and reading its `vars()` would
        # canon the whole class body — methods and all — as if it were state.
        shape = canon(PlainObject)
        self.assertIn(
            "__repr__", shape,
            "the class itself canon'd to {!r} rather than to its "
            "repr".format(shape))


class TestCanonStopsWhereItSaysItStops(unittest.TestCase):

    def test_it_descends_all_the_way_to_the_stated_depth(self):
        # One level shallower than the cut-off has to come back whole.  A
        # cut-off that fires one level early throws away real data and every
        # value below it compares equal to every other.
        shape = json.dumps(canon(nest(_MAX_DEPTH)))
        self.assertNotIn(
            "__deep__", shape,
            "canon gave up at depth {}, which is the last depth it promises "
            "to descend".format(_MAX_DEPTH))

    def test_it_stops_one_level_further_down(self):
        shape = json.dumps(canon(nest(_MAX_DEPTH + 1)))
        self.assertIn(
            "__deep__", shape,
            "canon descended past depth {}, so a value deep enough to recurse "
            "forever would take the process with it".format(_MAX_DEPTH))

    def test_what_it_gives_up_on_is_still_told_apart(self):
        # Giving up is not the same as forgetting.  Two values too deep to
        # walk still have to compare unequal if they differ, or the cut-off
        # becomes a way of hiding a change.
        self.assertNotEqual(
            canon(nest(_MAX_DEPTH + 5)),
            canon([[[[[["something else"]]]]]] * 1),
            "two values past the depth cut-off canon'd to the same thing")


class TestTheReprCapDoesNotLieAboutWhereItFired(unittest.TestCase):

    def test_a_repr_exactly_at_the_cap_is_left_alone(self):
        # `"a" * (cap - 2)` reprs to exactly the cap, because the quotes
        # count.  A cap that fires here appends `...<+0 chars>` — a truncation
        # notice on a value that was not truncated.
        exact = "a" * (_MAX_REPR - 2)
        said = safe_repr(exact)
        self.assertEqual(
            len(said), _MAX_REPR,
            "a repr of exactly {} characters came back {} long".format(
                _MAX_REPR, len(said)))
        self.assertNotIn(
            "<+", said,
            "a repr that fits inside the cap was reported as truncated: "
            "...{!r}".format(said[-30:]))

    def test_one_character_over_the_cap_is_marked(self):
        said = safe_repr("a" * (_MAX_REPR - 1))
        self.assertIn(
            "<+1 chars>", said,
            "a repr one character too long was not marked as cut, or the "
            "count of what was dropped is wrong: ...{!r}".format(said[-30:]))


class TestADrainedIteratorSaysWhetherItWasCutShort(unittest.TestCase):
    """A generator is consumed to compare it, and consuming it is destructive.

    So there is a limit, and with the limit comes a flag: `truncated` is the
    only thing telling a reader whether they are looking at a whole result or
    the front of one.  Three mutants live in those four lines — the limit off
    by one, and the flag stuck at each of its two values — and all three end
    the same way, with a partial result presented as complete.
    """

    def test_an_iterator_that_fits_is_not_marked_as_cut(self):
        drained = run_call(lambda: counting_up_to(3), [], {})["canon"]
        self.assertEqual(
            drained.get("truncated"), False,
            "a generator of 3 items was reported as truncated: "
            "{!r}".format(drained))
        self.assertEqual(
            len(drained.get("__iter__", [])), 3,
            "a generator of 3 items drained to {!r}".format(drained))

    def test_an_iterator_of_exactly_the_limit_is_marked(self):
        # The boundary.  Draining stops at the limit without ever learning
        # whether more was coming, so the honest answer is "cut short" — and
        # a limit that fires one item late reports it complete instead.
        drained = run_call(lambda: counting_up_to(_Drained._LIMIT), [], {})["canon"]
        self.assertEqual(
            len(drained.get("__iter__", [])), _Drained._LIMIT,
            "draining a generator of exactly {} items kept {} of "
            "them".format(_Drained._LIMIT, len(drained.get("__iter__", []))))
        self.assertEqual(
            drained.get("truncated"), True,
            "a generator drained right up to the limit was reported "
            "complete: this is how a partial result gets trusted")

    def test_a_long_iterator_keeps_the_limit_and_says_so(self):
        drained = run_call(
            lambda: counting_up_to(_Drained._LIMIT * 2), [], {})["canon"]
        self.assertEqual(
            len(drained.get("__iter__", [])), _Drained._LIMIT,
            "the drain kept {} items, and the limit is {}".format(
                len(drained.get("__iter__", [])), _Drained._LIMIT))
        self.assertEqual(
            drained.get("truncated"), True,
            "a generator twice the length of the limit was reported complete")

    def test_a_short_and_a_long_iterator_are_not_the_same_result(self):
        # What the flag is for.  If `truncated` were stuck at one value, these
        # two would differ only in a list length that the flag exists to
        # explain — and a function that started returning an endless generator
        # instead of three items would read as unchanged.
        short = run_call(lambda: counting_up_to(3), [], {})
        long_one = run_call(lambda: counting_up_to(_Drained._LIMIT * 2), [], {})
        self.assertFalse(
            outcomes_equal(short, long_one),
            "a generator of 3 items and one of {} were called the same "
            "behavior".format(_Drained._LIMIT * 2))


class TestTheFloatsThatDoNotCompareLikeNumbers(unittest.TestCase):
    """nan and the infinities, which canon writes out by name.

    nan is the reason this branch exists at all: `nan != nan`, so a function
    that returns one would be reported as changed on every single run — a red
    build that no edit can make green. Writing it as the word means two nans
    match.

    The infinities are the other half. They are ordinary comparable values, so
    nothing forces them apart except canon keeping their sign — and the sign
    test is written as `value > 0` on a value that has already been found to
    be an infinity, so `>=` there is the same test. That equivalence holds only
    while these two stay distinct, and this is what says they do.
    """

    def test_two_nans_are_the_same_result(self):
        self.assertTrue(
            outcomes_equal(run_call(lambda: float("nan"), [], {}),
                           run_call(lambda: float("nan"), [], {})),
            "a function that returns nan was reported as changed against "
            "itself, so its lockfile can never be green again")

    def test_the_two_infinities_are_not(self):
        self.assertNotEqual(
            canon(float("inf")), canon(float("-inf")),
            "positive and negative infinity canon'd to the same thing, so an "
            "overflow that flipped sign would read as no change")

    def test_an_infinity_is_not_a_nan(self):
        self.assertNotEqual(
            canon(float("inf")), canon(float("nan")),
            "an infinity and a nan canon'd to the same thing")


class TestCanonPutsThingsInAnOrderSoNothingElseHasTo(unittest.TestCase):
    """Why three `sort_keys=True` mutants elsewhere are equivalent, pinned.

    `outcomes_equal` and `canon_hash` both dump canon's output with
    `sort_keys=True`, and turning either off changes nothing — because canon
    has already put every set and every mapping in a fixed order by the time
    they run.  That is a property of canon, not a coincidence, and nothing was
    holding canon to it.  These tests do.
    """

    def test_a_set_canons_the_same_however_it_was_built(self):
        # Set iteration order is not the insertion order and is not stable
        # across processes for some element types.  Without canon sorting
        # them, the same set would hash differently between two runs.
        self.assertEqual(
            canon({"c", "a", "b"}), canon({"b", "c", "a"}),
            "the same set built in a different order canon'd differently")

    def test_a_mapping_canons_the_same_however_it_was_built(self):
        self.assertEqual(
            canon({"b": 1, "a": 2}), canon({"a": 2, "b": 1}),
            "the same dict built in a different key order canon'd differently")

    def test_a_nested_one_does_too(self):
        # The shallow case can pass on luck.  Sorting has to happen at every
        # level or a difference just moves one layer down.
        self.assertEqual(
            canon({"outer": {"b": [1, {"y", "x"}], "a": 2}}),
            canon({"outer": {"a": 2, "b": [1, {"x", "y"}]}}),
            "the same nested structure built in a different order canon'd "
            "differently, so canon sorts the top level and not the rest")

    def test_the_hash_follows(self):
        self.assertEqual(
            canon_hash({"b": 1, "a": 2}), canon_hash({"a": 2, "b": 1}),
            "the same dict hashed differently depending on how it was built")

    def test_different_contents_still_hash_differently(self):
        # Vacuity guard for the three above: a canon that flattened
        # everything to a constant would pass all of them.
        self.assertNotEqual(
            canon_hash({"a": 1}), canon_hash({"a": 2}),
            "two different dicts hashed the same")


if __name__ == "__main__":
    unittest.main()
