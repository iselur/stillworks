"""Tests for stillworks CLI (cli.main() driven in-process)."""

import io
import os
import sys
import tempfile
import types
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from stillworks import core
from stillworks.cli import main


class TestCLIExitCodes(unittest.TestCase):
    """Drive cli.main() in-process and verify exit codes."""

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _project_with_module(self, src="def add(x: int, y: int) -> int:\n    return x + y\n"):
        """Return (tmpdir, module_path) with src written to mymod.py."""
        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "mymod.py")
        with open(path, "w") as f:
            f.write(src)
        return tmpdir, path

    def _lock(self, tmpdir, path, fuzz=3):
        return main(["--project", tmpdir, "lock", path, "--fuzz", str(fuzz)])

    # ------------------------------------------------------------------
    # lock
    # ------------------------------------------------------------------

    def test_lock_no_target_returns_2(self):
        # No TARGET and no --cmd: usage error.
        with tempfile.TemporaryDirectory() as tmpdir:
            result = main(["--project", tmpdir, "lock"])
            self.assertEqual(result, 2)

    def test_lock_fuzz_run_without_target_returns_2(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = main(["--project", tmpdir, "lock", "--fuzz", "3"])
            self.assertEqual(result, 2)

    def test_lock_success_returns_0(self):
        tmpdir, path = self._project_with_module()
        try:
            result = self._lock(tmpdir, path)
            self.assertEqual(result, 0)
            # Lockfile must exist.
            self.assertTrue(os.path.exists(core.lock_path(tmpdir)))
        finally:
            import shutil; shutil.rmtree(tmpdir)

    def test_lock_bad_module_returns_2(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = main(["--project", tmpdir, "lock", "nonexistent.py", "--fuzz", "3"])
            self.assertEqual(result, 2)

    # ------------------------------------------------------------------
    # check
    # ------------------------------------------------------------------

    def test_check_no_lockfile_returns_2(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = main(["--project", tmpdir, "check"])
            self.assertEqual(result, 2)

    def test_check_pass_returns_0(self):
        tmpdir, path = self._project_with_module()
        try:
            self._lock(tmpdir, path)
            result = main(["--project", tmpdir, "check"])
            self.assertEqual(result, 0)
        finally:
            import shutil; shutil.rmtree(tmpdir)

    def test_check_changed_returns_1(self):
        tmpdir, path = self._project_with_module()
        try:
            self._lock(tmpdir, path)
            # Change the function so behavior differs.
            with open(path, "w") as f:
                f.write("def add(x: int, y: int) -> int:\n    return x + y + 1000\n")
            result = main(["--project", tmpdir, "check"])
            self.assertEqual(result, 1)
        finally:
            import shutil; shutil.rmtree(tmpdir)

    def test_check_json_flag_still_returns_0_on_pass(self):
        tmpdir, path = self._project_with_module()
        try:
            self._lock(tmpdir, path)
            result = main(["--project", tmpdir, "check", "--json"])
            self.assertEqual(result, 0)
        finally:
            import shutil; shutil.rmtree(tmpdir)

    # ------------------------------------------------------------------
    # accept
    # ------------------------------------------------------------------

    def test_accept_no_ids_and_no_all_returns_2(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = main(["--project", tmpdir, "accept"])
            self.assertEqual(result, 2)

    def test_accept_all_returns_0(self):
        tmpdir, path = self._project_with_module()
        try:
            self._lock(tmpdir, path)
            with open(path, "w") as f:
                f.write("def add(x: int, y: int) -> int:\n    return x + y + 1000\n")
            result = main(["--project", tmpdir, "accept", "--all"])
            self.assertEqual(result, 0)
        finally:
            import shutil; shutil.rmtree(tmpdir)

    def test_accept_specific_id_returns_0(self):
        tmpdir, path = self._project_with_module()
        try:
            self._lock(tmpdir, path)
            with open(path, "w") as f:
                f.write("def add(x: int, y: int) -> int:\n    return x + y + 1000\n")
            # Run check first so accept knows what changed.
            main(["--project", tmpdir, "check"])
            lock = core.load_lock(tmpdir)
            first_id = lock["records"][0]["id"]
            result = main(["--project", tmpdir, "accept", first_id])
            self.assertEqual(result, 0)
        finally:
            import shutil; shutil.rmtree(tmpdir)

    def test_accept_no_lockfile_returns_2(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = main(["--project", tmpdir, "accept", "--all"])
            self.assertEqual(result, 2)

    # ------------------------------------------------------------------
    # report
    # ------------------------------------------------------------------

    def test_report_no_lockfile_returns_2(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = main(["--project", tmpdir, "report"])
            self.assertEqual(result, 2)

    def test_report_with_lockfile_returns_0(self):
        tmpdir, path = self._project_with_module()
        try:
            self._lock(tmpdir, path)
            result = main(["--project", tmpdir, "report"])
            self.assertEqual(result, 0)
        finally:
            import shutil; shutil.rmtree(tmpdir)

    def test_report_to_file(self):
        tmpdir, path = self._project_with_module()
        try:
            self._lock(tmpdir, path)
            out_file = os.path.join(tmpdir, "report.md")
            result = main(["--project", tmpdir, "report", "-o", out_file])
            self.assertEqual(result, 0)
            self.assertTrue(os.path.exists(out_file))
            with open(out_file) as f:
                content = f.read()
            self.assertIn("stillworks evidence report", content)
        finally:
            import shutil; shutil.rmtree(tmpdir)

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def test_status_no_lockfile_returns_0(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = main(["--project", tmpdir, "status"])
            self.assertEqual(result, 0)

    def test_status_with_lockfile_returns_0(self):
        tmpdir, path = self._project_with_module()
        try:
            self._lock(tmpdir, path)
            result = main(["--project", tmpdir, "status"])
            self.assertEqual(result, 0)
        finally:
            import shutil; shutil.rmtree(tmpdir)

    # ------------------------------------------------------------------
    # no subcommand
    # ------------------------------------------------------------------

    def test_no_command_returns_0(self):
        result = main([])
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
