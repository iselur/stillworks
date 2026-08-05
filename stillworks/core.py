"""stillworks core: record what code does now, verify it still does it later.

Zero dependencies. Python >= 3.9.

The model is simple:
  * lock  -> run the code (via a script, fuzzing, or shell commands) and record
             every observed input/output pair into .stillworks/lock.json
  * check -> re-run every recorded input against the current code and compare
  * accept -> bless intentional changes into the baseline

Everything here is deterministic on the replay side: no LLM, no network, no
opinion. A record either reproduces or it doesn't.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import importlib
import importlib.util
import io
import json
import math
import os
import pickle
import random
import re
import runpy
import shlex
import subprocess
import sys
import time
import types

SCHEMA_VERSION = 1
LOCK_DIR = ".stillworks"
LOCK_FILE = "lock.json"
LAST_CHECK_FILE = "last-check.json"

_MAX_REPR = 2000
_ADDR_RE = re.compile(r"0x[0-9a-fA-F]{6,}")


# ---------------------------------------------------------------------------
# Canonicalization: turn arbitrary Python values into a stable, JSON-able
# projection so equality is meaningful across processes.
# ---------------------------------------------------------------------------

def safe_repr(value):
    """repr() with memory addresses scrubbed and length capped."""
    try:
        r = repr(value)
    except Exception as exc:  # repr itself can raise
        r = "<unreprable {}: {}>".format(type(value).__name__, exc.__class__.__name__)
    r = _ADDR_RE.sub("0x...", r)
    if len(r) > _MAX_REPR:
        r = r[:_MAX_REPR] + "...<+{} chars>".format(len(r) - _MAX_REPR)
    return r


def canon(value, _depth=0):
    """Stable JSON-able projection of a Python value.

    Falls back to a scrubbed repr for anything it doesn't understand, so two
    equal-but-exotic values still compare equal as long as their reprs do.
    """
    if _depth > 20:
        return {"__deep__": safe_repr(value)}
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return {"__float__": "nan"}
        if math.isinf(value):
            return {"__float__": "inf" if value > 0 else "-inf"}
        return value
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (list, tuple)):
        return {"__seq__": [canon(v, _depth + 1) for v in value],
                "__tuple__": isinstance(value, tuple)}
    if isinstance(value, (set, frozenset)):
        items = [canon(v, _depth + 1) for v in value]
        items.sort(key=lambda x: json.dumps(x, sort_keys=True, default=str))
        return {"__set__": items}
    if isinstance(value, dict):
        pairs = []
        for k, v in value.items():
            pairs.append([canon(k, _depth + 1), canon(v, _depth + 1)])
        pairs.sort(key=lambda kv: json.dumps(kv[0], sort_keys=True, default=str))
        return {"__map__": pairs}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"__obj__": type(value).__name__,
                "fields": canon(dataclasses.asdict(value), _depth + 1)}
    if isinstance(value, _Drained):
        return {"__iter__": [canon(v, _depth + 1) for v in value.items],
                "truncated": value.truncated}
    # Objects with only the default repr would all canon to the same scrubbed
    # '<Foo object at 0x...>' and falsely compare equal; use their attributes.
    if not isinstance(value, type) and type(value).__repr__ is object.__repr__:
        try:
            fields = vars(value)
        except TypeError:
            fields = None
        if fields is not None:
            return {"__obj__": type(value).__name__,
                    "fields": canon(fields, _depth + 1)}
    return {"__repr__": safe_repr(value)}


class _Drained:
    """A lazy iterator's contents, materialized by run_call for comparison."""

    __slots__ = ("items", "truncated")
    _LIMIT = 200

    def __init__(self, items, truncated):
        self.items = items
        self.truncated = truncated

    def __repr__(self):
        suffix = ", ...truncated" if self.truncated else ""
        return "<iterator: {!r}{}>".format(self.items, suffix)


def _is_lazy_iter(value):
    import collections.abc
    return isinstance(value, collections.abc.Iterator) \
        and not isinstance(value, (str, bytes))


def _drain(it):
    items = []
    for v in it:
        items.append(v)
        if len(items) >= _Drained._LIMIT:
            return _Drained(items, truncated=True)
    return _Drained(items, truncated=False)


