"""The part of a command line that is the same in every command line.

Five tools in this family, and each one had its own copy of four things that
have nothing to do with what the tool does: reconfigure the streams if the
locale claimed ASCII, flush before leaving, turn ctrl-c into 130, turn a closed
pipe into 141 without letting the interpreter print a second failure on the way
out.  Ninety lines, four times over, in five files.

Copies drift, and these had.  Three of the five returned their exit code and
two raised `SystemExit` with it, so `main()` meant two different things
depending on which tool you imported; two had `as_typed` and three did not; the
wording of the same comment had gone three separate ways.  None of that was a
decision anybody made.

What a command line needs to know is one thing:

    def main(argv=None):
        return run_as_a_command(_run, argv)

Hand it the function that does the work and it hands back the number the
process should exit with -- always a number, whether the work returned one,
raised `SystemExit` with one, or was interrupted.

The *reasoning* stays where it differs.  Why an abandoned `stillworks check`
must not answer 0 or 1, why a cut-off `agentlog` digest reported nothing about
your day -- those are facts about a particular tool and they belong in that
tool's `main`.  Only the mechanism moved.

This file is byte-identical in all five packages, and a family test says so.
It has to be copied rather than imported: nothing in this family imports
anything outside its own package, which is the promise `pip install stillworks`
makes and which `test_every_import_is_stdlib_or_the_packages_own` enforces.  A
copy that is checked is worth more than an import that would break the claim --
and `as_typed`, which only two of the five have a use for today, is carried in
all five for the same reason.  An unused copy costs nothing; a copy that has
quietly diverged costs an afternoon.
"""

from __future__ import annotations

import codecs
import os
import sys

#: Ctrl-c.  128 + SIGINT, which is how the shell spells it, and deliberately
#: not a code any of these tools uses for an answer: a run that was stopped
#: partway through did not find anything and did not clear anything.
INTERRUPTED = 130

#: A closed pipe.  128 + SIGPIPE.  `... | head` and `... | less` quit with `q`
#: are ordinary, and both leave us writing where nobody is reading.
PIPE_CLOSED = 141


def run_as_a_command(run, argv=None):
    """Run `run(argv)` the way a process should, and return its exit code.

    Three things happen out here that cannot happen inside, and one that could
    but should not.

    The streams are reconfigured *before* `run` sees anything, because argparse
    prints its own errors and its own `--help`, and on a machine with no locale
    those were coming out as question marks too.

    The flush is in a `finally` for the same reason from the other end:
    argparse prints `--version` and then exits, so the write that fails is one
    nothing inside `run` would ever get the chance to see.

    Ctrl-c and a closed pipe are caught here and nowhere else.  Both are
    ordinary ways to stop a command -- you change your mind, or you close
    `less` -- and answering either with a traceback reads as a crash and sends
    people looking for a bug they caused on purpose.

    And `SystemExit` is turned back into a number rather than left to fly.
    `sys.exit(2)` from inside a subcommand is a perfectly good way to say "exit
    2"; it is just not a good way to say it to a Python caller, who then has to
    write a `try` around a function whose whole job is to return the code.  The
    non-integer case follows the interpreter exactly -- print it and answer 1 --
    so a tool that says `sys.exit("no such project")` behaves the same whether
    it went through here or not.
    """
    _write_utf8_if_the_locale_said_nothing()
    try:
        try:
            code = run(argv)
        finally:
            # Whatever was printed is still worth having.  It is only no
            # longer allowed to call itself whole.
            sys.stdout.flush()
    except KeyboardInterrupt:
        return INTERRUPTED
    except BrokenPipeError:
        _stop_writing_down_a_closed_pipe()
        return PIPE_CLOSED
    except SystemExit as chosen:
        return _the_code_it_asked_for(chosen)
    return 0 if code is None else code


def _the_code_it_asked_for(chosen):
    """What the interpreter would have done with this `SystemExit`.

    Copied from CPython's own behaviour rather than invented: `None` is 0, an
    integer is itself, and anything else is a message -- printed to stderr,
    answered with 1.  Matching it means catching `SystemExit` here changes
    nothing a person at a terminal can see.
    """
    code = chosen.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(code, file=sys.stderr)
    return 1


def as_typed(text):
    """An argument in the form it was typed, not the form the locale allowed.

    Python decodes ``sys.argv`` with the filesystem encoding, and on a machine
    with no locale that encoding is ASCII -- so ``--project 設定`` arrives as a
    run of surrogates and matches nothing.  A filter that silently matches
    nothing is the worst way for this to fail: it reads as a quiet day rather
    than as an error.  ``os.fsencode`` gives the bytes back untouched, and the
    shell that sent them was speaking UTF-8.
    """
    if text is None or text.isascii():
        return text                     # the overwhelmingly common case
    try:
        return os.fsencode(text).decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


def _write_utf8_if_the_locale_said_nothing():
    """Write UTF-8 when the machine claims it can only take ASCII.

    A container with no locale set -- a Dockerfile without ``ENV LANG``, cron,
    most of CI -- leaves Python believing stdout is ASCII, and then a single em
    dash of our own raises ``UnicodeEncodeError`` halfway through the output: a
    traceback and half a screen, over a character no one chose.  A watcher or a
    nightly digest is exactly the kind of thing that gets left running on a box
    like that, and under ``--json`` a path that came out as question marks is
    not a path the reading program can use either.

    An ASCII claim is not a claim about the terminal, though.  It is the
    absence of one, and the terminal on the other end is virtually always
    UTF-8.  So we write UTF-8 and keep ``surrogateescape``, which hands back
    unchanged the bytes of any filename this machine could not decode -- that is
    what makes a name it cannot spell come out spelled right anyway.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if codecs.lookup(stream.encoding or "").name == "ascii":
                stream.reconfigure(encoding="utf-8", errors="surrogateescape")
        except (AttributeError, LookupError, OSError, ValueError):
            pass                        # not a real stream, or already written to


def _stop_writing_down_a_closed_pipe():
    """Point stdout at nowhere, so nothing is left to fail on the way out.

    Catching the `BrokenPipeError` is only half of it: whatever is still in the
    buffer gets flushed again when the interpreter shuts down, too late for any
    `except` of ours, and that second failure is what prints `Exception ignored
    in: <_io.TextIOWrapper ...>` and turns the exit code into 120.  Redirecting
    the file descriptor gives that flush somewhere harmless to go.
    """
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
    except (AttributeError, OSError, ValueError):
        pass                            # not a real stream; nothing to protect
