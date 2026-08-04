"""Tests for stillworks.core — canonicalization, encoding, Recorder,
fuzz_function, load_module, check(), and accept()."""

import base64
import dataclasses
import json
import math
import os
import pickle
import shutil
import sys
import tempfile
import types
import unittest

# Ensure the package is importable when the test runner's cwd is the repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from stillworks import core


# ---------------------------------------------------------------------------
# canon()
# ---------------------------------------------------------------------------

class TestCanon(unittest.TestCase):

    def test_none_bool_int_str_are_identity(self):
        self.assertIsNone(core.canon(None))
        self.assertIs(core.canon(True), True)
        self.assertIs(core.canon(False), False)
        self.assertEqual(core.canon(42), 42)
        self.assertEqual(core.canon("hello"), "hello")

    def test_nan(self):
        self.assertEqual(core.canon(float("nan")), {"__float__": "nan"})

    def test_inf(self):
        self.assertEqual(core.canon(float("inf")), {"__float__": "inf"})
        self.assertEqual(core.canon(float("-inf")), {"__float__": "-inf"})

    def test_finite_float_passthrough(self):
        self.assertEqual(core.canon(3.14), 3.14)

    def test_bytes(self):
        b = b"hello bytes"
        result = core.canon(b)
        self.assertEqual(result, {"__bytes__": base64.b64encode(b).decode("ascii")})

    def test_list(self):
        result = core.canon([1, 2, 3])
        self.assertEqual(result, {"__seq__": [1, 2, 3], "__tuple__": False})

    def test_tuple_vs_list_differ(self):
        list_r = core.canon([1, 2])
        tuple_r = core.canon((1, 2))
        self.assertNotEqual(list_r, tuple_r)
        self.assertFalse(list_r["__tuple__"])
        self.assertTrue(tuple_r["__tuple__"])

    def test_set_ordering_stable(self):
        # Same elements, different iteration order — must produce identical canonical forms.
        r1 = core.canon({3, 1, 2})
        r2 = core.canon({2, 3, 1})
        self.assertEqual(r1, r2)
        # Items must be sorted by their JSON representation.
        items = r1["__set__"]
        expected = sorted(items, key=lambda x: json.dumps(x, sort_keys=True, default=str))
        self.assertEqual(items, expected)

    def test_frozenset(self):
        result = core.canon(frozenset([10, 20, 5]))
        self.assertIn("__set__", result)

    def test_dict_ordering_stable(self):
        r1 = core.canon({"b": 1, "a": 2})
        r2 = core.canon({"a": 2, "b": 1})
        self.assertEqual(r1, r2)
        pairs = r1["__map__"]
        keys = [p[0] for p in pairs]
        expected_keys = sorted(keys, key=lambda x: json.dumps(x, sort_keys=True, default=str))
        self.assertEqual(keys, expected_keys)

    def test_nested_dict(self):
        d = {"outer": {"inner": [1, 2, 3]}}
        result = core.canon(d)
        self.assertIn("__map__", result)
        # Drill into the nested value
        inner_val = result["__map__"][0][1]
        self.assertIn("__map__", inner_val)

    def test_dataclass(self):
        @dataclasses.dataclass
        class Point:
            x: int
            y: int

        p = Point(3, 7)
        result = core.canon(p)
        self.assertEqual(result["__obj__"], "Point")
        self.assertIn("fields", result)

    def test_unreprable_object(self):
        class BadRepr:
            def __repr__(self):
                raise RuntimeError("no repr for you")

        obj = BadRepr()
        result = core.canon(obj)
        self.assertIn("__repr__", result)
        self.assertIn("unreprable", result["__repr__"])

    def test_depth_limit(self):
        # Depth > 20 must return {"__deep__": ...} instead of recursing forever.
        d = {}
        cur = d
        for _ in range(25):
            cur["x"] = {}
            cur = cur["x"]
        # Should not raise RecursionError and must produce a dict.
        result = core.canon(d)
        self.assertIsInstance(result, dict)


