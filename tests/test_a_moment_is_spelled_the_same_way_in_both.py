"""Two commands ask "from when?" -- this is what makes it one question.

`agentlog since 10m` and `agentwatch --since 10m` want the same thing, from the
same person, about the same logs.  They answered it with two parsers.

They had drifted, and neither side could see it.  `agentwatch` knew four units
and `agentlog` knew three, so the first example in one tool's README was a usage
error in the other -- same install, same logs, five seconds apart.  `agentlog`
also refused `2026-08-03T14:30`, which the other took, so one string typed at
the two commands meant "from half past two" in one and "that is not a date" in
the other.  Nobody decided either of those: minutes went into the live tail
because a live tail is about the last few minutes, and the other tool was not
open at the time.

The help text was a third copy of the same fact and had drifted furthest.
`agentlog --help` offered `3d, 12h, 2w` -- agreeing with its own parser about
the units it had, silent about the one it was missing, and offering `2w` for as
long as it would have taken anybody to drop weeks.  A tool whose help promises a
spelling it does not accept is worse than one that never offered it: the person
who typed it has been told, by the program, that it works.

So the spellings moved into `when.py`, where one table is the parser *and* the
sentence the help prints.  It has to stay copied: nothing in this family imports
outside its own package -- the promise `pip install stillworks` makes, enforced
by `test_every_import_is_stdlib_or_the_packages_own` -- so a shared module is
not on offer.  What is on offer is a copy that cannot drift.

Four things get checked, because pinning the bytes is not enough on its own:

  * the two `when.py` are byte-identical, so a unit added on one side is added
    on both or it is a failing test;
  * both commands actually go through it, because the cheapest way to undo all
    of this is to leave the file sitting there unread;
  * neither command has quietly regrown a table or a regex of its own, which is
    exactly how the first duplication started; and
  * the two help texts say the same sentence -- the one built from the table --
    because the help was the copy that got furthest out of step, and the only
    one a user reads before typing.

What this file does not say is that the parsing is *correct*.  Sameness is not
correctness, and each source repository carries `test_the_moment_you_typed.py`
for that; this one only says there is one answer.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# The two packages that take a moment from whoever is running them, and the
# modules in each that ask for one -- the part that stays two.
#
# `agentdiff` also has a `--since`, and it is deliberately not here: it takes a
# git ref, which is a different question with the same flag name.  Pulling it in
# would make this file about the word rather than about the concept.
THE_CALLERS = {"agentlog": ("window.py", "cli.py"),
               "agentwatch": ("cli.py",)}

# What a caller is allowed to ask the spellings about.  Three names: what a
# moment is, whether it is a length rather than a date, and the one sentence
# describing both.
#
# `HOW_TO_SPELL_IT` is the one that matters most and is the least obvious.  A
# module that only exported the parser would have left every caller free to word
# its own help -- which is what they were doing, and how the parser and the help
# came apart while each stayed internally consistent.
THE_INTERFACE = {"parse_moment", "is_a_length", "HOW_TO_SPELL_IT"}

# Text that only appears in code deciding how a moment is spelled: the
# `timedelta` keywords the units mean, a character class over the unit letters,
# and the tail of the sentence itself.  A caller with any of these has started a
# second copy.
THE_SPELLING_FACTS = ("minutes", "hours", "weeks", "mhdw", "dhw",
                      "or a date like")


def _path(package, module):
    return os.path.join(_ROOT, package, module)


def _source(package, module):
    with open(_path(package, module), encoding="utf-8") as fh:
        return fh.read()


class TestTheyAreOneFile(unittest.TestCase):

    def test_both_packages_carry_it(self):
        missing = [p for p in THE_CALLERS
                   if not os.path.exists(_path(p, "when.py"))]
        self.assertEqual(missing, [],
                         "packages with no when.py: {}".format(missing))

    def test_byte_for_byte(self):
        digests = {}
        for package in THE_CALLERS:
            with open(_path(package, "when.py"), "rb") as fh:
                digests.setdefault(
                    hashlib.sha256(fh.read()).hexdigest(), []).append(package)
        self.assertEqual(
            len(digests), 1,
            "when.py has drifted into {} versions:\n  {}\nA unit added on one "
            "side belongs on both: rsync the source repo you changed, then the "
            "other.\n"
            .format(len(digests),
                    "\n  ".join(sorted(", ".join(sorted(group))
                                       for group in digests.values()))))

    def test_the_interface_is_the_three_names_both_callers_were_promised(self):
        # Read off the copy, not imported, so this says the same thing whether
        # or not the packages are installed.
        tree = ast.parse(_source("agentlog", "when.py"))
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
            "when.py's interface changed.  Adding a name is fine, but it is a "
            "name both commands now have to be understood against -- say so "
            "here.")


class TestBothCommandsGoThroughIt(unittest.TestCase):
    """The file existing is not the same as the file being used."""

    def _imported(self, package, module):
        tree = ast.parse(_source(package, module))
        return {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module == "when"
                for alias in node.names}

    def test_each_caller_asks_the_spellings_module(self):
        for package, modules in sorted(THE_CALLERS.items()):
            for module in modules:
                with self.subTest(package + "/" + module):
                    asked = self._imported(package, module)
                    self.assertTrue(
                        asked, "{}/{} stopped importing from when.py"
                        .format(package, module))
                    self.assertLessEqual(
                        asked, THE_INTERFACE,
                        "{}/{} reaches past the interface into when's "
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
            "when.py offers names no command asks for: {}"
            .format(sorted(THE_INTERFACE - used)))


class TestNeitherCommandKeepsItsOwnCopy(unittest.TestCase):
    """How the first duplication started: one regex, where it was needed."""

    def test_no_caller_declares_a_spelling_fact_of_its_own(self):
        for package, modules in sorted(THE_CALLERS.items()):
            for module in modules:
                with self.subTest(package + "/" + module):
                    source = _source(package, module)
                    tree = ast.parse(source)
                    for node in tree.body:
                        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                            continue
                        text = (ast.get_source_segment(source, node) or "")
                        for fact in THE_SPELLING_FACTS:
                            self.assertNotIn(
                                fact, text,
                                "{}/{} declares {!r} again -- that fact belongs "
                                "in when.py, where the other command can see "
                                "it".format(package, module, fact))

    def test_no_caller_writes_out_the_examples_by_hand(self):
        """The help text was the copy that drifted furthest.

        `3d, 12h, 2w` was a literal in `agentlog`'s help while its parser had
        never learnt minutes.  Nothing about that was detectable from either
        piece of code on its own -- they agreed with each other and disagreed
        with the sibling tool.  So no caller gets to write a list of offsets
        down: the ones printed are the ones the table produced.
        """
        # A comma-separated run of at least two offsets in one string literal.
        # One on its own is prose -- `on 3d` is a genuine example of a different
        # command -- but a list is somebody spelling out the table.
        written_out = re.compile(r"\d+\s*[mhdw]\s*,\s*\d+\s*[mhdw]",
                                 re.IGNORECASE)
        for package, modules in sorted(THE_CALLERS.items()):
            for module in modules:
                with self.subTest(package + "/" + module):
                    for node in ast.walk(ast.parse(_source(package, module))):
                        if not isinstance(node, ast.Constant):
                            continue
                        if not isinstance(node.value, str):
                            continue
                        found = written_out.search(node.value)
                        self.assertIsNone(
                            found, "{}/{} writes the offsets out as {!r} -- "
                            "print HOW_TO_SPELL_IT instead, so the list cannot "
                            "outlive the table".format(
                                package, module,
                                found.group(0) if found else ""))


class TestTheTwoHelpTextsSayTheSameSentence(unittest.TestCase):
    """The only copy of this fact a person reads before typing.

    Structure is checked above; this is the end of it, in the words that
    actually reach the terminal.  Both commands are built and asked what they
    tell people about `since`, and the answer has to be the sentence the table
    produced -- not something equal to it today.
    """

    def _help(self, package):
        # One spells it `build_parser` and the other `_build_parser`, which is
        # a difference between the two command lines and not one this file has
        # an opinion about.
        module = __import__(package + ".cli", fromlist=["cli"])
        build = getattr(module, "build_parser", None) or module._build_parser
        return build().format_help()

    def test_both_offer_the_sentence_the_table_built(self):
        from agentlog.when import HOW_TO_SPELL_IT
        for package in sorted(THE_CALLERS):
            with self.subTest(package):
                self.assertIn(
                    HOW_TO_SPELL_IT, " ".join(self._help(package).split()),
                    "{} --help no longer offers the spellings its parser "
                    "takes".format(package))

    def test_the_sentence_offers_every_unit_and_nothing_else(self):
        from agentlog.when import HOW_TO_SPELL_IT, parse_moment
        offered = re.findall(r"(\d+)\s*([a-z])\b", HOW_TO_SPELL_IT)
        self.assertGreaterEqual(len(offered), 4, HOW_TO_SPELL_IT)
        for number, unit in offered:
            with self.subTest(number + unit):
                # Every example printed is one both parsers take, because they
                # are one parser.
                parse_moment(number + unit)


if __name__ == "__main__":
    unittest.main()
