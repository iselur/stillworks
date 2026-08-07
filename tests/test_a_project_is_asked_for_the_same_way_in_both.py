"""Two commands ask "which project?" -- this is what makes it one question.

`agentlog --project relay` and `agentwatch --project relay` want the same thing,
from the same person, about the same logs.  They answered it with two rules.

They had drifted, and neither side could see it.  `agentlog` matched the name or
the path and said so in its help.  `agentwatch` matched the last component of
the path and nothing else, so `agentwatch --project /home/you/relay` printed

    nothing has happened in that window

on a project that had been busy all afternoon.  Same install, same logs, five
seconds apart.  And the wrong answer is the worse shape: it is a sentence about
the *window*, so it sends the reader off to widen `--since`, and they can widen
it as far as they like.  Nothing on screen suggests the flag was the problem.

Nobody decided that.  `agentwatch` follows a live tail, where the column shows a
name and not a path, so a name is what got compared.  The other tool was not
open at the time.  The help text was a third copy of the rule and had drifted
with it -- one command offering a spelling the other would refuse.

So the rule moved into `project.py`, where one function is the matcher *and* the
sentence the help prints.  It has to stay copied: nothing in this family imports
outside its own package -- the promise `pip install stillworks` makes, enforced
by `test_every_import_is_stdlib_or_the_packages_own` -- so a shared module is
not on offer.  What is on offer is a copy that cannot drift.

Five things get checked, because pinning the bytes is not enough on its own:

  * the two `project.py` are byte-identical, so a spelling accepted on one side
    is accepted on both or it is a failing test;
  * both commands actually go through it, because the cheapest way to undo all
    of this is to leave the file sitting there unread;
  * neither command has quietly regrown a comparison of its own, which is
    exactly how the first duplication started;
  * the two help texts say the same sentence -- the one that lives beside the
    rule -- because the help was the copy that drifted, and the only one a
    person reads before typing; and
  * the spellings a reader would expect to work do work, written out as
    examples, so the rule is stated here in cases and not only in prose.

What this file does not say is that the *matching* is right in every corner.
Each source repository carries its own tests for that; this one says there is
one answer.
"""

from __future__ import annotations

import ast
import hashlib
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# The two packages that take a project from whoever is running them, and the
# modules in each that ask for one -- the part that stays two.
#
# `agentdiff`, `unedit` and `stillworks` also have a `--project`, and they are
# deliberately not here: theirs is a directory to work in, which is a different
# question wearing the same flag name.  Pulling them in would make this file
# about the word rather than about the concept.
THE_CALLERS = {"agentlog": ("cli.py",),
               "agentwatch": ("cli.py", "follow.py")}

# What a caller is allowed to ask.  Two names: whether a project is one that was
# asked for, and the one sentence describing when it is.
#
# `HOW_IT_MATCHES` is the one that matters most and is the least obvious.  A
# module that only exported the matcher would have left each command free to
# word its own help -- which is what they were doing, and how the matcher and
# the help came apart while each stayed internally consistent.
THE_INTERFACE = {"matches", "HOW_IT_MATCHES"}

# Text that only appears in code deciding whether a project is the one asked
# for.  A caller with any of these has started a second copy.
THE_MATCHING_FACTS = ("projects whose", "name or path", "rstrip(\"/\")",
                      "rstrip('/')")


def _path(package, module):
    return os.path.join(_ROOT, package, module)


def _source(package, module):
    with open(_path(package, module), encoding="utf-8") as fh:
        return fh.read()


class TestTheyAreOneFile(unittest.TestCase):

    def test_both_packages_carry_it(self):
        missing = [p for p in THE_CALLERS
                   if not os.path.exists(_path(p, "project.py"))]
        self.assertEqual(missing, [],
                         "packages with no project.py: {}".format(missing))

    def test_byte_for_byte(self):
        digests = {}
        for package in THE_CALLERS:
            with open(_path(package, "project.py"), "rb") as fh:
                digests.setdefault(
                    hashlib.sha256(fh.read()).hexdigest(), []).append(package)
        self.assertEqual(
            len(digests), 1,
            "project.py has drifted into {} versions:\n  {}\nA spelling taken "
            "on one side belongs on both: rsync the source repo you changed, "
            "then the other.\n"
            .format(len(digests),
                    "\n  ".join(sorted(", ".join(sorted(group))
                                       for group in digests.values()))))

    def test_the_interface_is_the_two_names_both_callers_were_promised(self):
        # Read off the copy, not imported, so this says the same thing whether
        # or not the packages are installed.
        tree = ast.parse(_source("agentlog", "project.py"))
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
            "project.py's interface changed.  Adding a name is fine, but it is "
            "a name both commands now have to be understood against -- say so "
            "here.")


class TestBothCommandsGoThroughIt(unittest.TestCase):
    """The file existing is not the same as the file being used."""

    def _imported(self, package, module):
        tree = ast.parse(_source(package, module))
        return {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module == "project"
                for alias in node.names}

    def test_each_caller_asks_the_matching_module(self):
        for package, modules in sorted(THE_CALLERS.items()):
            for module in modules:
                with self.subTest(package + "/" + module):
                    asked = self._imported(package, module)
                    self.assertTrue(
                        asked, "{}/{} stopped importing from project.py"
                        .format(package, module))
                    self.assertLessEqual(
                        asked, THE_INTERFACE,
                        "{}/{} reaches past the interface into project's "
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
            "project.py offers names no command asks for: {}"
            .format(sorted(THE_INTERFACE - used)))


