"""The `stillworks tools` block in the README, produced by the code that prints it.

The README shows the real thing:

    $ stillworks tools
      stillworks  0.1.3  record what your code does now, catch when it changes
      ...
      missing: agentdiff
      install:  pip install agentdiff-cli
      or all five:  pip install 'stillworks[all]'

Everything in that block is generated — the column widths, the pitches, the
order, the wording of the missing-tool footer, and the spelled-out count that
had to be corrected once already when the family went from four to five.  None
of it was checked, and it had already drifted: the block claimed agentlog 0.2.2
against a released 0.2.4.

So the block is parsed back into rows and handed to the renderer that would
have produced it.  What that pins:

    the format      column alignment and the footer, without repeating the
                    format strings here
    the pitches     one-line descriptions, which live in FAMILY and are easy to
                    reword in one place only
    the count       "or all five" comes from len(FAMILY), so adding a sixth
                    tool without touching the README fails here
    our own version the stillworks row has to say what this package is

The sibling versions are not checkable from inside this repo — they are other
distributions with their own release cycles — so they are taken from the README
as given.  A stale one there is a documentation bug, not a broken promise, and
the versions are shown at all because the block is worth more as real output
than as an illustration.
"""

from __future__ import annotations

import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from stillworks import __version__, tools

README = os.path.join(_ROOT, "README.md")

_ROW = re.compile(r"^  (\S+) +(\S+) +(\S.*)$")


def readme_block() -> str:
    """The lines the README shows under `$ stillworks tools`."""
    with open(README, encoding="utf-8") as handle:
        text = handle.read()
    after = text.split("$ stillworks tools\n", 1)[1]
    return after.split("```", 1)[0].rstrip("\n")


def versions_from(block: str):
    """{command: version-or-None} for each tool line in the block."""
    found = {}
    for line in block.splitlines():
        row = _ROW.match(line)
        if not row or line.startswith("  missing") or line.startswith("  install"):
            continue
        command, version, _pitch = row.groups()
        found[command] = None if version == "—" else version
    return found


def rows_from(block: str):
    """(command, dist, pitch, version), built from FAMILY, versions from the README.

    Only the versions are read back.  Everything else — which tools, in what
    order, described how — comes from FAMILY, because those are the parts the
    README is being checked against: taking the pitch from the block and
    feeding it to the renderer would compare the README to itself, and a
    reworded pitch would pass.
    """
    found = versions_from(block)
    return [(command, dist, pitch, found.get(command))
            for command, dist, pitch in tools.FAMILY
            if command in found]


class TestTheREADMEShowsWhatItPrints(unittest.TestCase):
    def setUp(self):
        self.block = readme_block()
        self.rows = rows_from(self.block)

    def test_the_readme_still_has_the_block(self):
        # Otherwise the comparison below is between two empty strings, which is
        # a pass that means the example was deleted.
        self.assertEqual(len(self.rows), len(tools.FAMILY), self.block)

    def test_the_block_is_what_the_command_would_print(self):
        self.assertEqual(tools.render(self.rows), self.block,
                         "README.md shows output stillworks no longer prints")

    def test_it_names_this_version_of_stillworks(self):
        mine = [v for c, _d, _p, v in self.rows if c == "stillworks"]
        self.assertEqual(mine, [__version__],
                         "the README's own version line is stale")


if __name__ == "__main__":
    unittest.main()
