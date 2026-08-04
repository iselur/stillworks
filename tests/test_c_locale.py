"""What stillworks does on a machine whose locale says ASCII.

A container with no locale set is the ordinary case, not the exotic one: it is
what CI runs on, what a Dockerfile without `ENV LANG` gives you, and what cron
hands a hook.  Python takes the locale at its word there — stdout encodes as
ASCII, and `text=True` decodes a subprocess's output as ASCII too.

For this tool the second one is the serious one, and it is not a crash.  A
lockfile exists to be carried: recorded on a laptop, replayed in CI.  If the
codec that reads a command's output is chosen by whatever locale the machine
happens to have, then the same command produces two different recordings on two
machines, `stillworks check` reports a behavior change that did not happen, and
the one thing this tool is for stops being true.

Everything here runs the real command in a real subprocess with that
environment, because the codec is chosen when the process starts and cannot be
faked from inside one.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ascii_env():
    """The environment of a container nobody gave a locale to."""
    env = dict(os.environ)
    env.update(LC_ALL="C", LANG="C", LANGUAGE="C",
               PYTHONCOERCECLOCALE="0",   # or Python quietly upgrades C to C.UTF-8
               PYTHONUTF8="0",            # or UTF-8 mode overrides the locale
               PYTHONPATH=_ROOT)
    env.pop("PYTHONIOENCODING", None)
    return env


def _utf8_env():
    """The environment of the laptop the baseline was recorded on."""
    env = dict(os.environ)
    env.update(LC_ALL="C.UTF-8", LANG="C.UTF-8", PYTHONPATH=_ROOT)
    env.pop("PYTHONIOENCODING", None)
    return env


class TestAnAsciiMachine(unittest.TestCase):

    def setUp(self):
        self.project = tempfile.mkdtemp(prefix="sw_locale_")

    def tearDown(self):
        shutil.rmtree(self.project, ignore_errors=True)

    def run_sw(self, *args, **kwargs):
        env = kwargs.pop("env", None) or _ascii_env()
        result = subprocess.run(
            [sys.executable, "-m", "stillworks", "--project", self.project]
            + list(args),
            capture_output=True, text=True, env=env, cwd=_ROOT)
        self.assertNotIn("Traceback", result.stderr,
                         "{}: {}".format(args, result.stderr))
        return result

    def _echo_accented(self):
        """A command whose output is not ASCII, which most real ones are not.

        It writes UTF-8 whatever the locale says, the way `git`, `node`, `go
        test` and every compiler do — so what is under test here is how
        stillworks reads it, and not whether the child could speak.
        """
        script = os.path.join(self.project, "greet.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("import sys\n"
                     "sys.stdout.reconfigure(encoding='utf-8')\n"
                     "print('caf\\u00e9 \\u8a2d\\u5b9a')\n")
        return "{} {}".format(sys.executable, script)

    def test_tools_prints_without_a_traceback(self):
        # Nothing here touches a project.  The em dash is ours.
        result = self.run_sw("tools")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_locking_a_command_that_speaks_french_does_not_crash(self):
        result = self.run_sw("lock", "--cmd", self._echo_accented())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_recording_is_the_same_on_both_machines(self):
        # The claim the whole tool rests on: a baseline is portable.  Record
        # the same command under each locale and the bytes stored must match,
        # or CI fails a build over the locale of the machine that ran it.
        recordings = []
        for env in (_utf8_env(), _ascii_env()):
            shutil.rmtree(os.path.join(self.project, ".stillworks"),
                          ignore_errors=True)
            self.run_sw("lock", "--cmd", self._echo_accented(), env=env)
            with open(os.path.join(self.project, ".stillworks", "lock.json"),
                      encoding="utf-8") as fh:
                lock = json.load(fh)
            recordings.append([r.get("out") for r in lock.get("records", [])])
        self.assertEqual(recordings[0], recordings[1])
        self.assertIn("café", json.dumps(recordings[0], ensure_ascii=False))

    def test_a_baseline_from_a_utf8_machine_still_passes_on_this_one(self):
        # The same thing said from the other end, because this is how it would
        # actually be met: locked on a laptop, checked in a container.
        self.run_sw("lock", "--cmd", self._echo_accented(), env=_utf8_env())
        result = self.run_sw("check")
        self.assertEqual(result.returncode, 0,
                         result.stdout + result.stderr)

    def test_check_reports_without_a_traceback(self):
        self.run_sw("lock", "--cmd", self._echo_accented())
        for args in (("check",), ("check", "--json"), ("status",), ("report",)):
            self.run_sw(*args)

    def test_the_json_stays_json(self):
        self.run_sw("lock", "--cmd", self._echo_accented())
        for args in (("check", "--json"), ("tools", "--json")):
            json.loads(self.run_sw(*args).stdout)


if __name__ == "__main__":
    unittest.main()
