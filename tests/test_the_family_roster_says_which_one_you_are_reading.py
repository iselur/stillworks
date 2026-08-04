"""Five READMEs carry the same family list, differing in one arrow.

`test_family_claims.py` pins the paragraph above the list — the pitch, written
out five times, so a re-word in one repo cannot quietly disagree with the other
four.  It does not look at the list itself, and the list is the part that was
copied.

These sections were made by pasting one into the next.  What survives that is
five identical rosters; what does not is `← you are here`, which has to move
one row every time.  A marker left where the last paste put it points a reader
at the wrong tool, and reads exactly like a correct README — the sentence is
well-formed, the link works, it is just about somebody else's project.

The counts are the other half.  "Five tools" and "all five" are written in
words, in prose, nowhere near the list they count.  A sixth tool means editing
five READMEs in three places each, and the two words are the places most easily
missed: everything still renders, and the list quietly outnumbers its own
description.

So: the rows are counted against the words that count them, the arrow is
checked to be on this tool and no other, and each row is checked to link to the
tool it names rather than to whichever one was pasted next to it.
"""

from __future__ import annotations

import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(_ROOT, "README.md")

# The tool this repository is, as the roster spells it.
THIS_ONE = "stillworks"

# The one tool that can install the others, and the extra that does it.
INSTALLER = "stillworks"
INSTALL_LINE = "pip install 'stillworks[all]'"

# Where the roster's name and the PyPI name are not the same word.  Both were
# taken because the plain one was already on PyPI, and both are spelled the
# short way everywhere a person reads them — which is the whole reason the two
# lists need tying together rather than eyeballing.
ON_PYPI = {"agentdiff": "agentdiff-cli", "agentlog": "agentlog-tool"}

HEADING = "## Part of a small family"
MARKER = "← you are here"

# "- [agentdiff](https://github.com/iselur/agentdiff) — what it is for" with an
# optional marker on the end.  The description is required: a row that names a
# tool and says nothing about it is a link, not a roster entry.
_ROW = re.compile(
    r"^- \[([a-z][a-z0-9-]*)\]\((https://[^)]+)\) — (.+?)(\s+" + re.escape(MARKER)
    + r")?\s*$", re.M)

_FENCE = re.compile(r"^```[a-z]*\n(.*?)^```", re.S | re.M)

# Every way the README writes the family size out in words.  These are searched
# over the whole file rather than the family section, and that is the opposite
# of the rule the rest of these README tests follow — a bare token like `--home`
# has to be looked for in the section that explains it, because the same token
# turns up in tables and examples that explain nothing.
#
# These are not bare tokens.  Each pattern names what it is counting in the same
# breath as the number, so a match anywhere in the file is a claim about the
# family wherever it sits.  That matters here because the count is written at
# the top of the README, in the install comment, and again at the bottom, and
# the point of this test is the one that gets missed.
#
# Matched by shape rather than swept for, because the same page says "Each of
# those four claims" about something else and "Counting all four" about a
# screenshot, and neither is a disagreement about how many tools there are.
_COUNTS = (
    re.compile(r"([A-Z][a-z]+) tools for working with coding agents"),
    re.compile(r"all ([a-z]+) agent tools"),
    re.compile(r"One install gets all ([a-z]+)"),
    re.compile(r"or all ([a-z]+):\s+pip install"),
)

_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def section(text, heading):
    """One `##` section, from its heading to the next one at any level."""
    start = text.find(heading)
    if start < 0:
        return ""
    end = re.search(r"\n#{1,3} ", text[start + len(heading):])
    return text[start:start + len(heading) + end.start()] if end else text[start:]


def rows(body):
    """[(name, url, description, is_marked)] for every roster line."""
    return [(m.group(1), m.group(2), m.group(3).strip(), bool(m.group(4)))
            for m in _ROW.finditer(body)]


def counts_written_out(text):
    """[(the word, where it sits)] for every family size the README states."""
    return [(m.group(1), m.start())
            for pattern in _COUNTS for m in pattern.finditer(text)]


