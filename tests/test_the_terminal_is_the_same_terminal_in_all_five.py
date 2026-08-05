"""Five tools print text they did not write.  This is what makes it one seam.

A filename, a command, a commit subject, a snapshot message, a locked target --
every one of these tools puts text on a terminal that came from somewhere else,
and in four of the five it came out of a file the thing being audited is free to
rewrite.  That is one place: where text from outside meets a terminal.  Five
packages had found it separately.

All five knew what the danger was.  Four of them wrote out the same four Unicode
categories -- `Cc`, where the escapes live; `Cf`, where the bidi overrides live;
`Zl` and `Zp`, the two separators that end a line for a reader and not for
`str.splitlines`.  Then they drifted, as copies do, and by the time this test was
written no two of the five agreed on what to *do* about it:

    agentdiff   escaped the character and quoted the string
    agentwatch  turned it into a space
    unedit      turned it into a space
    agentlog    deleted it
    stillworks  split on the line breakers and passed everything else through

The last two are wrong, and each was wrong in a way the others had already found
out about.  Deleting fuses two components of a path into one word that is not on
disk -- agentdiff had hit that, written a paragraph about it, and neither of the
other three ever heard.  Deleting also moves a column: agentlog measured the
width of a value, gave it that many cells, and then removed a character from it,
so a row holding one stood a cell to the left of every other row in the table.
And splitting on line breakers alone let `\\x1b[2J` through untouched, so a
target name in a lockfile could clear the screen of the person reading `check`
to decide whether the lock still held.

So the fact lives in `terminal.py` once, and the answers to it are named
functions with the reason for each written down.  Choosing between them stays in
the tool, because which answer a particular column wants is a fact about that
column.  It has to stay copied rather than imported: nothing in this family
imports another package -- the promise `pip install stillworks` makes, enforced
by `test_every_import_is_stdlib_or_the_packages_own`.  What is on offer is a
copy that cannot drift.

Four things get checked, because pinning the bytes is not enough on its own:

  * the five `terminal.py` are byte-identical, so a fix made in one is a fix in
    all five or it is a failing test;
  * every package that prints actually goes through it, since the cheapest way
    to undo all of this is to leave the file sitting there unread;
  * between them they use all six names, because a name nothing asks for is a
    name that can be wrong without anyone finding out; and
  * no package has quietly grown its own copy of the fact back -- which is
    exactly how the first five started, one category tuple at a time, in the
    file that needed it, at the moment it was needed.

What this file does not say is that the answers are *right*.  Sameness is not
correctness.  `test_printing_what_an_agent_wrote.py` is the same file in all
five and says what the answers should be; this one only says there is one set.
"""

from __future__ import annotations

import ast
import hashlib
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# Every package in the family prints text it did not write, so every package is
# here.  A sixth would be added by copying the file in, not by deciding it is
# somehow exempt.
THE_PRINTERS = ("agentdiff", "agentlog", "agentwatch", "stillworks", "unedit")

# What a tool is allowed to ask the terminal about.  Three of these are answers
# to the same question -- blank it, blank it but keep the lines, blank it and
# bound it -- and the fourth is the other answer, escape it and say so.  Which
# one a column wants is that column's business; a seventh name here is a
# decision about the seam rather than something that accumulated.
THE_INTERFACE = {
    "block",
    "display_width",
    "one_line",
    "pad",
    "quoted",
    "row",
}

# Text that only appears in code deciding for itself what a terminal does with a
# character: the four categories, the two widths a character can be drawn in, and
# the escape table.  A printing module with any of these has started a sixth copy.
THE_TERMINAL_FACTS = ("Cc", "Cf", "Zl", "Zp", "Mn", "Me", "east_asian_width")


def _modules(package):
    """Every module in the package except the one this is all about."""
    directory = os.path.join(_ROOT, package)
    return sorted(name for name in os.listdir(directory)
                  if name.endswith(".py") and name != "terminal.py")


def _source(package, module):
    with open(os.path.join(_ROOT, package, module), encoding="utf-8") as fh:
        return fh.read()


def _asked_for(package, module):
    """The names this module imports from `terminal`, aliases resolved."""
    tree = ast.parse(_source(package, module))
    return {alias.name for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "terminal"
            for alias in node.names}


