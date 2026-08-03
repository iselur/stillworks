"""Tests for `stillworks tools` — the family install report."""

import io
import os
import sys
import types
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from stillworks import tools
from stillworks.cli import main


def _rows(**versions):
    """Detection rows with the versions the test wants, absent by default."""
    return [(c, d, p, versions.get(c)) for c, d, p in tools.FAMILY]


class TestVersionOf(unittest.TestCase):

    def setUp(self):
        self._which = tools.shutil.which
        self._run = tools.subprocess.run

    def tearDown(self):
        tools.shutil.which = self._which
        tools.subprocess.run = self._run

    def _fake(self, found, stdout=b"", exc=None):
        tools.shutil.which = lambda c: "/usr/bin/" + c if found else None

        def run(*a, **kw):
            if exc:
                raise exc
            return types.SimpleNamespace(stdout=stdout)
        tools.subprocess.run = run

    def test_stillworks_reports_its_own_running_version(self):
        # Never shells out — this is the copy the user is talking to.
        self._fake(found=False)
        self.assertEqual(tools._version_of("stillworks"), tools.__version__)

    def test_absent_command_is_none(self):
        self._fake(found=False)
        self.assertIsNone(tools._version_of("unedit"))

    def test_version_is_parsed_from_conventional_output(self):
        self._fake(found=True, stdout=b"agentlog 0.2.0\n")
        self.assertEqual(tools._version_of("agentlog"), "0.2.0")

    def test_bare_version_number_is_accepted(self):
        self._fake(found=True, stdout=b"1.4.2\n")
        self.assertEqual(tools._version_of("agentlog"), "1.4.2")

    def test_unparseable_output_is_unknown_not_missing(self):
        self._fake(found=True, stdout=b"usage: agentlog [-h]\n")
        self.assertEqual(tools._version_of("agentlog"), "?")

    def test_empty_output_is_unknown(self):
        self._fake(found=True, stdout=b"")
        self.assertEqual(tools._version_of("agentlog"), "?")

    def test_command_that_crashes_is_unknown_not_missing(self):
        self._fake(found=True, exc=OSError("boom"))
        self.assertEqual(tools._version_of("agentlog"), "?")

    def test_command_that_hangs_is_unknown_not_missing(self):
        self._fake(found=True, exc=tools.subprocess.TimeoutExpired("x", 5))
        self.assertEqual(tools._version_of("agentlog"), "?")

    def test_undecodable_output_does_not_crash(self):
        self._fake(found=True, stdout=b"\xff\xfe 0.3.0\n")
        self.assertEqual(tools._version_of("agentlog"), "0.3.0")

    def test_no_sibling_package_is_imported(self):
        # The extra must stay optional: importing a sibling would make it real.
        src = open(os.path.join(_REPO_ROOT, "stillworks", "tools.py")).read()
        for mod in ("unedit", "agentdiff", "agentlog"):
            self.assertNotIn("import " + mod, src)


class TestRender(unittest.TestCase):

    def test_every_tool_gets_a_line(self):
        out = tools.render(_rows(stillworks="0.1.0"))
        for command, _dist, _pitch in tools.FAMILY:
            self.assertIn(command, out)

    def test_missing_tool_shows_a_dash(self):
        out = tools.render(_rows(stillworks="0.1.0"))
        self.assertIn("—", out)

    def test_all_present_says_so_and_offers_nothing(self):
        out = tools.render(_rows(
            stillworks="0.1.0", unedit="0.1.0",
            agentdiff="0.1.0", agentlog="0.2.0"))
        self.assertIn("all four installed", out)
        self.assertNotIn("missing", out)
        self.assertNotIn("pip install", out)

    def test_partial_install_names_what_is_missing(self):
        out = tools.render(_rows(
            stillworks="0.1.0", unedit="0.1.0", agentlog="0.2.0"))
        self.assertIn("missing: agentdiff", out)
        self.assertNotIn("unedit,", out.split("missing:")[1])

    def test_partial_install_offers_the_exact_distribution_name(self):
        # agentdiff's PyPI name is suffixed; a user cannot guess it.
        out = tools.render(_rows(stillworks="0.1.0", unedit="0.1.0"))
        self.assertIn("agentdiff-cli", out)
        self.assertIn("agentlog-tool", out)

    def test_only_stillworks_present_recommends_the_extra_alone(self):
        out = tools.render(_rows(stillworks="0.1.0"))
        self.assertIn("pip install 'stillworks[all]'", out)
        self.assertNotIn("agentdiff-cli", out)

    def test_unknown_version_still_counts_as_installed(self):
        out = tools.render(_rows(stillworks="0.1.0", unedit="?"))
        self.assertNotIn("unedit", out.split("missing:")[1])

    def test_columns_line_up(self):
        rows = _rows(stillworks="0.1.0", unedit="0.10.11",
                     agentdiff="0.1.0", agentlog="0.2.0")
        starts = {line.index(pitch)
                  for line, (_c, _d, pitch, _v) in zip(
                      tools.render(rows).splitlines(), rows)}
        self.assertEqual(len(starts), 1)


class TestToolsCommand(unittest.TestCase):

    def setUp(self):
        self._stdout = sys.stdout
        sys.stdout = io.StringIO()

    def tearDown(self):
        sys.stdout = self._stdout

    def _out(self):
        return sys.stdout.getvalue()

    def test_exit_code_is_always_zero(self):
        self.assertEqual(main(["tools"]), 0)

    def test_output_mentions_the_running_stillworks(self):
        main(["tools"])
        self.assertIn(tools.__version__, self._out())

    def test_json_is_machine_readable_and_complete(self):
        import json
        self.assertEqual(main(["tools", "--json"]), 0)
        data = json.loads(self._out())
        self.assertEqual(len(data["tools"]), len(tools.FAMILY))
        for entry in data["tools"]:
            self.assertEqual(entry["installed"], entry["version"] is not None)
            self.assertIn("distribution", entry)

    def test_json_reports_stillworks_as_installed(self):
        import json
        main(["tools", "--json"])
        first = json.loads(self._out())["tools"][0]
        self.assertEqual(first["command"], "stillworks")
        self.assertTrue(first["installed"])


if __name__ == "__main__":
    unittest.main()