class TestNeitherCommandKeepsItsOwnCopy(unittest.TestCase):
    """How the first duplication started: one `in`, where it was needed."""

    def test_no_caller_declares_a_matching_fact_of_its_own(self):
        for package, modules in sorted(THE_CALLERS.items()):
            for module in modules:
                with self.subTest(package + "/" + module):
                    source = _source(package, module)
                    for node in ast.walk(ast.parse(source)):
                        if not isinstance(node, ast.Constant):
                            continue
                        if not isinstance(node.value, str):
                            continue
                        for fact in THE_MATCHING_FACTS:
                            self.assertNotIn(
                                fact, node.value,
                                "{}/{} words {!r} itself -- that belongs in "
                                "project.py, where the other command can see "
                                "it".format(package, module, fact))

    def test_no_caller_compares_a_project_by_hand(self):
        """The shape of the original bug, in one line of code.

        `if self.project and self.project not in name.lower()` was the whole of
        `agentwatch`'s rule.  It reads as housekeeping rather than as a
        decision, which is why it sat there for as long as it did while the
        other command's rule grew a second thing to match on.
        """
        for package, modules in sorted(THE_CALLERS.items()):
            for module in modules:
                with self.subTest(package + "/" + module):
                    source = _source(package, module)
                    for node in ast.walk(ast.parse(source)):
                        if not isinstance(node, ast.Compare):
                            continue
                        if not any(isinstance(op, (ast.In, ast.NotIn))
                                   for op in node.ops):
                            continue
                        text = ast.get_source_segment(source, node) or ""
                        self.assertNotIn(
                            "project", text,
                            "{}/{} decides for itself whether a project "
                            "matches: {!r} -- ask matches() instead"
                            .format(package, module, text))


class TestTheTwoHelpTextsSayTheSameSentence(unittest.TestCase):
    """The only copy of this fact a person reads before typing.

    Structure is checked above; this is the end of it, in the words that
    actually reach the terminal.  Both commands are built and asked what they
    tell people about `--project`, and the answer has to be the sentence that
    lives beside the rule -- not something equal to it today.
    """

    def _help(self, package):
        # One spells it `build_parser` and the other `_build_parser`, which is
        # a difference between the two command lines and not one this file has
        # an opinion about.
        module = __import__(package + ".cli", fromlist=["cli"])
        build = getattr(module, "build_parser", None) or module._build_parser
        return " ".join(build().format_help().split())

    def test_both_offer_the_sentence_that_lives_beside_the_rule(self):
        from agentlog.project import HOW_IT_MATCHES
        for package in sorted(THE_CALLERS):
            with self.subTest(package):
                self.assertIn(
                    HOW_IT_MATCHES, self._help(package),
                    "{} --help no longer describes --project the way it "
                    "actually matches".format(package))

    def test_the_sentence_names_the_two_things_it_matches(self):
        # If the rule widens, this is the assertion that notices the sentence
        # did not.  Both words, because the missing one was the whole bug.
        from agentlog.project import HOW_IT_MATCHES
        self.assertIn("name", HOW_IT_MATCHES)
        self.assertIn("path", HOW_IT_MATCHES)


class TestWhatItSaysYesTo(unittest.TestCase):
    """The rule in cases, so it is stated and not only described.

    Each of these is a thing somebody types.  The path is the one that used to
    work at one command and not the other; the rest are here so that widening
    the rule again cannot quietly stop them working.
    """

    def setUp(self):
        from agentlog.project import matches
        self.matches = matches
        self.relay = ("relay", "/home/you/relay")

    def yes(self, needle):
        self.assertTrue(self.matches(needle, *self.relay),
                        "{!r} no longer finds the project".format(needle))

    def no(self, needle):
        self.assertFalse(self.matches(needle, *self.relay),
                         "{!r} now finds a project it did not name".format(needle))

    def test_the_name(self):
        self.yes("relay")

    def test_part_of_the_name(self):
        self.yes("rel")

    def test_the_whole_path(self):
        self.yes("/home/you/relay")

    def test_a_directory_the_projects_live_in(self):
        self.yes("/home/you")

    def test_the_slash_tab_completion_adds(self):
        self.yes("/home/you/relay/")

    def test_any_case(self):
        self.yes("RELAY")
        self.yes("/Home/You/Relay")

    def test_the_spaces_around_a_quoted_argument(self):
        self.yes("  relay  ")

    def test_nobody_asked(self):
        # The default, and the one that must never be read the other way round:
        # a command that shows nothing until you pass a flag.
        self.yes("")
        self.assertTrue(self.matches(None, *self.relay))

    def test_a_different_project(self):
        self.no("web")

    def test_a_path_that_only_looks_like_it(self):
        self.no("/home/you/relay-ui")

    def test_a_project_the_caller_knows_nothing_about(self):
        # A live tail before the log has said anything: no name, no path.  A
        # filter that was asked for something cannot be satisfied by silence.
        self.assertFalse(self.matches("relay", "", None))


if __name__ == "__main__":
    unittest.main()
