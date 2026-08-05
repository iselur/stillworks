"""What `main` is allowed to assume about the shell it runs inside.

`shell.py` is the same file in all five packages, so this is the same test in
all five -- byte for byte, which is why it works out its own package name
rather than writing it down.  A copy that has quietly diverged costs an
afternoon; a copy with the same test pointed at it does not get the chance.

The interface is two functions and two numbers, and everything a caller must
know is here: what comes back for each way a command can end, that the work is
handed the argv it was given, that whatever was printed is flushed even when
the run fell over, and that a stream the locale claimed was ASCII is fixed
before the work starts rather than after it has already printed.

The exit codes are checked against CPython's own rule for `SystemExit`, not
against a rule of ours, because the whole point of catching it here is that
nobody at a terminal can tell the difference.  tests/test_interrupt.py in the
packages that have one covers the same ground from the outside, through a real
process; this covers it from the inside, where the cases are cheap.
"""

from __future__ import annotations

import importlib
import io
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

shell = importlib.import_module(os.path.basename(_ROOT) + ".shell")
as_typed = shell.as_typed
run_as_a_command = shell.run_as_a_command


class _Captured(unittest.TestCase):
    """Real `StringIO` streams, because the shell reconfigures what it is given."""

    def setUp(self):
        self.out, self.err = io.StringIO(), io.StringIO()
        self._real = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = self.out, self.err
        self.addCleanup(self._put_them_back)

    def _put_them_back(self):
        sys.stdout, sys.stderr = self._real


class TestTheCodeItComesBackWith(_Captured):

    def test_a_run_that_returns_nothing_succeeded(self):
        # The common case by a distance: a `_run` that falls off the end.
        self.assertEqual(run_as_a_command(lambda argv: None), 0)

    def test_a_run_that_returns_a_number_gets_that_number(self):
        self.assertEqual(run_as_a_command(lambda argv: 3), 3)

    def test_zero_is_not_mistaken_for_nothing(self):
        self.assertEqual(run_as_a_command(lambda argv: 0), 0)

    def test_ctrl_c_is_the_families_number_for_you_stopped_it(self):
        def stopped(argv):
            raise KeyboardInterrupt
        self.assertEqual(run_as_a_command(stopped), shell.INTERRUPTED)
        self.assertEqual(shell.INTERRUPTED, 130, "128 + SIGINT is what the shell says")

    def test_ctrl_c_says_nothing_on_the_way_out(self):
        def stopped(argv):
            raise KeyboardInterrupt
        run_as_a_command(stopped)
        self.assertEqual(self.err.getvalue(), "",
                         "a traceback here reads as a crash you caused on purpose")


class TestTheInterpretersOwnRuleForSystemExit(_Captured):
    """`sys.exit(x)` from inside a subcommand must mean what it always meant."""

    def test_a_bare_sys_exit_is_success(self):
        def quits(argv):
            sys.exit()
        self.assertEqual(run_as_a_command(quits), 0)

    def test_an_integer_is_itself(self):
        for code in (0, 1, 2, 5, 130):
            with self.subTest(code=code):
                def quits(argv, code=code):
                    sys.exit(code)
                self.assertEqual(run_as_a_command(quits), code)

    def test_a_message_is_printed_and_answered_with_one(self):
        def complains(argv):
            sys.exit("no such project")
        self.assertEqual(run_as_a_command(complains), 1)
        self.assertEqual(self.err.getvalue(), "no such project\n")

    def test_the_message_goes_to_stderr_not_stdout(self):
        def complains(argv):
            sys.exit("no such project")
        run_as_a_command(complains)
        self.assertEqual(self.out.getvalue(), "",
                         "an error on stdout ends up inside somebody's pipe")

    def test_argparse_getting_its_way_is_the_case_that_matters(self):
        # `--help` and a usage error both leave through here, and both are
        # `SystemExit` raised somewhere we do not control.
        import argparse
        parser = argparse.ArgumentParser(prog="x", add_help=True)

        def parses(argv):
            parser.parse_args(argv)
        self.assertEqual(run_as_a_command(parses, ["--help"]), 0)
        self.assertIn("usage", self.out.getvalue())
        self.assertEqual(run_as_a_command(parses, ["--nope"]), 2)


class TestAClosedPipe(_Captured):

    def test_it_is_the_families_number_for_nobody_is_reading(self):
        def writes(argv):
            raise BrokenPipeError
        self.assertEqual(run_as_a_command(writes), shell.PIPE_CLOSED)
        self.assertEqual(shell.PIPE_CLOSED, 141, "128 + SIGPIPE")

    def test_it_says_nothing_either(self):
        def writes(argv):
            raise BrokenPipeError
        run_as_a_command(writes)
        self.assertEqual(self.err.getvalue(), "",
                         "`| head` is an ordinary thing to do")

    def test_a_pipe_that_broke_during_the_last_flush_is_still_caught(self):
        # The one that is easy to get wrong: nothing inside the run raised.
        # The write that failed is the flush on the way out, which happens
        # inside the same `try` for exactly this reason.
        class Refuses(io.StringIO):
            def flush(self):
                raise BrokenPipeError

        sys.stdout = Refuses()
        self.assertEqual(run_as_a_command(lambda argv: None), shell.PIPE_CLOSED)


