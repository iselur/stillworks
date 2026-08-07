"""One file, one spelling, whichever of the two commands you happened to run.

`agentlog` lists the files a session edited and `agentwatch` names them as they
are written, so the same path goes on a screen in both -- and it went on
differently.  For a file inside the project they agreed by accident.  For one
outside it, which is most of a working day, they did not:

    agentlog:   edited   todo.md
    agentwatch: ✎ ~/notes/todo.md

The first of those is the one that is wrong, and not slightly.  A digest is a
list, and a bare basename in a list is a file you cannot go and look at: two
`cli.py` from two repositories are the same line, printed twice, and `todo.md`
could be anywhere on the disk.  The path was thrown away by the half of the code
that had it.

They also measured differently.  `agentwatch` cut its line by terminal cells,
because the family has `display_width` for exactly that; `agentlog` cut its
names with `len`, so a path with a CJK directory in it counted seventeen and
drew twenty-seven, and left the column it was measured into.  And the digest's
row had no limit at all -- three deep paths ran off the edge of an 80-column
terminal, directly under the project row, which is measured to the cell.

None of that is a formatting bug in either place.  It is one question -- how do
you write a file path for somebody reading a screen -- that had no single place
to be answered, so each command answered the part in front of it.
`which_file.py` is that place: what the reader already knows comes off the front
(the project, or their own home), what is left is what tells the files apart,
and room is taken off the front too, because the end of a path is the file.

It has to stay copied.  Nothing in this family imports outside its own package
-- the promise `pip install stillworks` makes, enforced by
`test_every_import_is_stdlib_or_the_packages_own` -- so a shared module is not
on offer.  What is on offer is a copy that cannot drift.

What this file checks, and why each is not covered by the ones before it:

  * the two `which_file.py` are byte-identical, so a rule changed on one side is
    changed on both or it is a failing test;
  * the interface is the one name, because a second name is a second thing both
    commands have to be understood against;
  * both `render.py` actually go through it -- the cheapest way to undo all of
    this is to leave the file sitting there unread;
  * neither has regrown a shortening of its own, which is exactly how the first
    divergence started and starts again the moment somebody reaches for
    `expanduser` at a call site; and
  * the same file comes out of both commands, run for real against one home,
    because everything above this line is satisfied by a module nobody prints.

What this file does not say is how much room either command gives a path.  That
differs on purpose -- a fixed 80-column digest row shared between three names is
not a live line on whatever terminal you have -- and each repository tests its
own.  This one says there is one spelling.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# The two packages that put a file path on a screen, and the module in each that
# does it.  One apiece: `render.py` is where a path becomes a line in both.
THE_CALLERS = {"agentlog": ("render.py",), "agentwatch": ("render.py",)}

# What a caller is allowed to ask for.  One name, and it should stay one: the
# question "how is this path written" has a single answer, and a second entry
# here would mean the two commands can differ again, with permission.
THE_INTERFACE = {"as_shown"}

# Moves that only belong to code deciding how a path is written.  A `render.py`
# doing any of them has started a second copy of the rule.
#
# `expanduser` is turning a home directory into `~`; `basename` is the fallback
# that lost the path in the first place; a bare `"~"` is the same decision
# spelled by hand.  Deliberately not `os.sep` or `split("/")` -- those are how
# you handle a path at all, they appear in half the family, and a check that
# flags them is an allowlist with a test attached.
THE_SHORTENING_MOVES = ("expanduser", "basename")


def _path(package, module):
    return os.path.join(_ROOT, package, module)


def _source(package, module):
    with open(_path(package, module), encoding="utf-8") as fh:
        return fh.read()


class TestTheyAreOneFile(unittest.TestCase):

    def test_both_packages_carry_it(self):
        missing = [p for p in THE_CALLERS
                   if not os.path.exists(_path(p, "which_file.py"))]
        self.assertEqual(missing, [],
                         "packages with no which_file.py: {}".format(missing))

    def test_byte_for_byte(self):
        digests = {}
        for package in THE_CALLERS:
            with open(_path(package, "which_file.py"), "rb") as fh:
                digests.setdefault(
                    hashlib.sha256(fh.read()).hexdigest(), []).append(package)
        self.assertEqual(
            len(digests), 1,
            "which_file.py has drifted into {} versions:\n  {}\nA rule changed "
            "on one side belongs on both: rsync the source repo you changed, "
            "then the other.\n"
            .format(len(digests),
                    "\n  ".join(sorted(", ".join(sorted(group))
                                       for group in digests.values()))))

    def test_the_interface_is_the_one_name_both_commands_were_promised(self):
        # Read off the copy, not imported, so this says the same thing whether
        # or not the packages are installed.
        tree = ast.parse(_source("agentlog", "which_file.py"))
        public = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    public.add(node.name)
            elif isinstance(node, ast.Assign):
                public |= {t.id for t in node.targets
                           if isinstance(t, ast.Name)
                           and not t.id.startswith("_")}
        self.assertEqual(
            public, THE_INTERFACE,
            "which_file.py's interface changed.  Adding a name is fine, but it "
            "is a name both commands now have to be understood against -- say "
            "so here.")


class TestBothCommandsGoThroughIt(unittest.TestCase):
    """The file existing is not the same as the file being used."""

    def _imported(self, package, module):
        tree = ast.parse(_source(package, module))
        return {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module == "which_file"
                for alias in node.names}

    def test_each_caller_asks_the_shared_module(self):
        for package, modules in sorted(THE_CALLERS.items()):
            for module in modules:
                with self.subTest(package + "/" + module):
                    asked = self._imported(package, module)
                    self.assertTrue(
                        asked, "{}/{} stopped importing from which_file.py"
                        .format(package, module))
                    self.assertLessEqual(
                        asked, THE_INTERFACE,
                        "{}/{} reaches past the interface into which_file's "
                        "privates: {}".format(package, module,
                                              sorted(asked - THE_INTERFACE)))


class TestNeitherCommandKeepsItsOwnCopy(unittest.TestCase):
    """How the divergence started: a prefix stripped at the call site."""

    def test_no_caller_shortens_a_path_itself(self):
        for package, modules in sorted(THE_CALLERS.items()):
            for module in modules:
                with self.subTest(package + "/" + module):
                    source = _source(package, module)
                    tree = ast.parse(source)
                    names = {node.attr for node in ast.walk(tree)
                             if isinstance(node, ast.Attribute)}
                    names |= {node.id for node in ast.walk(tree)
                              if isinstance(node, ast.Name)}
                    for move in THE_SHORTENING_MOVES:
                        self.assertNotIn(
                            move, names,
                            "{}/{} calls {} -- deciding how much of a path to "
                            "show belongs in which_file.py, where the other "
                            "command can see it".format(package, module, move))
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Constant) and node.value == "~":
                            self.fail(
                                "{}/{} spells the home directory out by hand "
                                "-- that decision is which_file.py's"
                                .format(package, module))


def _a_stamp_that_is_recent_and_still_today(minutes_ago=10):
    """A time inside `--since 1h` that `today` also agrees is today.

    A fixture that writes "ten minutes ago" lands in yesterday for the first ten
    minutes of every day, and `agentlog today` then has nothing to print: a
    suite that goes red at 00:03 and green again on its own.  So the clock the
    offsets are taken from is moved forward off midnight instead.
    """
    now = datetime.now().astimezone()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    anchor = max(now, midnight + timedelta(minutes=minutes_ago))
    return anchor - timedelta(minutes=minutes_ago)


class TestTheSameFileComesOutOfBothCommands(unittest.TestCase):
    """Everything above is satisfied by a module nobody prints.

    So both commands are run for real, against one session that edited two
    files -- one inside the project and one outside it, which is the pair that
    used to disagree -- and the names are read off the screens.
    """

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        project = os.path.join(self.home, "work", "myproj")
        os.makedirs(project)
        logs = os.path.join(self.home, ".claude", "projects", "p")
        os.makedirs(logs)

        self.inside = os.path.join(project, "src", "deep", "app.py")
        self.outside = os.path.join(self.home, "notes", "todo.md")

        at = _a_stamp_that_is_recent_and_still_today().astimezone(timezone.utc)

        def edit(path, seconds):
            return {"type": "assistant", "sessionId": "s1", "cwd": project,
                    "timestamp": (at + timedelta(seconds=seconds)).isoformat(),
                    "message": {"role": "assistant", "model": "claude-opus-5",
                                "content": [{"type": "tool_use",
                                             "id": "t{}".format(seconds),
                                             "name": "Write",
                                             "input": {"file_path": path,
                                                       "content": "x"}}]}}

        records = [
            {"type": "user", "sessionId": "s1", "cwd": project,
             "timestamp": at.isoformat(),
             "message": {"role": "user", "content": "go"}},
            edit(self.inside, 1),
            edit(self.outside, 2),
        ]
        with open(os.path.join(logs, "s1.jsonl"), "w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record) + "\n")

    def _run(self, *argv):
        env = dict(os.environ, PYTHONPATH=_ROOT, HOME=self.home,
                   COLUMNS="120", NO_COLOR="1")
        return subprocess.run([sys.executable, "-m"] + list(argv),
                              cwd=_ROOT, env=env, capture_output=True,
                              text=True, timeout=120)

    def _from_the_digest(self):
        """The names on the digest's `edited` row."""
        screen = self._run("agentlog.cli", "today").stdout
        rows = [line for line in screen.splitlines()
                if line.strip().startswith("edited")]
        self.assertEqual(len(rows), 1,
                         "expected one edited row, got:\n{}".format(screen))
        return [name.strip()
                for name in rows[0].split("edited", 1)[1].split(",")]

    def _from_the_live_view(self):
        """The text of each write line, whichever mark this terminal got."""
        screen = self._run("agentwatch.cli", "--once", "--since", "1h").stdout
        out = []
        for line in screen.splitlines():
            for mark in ("✎", " w "):
                if mark in line:
                    out.append(line.split(mark, 1)[1].strip())
                    break
        self.assertTrue(out, "no write lines on:\n{}".format(screen))
        return out

    def test_neither_screen_reduces_a_file_to_its_bare_name(self):
        """The bug, stated as the thing that must not come back.

        Checked before the comparison below, because two commands agreeing on
        `todo.md` would pass that one and still be useless.
        """
        for where, names in (("agentlog", self._from_the_digest()),
                             ("agentwatch", self._from_the_live_view())):
            with self.subTest(where):
                self.assertTrue(
                    [n for n in names if n.endswith("todo.md")],
                    "{} does not mention the file outside the project at all: "
                    "{}".format(where, names))
                self.assertNotIn(
                    "todo.md", names,
                    "{} says just `todo.md` -- which todo.md?".format(where))

    def test_one_file_is_one_spelling(self):
        digest = self._from_the_digest()
        live = self._from_the_live_view()
        self.assertEqual(
            sorted(digest), sorted(live),
            "one session, two spellings:\n  agentlog:   {}\n  agentwatch: {}"
            .format(digest, live))
        # And the spelling is the useful one, not merely a shared mistake.
        self.assertIn("src/deep/app.py", digest)
        self.assertIn("~/notes/todo.md", digest)


if __name__ == "__main__":
    unittest.main()
