"""Three commands take a directory; this is what makes it one flag.

`stillworks`, `agentdiff` and `unedit` each work inside a directory you point
them at, and each declared the flag for it twice — once before the subcommand
and once after.  Six copies of one fact, and the line of help everybody reads
had them saying three different things:

    usage: stillworks [-h] [--version] [--project PROJECT] ...
    usage: agentdiff [-h] [--version] [--project DIR] COMMAND ...
    usage: unedit [-h] [--version] [--dir DIR] COMMAND ...

`PROJECT` is a thing you have a name for, and in this family that reading is
correct twice over: `agentlog` and `agentwatch` take `--project NAME`, where a
name is exactly right.  So `stillworks --project relay` is somebody answering
the question the usage line asked, and being told there is no such directory.

And `unedit`'s usage line does not offer `--project` at all.  It accepts it —
the alias was added so the family would agree — but argparse shows whichever
name was written first, and it was written second.

The flag lives in `where.py` now, one copy per package.  It has to stay copied:
nothing in this family imports outside its own package — the promise `pip
install stillworks` makes, enforced by
`test_every_import_is_stdlib_or_the_packages_own` — so this file is what keeps
the copies from becoming three flags again.

Four things get checked:

  * the three `where.py` are byte-identical;
  * all three commands go through it, and none has kept a declaration of its
    own — the file existing is not the file being used;
  * the usage line offers `--project DIR` at all three, which is the sentence
    the bug was written in; and
  * so does the help *after* the subcommand, at all three, in the same words —
    because that is the second of the six copies, and the one that was hidden
    at one command and spelled out at another.
"""

from __future__ import annotations

import ast
import hashlib
import os
import subprocess
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# The three commands pointed at a directory, and a subcommand of each -- one is
# enough, because they all take the flag from the same shared parser.
#
# `agentlog` and `agentwatch` are deliberately absent: their `--project` takes a
# name, which is a different question wearing the same flag.  That one is pinned
# by `test_a_project_is_asked_for_the_same_way_in_both.py`.
THE_COMMANDS = {"stillworks": "check",
                "agentdiff": "review",
                "unedit": "save"}

# What a caller may ask of `where.py`.  One name: everything about the flag is
# behind it, so there is nothing to get right at a call site and nothing to get
# wrong.
THE_INTERFACE = {"add_project_flag"}

# What the usage line has to offer.  Not `--project PROJECT`, which reads as a
# name; not `--dir DIR`, which is a spelling two of the three never had.
THE_FLAG = "--project DIR"


def _source(package, module):
    with open(os.path.join(_ROOT, package, module), encoding="utf-8") as fh:
        return fh.read()


def _help(*command):
    """What somebody actually sees, from a real run of the real command."""
    result = subprocess.run(
        [sys.executable, "-m"] + list(command) + ["--help"],
        cwd=_ROOT, capture_output=True, text=True, timeout=60,
        env=dict(os.environ, PYTHONPATH=_ROOT))
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _flattened(text):
    """Help as one line, so a sentence argparse wrapped is still one sentence."""
    return " ".join(text.split())


def _the_shared_sentence():
    """The sentence where.py holds, read out of it rather than retyped here."""
    for node in ast.parse(_source("agentdiff", "where.py")).body:
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", "") == "_WHAT_IT_TAKES"
                        for t in node.targets)):
            return node.value.value
    raise AssertionError("where.py no longer holds a sentence for the flag")


class TestTheyAreOneFile(unittest.TestCase):

    def test_all_three_carry_it(self):
        missing = [p for p in THE_COMMANDS
                   if not os.path.exists(os.path.join(_ROOT, p, "where.py"))]
        self.assertEqual(missing, [],
                         "packages with no where.py: {}".format(missing))

    def test_byte_for_byte(self):
        digests = {}
        for package in THE_COMMANDS:
            with open(os.path.join(_ROOT, package, "where.py"), "rb") as fh:
                digests.setdefault(
                    hashlib.sha256(fh.read()).hexdigest(), []).append(package)
        self.assertEqual(
            len(digests), 1,
            "where.py has drifted into {} versions:\n  {}\nA change to the flag "
            "belongs at all three: rsync the source repo you changed, then the "
            "others.\n".format(
                len(digests),
                "\n  ".join(sorted(", ".join(sorted(group))
                                   for group in digests.values()))))

    def test_the_interface_is_the_one_name_all_three_call(self):
        tree = ast.parse(_source("agentdiff", "where.py"))
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
            "where.py's interface changed.  A second name is a second thing a "
            "call site can get right or wrong -- which is what the one name was "
            "for.")


