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

# "all five agent tools", "Five tools for working with", "all five:" — the three
# shapes the sentence takes.  Anchored on the word "tools" or on "all", because
# a bare number word in prose is not a claim about the family.
_CLAIMS = (
    re.compile(r"\b({})\s+(?:agent\s+)?tools\b".format("|".join(_WORDS)), re.I),
    re.compile(r"\ball\s+({})\b".format("|".join(_WORDS)), re.I),
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


if __name__ == "__main__":
    unittest.main()