# ---------------------------------------------------------------------------
# encode_args / decode_args
# ---------------------------------------------------------------------------

class TestEncodeDecodeArgs(unittest.TestCase):

    def test_roundtrip(self):
        args = (1, "hello", [1, 2, 3])
        kwargs = {"key": "value", "n": 42}
        b64 = core.encode_args(args, kwargs)
        self.assertIsNotNone(b64)
        dec_args, dec_kwargs = core.decode_args(b64)
        self.assertEqual(dec_args, args)
        self.assertEqual(dec_kwargs, kwargs)

    def test_empty_args(self):
        b64 = core.encode_args((), {})
        self.assertIsNotNone(b64)
        a, k = core.decode_args(b64)
        self.assertEqual(a, ())
        self.assertEqual(k, {})

    def test_unpicklable_returns_none(self):
        # Lambdas cannot be pickled with the default protocol.
        result = core.encode_args((lambda x: x,), {})
        self.assertIsNone(result)

    def test_bytes_roundtrip(self):
        b64 = core.encode_args((b"\x00\xff\xab",), {})
        self.assertIsNotNone(b64)
        (b,), _ = core.decode_args(b64)
        self.assertEqual(b, b"\x00\xff\xab")


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------

def _make_mod(src, name="test_mod_sw"):
    """Exec src into a fresh module whose __name__ matches so public_functions works."""
    mod = types.ModuleType(name)
    exec(compile(src, "<test>", "exec"), mod.__dict__)
    return mod


class TestRecorder(unittest.TestCase):

    def test_records_call(self):
        mod = _make_mod("def add(x, y): return x + y\n")
        with core.Recorder(mod) as rec:
            mod.add(1, 2)
        self.assertEqual(len(rec.records), 1)
        self.assertEqual(rec.records[0]["target"], "add")
        self.assertEqual(rec.records[0]["out"]["kind"], "value")

    def test_nested_call_not_double_recorded(self):
        # main_fn calls helper via module globals — only main_fn must be recorded.
        src = """\
def helper(x):
    return x * 2

def main_fn(x):
    return helper(x) + 1
"""
        mod = _make_mod(src)
        with core.Recorder(mod) as rec:
            mod.main_fn(5)
        targets = [r["target"] for r in rec.records]
        self.assertIn("main_fn", targets)
        self.assertNotIn("helper", targets)
        self.assertEqual(len(targets), 1)

    def test_reraises_exception(self):
        mod = _make_mod("def boom(): raise ValueError('oops')\n")
        with core.Recorder(mod) as rec:
            with self.assertRaises(ValueError):
                mod.boom()
        self.assertEqual(len(rec.records), 1)
        self.assertEqual(rec.records[0]["out"]["kind"], "exception")
        self.assertEqual(rec.records[0]["out"]["type"], "ValueError")

    def test_dedup_identical_args(self):
        mod = _make_mod("def fn(x): return x\n")
        with core.Recorder(mod) as rec:
            mod.fn(1)
            mod.fn(1)   # duplicate — must not create a second record
            mod.fn(2)   # different
        self.assertEqual(len(rec.records), 2)

    def test_restores_originals_on_clean_exit(self):
        mod = _make_mod("def fn(x): return x\n")
        original_fn = mod.fn
        with core.Recorder(mod):
            pass
        self.assertIs(mod.fn, original_fn)

    def test_restores_originals_on_exception_exit(self):
        mod = _make_mod("def fn(x): return x\n")
        original_fn = mod.fn
        try:
            with core.Recorder(mod):
                raise RuntimeError("test exception")
        except RuntimeError:
            pass
        self.assertIs(mod.fn, original_fn)

    def test_skipped_unpicklable_counted(self):
        # Pass a lambda (unpicklable) as argument.
        src = "def fn(x): return 1\n"
        mod = _make_mod(src)
        with core.Recorder(mod) as rec:
            mod.fn(lambda: None)
        self.assertEqual(len(rec.records), 0)
        self.assertEqual(rec.skipped_unpicklable, 1)