class TestAllThreeGoThroughIt(unittest.TestCase):
    """A shared file nobody calls is three declarations and a spare file."""

    def _imported(self, package):
        tree = ast.parse(_source(package, "cli.py"))
        return {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module == "where"
                for alias in node.names}

    def test_each_command_asks_the_shared_module(self):
        for package in sorted(THE_COMMANDS):
            with self.subTest(package):
                asked = self._imported(package)
                self.assertTrue(
                    asked, "{}/cli.py stopped importing from where.py"
                    .format(package))
                self.assertLessEqual(
                    asked, THE_INTERFACE,
                    "{}/cli.py reaches past the interface: {}"
                    .format(package, sorted(asked - THE_INTERFACE)))

    def test_no_command_still_declares_the_flag_itself(self):
        """The shape the drift was in: `add_argument("--project", ...)`, twice.

        A hand-written declaration is not wrong on the day it is written -- it
        is a copy of what `where.py` says, and it agrees.  It is wrong the day
        after somebody edits one of the other five.
        """
        for package in sorted(THE_COMMANDS):
            with self.subTest(package):
                source = _source(package, "cli.py")
                for node in ast.walk(ast.parse(source)):
                    if not isinstance(node, ast.Call):
                        continue
                    if getattr(node.func, "attr", "") != "add_argument":
                        continue
                    for argument in node.args:
                        if not isinstance(argument, ast.Constant):
                            continue
                        self.assertNotIn(
                            argument.value, ("--project", "--dir"),
                            "{}/cli.py declares {} by hand -- call "
                            "add_project_flag() so all three keep agreeing"
                            .format(package, argument.value))


class TestTheUsageLineOffersTheSameFlag(unittest.TestCase):
    """The line the bug was visible in, and the only help many people read."""

    def test_the_usage_line_names_the_flag_and_what_goes_after_it(self):
        for package in sorted(THE_COMMANDS):
            with self.subTest(package):
                usage = _flattened(_help(package).split("\n\n")[0])
                self.assertIn(
                    THE_FLAG, usage,
                    "{} --help opens with {!r}, which is not the flag the other "
                    "two offer".format(package, usage))

    def test_no_usage_line_asks_for_a_project_by_name(self):
        # `--project PROJECT` is the shape of the original stillworks bug, and
        # it is exactly what argparse writes if a metavar is ever dropped.
        for package in sorted(THE_COMMANDS):
            with self.subTest(package):
                self.assertNotIn("--project PROJECT",
                                 _flattened(_help(package)))


class TestAfterTheSubcommandToo(unittest.TestCase):
    """The other three of the six copies, at the parser the subcommands share.

    This one was hidden at `stillworks` and spelled out at `agentdiff`, so
    `stillworks check --help` did not mention a flag `stillworks check` takes.
    """

    def test_the_subcommand_help_offers_the_flag(self):
        for package, subcommand in sorted(THE_COMMANDS.items()):
            with self.subTest(package + " " + subcommand):
                self.assertIn(THE_FLAG,
                              _flattened(_help(package, subcommand)),
                              "{} {} --help does not mention the flag it takes"
                              .format(package, subcommand))

    def test_all_six_print_the_sentence_the_shared_module_holds(self):
        """Not a sentence retyped here -- the one where.py actually carries.

        Reworded there, all six reword together and this still passes, which is
        the point of it being one fact.  Reworded at a call site, it stops
        being one.
        """
        sentence = _the_shared_sentence()
        for package, subcommand in sorted(THE_COMMANDS.items()):
            for words in (_flattened(_help(package)),
                          _flattened(_help(package, subcommand))):
                with self.subTest(package + " " + subcommand):
                    self.assertIn(sentence, words)

    def test_no_command_still_describes_it_in_words_of_its_own(self):
        # The wording each of them had before there was one.  `unedit` called
        # it a project root, the other two called it a project directory, and
        # `stillworks` did not describe the sub-level flag at all.
        for package, subcommand in sorted(THE_COMMANDS.items()):
            for words in (_flattened(_help(package)),
                          _flattened(_help(package, subcommand))):
                with self.subTest(package + " " + subcommand):
                    self.assertNotIn("project root to operate on", words)


if __name__ == "__main__":
    unittest.main()