class TestTheyAreOneFile(unittest.TestCase):

    def test_every_package_carries_it(self):
        missing = [p for p in THE_PRINTERS
                   if not os.path.exists(os.path.join(_ROOT, p, "terminal.py"))]
        self.assertEqual(missing, [],
                         "packages with no terminal.py: {}".format(missing))

    def test_byte_for_byte(self):
        digests = {}
        for package in THE_PRINTERS:
            with open(os.path.join(_ROOT, package, "terminal.py"), "rb") as fh:
                digests.setdefault(
                    hashlib.sha256(fh.read()).hexdigest(), []).append(package)
        self.assertEqual(
            len(digests), 1,
            "terminal.py has drifted into {} versions:\n  {}\nA fix belongs in "
            "all five: rsync the source repo you fixed, then the others.\n"
            .format(len(digests),
                    "\n  ".join(sorted(", ".join(sorted(group))
                                       for group in digests.values()))))

    def test_the_interface_is_the_six_names_every_tool_was_promised(self):
        # Read off the copy rather than imported, so this says the same thing
        # whether or not the packages are installed.
        tree = ast.parse(_source("stillworks", "terminal.py"))
        public = {node.name for node in tree.body
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and not node.name.startswith("_")}
        self.assertEqual(
            public, THE_INTERFACE,
            "terminal.py's interface changed.  Adding a name is fine, but it is "
            "a name all five tools now have to be understood against -- say so "
            "here, and say which of the answers it is.")


class TestEveryPrinterGoesThroughIt(unittest.TestCase):
    """The file existing is not the same as the file being used."""

    def test_each_package_asks_the_terminal_module(self):
        for package in THE_PRINTERS:
            with self.subTest(package):
                asked = set()
                for module in _modules(package):
                    asked |= _asked_for(package, module)
                self.assertTrue(
                    asked, "{} stopped importing from terminal.py -- it prints "
                    "text it did not write, so it has to go through the seam"
                    .format(package))
                self.assertLessEqual(
                    asked, THE_INTERFACE,
                    "{} reaches past the interface into terminal's privates: {}"
                    .format(package, sorted(asked - THE_INTERFACE)))

    def test_between_them_they_use_all_of_it(self):
        used = set()
        for package in THE_PRINTERS:
            for module in _modules(package):
                used |= _asked_for(package, module)
        self.assertEqual(
            THE_INTERFACE - used, set(),
            "terminal.py offers names no tool asks for: {}"
            .format(sorted(THE_INTERFACE - used)))


class TestNobodyKeepsTheirOwnCopy(unittest.TestCase):
    """How the first five started: one category tuple, where it was needed."""

    def test_only_terminal_asks_the_unicode_tables_anything(self):
        # The cleanest line to hold.  What a terminal obeys and how wide a
        # character is drawn are both answered out of `unicodedata`, so a
        # printing module that imports it at all is answering one of them for
        # itself -- whatever it decided to call the result.
        for package in THE_PRINTERS:
            for module in _modules(package):
                with self.subTest(package=package, module=module):
                    tree = ast.parse(_source(package, module))
                    names = {alias.name
                             for node in ast.walk(tree)
                             if isinstance(node, ast.Import)
                             for alias in node.names}
                    names |= {node.module for node in ast.walk(tree)
                              if isinstance(node, ast.ImportFrom) and node.module}
                    self.assertNotIn(
                        "unicodedata", names,
                        "{}/{} reads the Unicode tables itself -- what a "
                        "terminal does with a character belongs in terminal.py, "
                        "where the other four can see it".format(package, module))

    def test_no_printing_module_declares_a_terminal_fact_of_its_own(self):
        for package in THE_PRINTERS:
            for module in _modules(package):
                with self.subTest(package=package, module=module):
                    source = _source(package, module)
                    tree = ast.parse(source)
                    for node in tree.body:
                        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                            continue
                        text = ast.get_source_segment(source, node) or ""
                        # Backslashes dropped first: an escape table spells the
                        # same fact as `\\n`, which does not contain the string
                        # it is about.
                        text = text.replace("\\", "")
                        for fact in THE_TERMINAL_FACTS:
                            self.assertNotIn(
                                '"{}"'.format(fact), text,
                                "{}/{} declares {!r} again -- that fact belongs "
                                "in terminal.py".format(package, module, fact))
                            self.assertNotIn(
                                "'{}'".format(fact), text,
                                "{}/{} declares {!r} again -- that fact belongs "
                                "in terminal.py".format(package, module, fact))


if __name__ == "__main__":
    unittest.main()
