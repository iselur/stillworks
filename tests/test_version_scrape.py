"""A version number must come from a command that answered, not one that failed.

`stillworks tools` finds each sibling and asks it `--version`.  The answer is
scraped out of whatever the command printed.  Two things were wrong with that.

First, the exit code was never looked at.  A sibling too old to know the flag
does not print a version — argparse prints a usage error and exits 2 — and that
error text routinely contains a number: "error: argument --context: expected 1
argument".  The scrape found the `1` and the report said the tool was installed
at version 1.  Confidently, in the version column, next to four correct ones.

Second, the scrape read the line backwards, so the *last* number won.  A
`--version` string of the common form "agentlog 0.2.2 (python 3.11.4)" reported
the interpreter's version as the tool's.

Both are the same mistake in different clothes: printing a number that is not
the answer, in the place where the answer goes.  Nothing about the output says
it is wrong, which is what makes it worth a test.  "unknown" is a fine thing for
this command to say; a wrong number is not.
"""

import json
import os
import stat
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from stillworks import tools


class _WithAFakeSibling(unittest.TestCase):
    """Run `_version_of` against a real command we wrote, not a mock.

    The bug lives in what happens to a subprocess's exit code, so the test uses
    a real subprocess.  `_neighbour` is pointed straight at the script, which
    also keeps the test hermetic: it does not matter what is on PATH.
    """

    def sibling(self, body, name="agentdiff"):
        d = tempfile.mkdtemp(prefix="sw-sibling-")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        path = os.path.join(d, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n" + body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)

        old = tools._neighbour
        tools._neighbour = lambda command: path if command == name else None
        self.addCleanup(setattr, tools, "_neighbour", old)
        return path


class TestACommandThatFailedHasNotToldUsItsVersion(_WithAFakeSibling):

    # The exact shape of the real case: a sibling from before `--version`
    # existed.  argparse prints this and exits 2.
    OLD_TOOL = (
        'echo "usage: agentdiff [-h] [--context N] PATH" >&2\n'
        'echo "agentdiff: error: argument --context: expected 1 argument" >&2\n'
        'exit 2\n'
    )

    def test_the_number_in_the_error_is_not_a_version(self):
        self.sibling(self.OLD_TOOL)
        self.assertEqual(tools._version_of("agentdiff"), "?",
                         "reported a number scraped out of an error message")

    def test_it_still_counts_as_installed(self):
        # Absent and broken are different situations with different advice.
        self.sibling(self.OLD_TOOL)
        self.assertIsNotNone(tools._version_of("agentdiff"))

    def test_the_report_shows_it_as_unknown(self):
        self.sibling(self.OLD_TOOL)
        rows = [(c, d, p, tools._version_of(c)) for c, d, p in tools.FAMILY]
        line = [l for l in tools.render(rows).splitlines()
                if l.strip().startswith("agentdiff")][0]
        self.assertIn("?", line)

    def test_json_says_installed_with_an_unknown_version(self):
        self.sibling(self.OLD_TOOL)
        rows = [(c, d, p, tools._version_of(c)) for c, d, p in tools.FAMILY]
        row = [r for r in rows if r[0] == "agentdiff"][0]
        self.assertEqual(row[3], "?")

    def test_output_from_a_failed_call_is_not_trusted_even_if_it_looks_right(self):
        # `action='version'` exits 0.  A non-zero exit means the flag was not
        # understood, so anything version-shaped in the output is coincidence.
        self.sibling('echo "agentdiff 9.9.9"\nexit 2\n')
        self.assertEqual(tools._version_of("agentdiff"), "?")

    def test_a_command_that_answers_still_reports_its_version(self):
        self.sibling('echo "agentdiff 0.1.2"\n')
        self.assertEqual(tools._version_of("agentdiff"), "0.1.2")

    def test_a_command_killed_by_a_signal_is_unknown(self):
        self.sibling('kill -TERM $$\n')
        self.assertEqual(tools._version_of("agentdiff"), "?")


class TestTheVersionIsTheToolsOwn(_WithAFakeSibling):
    """Read the line forwards.  The first number after the name is the answer."""

    def test_a_trailing_interpreter_version_is_not_the_tools_version(self):
        self.sibling('echo "agentdiff 0.1.2 (python 3.11.4)"\n')
        self.assertEqual(tools._version_of("agentdiff"), "0.1.2",
                         "reported the interpreter's version as the tool's")

    def test_a_bracketed_version_loses_its_brackets(self):
        self.sibling('echo "agentdiff [0.1.2]"\n')
        self.assertEqual(tools._version_of("agentdiff"), "0.1.2")

    def test_a_trailing_full_stop_is_not_part_of_the_number(self):
        self.sibling('echo "agentdiff version 0.1.2."\n')
        self.assertEqual(tools._version_of("agentdiff"), "0.1.2")

    def test_a_leading_v_is_not_part_of_the_number(self):
        self.sibling('echo "agentdiff v0.1.2"\n')
        self.assertEqual(tools._version_of("agentdiff"), "0.1.2")

    def test_a_suffixed_distribution_name_is_still_skipped(self):
        self.sibling('echo "agentdiff-cli 0.1.2"\n')
        self.assertEqual(tools._version_of("agentdiff"), "0.1.2")

    def test_a_prerelease_survives_intact(self):
        self.sibling('echo "agentdiff 0.2.0rc1"\n')
        self.assertEqual(tools._version_of("agentdiff"), "0.2.0rc1")

    def test_output_with_no_number_at_all_is_unknown(self):
        self.sibling('echo "agentdiff"\n')
        self.assertEqual(tools._version_of("agentdiff"), "?")


class TestTheWholeCommandStillReadsRight(_WithAFakeSibling):

    def test_tools_exits_zero_with_a_broken_sibling(self):
        import io
        import types
        from contextlib import redirect_stdout
        self.sibling(TestACommandThatFailedHasNotToldUsItsVersion.OLD_TOOL)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = tools.cmd_tools(types.SimpleNamespace(json=False))
        self.assertEqual(rc, 0)
        self.assertIn("agentdiff", buf.getvalue())

    def test_json_stays_parseable_with_a_broken_sibling(self):
        import io
        import types
        from contextlib import redirect_stdout
        self.sibling(TestACommandThatFailedHasNotToldUsItsVersion.OLD_TOOL)
        buf = io.StringIO()
        with redirect_stdout(buf):
            tools.cmd_tools(types.SimpleNamespace(json=True))
        got = json.loads(buf.getvalue())
        row = [t for t in got["tools"] if t["command"] == "agentdiff"][0]
        self.assertTrue(row["installed"])
        self.assertEqual(row["version"], "?")


if __name__ == "__main__":
    unittest.main()
