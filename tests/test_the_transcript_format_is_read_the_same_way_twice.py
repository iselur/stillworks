"""Two tools read the same two log formats.  This is what makes it one reading.

Claude Code and Codex each write a session to a JSONL file, and two packages in
this family read those files: `agentlog` reads a finished session and says what
happened in it, `agentwatch` tails a live one and says what is happening now.
Different questions, different output, same two formats -- and for a long time
two independent readings of them.

They had drifted the way copies do.  The timestamp reader was written twice,
guarding on `isinstance` in one and catching `AttributeError` in the other, each
with its own paragraph explaining why it assumed UTC.  The apply_patch scanner
was twenty-one identical lines in both files until one of the two was taught
that a patch envelope can arrive inside a JavaScript string literal and the
other was not -- after which one tool reported the edit and the other reported
none, on the same log line, for a year.  Five regexes and dicts were declared
twice.  Nobody decided any of that.

So the facts of the formats moved into `transcript.py` and the two views stayed
where they were.  It has to stay copied: nothing in this family imports outside
its own package -- the promise `pip install stillworks` makes, enforced by
`test_every_import_is_stdlib_or_the_packages_own` -- so a shared module is not
on offer.  What is on offer is a copy that cannot drift.

Three things get checked, because pinning the bytes is not enough on its own:

  * the two `transcript.py` are byte-identical, so a fix made in one is a fix in
    both or it is a failing test;
  * both readers actually go through it, because the cheapest way to undo all of
    this is to leave the file sitting there unread; and
  * neither reader has quietly grown its own copy of a format fact back, because
    that is exactly how the first duplication started -- one regex, in the file
    that needed it, at the moment it was needed.

What this file does not say is that the reading is *correct*.  Sameness is not
correctness, and the copy that made this worth writing was perfectly consistent
right up until it wasn't.  Each source repository carries its own tests for what
the answers should be; this one only says there is one answer.
"""

from __future__ import annotations

import ast
import hashlib
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# The two packages that read a session transcript, and the module in each that
# holds the view -- the part that stays two.
THE_READERS = {"agentlog": "parser.py", "agentwatch": "events.py"}

# What a reader is allowed to ask the format about.  A ninth name here is a
# decision about the seam rather than something that accumulated.
THE_INTERFACE = {
    "parse_time",
    "tool_path",
    "is_write_tool",
    "is_work_call",
    "script_commands",
    "script_workdir",
    "patched_files",
    "script_failed",
}

# Text that only appears in code that is reading one of these two formats: the
# patch envelope's marker, the fields a Codex snippet carries, the tool that
# spells its path differently, and how a failure announces itself.  A reader
# with any of these in a module-level definition has started a second copy.
THE_FORMAT_FACTS = (
    "*** ",
    "notebook_path",
    "exec_command",
    "apply_patch",
    "script failed",
    "workdir",
)


def _path(package, module):
    return os.path.join(_ROOT, package, module)


def _source(package, module):
    with open(_path(package, module), encoding="utf-8") as fh:
        return fh.read()


class TestTheyAreOneFile(unittest.TestCase):

    def test_both_packages_carry_it(self):
        missing = [p for p in THE_READERS
                   if not os.path.exists(_path(p, "transcript.py"))]
        self.assertEqual(missing, [],
                         "packages with no transcript.py: {}".format(missing))

    def test_byte_for_byte(self):
        digests = {}
        for package in THE_READERS:
            with open(_path(package, "transcript.py"), "rb") as fh:
                digests.setdefault(
                    hashlib.sha256(fh.read()).hexdigest(), []).append(package)
        self.assertEqual(
            len(digests), 1,
            "transcript.py has drifted into {} versions:\n  {}\nA fix belongs "
            "in both: rsync the source repo you fixed, then the other.\n"
            .format(len(digests),
                    "\n  ".join(sorted(", ".join(sorted(group))
                                       for group in digests.values()))))

    def test_the_interface_is_the_eight_names_both_readers_were_promised(self):
        # Read off the copy, not imported, so this says the same thing whether
        # or not the packages are installed.
        tree = ast.parse(_source("agentlog", "transcript.py"))
        public = {node.name for node in tree.body
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and not node.name.startswith("_")}
        self.assertEqual(
            public, THE_INTERFACE,
            "transcript.py's interface changed.  Adding a name is fine, but it "
            "is a name both readers now have to be understood against -- say so "
            "here.")


class TestBothReadersGoThroughIt(unittest.TestCase):
    """The file existing is not the same as the file being used."""

    def _imported(self, package, module):
        tree = ast.parse(_source(package, module))
        return {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module == "transcript"
                for alias in node.names}

    def test_each_reader_asks_the_format_module(self):
        for package, module in sorted(THE_READERS.items()):
            with self.subTest(package):
                asked = self._imported(package, module)
                self.assertTrue(
                    asked, "{}/{} stopped importing from transcript.py"
                    .format(package, module))
                self.assertLessEqual(
                    asked, THE_INTERFACE,
                    "{}/{} reaches past the interface into transcript's "
                    "privates: {}".format(package, module,
                                          sorted(asked - THE_INTERFACE)))

    def test_between_them_they_use_all_of_it(self):
        # A name nothing asks for is a name that is wrong without anyone
        # finding out.  `script_workdir` has one caller today and is kept
        # anyway -- but one caller, not none.
        used = set()
        for package, module in THE_READERS.items():
            used |= self._imported(package, module)
        self.assertEqual(
            THE_INTERFACE - used, set(),
            "transcript.py offers names no reader asks for: {}"
            .format(sorted(THE_INTERFACE - used)))


class TestNeitherReaderKeepsItsOwnCopy(unittest.TestCase):
    """How the first duplication started: one regex, where it was needed."""

    def test_no_reader_declares_a_format_fact_of_its_own(self):
        for package, module in sorted(THE_READERS.items()):
            with self.subTest(package):
                source = _source(package, module)
                tree = ast.parse(source)
                for node in tree.body:
                    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                        continue
                    # Backslashes dropped first: a regex spells the patch
                    # marker `\*\*\* `, which is the same fact and does not
                    # contain the string it is about.
                    text = (ast.get_source_segment(source, node) or "")
                    text = text.replace("\\", "")
                    for fact in THE_FORMAT_FACTS:
                        self.assertNotIn(
                            fact, text,
                            "{}/{} declares {!r} again -- that fact belongs in "
                            "transcript.py, where the other reader can see it"
                            .format(package, module, fact))


if __name__ == "__main__":
    unittest.main()