def canon_hash(value):
    blob = json.dumps(canon(value), sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Argument encoding: pickle so we can replay real objects, base64 so it lives
# in JSON. Unpicklable inputs are skipped (counted, reported).
# ---------------------------------------------------------------------------

def encode_args(args, kwargs):
    """Return base64(pickle((args, kwargs))) or None if unpicklable."""
    try:
        blob = pickle.dumps((args, kwargs), protocol=4)
    except Exception:
        return None
    return base64.b64encode(blob).decode("ascii")


def decode_args(b64):
    blob = base64.b64decode(b64.encode("ascii"))
    return pickle.loads(blob)


# ---------------------------------------------------------------------------
# Running and comparing calls
# ---------------------------------------------------------------------------

def run_call(fn, args, kwargs):
    """Call fn and normalize the outcome to a comparable dict."""
    try:
        result = fn(*args, **kwargs)
        if _is_lazy_iter(result):
            # Generators/iterators would all canon to the same scrubbed repr
            # and falsely compare equal. We own this execution, so it is safe
            # to materialize a bounded prefix and compare the actual items.
            result = _drain(result)
    except Exception as exc:
        return {"kind": "exception", "type": type(exc).__name__,
                "canon": canon(str(exc)), "repr": safe_repr(exc)}
    return {"kind": "value", "canon": canon(result), "repr": safe_repr(result)}


def outcomes_equal(a, b):
    if a.get("kind") != b.get("kind"):
        return False
    if a["kind"] == "exception" and a.get("type") != b.get("type"):
        return False
    return json.dumps(a.get("canon"), sort_keys=True, default=str) == \
           json.dumps(b.get("canon"), sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Module loading: accept either a file path (src/pricing.py) or a dotted
# module name (pkg.pricing). Always load fresh.
# ---------------------------------------------------------------------------

def _exec_source(name, path):
    """Load a module by compiling its source directly.

    Never touches __pycache__: pyc staleness checks compare mtimes with
    one-second granularity, so a file edited within a second of being locked
    would replay against STALE bytecode and report a false pass. Compiling
    from source makes that impossible.
    """
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    mod = types.ModuleType(name)
    mod.__file__ = path
    # Register before exec so pickled objects of its classes resolve.
    sys.modules[name] = mod
    # dont_inherit: without it the user's module would inherit OUR
    # `from __future__ import annotations` compiler flag.
    code = compile(src, path, "exec", dont_inherit=True)
    exec(code, mod.__dict__)
    return mod


def load_module(spec_str, project_root=None):
    root = os.path.abspath(project_root or os.getcwd())
    if root not in sys.path:
        sys.path.insert(0, root)
    importlib.invalidate_caches()
    looks_like_path = spec_str.endswith(".py") or os.sep in spec_str or "/" in spec_str
    if looks_like_path:
        path = os.path.abspath(os.path.join(root, spec_str)) \
            if not os.path.isabs(spec_str) else spec_str
        if not os.path.exists(path):
            raise FileNotFoundError("no such file: {}".format(path))
        name = os.path.splitext(os.path.basename(path))[0]
        mod = _exec_source(name, path)
        return mod, {"module": name, "path": os.path.relpath(path, root)}
    # dotted module name: purge cached copies, resolve to a source file,
    # and load that source fresh (same pyc-staleness reasoning as above).
    for key in [k for k in list(sys.modules) if k == spec_str or k.startswith(spec_str + ".")]:
        del sys.modules[key]
    found = importlib.util.find_spec(spec_str)
    if found is None:
        raise ImportError("module not found: {}".format(spec_str))
    if found.origin and found.origin.endswith(".py"):
        if "." in spec_str:  # ensure parent package exists for relative imports
            importlib.import_module(spec_str.rsplit(".", 1)[0])
        mod = _exec_source(spec_str, found.origin)
    else:
        mod = importlib.import_module(spec_str)
    path = getattr(mod, "__file__", None)
    rel = os.path.relpath(path, root) if path else None
    return mod, {"module": spec_str, "path": rel}


def public_functions(mod):
    """Module-level plain functions defined in this module, non-underscore."""
    out = []
    for name in sorted(dir(mod)):
        if name.startswith("_"):
            continue
        fn = getattr(mod, name)
        if isinstance(fn, types.FunctionType) and fn.__module__ == mod.__name__:
            out.append((name, fn))
    return out


# ---------------------------------------------------------------------------
# Recorder: wrap a module's public functions so real usage (a script run)
# gets captured as it happens. Only top-level calls are recorded — if f calls
# g internally, we record f's behavior, which is what callers depend on.
# ---------------------------------------------------------------------------

class Recorder:
    def __init__(self, mod):
        self.mod = mod
        self.records = []
        self.skipped_unpicklable = 0
        self.skipped_lazy = 0
        self._seen = set()
        self._depth = 0
        self._originals = {}

    def __enter__(self):
        for name, fn in public_functions(self.mod):
            self._originals[name] = fn
            setattr(self.mod, name, self._wrap(name, fn))
        return self

    def __exit__(self, *exc):
        for name, fn in self._originals.items():
            setattr(self.mod, name, fn)
        return False

    def _wrap(self, name, fn):
        recorder = self

        def wrapper(*args, **kwargs):
            if recorder._depth > 0:
                return fn(*args, **kwargs)
            recorder._depth += 1
            try:
                # Encode args BEFORE the call: the function may mutate them.
                args_b64 = encode_args(args, kwargs)
                exc = None
                try:
                    result = fn(*args, **kwargs)
                    outcome = {"kind": "value", "canon": canon(result),
                               "repr": safe_repr(result)}
                except Exception as e:
                    exc = e
                    outcome = {"kind": "exception", "type": type(e).__name__,
                               "canon": canon(str(e)), "repr": safe_repr(e)}
                if exc is None and _is_lazy_iter(result):
                    # We must hand the iterator back to the caller unconsumed,
                    # so there is nothing observable to record here. (Fuzz and
                    # check own their executions and CAN materialize.)
                    recorder.skipped_lazy += 1
                elif args_b64 is None:
                    recorder.skipped_unpicklable += 1
                else:
                    key = (name, canon_hash((list(args), kwargs)))
                    if key not in recorder._seen:
                        recorder._seen.add(key)
                        recorder.records.append({
                            "kind": "call",
                            "target": name,
                            "args_repr": safe_repr((args, kwargs)),
                            "args_b64": args_b64,
                            "args_hash": key[1],
                            "out": outcome,
                            "source": "run",
                        })
                if exc is not None:
                    raise exc  # preserve real program behavior
                return result
            finally:
                recorder._depth -= 1

        wrapper.__name__ = name
        wrapper.__wrapped__ = fn
        return wrapper


# ---------------------------------------------------------------------------
# Fuzz capture: generate seeded inputs from simple annotations.
# ---------------------------------------------------------------------------

_SAMPLES = {
    int: [0, 1, -1, 2, 10, 100, -7, 999999],
    float: [0.0, 1.0, -1.5, 3.14159, 100.5, -0.001],
    str: ["", "a", "hello", "Hello, World!", "0", "-1", "  spaced  ", "unicode: café"],
    bool: [True, False],
    list: [[], [1], [1, 2, 3], ["a", "b"]],
    dict: [{}, {"key": "value"}, {"n": 1}],
}


def _constants_of(fn):
    """Literal constants used inside fn's bytecode, grouped by type.

    Branch conditions like `if tier == "GOLD"` compare against literals; a
    fuzzer that never tries "GOLD" never covers that branch. Mining the
    function's own constants makes the samples branch-aware for free.
    """
    pools = {int: [], float: [], str: []}

    def walk(code):
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                walk(const)
            elif isinstance(const, bool):
                continue  # bool is int; skip, True/False already sampled
            elif isinstance(const, str) and 0 < len(const) <= 100:
                pools[str].append(const)
            elif isinstance(const, int) and abs(const) < 10**9:
                pools[int].append(const)
            elif isinstance(const, float) and math.isfinite(const):
                pools[float].append(const)

    try:
        walk(fn.__code__)
    except Exception:
        pass
    return {t: sorted(set(v), key=repr) for t, v in pools.items() if v}


def _base_type(annotation):
    if annotation in _SAMPLES:
        return annotation
    origin = getattr(annotation, "__origin__", None)  # typing.List[int] etc.
    return origin if origin in _SAMPLES else None


def _default_for(base):
    return {int: 1, float: 1.0, str: "a", bool: True,
            list: [1], dict: {"key": "value"}}[base]


def _sample_for(annotation, rng, constants=None):
    base = _base_type(annotation)
    if base is None:
        return None  # unknown annotation
    pool = list(_SAMPLES[base])
    if constants and base in constants:
        pool = pool + list(constants[base]) * 2
    if base is float and constants and int in constants:
        pool = pool + [float(i) for i in constants[int]]
    return rng.choice(pool)


def _sweep_candidates(annotations, constants):
    """One arg tuple per mined literal, so every branch constant is exercised
    at least once: literal at its parameter, plain defaults elsewhere."""
    bases = [_base_type(a) for a in annotations]
    defaults = [_default_for(b) for b in bases]
    combos = []
    for j, base in enumerate(bases):
        values = list(constants.get(base, []))
        if base is float:
            values += [float(i) for i in constants.get(int, [])]
        for v in values:
            args = list(defaults)
            args[j] = v
            combos.append(tuple(args))
    return combos


def fuzz_function(name, fn, rng, per_function):
    """Generate inputs for fn from its annotations; return records.

    Exceptions are recorded as behavior, not failures: if divide(1, 0) raises
    ZeroDivisionError today, the lock says so, and a refactor that silently
    returns 0 instead is a CHANGE.
    """
    import inspect
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return [], 0
    # A required keyword-only param means every positional-only call we could
    # generate raises TypeError — recording those would lock in noise.
    for p in sig.parameters.values():
        if p.kind == p.KEYWORD_ONLY and p.default is inspect.Parameter.empty:
            return [], 0
    params = [p for p in sig.parameters.values()
              if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    if not params:
        return [], 0
    # Modules using `from __future__ import annotations` give string
    # annotations; resolve them to real types.
    hints = {}
    try:
        import typing
        hints = typing.get_type_hints(fn)
    except Exception:
        pass
    annotations = []
    for p in params:
        ann = hints.get(p.name, p.annotation)
        if ann is inspect.Parameter.empty or isinstance(ann, str):
            return [], 0  # can't guess safely without a resolvable annotation
        if _sample_for(ann, random.Random(0)) is None:
            return [], 0  # a param type we can't generate values for
        annotations.append(ann)
    constants = _constants_of(fn)
    records, seen, skipped = [], set(), 0

    def record_one(args):
        h = canon_hash((list(args), {}))
        if h in seen:
            return False
        seen.add(h)
        args_b64 = encode_args(args, {})
        if args_b64 is None:
            return False  # counted by caller
        outcome = run_call(fn, args, {})
        records.append({
            "kind": "call",
            "target": name,
            "args_repr": safe_repr((args, {})),
            "args_b64": args_b64,
            "args_hash": h,
            "out": outcome,
            "source": "fuzz",
        })
        return True

    # Sweep first: every literal the function compares against gets tried,
    # guaranteeing branch constants like "GOLD" are covered (capped for
    # pathological constant counts).
    for args in _sweep_candidates(annotations, constants)[:per_function * 5]:
        record_one(args)
    # Then random fill up to the requested budget.
    budget = max(per_function, len(records))
    attempts = 0
    while len(records) < budget and attempts < per_function * 10:
        attempts += 1
        args = tuple(_sample_for(a, rng, constants) for a in annotations)
        record_one(args)
    return records, skipped


# ---------------------------------------------------------------------------
# Command capture: universal, works for any language. Record exit code,
# stdout, stderr of a shell command; replay by running it again.
# ---------------------------------------------------------------------------

DEFAULT_CMD_TIMEOUT = 120


def run_cmd(cmd, cwd=None, timeout=DEFAULT_CMD_TIMEOUT):
    """Record one command's exit code, stdout and stderr.

    Every way a command can fail to even start is recorded as a result rather
    than raised: a baseline that captures "this command does not exist here" is
    more useful than a stack trace, and it stays comparable on the next run.
    """
    try:
        argv = shlex.split(cmd or "")
    except ValueError as exc:
        return {"exit": -1, "stdout": "",
                "stderr": "<stillworks: cannot read command: {}>".format(exc)}
    if not argv:
        return {"exit": -1, "stdout": "",
                "stderr": "<stillworks: empty command>"}
    try:
        proc = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True,
            # A lockfile is carried: recorded on a laptop, replayed in CI.
            # `text=True` alone would let each machine's locale pick the codec,
            # so the same command records differently in a container with no
            # `LANG` and `check` reports a change that never happened.  UTF-8
            # is what compilers and test runners emit whatever the locale says;
            # `replace` because output we cannot decode is still a recording,
            # and it is at least the same one on every machine.
            encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        return {"exit": proc.returncode,
                "stdout": _scrub_output(proc.stdout),
                "stderr": _scrub_output(proc.stderr)}
    except subprocess.TimeoutExpired:
        return {"exit": -1, "stdout": "", "stderr": "<stillworks: timeout after {}s>".format(timeout)}
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
        return {"exit": -1, "stdout": "", "stderr": "<stillworks: {}>".format(exc)}


def _scrub_output(text):
    if text is None:
        return ""
    text = _ADDR_RE.sub("0x...", text)
    if len(text) > 20000:
        text = text[:20000] + "\n...<truncated by stillworks>"
    return text


def cmd_outcomes_equal(a, b):
    return a.get("exit") == b.get("exit") and a.get("stdout") == b.get("stdout") \
        and a.get("stderr") == b.get("stderr")


# ---------------------------------------------------------------------------
# Lockfile I/O
# ---------------------------------------------------------------------------

def lock_path(project_dir):
    return os.path.join(project_dir, LOCK_DIR, LOCK_FILE)


class LockfileError(Exception):
    """The lockfile is there, and cannot be used.

    Separate from "there is no lockfile" on purpose.  The caller turns this
    into a sentence; the point of the type is that it cannot be mistaken for
    the empty case on the way up.
    """


def load_lock(project_dir):
    """The recorded baseline, or None if this project has never been locked.

    None means *no lockfile*, and nothing else.  A merge conflict left in the
    middle of one, a `lock` that ran out of disk halfway, a path that turned
    out to be a directory — none of those are the empty case, and answering
    them with it sends people to lock a project that is already locked.

    `lock.json` is meant to be committed, which is what makes `check` work in
    a reviewer's checkout.  A file that ships in a repo gets merged, so
    `<<<<<<< HEAD` in the middle of this one is an ordinary Tuesday rather
    than a hostile input.  Before this it was twenty lines of interpreter
    internals ending in `Expecting value: line 1 column 1`, which names a
    column in a file it does not name.
    """
    path = lock_path(project_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            lock = json.load(f)
    except ValueError as exc:           # JSONDecodeError is a ValueError
        raise LockfileError(
            "{}: not readable as JSON — {}".format(path, exc)) from exc
    except OSError as exc:
        raise LockfileError(
            "{}: {}".format(path, exc.strerror or exc)) from exc
    # Parsing is not the same as being a lockfile.  `null` is the one that
    # matters: it decodes cleanly and would have come straight back here as
    # None, which is how a project that was never locked answers.
    if not isinstance(lock, dict) or not isinstance(lock.get("records"), list):
        raise LockfileError(
            "{}: readable, but not a stillworks lockfile".format(path))
    return lock


def save_lock(project_dir, lock):
    """Write the lockfile, atomically.

    Raises OSError if the project directory will not take it — a read-only CI
    checkout is a normal thing to be pointed at, and the caller turns that into
    a sentence rather than a traceback.
    """
    d = os.path.join(project_dir, LOCK_DIR)
    os.makedirs(d, exist_ok=True)
    path = lock_path(project_dir)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lock, f, indent=1, sort_keys=False, default=str)
            f.write("\n")
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)      # never leave a half-written .tmp behind
        except OSError:
            pass
        raise


def new_lock(module_info, seed):
    return {
        "schema": SCHEMA_VERSION,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tool": "stillworks",
        "module": module_info,
        "seed": seed,
        "records": [],
        "history": [],
        # Why the recording run stopped short, if it did.  Always present, so
        # that "" is a statement — this baseline is everything it was meant to
        # be — rather than a key nobody got round to writing.  Lockfiles from
        # before this field are read as unknown and say nothing, which is the
        # honest answer for them.
        "partial": "",
    }


def assign_ids(records):
    counters = {}
    for rec in records:
        base = rec["target"] if rec["kind"] == "call" else "cmd"
        counters[base] = counters.get(base, 0) + 1
        rec["id"] = "{}#{}".format(base, counters[base])
    return records


# ---------------------------------------------------------------------------
# Determinism guard: replay each record immediately; if the second run
# disagrees with the first, the behavior is nondeterministic and the record
# is excluded from gating (kept, flagged, reported).
# ---------------------------------------------------------------------------

def mark_nondeterministic(records, mod, project_dir=None):
    fns = dict(public_functions(mod)) if mod is not None else {}
    for rec in records:
        if rec["kind"] == "cmd":
            # Replay in the project dir — the same cwd `check` will use.
            # Anything else (e.g. the caller's cwd when --project is remote)
            # would flag every path-dependent command as falsely nondet.
            second = run_cmd(rec["cmd"], cwd=project_dir)
            rec["nondet"] = not cmd_outcomes_equal(rec["out"], second)
            continue
        fn = fns.get(rec["target"])
        if fn is None:
            rec["nondet"] = True
            continue
        try:
            args, kwargs = decode_args(rec["args_b64"])
        except Exception:
            rec["nondet"] = True
            continue
        second = run_call(fn, args, kwargs)
        rec["nondet"] = not outcomes_equal(rec["out"], second)
    return records


# ---------------------------------------------------------------------------
# Lock: run the code and write down what it did.
# ---------------------------------------------------------------------------

def lock(project_dir, target=None, run=None, script_args=None, fuzz=0,
         seed=None, cmds=(), timeout=None, max_records=None):
    """Record current behavior into a lockfile.  Returns a result dict.

    The same shape `check` hands back, for the same reason: a caller renders
    it, and never has to know how any of it was found out.  Three things it
    reports that used to be printed from inside the run --

      * `error`   -- nothing was recorded and nothing was written;
      * `notes`   -- something worth saying that is not the answer, in the
                     order it happened;
      * `partial` -- the recording run stopped short, which also goes into the
                     lockfile because a lockfile outlives its terminal.

    Nothing here is printed and nothing here is a flag name this module chose:
    the wording is the tool's, the streams are the caller's.
    """
    notes = []
    records = []
    module_info = None
    mod = None
    skipped = 0
    partial = ""

    if timeout is not None and timeout <= 0:
        return {"error": "--timeout must be greater than zero (got {})"
                         .format(timeout)}
    if os.path.exists(project_dir) and not os.path.isdir(project_dir):
        return {"error": "--project must be a directory, and {} is a file"
                         .format(project_dir)}
    if not target and not cmds:
        return {"error": "nothing to lock — give a TARGET module/file, or --cmd"}
    if run and not target:
        return {"error": "--run needs a TARGET module or file to record calls "
                         "into, e.g.: stillworks lock src/mod.py --run "
                         "scripts/daily.py"}
    if fuzz and not target:
        return {"error": "--fuzz needs a TARGET module or file, "
                         "e.g.: stillworks lock src/mod.py --fuzz 8"}

    if target:
        try:
            mod, module_info = load_module(target, project_dir)
        except Exception as exc:
            return {"error": "could not load {}: {}".format(target, exc)}

    if run and mod is not None:
        script = os.path.abspath(run)
        if not os.path.exists(script):
            return {"error": "no such script: {}".format(script)}
        with Recorder(mod) as rec:
            old_argv = sys.argv
            sys.argv = [script] + list(script_args or [])
            try:
                runpy.run_path(script, run_name="__main__")
            except SystemExit as exc:
                # A nonzero exit is how a script says it failed — an argparse
                # error, a `sys.exit(main())`, a test runner.  Swallowing it
                # made a driver that died after one of its ten calls print
                # exactly what one that ran to the end prints, on exit 0.
                if exc.code not in (0, None):
                    partial = "the recording run did not finish: the script " \
                              "exited {}".format(exc.code)
            except Exception as exc:
                partial = "the recording run did not finish: the script " \
                          "raised {}: {}".format(type(exc).__name__, exc)
            finally:
                sys.argv = old_argv
        records.extend(rec.records)
        skipped += rec.skipped_unpicklable
        if partial:
            notes.append(
                "{}\n"
                "  (the {} call(s) recorded before that are kept — whatever "
                "the script would\n   have exercised afterwards is not in "
                "this baseline)".format(partial, len(rec.records)))

    if fuzz and mod is not None:
        rng = random.Random(seed)
        per_fn = max(1, fuzz)
        fuzz_empty = []
        for name, fn in public_functions(mod):
            recs, sk = fuzz_function(name, fn, rng, per_fn)
            if not recs:
                fuzz_empty.append(name)
            records.extend(recs)
            skipped += sk
        if fuzz_empty:
            notes.append(
                "could not generate inputs for: {}\n"
                "  (--fuzz needs positional parameters annotated with "
                "int/float/str/bool/list/dict\n   and no required "
                "keyword-only parameters — capture these with --run or --cmd)"
                .format(", ".join(fuzz_empty)))

    for c in (cmds or []):
        out = run_cmd(c, cwd=project_dir,
                      timeout=timeout or DEFAULT_CMD_TIMEOUT)
        records.append({"kind": "cmd", "cmd": c, "out": out, "source": "cmd"})

    if not records:
        hint = ""
        if target and not run and not fuzz:
            hint = " (try --fuzz 8, or --run your_script.py; fuzzing needs " \
                   "type annotations on function parameters)"
        return {"error": "no behavior captured{}".format(hint)}

    if max_records and len(records) > max_records:
        records = records[:max_records]

    assign_ids(records)
    # Determinism guard: replay each record once; flag flaky ones.
    mark_nondeterministic(records, mod, project_dir)

    # `lock` is the way out of a damaged lockfile, so it must not be blocked by
    # one.  It only reads the old file to say what it is about to replace.
    try:
        existing = load_lock(project_dir)
    except LockfileError as exc:
        existing = None
        notes.append("replacing a lockfile that could not be read\n"
                     "  {}".format(exc))
    if existing is not None:
        n_hist = len(existing.get("history") or [])
        notes.append(
            "replacing existing lockfile ({} records{})\n"
            "  (to capture several modes in one baseline, combine them in a "
            "single lock command)".format(
                len(existing.get("records") or []),
                ", {} accepted changes".format(n_hist) if n_hist else ""))

    new = new_lock(module_info, seed)
    new["records"] = records
    new["partial"] = partial
    try:
        save_lock(project_dir, new)
    except OSError as exc:
        return {"error": "could not write the lockfile into {}\n"
                         "  {}\n"
                         "  stillworks needs to create a {} directory in the "
                         "project it locks.".format(
                             os.path.join(project_dir, LOCK_DIR), exc, LOCK_DIR),
                "notes": notes}

    return {
        "path": lock_path(project_dir),
        "records": len(records),
        "calls": sum(1 for r in records if r["kind"] == "call"),
        "cmds": sum(1 for r in records if r["kind"] == "cmd"),
        "nondet": sum(1 for r in records if r.get("nondet")),
        "skipped": skipped,
        "partial": partial,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Status: what the lockfile on disk says about itself.
# ---------------------------------------------------------------------------

def status(project_dir):
    """What is on disk, counted.  Returns a result dict.

    `none` is not an error: a project with no lockfile is the ordinary state of
    a project nobody has locked yet, and the answer to "what is here" is
    "nothing yet", on exit 0.
    """
    lock_file = load_lock(project_dir)
    if lock_file is None:
        return {"none": os.path.join(project_dir, LOCK_DIR)}
    records = lock_file["records"]
    module = lock_file.get("module") or {}
    return {
        "records": len(records),
        "created": lock_file["created"],
        "module": module.get("path") or module.get("module") or "",
        "nondet": sum(1 for r in records if r.get("nondet")),
        "history": len(lock_file.get("history") or []),
        "partial": lock_file.get("partial") or "",
    }


# ---------------------------------------------------------------------------
# Check: replay everything against current code.
# ---------------------------------------------------------------------------

def check(project_dir):
    """Replay all records. Returns a result dict; never raises for behavior
    differences (that's what statuses are for)."""
    lock = load_lock(project_dir)
    if lock is None:
        return {"error": "no lockfile — run `stillworks lock` first"}

    results = []
    mod = None
    mod_error = None
    has_calls = any(r["kind"] == "call" for r in lock["records"])
    if has_calls and lock.get("module"):
        spec = lock["module"].get("path") or lock["module"].get("module")
        try:
            # Load module FIRST: unpickling args may need its classes.
            mod, _ = load_module(spec, project_dir)
        except Exception as exc:
            mod_error = "{}: {}".format(type(exc).__name__, exc)

    fns = dict(public_functions(mod)) if mod else {}

    for rec in lock["records"]:
        entry = {"id": rec["id"], "kind": rec["kind"],
                 "target": rec.get("target") or rec.get("cmd")}
        if rec.get("nondet"):
            entry["status"] = "SKIP"
            entry["note"] = "nondeterministic at lock time"
            results.append(entry)
            continue
        if rec["kind"] == "cmd":
            now = run_cmd(rec["cmd"], cwd=project_dir)
            if cmd_outcomes_equal(rec["out"], now):
                entry["status"] = "OK"
            else:
                entry["status"] = "CHANGED"
                entry["was"] = rec["out"]
                entry["now"] = now
            results.append(entry)
            continue
        # call record
        if mod_error:
            entry["status"] = "BROKEN"
            entry["note"] = "module failed to load: {}".format(mod_error)
            results.append(entry)
            continue
        fn = fns.get(rec["target"])
        if fn is None:
            entry["status"] = "GONE"
            entry["note"] = "function no longer exists (or is no longer public)"
            results.append(entry)
            continue
        try:
            args, kwargs = decode_args(rec["args_b64"])
        except Exception as exc:
            entry["status"] = "BROKEN"
            entry["note"] = "recorded args no longer decodable: {}".format(
                type(exc).__name__)
            results.append(entry)
            continue
        now = run_call(fn, args, kwargs)
        if outcomes_equal(rec["out"], now):
            entry["status"] = "OK"
        else:
            entry["status"] = "CHANGED"
            entry["args"] = rec["args_repr"]
            entry["was"] = rec["out"]
            entry["now"] = now
        results.append(entry)

    counts = {}
    for e in results:
        counts[e["status"]] = counts.get(e["status"], 0) + 1

    # How many records this run actually compared against the code.
    #
    # SKIP is the one status that is not a verdict: the record was flagged
    # nondeterministic at lock time and never replayed.  Every other status —
    # including GONE and BROKEN — is something this run went and found out.
    #
    # `ok` used to ask only whether anything had CHANGED, GONE or BROKEN, which
    # is true of a run that compared nothing at all.  A module of clock- and
    # RNG-reading functions locks entirely to SKIP, and `check` then said STILL
    # WORKS about it, on exit 0, forever — including after the module had been
    # rewritten so that one function raised and the other returned a string.
    # A gate that cannot go red is not a gate, and this one said so in green.
    #
    # `lock` already refuses to write an empty lockfile rather than leave a
    # check that passes by having nothing to check.  A lockfile whose records
    # are all excluded is that same emptiness arriving later, so it gets the
    # same answer.  One verified record is a real check and keeps its verdict.
    verified = len(results) - counts.get("SKIP", 0)
    ok = verified > 0 \
        and counts.get("CHANGED", 0) == 0 and counts.get("GONE", 0) == 0 \
        and counts.get("BROKEN", 0) == 0
    out = {"ok": ok, "verified": verified, "counts": counts, "results": results,
           # Carried through from the lockfile rather than re-derived, because
           # it is a fact about how the baseline was made and this run cannot
           # see it any other way.  It does not change `ok`: every record here
           # was really replayed, so the verdict is true — only narrower than
           # it was meant to be, and that is what gets said out loud.
           "partial": lock.get("partial") or "",
           "checked": time.strftime("%Y-%m-%dT%H:%M:%S")}
    # Persist a receipt of this run, for `accept` and `report` to read.
    #
    # This is bookkeeping; the comparison above is the verdict.  A read-only
    # `.stillworks` — a CI checkout, a mounted volume, a directory owned by
    # somebody else — used to make this raise, and the traceback escaped as
    # exit 1, which is this tool's word for BEHAVIOR CHANGED.  A project whose
    # behavior had not moved failed the gate on a filesystem permission.  So
    # the failure is recorded and handed back for the caller to say out loud,
    # and `ok` stays whatever the records actually showed.
    d = os.path.join(project_dir, LOCK_DIR)
    try:
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, LAST_CHECK_FILE)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, default=str)
    except OSError as exc:
        out["not_saved"] = "could not save the check result to {}: {}".format(
            os.path.join(d, LAST_CHECK_FILE), exc)
    return out


# ---------------------------------------------------------------------------
# Accept: bless current behavior for changed records.
# ---------------------------------------------------------------------------

def accept(project_dir, ids=None):
    """Re-run changed/gone records and write current behavior into the lock.

    ids=None means accept everything that differs. GONE records are removed
    from the lock when accepted (the function is gone on purpose).
    """
    lock = load_lock(project_dir)
    if lock is None:
        return {"error": "no lockfile"}
    chk = check(project_dir)
    if "error" in chk:
        return chk
    by_id = {e["id"]: e for e in chk["results"]}
    if ids:
        unknown = sorted(set(ids) - set(by_id))
        if unknown:
            return {"error": "no such record id(s): {} (see `stillworks status` "
                             "for valid ids)".format(", ".join(unknown))}
    wanted = set(ids) if ids else {
        e["id"] for e in chk["results"] if e["status"] in ("CHANGED", "GONE")}
    accepted, removed = [], []
    new_records = []
    for rec in lock["records"]:
        entry = by_id.get(rec["id"])
        if entry is None or rec["id"] not in wanted:
            new_records.append(rec)
            continue
        if entry["status"] == "GONE":
            removed.append(rec["id"])
            lock["history"].append({
                "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "id": rec["id"], "action": "removed (function gone)",
            })
            continue
        if entry["status"] == "CHANGED":
            lock["history"].append({
                "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "id": rec["id"], "action": "accepted change",
                "was": rec["out"], "now": entry["now"],
            })
            rec["out"] = entry["now"]
            accepted.append(rec["id"])
        new_records.append(rec)
    lock["records"] = new_records
    # This command exists only to write the lockfile.  If the write does not
    # land nothing was accepted, so a caller that said "accepted new behavior"
    # on the strength of the list above would be telling a straight lie: the
    # next `check` still fails and the baseline on disk is still the old one.
    # Handed back rather than raised, so it arrives the same way every other
    # refusal in this module does.
    try:
        save_lock(project_dir, lock)
    except OSError as exc:
        return {"error": "could not update the baseline in {}: {}".format(
            os.path.join(project_dir, LOCK_DIR), exc)}
    return {"accepted": accepted, "removed": removed}
