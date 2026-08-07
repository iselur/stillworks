"""Two commands hit the same wall -- this is what makes it one sentence.

Some log files on disk are not in what you are looking at.  `agentlog` reaches
that situation when it counts, `agentwatch` when it follows, and both have to
say so, because the alternative in either is a screen that reads as a quiet
afternoon.  They said it in two vocabularies:

    agentlog:   note: 2 log files were not counted — could not be read
                      (run with --verbose to see which)

    agentwatch: 2 session logs could not be read — that activity is not shown
                    /home/val/a.jsonl
                    /home/val/b.jsonl

Same fact, from one `pip install`, five seconds apart.  Different noun for the
thing, different verb for what happened to it, different indent, and only one of
them with the word "note" in front -- so the two do not read as one tool having
one problem, they read as two tools with two.

The halves were also split the wrong way round.  `agentlog` knew *why* each file
was skipped -- it has two reasons, a file that will not open and a file that
opens with nothing usable in it -- and would not tell you unless you asked
twice.  `agentwatch` printed the paths and threw the reason away at the
`except OSError:` that produced it, so the tool that showed you which file did
not tell you what to do to it.  A chmod is nearly always what to do to it.

Neither of those is a wording bug.  They are one situation that had no single
place to be described, so each command described the part of it that was in
front of it.  `unusable.py` is that place: the noun, the verb, the plural, how
several reasons read as one clause, when a path is worth printing and how many.
A caller says only how many names it has room for -- `0` for a report that
offers a `--verbose` instead, a few for a live view with a screen to share,
`ALL` for a reader who asked to see them.  That is the one part that genuinely
differs between the two, and it is a number rather than a flag because the three
answers are one question.

It has to stay copied: nothing in this family imports outside its own package --
the promise `pip install stillworks` makes, enforced by
`test_every_import_is_stdlib_or_the_packages_own` -- so a shared module is not
on offer.  What is on offer is a copy that cannot drift.

What this file checks, and why each one is not covered by the ones before it:

  * the two `unusable.py` are byte-identical, so a word changed on one side is
    changed on both or it is a failing test;
  * both commands actually go through it, because the cheapest way to undo all
    of this is to leave the file sitting there unread;
  * neither command has regrown a sentence of its own -- exactly how the first
    duplication started, and it starts again the moment somebody formats a
    count and a noun at a call site;
  * `agentwatch` carries the reason through from where it is known, since a
    caller that drops it there cannot get it back and a shared shape that only
    one side fills in is a shared shape in name; and
  * the sentence comes out of both commands, run for real against a locked file,
    because everything above this line is satisfied by a module nobody prints.

What this file does not say is that either tool notices every unusable log.
Each source repository tests that for itself; this one says there is one answer.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# The two packages that read other people's log files, and the modules in each
# that meet this situation.  Two per package, and they are the same two: the one
# that discovers a file it cannot use, and the one that prints the note.  The
# discovering half -- `agentwatch/follow.py`, `agentlog/parser.py` -- is here
# because that is where the reason is known and nowhere else, and it is the half
# `agentwatch` used to throw away.
THE_CALLERS = {"agentlog": ("cli.py", "parser.py"),
               "agentwatch": ("cli.py", "follow.py")}

# What a caller is allowed to ask for.  Four names: the sentence, the two
# reasons it can carry, and the one that means "name every one of them".
#
# The two reasons are part of the interface and not an implementation detail,
# because a caller is the only thing that knows which of them happened.  They
# are strings and not an enum so that the note can print one straight into a
# sentence without a table of prose sitting somewhere else.
THE_INTERFACE = {"note_about", "UNREADABLE", "NO_RECORDS", "ALL"}

# Words that only appear in code writing this sentence itself.  A caller with
# any of these has started a second copy.
#
# Deliberately not "session log" or "log file": those are the family's ordinary
# vocabulary, in the help text and half the docstrings in both packages, and a
# check that flags them is an allowlist with a test attached.  What is listed is
# the part that is *this note* -- the verb, the two reasons, and the two lines
# that can follow it.
THE_SENTENCE_FACTS = ("not shown", "not counted", "could not be read",
                      "had no readable records", "and {} more", "to see which")


def _path(package, module):
    return os.path.join(_ROOT, package, module)


def _source(package, module):
    with open(_path(package, module), encoding="utf-8") as fh:
        return fh.read()


class TestTheyAreOneFile(unittest.TestCase):

    def test_both_packages_carry_it(self):
        missing = [p for p in THE_CALLERS
                   if not os.path.exists(_path(p, "unusable.py"))]
        self.assertEqual(missing, [],
                         "packages with no unusable.py: {}".format(missing))

    def test_byte_for_byte(self):
        digests = {}
        for package in THE_CALLERS:
            with open(_path(package, "unusable.py"), "rb") as fh:
                digests.setdefault(
                    hashlib.sha256(fh.read()).hexdigest(), []).append(package)
        self.assertEqual(
            len(digests), 1,
            "unusable.py has drifted into {} versions:\n  {}\nA word changed "
            "on one side belongs on both: rsync the source repo you changed, "
            "then the other.\n"
            .format(len(digests),
                    "\n  ".join(sorted(", ".join(sorted(group))
                                       for group in digests.values()))))

    def test_the_interface_is_the_four_names_both_commands_were_promised(self):
        # Read off the copy, not imported, so this says the same thing whether
        # or not the packages are installed.
        tree = ast.parse(_source("agentlog", "unusable.py"))
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
            "unusable.py's interface changed.  Adding a name is fine, but it "
            "is a name both commands now have to be understood against -- say "
            "so here.")


class TestBothCommandsGoThroughIt(unittest.TestCase):
    """The file existing is not the same as the file being used."""

    def _imported(self, package, module):
        tree = ast.parse(_source(package, module))
        return {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module == "unusable"
                for alias in node.names}

    def test_each_caller_asks_the_shared_module(self):
        for package, modules in sorted(THE_CALLERS.items()):
            for module in modules:
                with self.subTest(package + "/" + module):
                    asked = self._imported(package, module)
                    self.assertTrue(
                        asked, "{}/{} stopped importing from unusable.py"
                        .format(package, module))
                    self.assertLessEqual(
                        asked, THE_INTERFACE,
                        "{}/{} reaches past the interface into unusable's "
                        "privates: {}".format(package, module,
                                              sorted(asked - THE_INTERFACE)))

    def test_between_them_they_use_all_of_it(self):
        # A name nothing asks for is a name that is wrong without anyone
        # finding out.
        used = set()
        for package, modules in THE_CALLERS.items():
            for module in modules:
                used |= self._imported(package, module)
        self.assertEqual(
            THE_INTERFACE - used, set(),
            "unusable.py offers names no command asks for: {}"
            .format(sorted(THE_INTERFACE - used)))


class TestNeitherCommandKeepsItsOwnCopy(unittest.TestCase):
    """How the first duplication started: a count and a noun, formatted here."""

    def test_no_caller_words_the_sentence_itself(self):
        for package, modules in sorted(THE_CALLERS.items()):
            for module in modules:
                with self.subTest(package + "/" + module):
                    for node in ast.walk(ast.parse(_source(package, module))):
                        if not isinstance(node, ast.Constant):
                            continue
                        if not isinstance(node.value, str):
                            continue
                        for fact in THE_SENTENCE_FACTS:
                            self.assertNotIn(
                                fact, node.value,
                                "{}/{} words {!r} itself -- that belongs in "
                                "unusable.py, where the other command can see "
                                "it".format(package, module, fact))


class TestTheReasonSurvivesTheExceptThatKnowsIt(unittest.TestCase):
    """The half `agentwatch` used to throw away.

    `except OSError: self._unreadable.add(path)` is a complete and reasonable
    line of code, and it is where the information was lost: at that point the
    program knows the file would not open, and one line later nothing does.  A
    shared shape that one side fills in with paths and no reasons is a shared
    shape in name only, so this is checked rather than assumed.
    """

    def test_the_watcher_hands_back_pairs_and_not_paths(self):
        sys.path.insert(0, _ROOT)
        from agentwatch.follow import Watcher
        from agentwatch.unusable import UNREADABLE

        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        project = os.path.join(home, ".claude", "projects", "p")
        os.makedirs(project)
        locked = os.path.join(project, "locked.jsonl")
        now = datetime.now(timezone.utc).isoformat()
        with open(locked, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user", "timestamp": now,
                                 "sessionId": "s", "cwd": "/p",
                                 "message": {"role": "user",
                                             "content": "hi"}}) + "\n")
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, stat.S_IRUSR | stat.S_IWUSR)
        if os.access(locked, os.R_OK):  # pragma: no cover - running as root
            self.skipTest("chmod 0 does not stop this user reading the file")

        watcher = Watcher(home=home, sources=("claude",), since=None,
                          stale_s=10 ** 9)
        watcher.poll()
        self.assertEqual(watcher.unreadable(), [(locked, UNREADABLE)],
                         "the reason did not survive the except that knows it")


class TestItComesOutOfBothCommands(unittest.TestCase):
    """Everything above is satisfied by a module nobody prints.

    So both commands are run for real, against a directory holding one readable
    log and one whose permissions say no, and the note is read off the screen.
    """

    def setUp(self):
        from agentlog.unusable import UNREADABLE
        self.reason = UNREADABLE
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        project = os.path.join(self.home, ".claude", "projects", "p")
        os.makedirs(project)
        now = datetime.now(timezone.utc).isoformat()

        def write(name, sid):
            path = os.path.join(project, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "user", "timestamp": now,
                                     "sessionId": sid, "cwd": "/p",
                                     "message": {"role": "user",
                                                 "content": "hi"}}) + "\n")
            return path

        write("fine.jsonl", "a")
        self.locked = write("locked.jsonl", "b")
        os.chmod(self.locked, 0)
        self.addCleanup(os.chmod, self.locked, stat.S_IRUSR | stat.S_IWUSR)
        if os.access(self.locked, os.R_OK):  # pragma: no cover - as root
            self.skipTest("chmod 0 does not stop this user reading the file")

    def _run(self, *argv):
        env = dict(os.environ, PYTHONPATH=_ROOT, HOME=self.home,
                   COLUMNS="120", NO_COLOR="1")
        return subprocess.run([sys.executable, "-m"] + list(argv),
                              cwd=_ROOT, env=env, capture_output=True,
                              text=True, timeout=120)

    def test_the_same_headline_comes_out_of_both(self):
        """Not a shared string constant -- the printed screens, compared.

        The headline is everything up to the paths, which is the part that is
        one tool's problem said once.  What follows it differs on purpose: a
        report offers `--verbose`, a live view names a few.
        """
        seen = {}
        for name, argv in (("agentlog", ("agentlog.cli", "today")),
                           ("agentwatch", ("agentwatch.cli", "--once"))):
            result = self._run(*argv)
            screen = result.stdout + result.stderr
            lines = [line.strip() for line in screen.splitlines()
                     if line.strip().startswith("note:")]
            self.assertEqual(
                len(lines), 1,
                "{} printed {} note lines, expected 1:\n{}"
                .format(name, len(lines), screen))
            seen[name] = lines[0]
        self.assertEqual(
            seen["agentlog"], seen["agentwatch"],
            "one situation, two sentences:\n  agentlog:   {}\n  agentwatch: {}"
            .format(seen["agentlog"], seen["agentwatch"]))
        self.assertIn(self.reason, seen["agentlog"],
                      "the note does not say why: {}".format(seen["agentlog"]))

    def test_the_live_view_names_the_file_and_the_report_offers_to(self):
        """The one part that is each tool's own, and it is a number.

        `agentwatch` has a screen and no `--verbose`, so it names what it can.
        `agentlog` has a `--verbose` and a report that is worth re-running, so
        it offers that instead of spending lines on paths nobody asked for.
        """
        watching = self._run("agentwatch.cli", "--once")
        self.assertIn(self.locked, watching.stdout + watching.stderr,
                      "the live view did not name the file")

        report = self._run("agentlog.cli", "today")
        self.assertNotIn(self.locked, report.stdout + report.stderr,
                         "the report named the file without being asked")
        self.assertIn("--verbose", report.stdout + report.stderr,
                      "the report did not say how to see which")

        asked = self._run("agentlog.cli", "today", "--verbose")
        self.assertIn(self.locked, asked.stdout + asked.stderr,
                      "--verbose still did not name the file")


if __name__ == "__main__":
    unittest.main()