class TestWhatItHandsThroughAndWhatItFlushes(_Captured):

    def test_the_work_is_given_the_argv_it_was_given(self):
        seen = []
        run_as_a_command(seen.append, ["today", "--json"])
        self.assertEqual(seen, [["today", "--json"]])

    def test_no_argv_stays_no_argv(self):
        # `main()` with nothing means "read sys.argv", and that decision is the
        # command's to make, not this module's.
        seen = []
        run_as_a_command(seen.append)
        self.assertEqual(seen, [None])

    def test_what_was_printed_before_it_fell_over_is_still_printed(self):
        flushed = []

        class Counting(io.StringIO):
            def flush(self):
                flushed.append(self.getvalue())

        sys.stdout = Counting()

        def prints_then_stops(argv):
            sys.stdout.write("half a day\n")
            raise KeyboardInterrupt

        self.assertEqual(run_as_a_command(prints_then_stops), shell.INTERRUPTED)
        self.assertEqual(flushed, ["half a day\n"],
                         "truncated output is still worth having")

    def test_an_exception_that_is_none_of_these_is_left_alone(self):
        # A real bug must still arrive as a real traceback.
        def breaks(argv):
            raise ZeroDivisionError("this one is ours")
        with self.assertRaises(ZeroDivisionError):
            run_as_a_command(breaks)


class TestTheStreamsAreFixedFirst(_Captured):
    """A locale that claimed ASCII is fixed before anything is printed.

    argparse prints its own `--help` and its own errors, so a reconfigure that
    happened after the work started would be too late for the output people are
    most likely to be looking at.
    """

    class _Ascii(io.StringIO):
        encoding = "ascii"

        def __init__(self):
            io.StringIO.__init__(self)
            self.reconfigured = None

        def reconfigure(self, **kw):
            self.reconfigured = kw

    def test_an_ascii_stream_is_told_to_write_utf8(self):
        sys.stdout = out = self._Ascii()
        sys.stderr = self._Ascii()
        run_as_a_command(lambda argv: None)
        self.assertEqual(out.reconfigured,
                         {"encoding": "utf-8", "errors": "surrogateescape"})

    def test_stderr_too_because_that_is_where_argparse_complains(self):
        sys.stdout = self._Ascii()
        sys.stderr = err = self._Ascii()
        run_as_a_command(lambda argv: None)
        self.assertIsNotNone(err.reconfigured)

    def test_it_happens_before_the_work_does(self):
        sys.stdout = out = self._Ascii()
        sys.stderr = self._Ascii()
        when = []
        run_as_a_command(lambda argv: when.append(out.reconfigured))
        self.assertEqual(when, [{"encoding": "utf-8", "errors": "surrogateescape"}])

    def test_a_stream_that_already_says_utf8_is_left_alone(self):
        class Utf8(self._Ascii):
            encoding = "utf-8"

        sys.stdout = out = Utf8()
        sys.stderr = Utf8()
        run_as_a_command(lambda argv: None)
        self.assertIsNone(out.reconfigured,
                          "reconfiguring a stream that was already right can "
                          "throw away what is in its buffer")

    def test_a_stream_that_is_not_a_stream_is_not_an_error(self):
        # Under some test runners and some embeddings stdout is a plain object
        # with no `encoding` at all.  That is not the command's problem.
        class Bare:
            def write(self, text):
                return len(text)

            def flush(self):
                pass

        sys.stdout = Bare()
        self.assertEqual(run_as_a_command(lambda argv: None), 0)


class TestAnArgumentInTheFormItWasTyped(unittest.TestCase):
    """`as_typed` -- carried by all five, used today by two.

    A filter that silently matches nothing is the worst way for a locale
    problem to show up: it reads as a quiet day rather than as an error.
    """

    def test_ascii_comes_back_as_it_went_in(self):
        for text in ("today", "", "--json", "api-server"):
            with self.subTest(text=text):
                self.assertEqual(as_typed(text), text)

    def test_nothing_comes_back_as_nothing(self):
        self.assertIsNone(as_typed(None))

    def test_a_name_the_machine_could_not_decode_is_spelled_right_again(self):
        # What a machine claiming an ASCII locale hands the interpreter: the
        # real bytes, decoded with surrogateescape because they would not go
        # through ASCII.
        typed = "設定"
        mangled = typed.encode("utf-8").decode("ascii", "surrogateescape")
        self.assertNotEqual(mangled, typed, "the setup did not mangle anything")
        self.assertEqual(as_typed(mangled), typed)

    def test_a_name_that_is_already_right_survives_the_trip(self):
        # On a machine with a real locale nothing was mangled in the first
        # place, and the repair must be a no-op rather than a second mangling.
        self.assertEqual(as_typed("設定"), "設定")

    def test_bytes_that_are_not_utf8_are_handed_back_untouched(self):
        # Better a name that looks odd than an exception from a filter.
        latin = b"caf\xe9".decode("ascii", "surrogateescape")
        self.assertEqual(as_typed(latin), latin)


if __name__ == "__main__":
    unittest.main()
