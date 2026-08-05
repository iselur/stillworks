"""What happens to text an agent wrote when this package prints it.

`terminal.py` is the same file in the four packages that print, so this is the
same test in all four -- byte for byte, which is why it works out its own
package name rather than writing it down.

The four had each found this seam separately and drifted into three different
answers to it: agentdiff escaped and quoted, agentwatch and unedit blanked, and
agentlog deleted.  Deleting is the wrong one twice over, and both harms are
pinned below: two path components printed with nothing between them read as one
component that is not on disk, and a table that measures a cell for a character
and then removes it stands that row's right edge one cell left of every other
row's.  Nothing in any of the four suites caught the second one when it was
fixed, which is why this file exists.

The interface is six names.  Everything a caller must know is here: which
characters go, what replaces them and why it is a space rather than nothing,
which of the six keeps newlines, what a bound looks like when it is reached,
what quoting is for, and that a width is counted in cells rather than in
characters.
"""

from __future__ import annotations

import importlib
import os
import sys
import unicodedata
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

terminal = importlib.import_module(os.path.basename(_ROOT) + ".terminal")
block = terminal.block
display_width = terminal.display_width
one_line = terminal.one_line
pad = terminal.pad
quoted = terminal.quoted
row = terminal.row

# One character from each category a terminal acts on instead of showing.
ESCAPE = "\x1b"             # Cc -- clears the screen, retitles the window
OVERRIDE = "‮"         # Cf -- makes a path name a different file
LINE_SEP = " "         # Zl -- a line break to a reader, not to splitlines
PARA_SEP = " "         # Zp -- the same
DRIVEN = (ESCAPE, OVERRIDE, LINE_SEP, PARA_SEP, "\n", "\r", "\t", "\x7f")


class TestWhichCharactersGo(unittest.TestCase):

    def test_the_four_categories_are_the_four_that_matter(self):
        # Cc is where the escapes live, Cf is where the bidi overrides live,
        # and Zl/Zp are the two separators `str.splitlines` breaks on and a
        # terminal does not.  Written out so that a copy of this file which
        # quietly lost one of them fails here rather than in a review.
        self.assertEqual(
            {unicodedata.category(c) for c in (ESCAPE, OVERRIDE, LINE_SEP, PARA_SEP)},
            {"Cc", "Cf", "Zl", "Zp"})

    def test_every_one_of_them_goes(self):
        for char in DRIVEN:
            with self.subTest(char=repr(char)):
                self.assertNotIn(char, one_line("a" + char + "b"))

    def test_ordinary_text_is_untouched(self):
        for text in ("fixed the parser", "src/auth/session.py", "café/naïve.py",
                     "設定/ファイル.py", "a b  c", ""):
            with self.subTest(text):
                self.assertEqual(one_line(text), text)

    def test_a_codepoint_that_is_merely_unassigned_stays(self):
        # Unprintable, but it cannot break a row or drive anything.  Removing
        # it would be a second silent edit to a filename for no gain.
        self.assertEqual(one_line("a͸b"), "a͸b")


class TestWhyItIsASpaceAndNotNothing(unittest.TestCase):
    """The two harms of deleting, which is what one of the four used to do."""

    def test_two_components_of_a_path_stay_two_words(self):
        # `deps` and `HIGH   forged.py   x` were two components of a real path.
        # Deleted, they print as `depsHIGH`, which is not on disk and which
        # nothing on screen says was ever two things.
        got = one_line("deps\nHIGH   forged.py   x/requirements.txt")
        self.assertNotIn("depsHIGH", got)
        self.assertTrue(got.startswith("deps "), got)

    def test_a_hidden_character_is_drawn_in_the_cell_measured_for_it(self):
        # The column is worked out from the value and the value is sanitised
        # afterwards, so anything that changes the width between those two
        # moments moves that row's right-hand edge and nobody else's.  A space
        # is one cell, which is what the hidden character was counted as.
        for char in DRIVEN:
            with self.subTest(char=repr(char)):
                text = "one" + char + "two"
                self.assertEqual(display_width(one_line(text)),
                                 display_width(text))

    def test_a_table_column_still_lines_up(self):
        # The same fact said the way it is seen: two rows, one with a hidden
        # character in it, padded to the same column.
        plain, hidden = "a name here", "a name" + ESCAPE + "here"
        width = max(display_width(plain), display_width(hidden))
        self.assertEqual(display_width(pad(one_line(plain), width)),
                         display_width(pad(one_line(hidden), width)))


