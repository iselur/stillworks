"""A project directory that does not exist is a typo, not a request to make one.

`--project` names where `.stillworks/lock.json` lives, and the directory-making
was unconditional, so `stillworks --project ~/aap lock --cmd 'make test'`
created `~/aap/.stillworks/` and reported `locked 1 records` — a lockfile in a
directory nobody meant to have, while the project it was supposed to guard
stayed unlocked.  The reading commands had the mirror problem: `check` against a
path that is not there answers `no lockfile — run stillworks lock first`, which
is what an un-locked project says.  So the typo tells you to lock, locking
appears to work, and the next `check` passes against a baseline of nothing.

Only a directory somebody named can be wrong this way.  The default is the
current directory, which exists by definition.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestAProjectDirectoryThatIsNotThere(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stillworks_missing_")
        self.missing = os.path.join(self.tmp, "typoo")
        self.here = os.path.join(self.tmp, "here")
        os.makedirs(self.here)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_stillworks(self, *args):
        env = dict(os.environ, PYTHONPATH=_ROOT)
        return subprocess.run(
            [sys.executable, "-m", "stillworks"] + list(args),
            capture_output=True, text=True, encoding="utf-8",
            cwd=self.here, env=env, timeout=120)

    def test_lock_does_not_create_the_directory(self):
        self.run_stillworks("--project", self.missing, "lock", "--cmd",
                            "{} -c pass".format(sys.executable))
        self.assertFalse(os.path.exists(self.missing),
                         "a mistyped --project built the directory it named")

    def test_lock_says_so_rather_than_reporting_success(self):
        result = self.run_stillworks("--project", self.missing, "lock", "--cmd",
                                     "{} -c pass".format(sys.executable))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("locked", result.stdout.lower(), result.stdout)
        self.assertIn("typoo", result.stdout + result.stderr,
                      result.stdout + result.stderr)

    def test_check_does_not_call_it_an_unlocked_project(self):
        # `no lockfile — run stillworks lock first` is the answer for a real
        # project that has not been locked yet.  Saying it about a path that is
        # not there sends people to lock the wrong thing.
        result = self.run_stillworks("--project", self.missing, "check")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("no lockfile", result.stdout.lower(), result.stdout)
        self.assertIn("typoo", result.stdout + result.stderr,
                      result.stdout + result.stderr)

    def test_no_command_answers_with_a_traceback(self):
        for command in ("lock", "check", "accept", "report", "tools"):
            with self.subTest(command=command):
                args = ["--project", self.missing, command]
                if command == "lock":
                    args += ["--cmd", "{} -c pass".format(sys.executable)]
                elif command == "accept":
                    args += ["some#1"]
                result = self.run_stillworks(*args)
                self.assertNotIn("Traceback", result.stderr, result.stderr)

    def test_a_file_where_a_directory_should_be_says_so(self):
        path = os.path.join(self.tmp, "a-file")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("not a directory\n")
        result = self.run_stillworks("--project", path, "check")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("not a directory",
                      (result.stdout + result.stderr).lower(),
                      result.stdout + result.stderr)

    def test_a_directory_that_is_there_still_works(self):
        # The other half: an ordinary run must be untouched.
        result = self.run_stillworks("--project", self.here, "lock", "--cmd",
                                     "{} -c pass".format(sys.executable))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(
            os.path.isfile(os.path.join(self.here, ".stillworks", "lock.json")),
            result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
