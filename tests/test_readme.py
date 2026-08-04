"""The README's count of the family, checked against the family it lists.

Every repo says how many tools there are in prose, and the same number lives a
few lines below it as a bullet list.  Adding a fifth tool left three READMEs
telling people to install "all four agent tools" from a family of five — the
kind of wrong that nothing catches, because no code reads a README.
"""

import os
import re
import unittest

_README = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "README.md")

_WORDS = ("two", "three", "four", "five", "six", "seven", "eight")

# "all five agent tools", "Five tools for working with", "all five:",
# "all five, and ..." — the shapes the sentence takes.  Anchored on the word
# "tools", or on "all" followed by punctuation, because a bare number word in
# prose is not a claim about the family: "a user record is written for three
# different things" is not this README promising a family of three.
#
# What ends the phrase is punctuation, a blank line or the end of the file —
# never a line break.  A README is wrapped to a column, so the word that
# follows a number lands on the next line as often as not, and reading that
# break as the end of the sentence turns "counting all four reported 38318
# turns" into a promise of four tools.  Where the editor wrapped is not
# something the sentence said.
_CLAIMS = (
    re.compile(r"\b({})\s+(?:agent\s+)?tools\b".format("|".join(_WORDS)), re.I),
    re.compile(r"\ball\s+({})\s*(?:[,:.]|\n[ \t]*\n|\Z)".format(
        "|".join(_WORDS)), re.I),
)


class TestTheFamilyCountIsRight(unittest.TestCase):

    def setUp(self):
        self.text = open(_README).read()

    def _listed(self):
        """The tools the README actually lists, one bullet each."""
        section = re.search(r"^## Part of a small family\n(.*?)(?=^## |\Z)",
                            self.text, re.S | re.M)
        self.assertIsNotNone(section, "the README lost its family section")
        return re.findall(r"^- \[([a-z]+)\]", section.group(1), re.M)

    def test_the_family_section_lists_the_tools(self):
        # If this list ever empties, the test below passes vacuously.
        self.assertGreaterEqual(len(self._listed()), 2)

    def test_this_repo_is_one_of_them(self):
        here = os.path.basename(os.path.dirname(_README))
        self.assertIn(here, self._listed())

    def test_every_count_the_readme_states_matches_that_list(self):
        expected = _WORDS[len(self._listed()) - 2]
        stated = {m.lower() for pattern in _CLAIMS
                  for m in pattern.findall(self.text)}
        self.assertTrue(stated, "the README no longer says how many there are")
        self.assertEqual(stated, {expected})

    def test_the_claim_patterns_still_catch_the_bug_they_were_written_for(self):
        # The patterns were narrowed once, after "a user record is written for
        # three different things" was read as a claim about the family.  Prose
        # about something else must not trip them, and the wrong-count
        # sentences must still be caught.
        caught = lambda s: {m.lower() for p in _CLAIMS for m in p.findall(s)}
        for wrong in ("Install all four agent tools, including this one.",
                      "Four tools for working with coding agents.",
                      "Install all four:  pip install 'stillworks[all]'",
                      "all four, and `stillworks tools` says so"):
            self.assertEqual(caught(wrong), {"four"}, wrong)
        for innocent in ("a user record is written for three different things",
                         "counting all three reported 38318 turns",
                         "all five of the records were skipped silently"):
            self.assertEqual(caught(innocent), set(), innocent)

    def test_where_a_line_wraps_does_not_change_what_a_sentence_claims(self):
        # A README is written to a column, so the word after a number often
        # lands on the next line.  An end-of-line anchor reads that as the end
        # of the sentence and calls "counting all four reported 38318 turns" a
        # promise of four tools -- a failure caused by nothing but where the
        # editor happened to wrap.  The claim must be read from the words.
        caught = lambda s: {m.lower() for p in _CLAIMS for m in p.findall(s)}
        for innocent in ("counting all four\nreported 38318 turns",
                         "install all four\nof them at once",
                         "Counting all four\n    reported 38318 turns"):
            self.assertEqual(caught(innocent), set(), innocent)
        # ...and a real claim at the end of a line is still a real claim.
        for wrong in ("Install all four\n\npip install 'stillworks[all]'",
                      "there are all four",
                      "the family is all four.\n\nInstall it."):
            self.assertEqual(caught(wrong), {"four"}, wrong)


if __name__ == "__main__":
    unittest.main()