class TestTextThatIsAllowedToHaveLinesInIt(unittest.TestCase):

    def test_newlines_and_tabs_stay(self):
        # A unified diff is many lines by definition, and a rendered table is
        # one string with newlines in it.
        self.assertEqual(block("a\nb\tc"), "a\nb\tc")

    def test_everything_else_still_goes(self):
        for char in (ESCAPE, OVERRIDE, LINE_SEP, PARA_SEP, "\r", "\x7f"):
            with self.subTest(char=repr(char)):
                self.assertEqual(block("a" + char + "b"), "a b")

    def test_the_separators_a_reader_breaks_on_are_not_newlines(self):
        # U+2028 and U+2029 end a line on screen and survive `splitlines` being
        # asked about `\n`, which is how a forged row gets into a block.
        self.assertNotIn(LINE_SEP, block("a" + LINE_SEP + "b"))
        self.assertNotIn(PARA_SEP, block("a" + PARA_SEP + "b"))


class TestTheBoundOnARow(unittest.TestCase):

    def test_an_ordinary_value_passes_through(self):
        self.assertEqual(row("fixed the parser"), "fixed the parser")

    def test_a_value_at_the_bound_is_not_cut(self):
        self.assertEqual(row("x" * 400), "x" * 400)

    def test_a_value_past_it_is_cut_and_says_so(self):
        # `save -m "$(cat NOTES.md)"` is an ordinary thing for a script to do,
        # and it put a 200,000-character row in a listing.
        got = row("x" * 200_000)
        self.assertTrue(got.startswith("x" * 400))
        self.assertIn("199,600 more characters", got)
        self.assertIn("--json", got)

    def test_the_cut_is_at_the_bound_and_not_one_past_it(self):
        # The test above cannot see this and neither can any test written the
        # same way: a row cut one character too long still starts with the
        # first 400, and the count of what is missing is worked out from the
        # whole value, so it agrees with itself whatever the cut did.  The
        # value has to change at the bound for the bound to be visible.
        got = row("x" * 400 + "y" * 100)
        self.assertNotIn("y", got)
        self.assertEqual(got.index("…"), 400)

    def test_the_cut_happens_after_the_blanking_not_before(self):
        # Otherwise the first 400 characters could still contain a newline.
        got = row("a\nb" + "x" * 1000)
        self.assertNotIn("\n", got)

    def test_nothing_is_lost_where_the_value_is_stored(self):
        # `one_line` also runs on its way to disk, so it must not bound: the
        # manifest and `--json` keep the whole value and the row says how much
        # of it is not being shown.
        self.assertEqual(len(one_line("x" * 200_000)), 200_000)


