"""Every flag the parser has, findable in the README — and the reverse.

A flag nobody can discover is a flag nobody uses.  This tool has nine of them
spread across five subcommands, and `--help` only shows one subcommand's worth
at a time, so the README is where anybody actually finds out that `lock` takes
a `--timeout` or that `check` speaks `--json`.

The opposite failure is worse.  A README quoting a flag the parser dropped
hands somebody a command line that exits 2 on them — and the people copying
these lines are largely agents, which will paste it, read the usage error, and
try to work around a tool they have been told is broken.

Both directions are checked here.  A flag counts as documented if any of its
spellings appears in the README, because `-o` and `--output` are the same flag
and the short one is what the examples use.

The reverse direction reads only fenced code blocks, and only the flags on a
command line belonging to stillworks itself.  READMEs discuss other programs —
this one shows `pip install`, `claude mcp add`, and the sibling tools — and a
naive sweep for `--` in the prose reads every one of those as a flag stillworks
has lost.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from stillworks.cli import build_parser as build_parser

README = os.path.join(_ROOT, "README.md")
TOOL = "stillworks"

# Programs the README talks about that are not this one.  A flag written after
# one of these names belongs to it, not here.
FOREIGN = {"git", "pip", "pipx", "python3", "python", "cd", "make", "claude",
           "npx", "unedit", "agentdiff", "agentwatch", "agentlog", "pytest",
           "uv", "export"}

# Flags deliberately not in the README, each with the reason it is not.
UNDOCUMENTED = {}

_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")


def readme() -> str:
    with open(README, encoding="utf-8") as handle:
        return handle.read()


def parser_flags(parser=None, path="", out=None):
    """Every long flag the parser accepts, as {flag: all its spellings}."""
    parser = parser or build_parser()
    out = {} if out is None else out
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                parser_flags(sub, path=name, out=out)
        longs = [o for o in action.option_strings if o.startswith("--")]
        for flag in longs:
            if flag != "--help":
                out[flag] = list(action.option_strings)
    return out


def code_blocks(text):
    return re.findall(r"```[a-z]*\n(.*?)```", text, re.S)


def flags_shown_for_this_tool(text):
    """Flags on README command lines that are this tool's own."""
    shown = {}
    for block in code_blocks(text):
        owner = None
        for line in block.splitlines():
            stripped = line.strip().lstrip("$ ").strip()
            if not stripped or stripped.startswith("#"):
                continue
            head = stripped.split()[0]
            if head == TOOL:
                owner = TOOL
            elif head in FOREIGN:
                owner = head
            elif not line.startswith((" ", "\t")):
                # A line that starts something else entirely ends the run; an
                # indented one continues the command above it.
                owner = None
            if owner != TOOL:
                continue
            for flag in _FLAG.findall(stripped):
                shown.setdefault(flag, stripped)
    return shown


class TestTheREADMEDocumentsTheFlags(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = readme()
        cls.flags = parser_flags()

    def test_the_parser_still_has_flags(self):
        # Without this both assertions below run over nothing and pass.
        self.assertGreaterEqual(len(self.flags), 3,
                                "no flags found — the parser walk is broken")

    def test_the_walk_reaches_inside_the_subcommands(self):
        # Almost every flag this tool has lives on a subparser, so a walk that
        # stopped at the top level would find `--version` and `--project`,
        # clear the count above, and check nothing that matters.
        self.assertIn("--fuzz", self.flags,
                      "the parser walk never descended into `lock`")

    def test_every_flag_is_in_the_readme(self):
        for flag, spellings in sorted(self.flags.items()):
            if flag in UNDOCUMENTED:
                continue
            self.assertTrue(
                any(re.search(r"(?<![\w-])" + re.escape(s) + r"(?![\w-])", self.text)
                    for s in spellings),
                "{} accepts {} and the README never mentions it, so the only "
                "way to find it is --help or the source".format(TOOL, flag))

    def test_every_flag_the_readme_shows_still_exists(self):
        shown = flags_shown_for_this_tool(self.text)
        self.assertTrue(shown, "no {} command lines with flags in README.md".format(TOOL))
        for flag, line in sorted(shown.items()):
            self.assertIn(
                flag, self.flags,
                "README.md shows `{}`, which {} does not accept — anyone "
                "copying that line gets a usage error".format(line, TOOL))


if __name__ == "__main__":
    unittest.main()
