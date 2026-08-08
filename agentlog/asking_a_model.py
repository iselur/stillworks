"""The one place in agentlog that talks to something outside this machine.

Everything else here reads files and prints.  That is the promise the README
makes and the reason people point the tool at a year of their own transcripts
without thinking about it twice.  ``agentlog brief`` needs a sentence written
about a day's work, which is the one job in the tool that arithmetic cannot do,
so this module exists to hold the exception where a reader can find it.

It is one file for that reason.  A privacy promise with an exception scattered
across the package is a promise nobody can check; an exception in a module
named after what it does is one line in a test:

    only ``asking_a_model.py`` may import ``subprocess``

which is what ``test_privacy_claims`` now asserts.

Nothing is imported that opens a socket.  The call goes to the ``claude``
command already installed and already logged in, which means no API key lives
here, no key has to be found, and the network happens in somebody else's
process.  That is a smaller thing to promise about than an HTTP client, and it
is the same thing the person running this tool is already doing all day.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional

#: The command asked, and the flag that makes it answer once and exit.
_CLI = "claude"

#: Long enough for a paragraph about a day, short enough that a wedged call
#: gives the terminal back.  A brief is not worth a hung prompt.
TIMEOUT_S = 180


class NoModel(Exception):
    """No model could be reached, and why -- in words for a person.

    Raised rather than returned because every caller has the same answer to it:
    print the deterministic report and say the sentence is missing.  A return
    value of ``""`` would let a caller forget, and the day a caller forgets is
    the day a brief silently loses the only part of it a person reads.
    """


def is_available(env: Optional[dict] = None) -> bool:
    """Whether a model can be asked at all, without asking one.

    Separate from :func:`ask` so a caller can say "install the claude CLI" up
    front instead of after it has already gathered a day of facts, and so the
    tests can cover the missing-CLI path without a missing CLI.
    """
    return _executable(env) is not None


def _executable(env: Optional[dict] = None) -> Optional[str]:
    env = env if env is not None else os.environ
    override = env.get("AGENTLOG_MODEL_CMD")
    if override:
        # Named on purpose: a person who wants a different model, or a test
        # that wants no model at all, should not have to reinstall anything.
        found = shutil.which(override)
        return found or (override if os.path.exists(override) else None)
    return shutil.which(_CLI)


def ask(prompt: str, timeout_s: int = TIMEOUT_S,
        env: Optional[dict] = None) -> str:
    """Put ``prompt`` to a model and return what it said.

    Raises :class:`NoModel` if there is nothing to ask or it would not answer.
    The prompt goes in on stdin rather than on the command line: a day of work
    does not fit in an argument list on any system, and the failure when it
    stops fitting is an exec error a long way from here.
    """
    exe = _executable(env)
    if exe is None:
        raise NoModel(
            "no model to ask: the 'claude' command is not on PATH\n"
            "  install it, or set AGENTLOG_MODEL_CMD to a command that "
            "reads a prompt on stdin and prints an answer")
    try:
        done = subprocess.run(
            [exe, "-p"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=dict(env if env is not None else os.environ),
        )
    except subprocess.TimeoutExpired:
        raise NoModel(
            "the model did not answer within {}s".format(timeout_s))
    except OSError as exc:
        raise NoModel("could not run {}: {}".format(exe, exc))

    if done.returncode != 0:
        detail = (done.stderr or "").strip().splitlines()
        raise NoModel(
            "{} exited {}{}".format(
                os.path.basename(exe), done.returncode,
                ": " + detail[-1] if detail else ""))
    answer = (done.stdout or "").strip()
    if not answer:
        raise NoModel("the model returned nothing")
    return answer
