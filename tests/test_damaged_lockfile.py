"""A lockfile you cannot read is a sentence, not a traceback.

`.stillworks/lock.json` ships in the repo — that is the whole point of it, and
it is what makes `check` work in CI and in a reviewer's checkout.  A file that
ships in a repo gets merged, and a merge that goes badly leaves `<<<<<<< HEAD`
in the middle of it.  It also gets written: a `lock` that is interrupted, or
that runs out of disk, leaves a file that starts as valid JSON and stops.

Either way the answer was twenty lines of interpreter internals ending in
`json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)`,
which names a column in a file it does not name, and reads as a bug in
stillworks rather than a conflict in your repo.

Exit code matters here as much as the words.  `check` gates a merge: 0 is
"nothing moved" and 1 is "something did", so a crash landing on either of those
is a lie.  A lockfile that cannot be read is neither answer — it is 2, the same
as any other usage problem.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_READING_COMMANDS = ["check", "status", "report"]


class TestALockfileThatCannotBeRead(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stillworks_damaged_")
        self.project = os.path.join(self.tmp, "app")
        os.makedirs(self.project)
        with open(os.path.join(self.project, "a.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("def value():\n    return 1\n")
        result = self.run_stillworks("lock", "--cmd",
                                     "{} -c pass".format(sys.executable))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def lockfile(self):
        return os.path.join(self.project, ".stillworks", "lock.json")

    def run_stillworks(self, *args):
        env = dict(os.environ, PYTHONPATH=_ROOT)
        return subprocess.run(
            [sys.executable, "-m", "stillworks", "--project", self.project]
            + list(args),
            capture_output=True, text=True, encoding="utf-8",
            cwd=self.tmp, env=env, timeout=120)

    def _leave_a_merge_conflict(self):
        with open(self.lockfile, encoding="utf-8") as fh:
            body = fh.read()
        with open(self.lockfile, "w", encoding="utf-8") as fh:
            fh.write("<<<<<<< HEAD\n" + body + "=======\n" + body
                     + ">>>>>>> feature/tax-api\n")

    def _truncate_it(self):
        with open(self.lockfile, encoding="utf-8") as fh:
            body = fh.read()
        with open(self.lockfile, "w", encoding="utf-8") as fh:
            fh.write(body[:len(body) // 2])

    def test_a_merge_conflict_is_not_a_traceback(self):
        self._leave_a_merge_conflict()
        for command in _READING_COMMANDS:
            with self.subTest(command=command):
                result = self.run_stillworks(command)
                self.assertNotIn("Traceback", result.stderr, result.stderr)
                self.assertNotIn("JSONDecodeError", result.stderr,
                                 result.stderr)

    def test_a_truncated_lockfile_is_not_a_traceback(self):
        self._truncate_it()
        for command in _READING_COMMANDS:
            with self.subTest(command=command):
                result = self.run_stillworks(command)
                self.assertNotIn("Traceback", result.stderr, result.stderr)

    def test_check_does_not_answer_the_merge_gate(self):
        # 0 means nothing moved and 1 means something did.  Neither is true of
        # a lockfile nobody can read, and both would be believed by a script.
        self._leave_a_merge_conflict()
        result = self.run_stillworks("check")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_it_names_the_file(self):
        # "Expecting value: line 1 column 1" names a column in a file it does
        # not name.  The next thing anybody wants is which file to go and open.
        self._leave_a_merge_conflict()
        result = self.run_stillworks("check")
        self.assertIn("lock.json", result.stdout + result.stderr,
                      result.stdout + result.stderr)

    def test_it_suggests_the_way_out(self):
        # There is exactly one: the lockfile is a recording, so re-record it.
        self._leave_a_merge_conflict()
        result = self.run_stillworks("check")
        self.assertIn("lock", (result.stdout + result.stderr).lower(),
                      result.stdout + result.stderr)

    def test_a_lockfile_that_is_a_directory_is_not_a_traceback(self):
        # An interrupted tool, a bad rsync, a container mount — a path can be
        # the wrong kind of thing without anybody typing it wrong.
        os.remove(self.lockfile)
        os.makedirs(self.lockfile)
        for command in _READING_COMMANDS:
            with self.subTest(command=command):
                result = self.run_stillworks(command)
                self.assertNotIn("Traceback", result.stderr, result.stderr)

    def test_a_lockfile_holding_json_that_is_not_a_lock_is_refused(self):
        # Valid JSON, wrong shape — `null`, or a list, or someone's config.
        # Parsing succeeding is not the same as the file being a lockfile.
        for body in ("null", "[]", '"hello"', '{"unrelated": true}'):
            with self.subTest(body=body):
                with open(self.lockfile, "w", encoding="utf-8") as fh:
                    fh.write(body)
                result = self.run_stillworks("check")
                self.assertNotIn("Traceback", result.stderr, result.stderr)
                self.assertNotEqual(result.returncode, 0,
                                    result.stdout + result.stderr)

    def test_a_good_lockfile_still_checks(self):
        # The other half: this must not become a tool that refuses to work.
        result = self.run_stillworks("check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_lock_can_still_replace_a_damaged_one(self):
        # `lock` writes rather than reads, so it is the way out of this state
        # and must not be blocked by it.
        self._leave_a_merge_conflict()
        result = self.run_stillworks("lock", "--cmd",
                                     "{} -c pass".format(sys.executable))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(self.lockfile, encoding="utf-8") as fh:
            json.load(fh)               # readable again


if __name__ == "__main__":
    unittest.main()