class TestTextThatNamesSomethingToGoAndFind(unittest.TestCase):
    """`quoted`, which escapes rather than blanks, and says that it did."""

    def test_an_ordinary_path_is_returned_untouched(self):
        for text in ("src/auth/session.py", "my docs/a b.txt", "café/naïve.py",
                     "設定/ファイル.py"):
            with self.subTest(text):
                self.assertEqual(quoted(text), text)

    def test_the_escapes_anybody_meets_are_gits(self):
        self.assertEqual(quoted("a\nb"), '"a\\nb"')
        self.assertEqual(quoted("a\tb"), '"a\\tb"')
        self.assertEqual(quoted("a\rb"), '"a\\rb"')

    def test_anything_rarer_is_written_as_a_number(self):
        self.assertEqual(quoted("a\x01b"), '"a\\x01b"')
        self.assertEqual(quoted("a" + OVERRIDE + "b"), '"a\\u202eb"')

    def test_two_digits_for_a_byte_and_four_for_anything_above_one(self):
        # Both sides of the boundary, because one side of it is not a test of
        # where it is: `\x01` alone is spelled the same by code that puts the
        # line anywhere below 0x01.  `\x7f` is the one anybody meets -- it is
        # what the backspace key sends -- and `\x9b` is a second escape
        # introducer that arrives looking like ordinary high-half bytes.
        self.assertEqual(quoted("a\x7fb"), '"a\\x7fb"')
        self.assertEqual(quoted("a\x9bb"), '"a\\x9bb"')
        self.assertEqual(quoted("a" + LINE_SEP + "b"), '"a\\u2028b"')

    def test_the_quoting_is_what_makes_the_escaping_mean_something(self):
        # Without the quotes a file named `a\nb` and a file named `a<newline>b`
        # print identically, and the reader cannot tell which one to open.
        self.assertNotEqual(quoted("a\nb"), quoted("a\\nb"))

    def test_a_backslash_or_a_quote_is_escaped_too(self):
        self.assertEqual(quoted("a\\b"), '"a\\\\b"')
        self.assertEqual(quoted('a"b'), '"a\\"b"')

    def test_a_codepoint_that_is_merely_unassigned_is_left_alone(self):
        self.assertEqual(quoted("a͸b"), "a͸b")

    def test_a_value_that_is_not_a_string_is_still_printable(self):
        self.assertEqual(quoted(7), "7")


class TestTextThatIsNotText(unittest.TestCase):
    """These values come off disk, out of files another program wrote."""

    def test_a_manifest_with_a_number_where_its_message_should_be(self):
        for value in (7, None, [], {}, 0.5):
            with self.subTest(value):
                self.assertEqual(one_line(value), "")
                self.assertEqual(block(value), "")
                self.assertEqual(row(value), "")


class TestHowWideItIsDrawn(unittest.TestCase):

    def test_ascii_is_one_cell_a_character(self):
        self.assertEqual(display_width("hello"), 5)
        self.assertEqual(display_width(""), 0)

    def test_an_east_asian_character_is_drawn_in_two(self):
        # A project named in Japanese is twice as wide as `len` says, so a
        # column padded with `ljust` puts the next one somewhere else.
        self.assertEqual(display_width("設定"), 4)
        self.assertEqual(display_width("ab設"), 4)

    def test_a_combining_mark_takes_no_cell_of_its_own(self):
        # It is drawn on top of the character before it.
        self.assertEqual(display_width("é"), 1)

    def test_an_enclosing_mark_takes_no_cell_either(self):
        # Mn and Me are two categories, and a test using only a combining
        # acute says nothing about the second: U+20DD is drawn *around* the
        # character before it, so it too is worth no cell of its own.
        self.assertEqual(display_width("a⃝"), 1)

    def test_a_fullwidth_form_is_drawn_in_two_as_well(self):
        # W and F are likewise two answers in the width table.  `設` is W;
        # `Ａ` is U+FF21, the fullwidth A, which is F -- an ASCII letter's own
        # double-width form, and the one that turns up in a pasted filename.
        self.assertEqual(display_width("Ａ"), 2)
        self.assertEqual(display_width("Ａb"), 3)

    def test_padding_counts_cells_rather_than_characters(self):
        self.assertEqual(display_width(pad("設定", 10)), 10)
        self.assertEqual(display_width(pad("ab", 10)), 10)

    def test_padding_never_cuts(self):
        # It is `ljust`, and `ljust` narrower than the value returns the value.
        self.assertEqual(pad("設定書類", 2), "設定書類")


if __name__ == "__main__":
    unittest.main()
