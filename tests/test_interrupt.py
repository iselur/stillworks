"""What happens when somebody presses ctrl-c.

Recording a baseline runs the commands being recorded, and those are test
suites and builds — the longest-running thing anybody points this tool at.
Interrupting one is ordinary.  Being answered with twenty lines of interpreter
internals ending in ``KeyboardInterrupt`` is not: it reads as a crash, and sends
people looking for the bug they just caused on purpose.

The exit code matters more here than anywhere else in the family, because this
tool's codes are already load-bearing: `check` exits 0 for unchanged and 1 for
changed, and it gets used as `stillworks check && deploy`.  An abandoned check
returning 0 would deploy on the strength of a check nobody finished.  130 is the
shell's own spelling of "stopped by ctrl-c", and it is neither of the two
answers a caller is looking for.
"""

import io
import os
import signal
import subprocess
import sys
import tempfile
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stillworks import cli  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestCtrlC(unittest.TestCase):

    def setUp(self):
        self.project = tempfile.mkdtemp(prefix="stillworks_interrupt_")
        self.real = {name: getattr(cli, name)
                     for name in ("cmd_lock", "cmd_check", "cmd_status")}

    def tearDown(self):
        for name, fn in self.real.items():
            setattr(cli, name, fn)
        shutil.rmtree(self.project, ignore_errors=True)

    def _interrupt(self, name):
        def boom(*args, **kwargs):
            raise KeyboardInterrupt
        setattr(cli, name, boom)

    def _code_for(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(args)
        return code, out.getvalue() + err.getvalue()

    def test_an_abandoned_check_is_neither_pass_nor_fail(self):
        # 0 means unchanged and 1 means changed.  Ctrl-c means neither, and
        # `stillworks check && deploy` had better agree.
        self._interrupt("cmd_check")
        code, _ = self._code_for(["--project", self.project, "check"])
        self.assertEqual(code, 130)
        self.assertNotIn(code, (0, 1))

    def test_it_does_not_print_a_traceback(self):
        self._interrupt("cmd_check")
        _, text = self._code_for(["--project", self.project, "check"])
        self.assertNotIn("Traceback", text)

    def test_the_other_commands_answer_the_same_way(self):
        for name, args in (("cmd_lock", ["lock"]), ("cmd_status", ["status"])):
            self._interrupt(name)
            code, _ = self._code_for(["--project", self.project] + args)
            self.assertEqual(code, 130, args)

    def test_ctrl_c_while_a_recorded_command_is_running(self):
        # The real shape of it: `stillworks lock --cmd 'pytest'` on a suite
        # that turns out to take four minutes.  The signal reaches this process
        # and the child both, which is what a terminal does to a process group.
        env = dict(os.environ, PYTHONPATH=_ROOT)
        proc = subprocess.Popen(
            [sys.executable, "-m", "stillworks", "--project", self.project,
             "lock", "--cmd", "{} -c \"import time; time.sleep(30)\"".format(
                 sys.executable)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=_ROOT)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass                      # good: it is busy, which is the point
        proc.send_signal(signal.SIGINT)
        _, err = proc.communicate(timeout=60)
        self.assertNotIn(b"KeyboardInterrupt", err,
                         err.decode("utf-8", "replace"))
        self.assertNotIn(b"Traceback", err, err.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