class TestTheFamilyRosterSaysWhichOneYouAreReading(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(README, encoding="utf-8") as fh:
            cls.text = fh.read()
        cls.body = section(cls.text, HEADING)
        cls.rows = rows(cls.body)

    def test_the_section_is_still_there(self):
        # Everything below reads this section.  If the heading is renamed the
        # section comes back empty and every other test passes over nothing.
        self.assertTrue(
            self.body,
            "README.md has no {!r} section, so nothing below this checked "
            "anything".format(HEADING))

    def test_the_roster_still_lists_tools(self):
        self.assertGreaterEqual(
            len(self.rows), 2,
            "the family section lists {} tools, which is not a family — either "
            "the rows changed shape or they are gone".format(len(self.rows)))

    def test_the_readme_still_says_how_many_of_them_there_are(self):
        # Vacuity guard.  Every count below is read out of the page, and a page
        # that has stopped stating the number passes a comparison against
        # nothing at all.
        self.assertGreaterEqual(
            len(counts_written_out(self.text)), 2,
            "README.md no longer writes the family size out in words anywhere "
            "this can find, so the count below compared nothing")

    def test_every_count_it_gives_is_the_number_of_rows(self):
        # "all five agent tools" sits in the install comment at the top of the
        # page; "One install gets all five" sits at the bottom.  Adding a tool
        # is three edits to this README and those two are the ones that still
        # render perfectly when they are missed.
        for word, where in counts_written_out(self.text):
            line = self.text.count("\n", 0, where) + 1
            self.assertIn(
                word.lower(), _WORDS,
                "README.md:{} says {!r} tools, which is not a number this "
                "knows how to check".format(line, word))
            self.assertEqual(
                _WORDS[word.lower()], len(self.rows),
                "README.md:{} says there are {} of them and the family list "
                "has {} rows".format(line, word.lower(), len(self.rows)))

    def test_exactly_one_row_says_you_are_here(self):
        marked = [name for name, _, _, is_marked in self.rows if is_marked]
        self.assertEqual(
            len(marked), 1,
            "{} rows carry {!r}: {}. A reader is in one repository at a "
            "time".format(len(marked), MARKER, marked or "none of them"))

    def test_the_row_that_says_you_are_here_is_this_one(self):
        # The failure this exists for: these sections were pasted between
        # repositories, and the arrow is the only thing in them that has to
        # move.  Left where it was, it reads as a correct README about a
        # different tool.
        marked = [name for name, _, _, is_marked in self.rows if is_marked]
        self.assertEqual(
            marked, [THIS_ONE],
            "this is the {} repository and its README points {!r} at {} — the "
            "arrow did not move when the section was copied".format(
                THIS_ONE, MARKER, marked or "nothing"))

    def test_this_tool_is_in_its_own_roster(self):
        listed = [name for name, _, _, _ in self.rows]
        self.assertIn(
            THIS_ONE, listed,
            "the family list leaves out {}, the tool whose README it "
            "is: {}".format(THIS_ONE, listed))

    def test_every_row_links_to_the_tool_it_names(self):
        # Copied rows keep their old URL.  The link text is what a reader
        # believes; the href is where they land.
        for name, url, _, _ in self.rows:
            self.assertTrue(
                url.rstrip("/").endswith("/" + name),
                "the row for {} links to {}, which is a different "
                "project".format(name, url))

    def test_every_row_says_what_the_tool_is_for(self):
        for name, _, description, _ in self.rows:
            self.assertGreaterEqual(
                len(description.split()), 4,
                "the row for {} says {!r}, which does not tell anyone what it "
                "is".format(name, description))

    def test_the_install_line_names_a_tool_on_the_list(self):
        # The section ends by telling you to install the family through one of
        # its own members.  If that member ever drops off the roster the
        # instruction still works and the list has stopped being the family.
        blocks = _FENCE.findall(self.body)
        self.assertTrue(blocks, "the family section has no install block")
        commands = "\n".join(blocks)
        self.assertIn(
            INSTALL_LINE, commands,
            "the family section no longer shows {!r}, which is the one "
            "command that gets all of them".format(INSTALL_LINE))
        self.assertIn(
            INSTALLER, [name for name, _, _, _ in self.rows],
            "the section says to install the family with {} and does not list "
            "it as one of the tools".format(INSTALLER))

    def test_the_roster_is_the_family_the_install_actually_gets(self):
        # This repository is the only one of the five where the list can be
        # checked against something a machine reads.  `pip install
        # 'stillworks[all]'` is the command four other READMEs point at, and
        # what it installs is the `[all]` extra, not the list above it.  The
        # two could disagree in either direction — a tool added to the roster
        # and not to the extra is a promise the install does not keep, and one
        # added to the extra and not the roster arrives on a machine without
        # ever having been described.
        #
        # `test_family_claims.py` already pins `[all]` to a set written out in
        # that file.  That is a second copy of the family, so it is pinned here
        # to the README rather than to another list; three copies that all have
        # to agree is what keeps any one of them from being edited alone.
        import tomllib
        with open(os.path.join(_ROOT, "pyproject.toml"), "rb") as fh:
            cfg = tomllib.load(fh)
        extra = cfg["project"]["optional-dependencies"]["all"]
        self.assertTrue(extra, "the [all] extra is empty")
        named = {re.split(r"[<>=!~\[ ]", s.strip())[0] for s in extra}
        listed = {name for name, _, _, _ in self.rows} - {INSTALLER}
        self.assertEqual(
            {ON_PYPI.get(name, name) for name in listed}, named,
            "the family list names {} and `[all]` installs {} — one of them "
            "was edited without the other".format(sorted(listed), sorted(named)))


if __name__ == "__main__":
    unittest.main()