# ---------------------------------------------------------------------------
# fuzz_function
# ---------------------------------------------------------------------------

import random as _random


class TestFuzzFunction(unittest.TestCase):

    def test_mines_branch_constants(self):
        def discount(tier: str) -> float:
            if tier == "GOLD":
                return 0.2
            if tier == "SILVER":
                return 0.1
            return 0.0

        rng = _random.Random(42)
        records, _ = core.fuzz_function("discount", discount, rng, 10)
        self.assertGreater(len(records), 0)
        tried = [core.decode_args(r["args_b64"])[0][0] for r in records]
        self.assertIn("GOLD", tried)
        self.assertIn("SILVER", tried)

    def test_skips_unannotated_params(self):
        def no_hints(x, y):
            return x + y

        rng = _random.Random(42)
        records, _ = core.fuzz_function("no_hints", no_hints, rng, 10)
        self.assertEqual(records, [])

    def test_skips_no_params(self):
        def no_params() -> int:
            return 42

        rng = _random.Random(42)
        records, _ = core.fuzz_function("no_params", no_params, rng, 10)
        self.assertEqual(records, [])

    def test_records_exceptions_as_behavior(self):
        def divide(x: int, y: int) -> float:
            return x / y

        rng = _random.Random(0)
        records, _ = core.fuzz_function("divide", divide, rng, 20)
        exception_records = [r for r in records if r["out"]["kind"] == "exception"]
        # y=0 is in _SAMPLES[int], so ZeroDivisionError must appear.
        self.assertGreater(len(exception_records), 0)

    def test_records_have_required_fields(self):
        def add(x: int, y: int) -> int:
            return x + y

        rng = _random.Random(7)
        records, _ = core.fuzz_function("add", add, rng, 5)
        for r in records:
            self.assertEqual(r["kind"], "call")
            self.assertEqual(r["target"], "add")
            self.assertIn("args_b64", r)
            self.assertIn("out", r)
            self.assertIn("source", r)


# ---------------------------------------------------------------------------
# load_module
# ---------------------------------------------------------------------------

