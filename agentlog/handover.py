"""The note a session leaves for itself before it forgets.

A long conversation with a coding agent gets compacted: the transcript is
replaced by a summary so the work can carry on, and what the agent knew about
the last four hours becomes whatever the summary happened to keep.  The
transcript itself is still on disk and still complete, so the facts are not
gone -- only the agent's hold on them is.

So: read the transcript at the moment before it is compacted, write down what
the session had actually done, and hand that back the moment the session
resumes.  Two hooks, one command, no model.

It is deliberately made of facts and nothing else.  A summary written by a
model is what compaction already produces; a second one would be another
account of the same conversation, with the same licence to drift.  What a
compacted session cannot reconstruct is the plain record -- which directory,
how long, which files were edited, which commands kept failing -- and that is
the whole of what this hands over.

The command must never be the reason an agent stops.  Every failure here comes
back as exit 0 with an explanation on stderr, which is where a hook's stderr is
kept for whoever goes looking.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional, Tuple

from .parser import read_one_session
from .render import render_digest

# A handover nobody collected is rubbish within days -- the session it belongs
# to is over.  Swept on write so nothing has to schedule anything.
KEEP_FOR_DAYS = 14

PREAMBLE = (
    "Handover from before this conversation was compacted.  This is what the "
    "session had already done, read back from its own transcript by agentlog "
    "-- a record, not a recollection, so it is safe to rely on where the "
    "summary above is thin."
)


def state_dir(home_dir: Optional[str] = None) -> str:
    """Where handovers wait between the two hooks.

    Under the same home the logs are read from, so pointing the command at a
    different home moves the notes with it.  A note is about one home's
    sessions and belongs beside them.
    """
    where = home_dir if home_dir else os.path.expanduser("~")
    return os.path.join(where, ".agentlog", "handover")


def _note_for(session_id: str, home_dir: Optional[str]) -> str:
    # The session id names the file because compaction keeps it: a transcript
    # here carries 276 compactions and one id, so the note written on the way
    # in is found by the session that comes back out.  Anything else -- newest
    # note, note for this directory -- would hand one session's work to
    # another the first time two of them ran side by side.
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:200]
    return os.path.join(state_dir(home_dir), safe + ".txt")


def _sweep(directory: str, now: float) -> None:
    cutoff = now - KEEP_FOR_DAYS * 86400
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        path = os.path.join(directory, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def _write_note(session_id: str, transcript_path: str,
                home_dir: Optional[str], now: float) -> str:
    sessions, _sources, _unusable = read_one_session(transcript_path)
    if not sessions:
        raise ValueError("nothing to read in {}".format(transcript_path))
    body = PREAMBLE + "\n\n" + render_digest(sessions, "this session")
    path = _note_for(session_id, home_dir)
    # A note is a condensed transcript -- prompts, paths, failing commands --
    # so the store is private to its owner from the moment it exists.
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    _sweep(os.path.dirname(path), now)
    # Written beside and renamed on top, so a note is either the old one or
    # the new one and never half of each.
    temporary = path + ".part"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.replace(temporary, path)
    return path


def _read_note(session_id: str, home_dir: Optional[str]) -> str:
    path = _note_for(session_id, home_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
    except OSError:
        return ""
    # Handed over once.  A note that stayed behind would be injected again at
    # the next compaction, describing a session two hours out of date, and
    # stale facts stated as facts are worse than none.
    try:
        os.remove(path)
    except OSError:
        pass
    return body


def handle(payload_text: str, home_dir: Optional[str] = None,
           now: Optional[float] = None) -> Tuple[str, str]:
    """Answer one hook payload.  Returns (stdout, stderr).

    The whole command is this function: a hook writes JSON on standard input
    and reads standard output, and which of the two jobs to do is settled by
    the event name in the payload rather than by a flag, so the same line goes
    in the settings file twice.

    Only the four fields both events are documented to carry are read --
    ``hook_event_name``, ``session_id``, ``transcript_path``.  ``trigger`` and
    ``source`` are matchers rather than payload fields, and a command that
    needed them would work until it met a build that agreed with the
    documentation.

    Never raises.  A hook that throws is a hook that interrupts somebody's
    work to report a problem with the note-taking.
    """
    now = time.time() if now is None else now
    try:
        payload = json.loads(payload_text)
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
    except (ValueError, TypeError) as bad:
        return "", "agentlog handover: unreadable hook payload: {}\n".format(bad)

    event = payload.get("hook_event_name") or ""
    session_id = payload.get("session_id") or ""
    if not session_id:
        return "", "agentlog handover: payload names no session\n"

    try:
        if event == "PreCompact":
            # A subagent compacting mid-task sends its parent's session_id
            # plus an agent_id of its own (Codex does this).  A note written
            # here would be the subagent's story handed to the root session at
            # its next resume, stated as the root's own past.  Say nothing.
            if payload.get("agent_id"):
                return "", ""
            path = _write_note(session_id, payload.get("transcript_path") or "",
                               home_dir, now)
            return "", "agentlog handover: wrote {}\n".format(path)
        if event == "SessionStart":
            body = _read_note(session_id, home_dir)
            if not body:
                return "", ""
            return json.dumps({"hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": body,
            }}) + "\n", ""
    except Exception as bad:  # noqa: BLE001 -- see the docstring
        return "", "agentlog handover: {}\n".format(bad)

    # Some other event, or none.  Saying nothing is the right answer: the same
    # line may well be in the settings file under a matcher we do not serve.
    return "", ""
