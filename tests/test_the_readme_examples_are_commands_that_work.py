"""Every command the README shows, handed to the parser that would get it.

The other README tests check what the page *says*. `test_the_command_table_is_
the_parser` pins the table of commands, `test_the_readme_documents_the_flags`
pins that each flag is written down somewhere, `test_the_readme_shows_what_it_
prints` pins the output blocks.  None of them touch the examples — the command
lines a reader copies.

Those are the lines most likely to be wrong and least likely to be noticed.  A
flag renamed in the parser leaves the table failing loudly and the examples
failing quietly, because to every test that has read this page so far an
example is prose.  The reader finds out by pasting it and getting `error:
unrecognized arguments`.

Nothing is run.  Each line is split the way a shell would split it and handed
to the parser the tool actually uses, which asks whether the command would be
accepted and not what it would do — so this is safe anywhere and touches
nothing on disk.

Two kinds of line live in these blocks and both are checked, because dropping
one kind quietly is how a page keeps a command that no longer exists:

  stillworks save -m "before agent refactor"     an example: it has to parse
  stillworks save [-m MSG] [--force]             a synopsis: `[` and `<` mean the
                                             reader fills that in, so it is
                                             read for its subcommand instead

Anything that is neither — a line naming the tool in the middle of a sentence
inside a fence — is not shaped like a command and is not treated as one.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shlex
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from stillworks.cli import build_parser  # noqa: E402

README = os.path.join(_ROOT, "README.md")
TOOL = "stillworks"

_FENCE = re.compile(r"^```[a-z]*\n(.*?)^```", re.S | re.M)

# A command line inside a fenced block: the tool's own name at the start, with
# or without the `$ ` prompt the page uses in transcripts.
_TYPED = re.compile(r"^(?:\$ )?(" + re.escape(TOOL) + r"(?: .*)?)$", re.M)

# What marks a line as a synopsis rather than something to type.  `[` and `<`
# are the two conventions this page uses for "you fill this in"; `...` means
# "and more of them".  A line carrying any of them describes the shape of a
# command instead of being one.
_PLACEHOLDERS = ("[", "<", "...")

# The other convention, which carries no punctuation at all: `--limit N`,
# `-m MSG`, `--project DIR`.  A bare capitalised word is a slot, and a line with
# one in it is a synopsis however it is punctuated — `agentlog list --limit N`
# is not a command anyone can run, it is a command with a blank in it.
#
# The word has to stand alone to count.  `EVIDENCE.md` is capitals too and it is
# nothing of the sort — it is a filename somebody typed, and reading it as a
# blank would quietly excuse that whole line from being parsed.  So the match is
# bounded by whitespace on both sides: a slot is a whole word, not a piece of
# one.  Quoted text is skipped first — what is inside quotes is a value.
_SLOT = re.compile(r"(?<!\S)[A-Z]+(?!\S)")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

# Where the tool's own command stops and the shell's work begins.  `agentwatch
# --once --json > events.json && process events.json` is one command being
# redirected into another; everything after the operator belongs to the shell.
_SHELL = re.compile(r"\s(?:\||>|>>|<|&&|\|\||;)\s")


def command_lines(text):
    """[(line text, line number)] for everything in a fence shaped like a command."""
    found = []
    for block in _FENCE.finditer(text):
        for m in _TYPED.finditer(block.group(1)):
            line = text.count("\n", 0, block.start(1) + m.start()) + 1
            found.append((m.group(1).strip(), line))
    return found


def is_a_synopsis(command):
    if any(mark in command for mark in _PLACEHOLDERS):
        return True
    return _SLOT.search(_QUOTED.sub("", command)) is not None


def is_a_caption(line):
    """A line that introduces the output below it rather than being a command.

    `agentlog list (first 3 rows):` is a heading for the block that follows.
    The colon is what says so — no shell command this page shows ends in one.
    """
    return line.rstrip().endswith(":")


def just_the_command(line):
    """The command, with any description column taken off the end.

    The command table lives in a fenced block and is laid out in two columns
    separated by a run of spaces:

        stillworks where                             print the snapshot directory

    The right-hand side is English, so parsing the whole line reports the
    description as unrecognized arguments — which is a bug in the reader, not
    in the page.  A single space never separates the columns, so the gap is
    what tells them apart.
    """
    return re.split(r"\s{2,}", line.strip(), maxsplit=1)[0].strip()


def as_a_shell_would_split_it(command):
    """The argument list, minus the tool's own name.

    Stops at the first shell operator: `stillworks diff | less` is one command
    being read by another, and the second half is not this tool's to parse.
    """
    words = shlex.split(_SHELL.split(command.strip(), maxsplit=1)[0].strip())
    return words[1:]


def subcommands(parser):
    """The subcommand names the parser knows, or None if it has none."""
    for action in parser._actions:
        if action.__class__.__name__ == "_SubParsersAction":
            return set(action.choices)
    return None


class TestTheReadmeExamplesAreCommandsThatWork(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(README, encoding="utf-8") as fh:
            cls.text = fh.read()
        cls.lines = [(just_the_command(c), n) for c, n in
                     command_lines(cls.text) if not is_a_caption(c)]
        cls.examples = [(c, n) for c, n in cls.lines if not is_a_synopsis(c)]
        cls.synopses = [(c, n) for c, n in cls.lines if is_a_synopsis(c)]

    def test_the_readme_still_shows_commands(self):
        # Vacuity guard.  Every check below reads these lists, and a page with
        # no command blocks left passes all of them without looking at
        # anything.
        self.assertGreaterEqual(
            len(self.lines), 3,
            "README.md shows {} lines shaped like a command, so the checks "
            "below compared almost nothing".format(len(self.lines)))

    def test_they_are_commands_and_not_just_the_bare_tool(self):
        # `stillworks` on its own parses under any parser that has ever existed, so
        # a page decayed to nothing but bare invocations would pass the check
        # below while documenting nothing.
        with_arguments = [c for c, _ in self.examples
                          if as_a_shell_would_split_it(c)]
        self.assertGreaterEqual(
            len(with_arguments), 2,
            "only {} of the README's examples pass any arguments, so parsing "
            "them proves close to nothing".format(len(with_arguments)))

    def test_every_example_parses(self):
        # Reported together: a renamed flag breaks every example that uses it,
        # and hearing about them one run at a time is one run per example.
        broken = []
        for command, line in self.examples:
            parser = build_parser()
            noise = io.StringIO()
            try:
                with contextlib.redirect_stderr(noise), \
                        contextlib.redirect_stdout(noise):
                    parser.parse_args(as_a_shell_would_split_it(command))
            except SystemExit as exc:
                # `--version` and `--help` leave through SystemExit too, and
                # they leave through it having worked.  Only a non-zero code
                # is the tool saying no.
                if exc.code in (0, None):
                    continue
                said = noise.getvalue().strip().splitlines()
                broken.append("  README.md:{}  {}\n      {}".format(
                    line, command, said[-1] if said else "rejected"))
            except Exception as exc:  # noqa: BLE001 - report, never hide
                broken.append("  README.md:{}  {}\n      {}: {}".format(
                    line, command, type(exc).__name__, exc))
        self.assertFalse(
            broken,
            "the README shows {} command{} the tool would refuse:\n\n{}\n\n"
            "Anyone reading the page would paste {} and get an error.".format(
                len(broken), "" if len(broken) == 1 else "s",
                "\n".join(broken), "it" if len(broken) == 1 else "them"))

    def test_every_synopsis_describes_a_real_subcommand(self):
        # A synopsis cannot be parsed — that is what the placeholders mean —
        # but the word after the tool's name is not a placeholder, and a
        # synopsis for a command that no longer exists is the same failure one
        # step earlier.
        known = subcommands(build_parser())
        if known is None:
            self.skipTest("{} has no subcommands to describe".format(TOOL))
        self.assertTrue(known, "the parser declares subcommands and has none")
        wrong = []
        for command, line in self.synopses:
            words = command.split()
            if len(words) < 2 or words[1].startswith("-"):
                continue
            if words[1] not in known:
                wrong.append("  README.md:{}  {}  ({!r} is not a "
                             "command)".format(line, command, words[1]))
        self.assertFalse(
            wrong,
            "the README describes commands the tool does not have:\n\n{}\n\n"
            "It accepts: {}".format("\n".join(wrong), ", ".join(sorted(known))))


if __name__ == "__main__":
    unittest.main()