class TestLoadModule(unittest.TestCase):

    def test_load_by_file_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "mymod.py")
            with open(path, "w") as f:
                f.write("def add(x, y): return x + y\n")
            mod, info = core.load_module(path, tmpdir)
            self.assertEqual(mod.add(2, 3), 5)
            self.assertEqual(info["module"], "mymod")

    def test_load_by_dotted_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "swmod_dotted.py")
            with open(path, "w") as f:
                f.write("def mul(x, y): return x * y\n")
            mod, info = core.load_module("swmod_dotted", tmpdir)
            self.assertEqual(mod.mul(3, 4), 12)
            self.assertEqual(info["module"], "swmod_dotted")

    def test_file_not_found_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(FileNotFoundError):
                core.load_module("nonexistent.py", tmpdir)

    def test_dotted_name_not_found_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ImportError):
                core.load_module("definitely_not_a_real_module_xyz", tmpdir)

    def test_file_edit_picked_up_immediately(self):
        """Rewrite within the same second; _exec_source must read new source, not stale pyc."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "freshmod.py")
            with open(path, "w") as f:
                f.write("def value(): return 'original'\n")
            mod1, _ = core.load_module(path, tmpdir)
            self.assertEqual(mod1.value(), "original")

            # Overwrite in the same second — if pyc were used, old bytecode would replay.
            with open(path, "w") as f:
                f.write("def value(): return 'updated'\n")
            mod2, _ = core.load_module(path, tmpdir)
            self.assertEqual(mod2.value(), "updated")


# ---------------------------------------------------------------------------
# check()  — end-to-end status coverage
# ---------------------------------------------------------------------------

def _build_lock(tmpdir, records, module_info=None):
    lock = core.new_lock(
        module_info or {"module": "target", "path": "target.py"}, 1234)
    lock["records"] = records
    core.save_lock(tmpdir, lock)


def _call_record(target, args, kwargs, outcome, record_id, nondet=False):
    return {
        "id": record_id,
        "kind": "call",
        "target": target,
        "args_b64": core.encode_args(args, kwargs),
        "args_repr": repr((args, kwargs)),
        "args_hash": core.canon_hash((list(args), kwargs)),
        "out": outcome,
        "source": "fuzz",
        "nondet": nondet,
    }


class TestCheck(unittest.TestCase):

    def test_no_lockfile_returns_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = core.check(tmpdir)
            self.assertIn("error", result)

    def test_ok_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "target.py"), "w") as f:
                f.write("def add(x, y): return x + y\n")
            rec = _call_record("add", (1, 2), {},
                               {"kind": "value", "canon": core.canon(3), "repr": "3"},
                               "add#1")
            _build_lock(tmpdir, [rec])
            result = core.check(tmpdir)
            self.assertTrue(result["ok"])
            self.assertEqual(result["counts"].get("OK", 0), 1)

    def test_changed_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "target.py"), "w") as f:
                f.write("def add(x, y): return x + y\n")
            # Locked result is 999, actual is 3 — CHANGED.
            rec = _call_record("add", (1, 2), {},
                               {"kind": "value", "canon": core.canon(999), "repr": "999"},
                               "add#1")
            _build_lock(tmpdir, [rec])
            result = core.check(tmpdir)
            self.assertFalse(result["ok"])
            self.assertEqual(result["counts"].get("CHANGED", 0), 1)

    def test_gone_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "target.py"), "w") as f:
                f.write("def other(): return 1\n")
            rec = _call_record("missing_fn", (), {},
                               {"kind": "value", "canon": core.canon(42), "repr": "42"},
                               "missing_fn#1")
            _build_lock(tmpdir, [rec])
            result = core.check(tmpdir)
            self.assertFalse(result["ok"])
            self.assertEqual(result["counts"].get("GONE", 0), 1)

    def test_skip_nondet(self):
        # SKIP does not count as a failure — a flagged record sitting beside a
        # verified one must not drag the verdict down.  It is also not a pass
        # on its own: this used to be written with the SKIP as the lockfile's
        # only record, which asserted that a run comparing nothing was `ok`.
        # See test_only_skip_is_not_a_pass, and the note in core.check.
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "target.py"), "w") as f:
                f.write("def fn(): return 1\n")
            skipped = _call_record(
                "fn", (), {},
                {"kind": "value", "canon": core.canon(1), "repr": "1"},
                "fn#1", nondet=True)
            checked = _call_record(
                "fn", (), {},
                {"kind": "value", "canon": core.canon(1), "repr": "1"}, "fn#2")
            _build_lock(tmpdir, [skipped, checked])
            result = core.check(tmpdir)
            self.assertTrue(result["ok"])
            self.assertEqual(result["counts"].get("SKIP", 0), 1)
            self.assertEqual(result["counts"].get("OK", 0), 1)
            self.assertEqual(result["verified"], 1)

    def test_only_skip_is_not_a_pass(self):
        # Every record excluded means nothing was compared, and `ok` is the
        # field CI reads.  The module below could be rewritten to raise and
        # this run would look identical.
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "target.py"), "w") as f:
                f.write("def fn(): return 1\n")
            rec = _call_record("fn", (), {},
                               {"kind": "value", "canon": core.canon(1), "repr": "1"},
                               "fn#1", nondet=True)
            _build_lock(tmpdir, [rec])
            result = core.check(tmpdir)
            self.assertEqual(result["verified"], 0)
            self.assertFalse(result["ok"])

    def test_broken_status_module_load_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "target.py"), "w") as f:
                f.write("def (\n")   # deliberate SyntaxError
            rec = _call_record("fn", (1,), {},
                               {"kind": "value", "canon": core.canon(1), "repr": "1"},
                               "fn#1")
            _build_lock(tmpdir, [rec])
            result = core.check(tmpdir)
            self.assertFalse(result["ok"])
            self.assertEqual(result["counts"].get("BROKEN", 0), 1)

    def test_broken_status_bad_args(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "target.py"), "w") as f:
                f.write("def fn(x): return x\n")
            # Valid base64 but the bytes are not valid pickle data.
            bad_b64 = base64.b64encode(b"not valid pickle data").decode()
            rec = {
                "id": "fn#1", "kind": "call", "target": "fn",
                "args_b64": bad_b64,
                "args_repr": "((1,), {})",
                "args_hash": "abc",
                "out": {"kind": "value", "canon": 1, "repr": "1"},
                "source": "fuzz",
                "nondet": False,
            }
            _build_lock(tmpdir, [rec])
            result = core.check(tmpdir)
            self.assertFalse(result["ok"])
            self.assertEqual(result["counts"].get("BROKEN", 0), 1)

    def test_check_writes_last_check_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "target.py"), "w") as f:
                f.write("def fn(x): return x\n")
            rec = _call_record("fn", (7,), {},
                               {"kind": "value", "canon": core.canon(7), "repr": "7"},
                               "fn#1")
            _build_lock(tmpdir, [rec])
            core.check(tmpdir)
            last_check_path = os.path.join(
                tmpdir, core.LOCK_DIR, core.LAST_CHECK_FILE)
            self.assertTrue(os.path.exists(last_check_path))


# ---------------------------------------------------------------------------
# accept()
# ---------------------------------------------------------------------------

class TestAccept(unittest.TestCase):

    def test_blesses_changed_and_appends_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "target.py"), "w") as f:
                f.write("def add(x, y): return x + y\n")
            # Lock says result is 999 — actual is 3.
            rec = _call_record("add", (1, 2), {},
                               {"kind": "value", "canon": core.canon(999), "repr": "999"},
                               "add#1")
            _build_lock(tmpdir, [rec])

            result = core.accept(tmpdir)
            self.assertIn("add#1", result["accepted"])
            self.assertEqual(result["removed"], [])

            new_lock = core.load_lock(tmpdir)
            # New outcome must reflect the current code.
            self.assertEqual(new_lock["records"][0]["out"]["repr"], "3")
            # History entry must have been appended.
            self.assertEqual(len(new_lock["history"]), 1)
            self.assertEqual(new_lock["history"][0]["id"], "add#1")

    def test_removes_gone_and_appends_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "target.py"), "w") as f:
                f.write("def other(): return 1\n")
            rec = _call_record("gone_fn", (), {},
                               {"kind": "value", "canon": core.canon(42), "repr": "42"},
                               "gone_fn#1")
            _build_lock(tmpdir, [rec])

            result = core.accept(tmpdir)
            self.assertIn("gone_fn#1", result["removed"])
            self.assertEqual(result["accepted"], [])

            new_lock = core.load_lock(tmpdir)
            self.assertEqual(len(new_lock["records"]), 0)
            self.assertEqual(len(new_lock["history"]), 1)
            self.assertIn("removed", new_lock["history"][0]["action"])

    def test_accept_specific_id_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "target.py"), "w") as f:
                f.write("def add(x, y): return x + y\n")
            # Two changed records; accept only the first.
            rec1 = _call_record("add", (1, 2), {},
                                {"kind": "value", "canon": core.canon(999), "repr": "999"},
                                "add#1")
            rec2 = _call_record("add", (3, 4), {},
                                {"kind": "value", "canon": core.canon(888), "repr": "888"},
                                "add#2")
            _build_lock(tmpdir, [rec1, rec2])

            result = core.accept(tmpdir, ids=["add#1"])
            self.assertIn("add#1", result["accepted"])
            self.assertNotIn("add#2", result["accepted"])

            new_lock = core.load_lock(tmpdir)
            # add#2 still has the old (wrong) outcome.
            add2 = next(r for r in new_lock["records"] if r["id"] == "add#2")
            self.assertEqual(add2["out"]["repr"], "888")

    def test_no_lockfile_returns_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = core.accept(tmpdir)
            self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
