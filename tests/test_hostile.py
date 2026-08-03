"""What stillworks does when the filesystem says no.

A lock file is written into the project being locked, and a project directory
is not always writable: a read-only CI checkout, a mounted image, a directory
somebody chmodded and forgot.  None of those are stillworks' problem to solve,
but a stack trace is not an answer to any of them.

Exit codes are the contract: 0 fine, 1 something detected, 2 usage or
environment error.  A traceback is none of the three.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stillworks.cli import main

MODULE = """
def add(a, b):
    return a + b
"""


class HostileFilesystemCase(unittest.TestCase):
    def setUp(self) -> None:
        self.project = tempfile.mkdtemp(prefix="stillworks-hostile-")
        with open(os.path.join(self.project, "mod.py"), "w", encoding="utf-8") as fh:
            fh.write(MODULE)

    def tearDown(self) -> None:
        try:
            os.chmod(self.project, 0o700)
        except OSError:
            pass
        shutil.rmtree(self.project, ignore_errors=True)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(list(argv))
            except SystemExit as exit_:
                code = exit_.code if isinstance(exit_.code, int) else 2
        return code, out.getvalue(), err.getvalue()

    def assertNoCrash(self, code, err):
        self.assertIn(code, (0, 1, 2), "exit {}: {}".format(code, err))
        self.assertNotIn("Traceback", err)


class TestUnwritableProject(HostileFilesystemCase):
    """The project directory cannot be written to."""

    def _make_readonly(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root ignores the permission bits this test relies on")
        os.chmod(self.project, 0o500)

    def test_lock_with_cmd_reports_instead_of_crashing(self):
        self._make_readonly()
        code, _, err = self.run_cli("lock", "--project", self.project, "--cmd", "true")
        self.assertNoCrash(code, err)
        self.assertEqual(code, 2)
        self.assertIn("stillworks:", err)

    def test_lock_with_failing_cmd_reports_instead_of_crashing(self):
        self._make_readonly()
        code, _, err = self.run_cli("lock", "--project", self.project, "--cmd", "exit 3")
        self.assertNoCrash(code, err)
        self.assertEqual(code, 2)

    def test_lock_with_a_target_module_reports_instead_of_crashing(self):
        self._make_readonly()
        code, _, err = self.run_cli("lock", "mod.py", "--project", self.project)
        self.assertNoCrash(code, err)
        self.assertEqual(code, 2)

    def test_the_message_names_the_directory(self):
        self._make_readonly()
        _, _, err = self.run_cli("lock", "--project", self.project, "--cmd", "true")
        self.assertIn(".stillworks", err)

    def test_check_on_a_project_with_no_lock_is_not_a_crash(self):
        code, _, err = self.run_cli("check", "--project", self.project)
        self.assertNoCrash(code, err)


class TestMissingProject(HostileFilesystemCase):
    def test_lock_in_a_directory_that_does_not_exist(self):
        gone = os.path.join(self.project, "nope", "deeper")
        code, _, err = self.run_cli("lock", "--project", gone, "--cmd", "true")
        self.assertNoCrash(code, err)

    def test_check_in_a_directory_that_does_not_exist(self):
        gone = os.path.join(self.project, "nope")
        code, _, err = self.run_cli("check", "--project", gone)
        self.assertNoCrash(code, err)

    def test_project_is_a_file_not_a_directory(self):
        code, _, err = self.run_cli(
            "lock", "--project", os.path.join(self.project, "mod.py"), "--cmd", "true")
        self.assertNoCrash(code, err)


class TestCommandTimeout(HostileFilesystemCase):
    """A recorded command that never finishes must not own the process."""

    def test_timeout_is_reachable_from_the_command_line(self):
        code, _, err = self.run_cli(
            "lock", "--project", self.project, "--cmd", "sleep 30", "--timeout", "1")
        self.assertNoCrash(code, err)
        self.assertEqual(code, 0)

    def test_a_timed_out_command_is_recorded_as_such(self):
        from stillworks import core
        out = core.run_cmd("sleep 30", cwd=self.project, timeout=1)
        self.assertEqual(out["exit"], -1)
        self.assertIn("timeout", out["stderr"])

    def test_timeout_must_be_positive(self):
        code, _, err = self.run_cli(
            "lock", "--project", self.project, "--cmd", "true", "--timeout", "0")
        self.assertEqual(code, 2)

    def test_default_timeout_still_applies_without_the_flag(self):
        from stillworks import core
        import inspect
        default = inspect.signature(core.run_cmd).parameters["timeout"].default
        self.assertTrue(default and default > 0)


class TestOddCommands(HostileFilesystemCase):
    def test_an_unparseable_command_is_not_a_crash(self):
        code, _, err = self.run_cli(
            "lock", "--project", self.project, "--cmd", "echo 'unbalanced")
        self.assertNoCrash(code, err)

    def test_an_empty_command_is_not_a_crash(self):
        code, _, err = self.run_cli("lock", "--project", self.project, "--cmd", "")
        self.assertNoCrash(code, err)

    def test_a_command_that_does_not_exist_is_recorded_not_raised(self):
        code, _, err = self.run_cli(
            "lock", "--project", self.project, "--cmd", "definitely-not-a-real-binary")
        self.assertNoCrash(code, err)


if __name__ == "__main__":
    unittest.main()
