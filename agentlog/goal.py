"""The goal a project declares, said back after every compaction.

The handover note carries receipts -- which directory, which files, which
commands kept failing -- and receipts cannot say what the work was *for*.
That is the part compaction loses slowest and most completely: each summary
of a summary reframes the job a little, and after a hundred of them the
session is confidently doing something adjacent to what was asked.

The fix is a seam, not a search.  Nothing in a transcript is reliably "the
goal" -- the first prompt is history, the latest prompt is a correction --
so the goal is declared, in so many words, by whoever holds it:

    agentlog goal "Ship the importer.  Done when a malformed row is
    reported and the clean rows still land.  Constraint: no new deps."

Declared once when the brief is accepted, redeclared when the brief changes,
and replayed verbatim in front of the handover note at every resume from
compaction.  Verbatim is the point: a quotation cannot drift, and this file
never summarizes, trims, or tidies what was declared.

Three rules keep the seam honest:

- **A cap, enforced at the door.**  The goal lands in a freshly compacted
  context; a page of it would be the bloat it exists to prevent.  Over the
  cap, the declaration is refused with the count -- a goal that long is a
  plan, and plans belong in the work, not in every resume.
- **Not the last word.**  A declaration is a record of intent from the
  moment it was made.  The label says when that was and that newer
  instructions from the user override it, so a stale goal re-anchors the
  work without overruling a legitimate pivot.
- **Redeclarable.**  The newest declaration replaces the old one outright.
  An immutable goal would amplify exactly the staleness it is meant to cure.

Goals are keyed by the directory they were declared in, and -- when the
declaring shell can prove which session it belongs to -- by the session too.
Two concurrent sessions in one directory are usually two different briefs,
and a goal declared inside one of them replayed into the other would be the
drift this seam exists to prevent.  So a declaration made inside a session
binds to it, a declaration made at a bare terminal is the directory's shared
north star, and at resume a session's own goal outranks the shared one.

The store lives under the same private ``~/.agentlog`` as the handover
notes, never in the project tree, so nothing here lands in anyone's
repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Optional, Tuple

# Room for a distilled brief -- objective, what done looks like, the
# constraints that bind it -- and not for the plan.  About half a page.
CAP = 2000

# A goal bound to one session is rubbish once that session is over; like the
# handover notes, they are swept on write so nothing schedules anything.  A
# shared goal is the project's and is never aged out.
KEEP_SESSION_GOALS_FOR_DAYS = 14


def state_dir(home_dir: Optional[str] = None) -> str:
    """Where declared goals live, beside the handover notes they ride with."""
    where = home_dir if home_dir else os.path.expanduser("~")
    return os.path.join(where, ".agentlog", "goal")


def _real(cwd: str) -> str:
    # realpath refuses an embedded null byte with a ValueError.  The result
    # is only ever hashed or echoed, never opened, so garbage can stay
    # garbage instead of becoming a crash.
    try:
        return os.path.realpath(cwd)
    except ValueError:
        return cwd


def _session() -> str:
    """The session the declaring shell belongs to, if it can prove one.

    Claude Code exports its session id to every command it runs, and it is
    the same id its hooks carry, so a goal bound here is found at resume.
    Codex exports nothing a hook payload is known to match, so a goal
    declared inside a Codex session is the directory's shared one -- which
    is also the only shape a relay chain of fresh sessions can use.
    """
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or ""


def _safe(session: str) -> str:
    return "".join(c for c in session if c.isalnum() or c in "-_")[:64]


def _file_for(cwd: str, home_dir: Optional[str], session: str = "") -> str:
    # The directory's real path is hashed into the filename, so a path with
    # separators, spaces, or a hostile shape cannot name a file outside the
    # store, and the same project reached through a symlink is the same goal.
    # A session-bound goal carries the session in the name, after a dash the
    # hex digest cannot contain, so the two kinds never collide.
    name = hashlib.sha256(_real(cwd).encode("utf-8", "replace")).hexdigest()[:16]
    if _safe(session):
        name += "-" + _safe(session)
    return os.path.join(state_dir(home_dir), name + ".json")


def _read(cwd: str, home_dir: Optional[str],
          session: str = "") -> Optional[dict]:
    try:
        with open(_file_for(cwd, home_dir, session), encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not record.get("goal"):
        return None
    return record


def _sweep(directory: str, now: float) -> None:
    cutoff = now - KEEP_SESSION_GOALS_FOR_DAYS * 86400
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if "-" not in name:
            continue  # a shared goal holds until it is cleared
        path = os.path.join(directory, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def _age(seconds: float) -> str:
    """How long ago, at the coarsest unit that still says something."""
    s = max(0, int(seconds))
    if s < 60:
        return "just now"
    if s < 3600:
        return "{}m ago".format(s // 60)
    if s < 86400:
        return "{}h ago".format(s // 3600)
    return "{}d ago".format(s // 86400)


def _set_at(record: dict, now: float) -> str:
    stamp = record.get("set_at") or 0
    local = time.localtime(stamp)
    return "set {} ({})".format(time.strftime("%Y-%m-%d %H:%M", local),
                                _age(now - stamp))


def declare(text: str, cwd: str, home_dir: Optional[str] = None,
            now: Optional[float] = None,
            session: Optional[str] = None) -> Tuple[str, str]:
    """Record the goal for ``cwd``.  Returns (message, complaint).

    A complaint means the declaration was refused and nothing was stored;
    the caller prints it and exits 2 like any other usage error.
    """
    now = time.time() if now is None else now
    session = _session() if session is None else session
    if not text.strip():
        return "", ("an empty goal declares nothing\n"
                    "  agentlog goal \"the objective\" | agentlog goal --clear")
    if len(text) > CAP:
        return "", ("that goal is {} characters; the cap is {}.\n"
                    "  A goal is the north star, not the plan: say the "
                    "objective, what done\n"
                    "  looks like, and the constraints that bind it."
                    .format(len(text), CAP))
    path = _file_for(cwd, home_dir, session)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    _sweep(os.path.dirname(path), now)
    record = {"cwd": _real(cwd), "goal": text, "set_at": now}
    if _safe(session):
        record["session"] = _safe(session)
    # Written beside and renamed on top, like the handover notes: the goal on
    # disk is either the old one or the new one and never half of each.
    temporary = path + ".part"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    os.replace(temporary, path)
    whose = ("bound to this session" if _safe(session)
             else "shared by every session there")
    return ("goal declared for {}, {} ({} characters, cap {})\n"
            "  replayed at every resume from compaction; redeclare when it "
            "changes,\n  `agentlog goal --clear` when it is done."
            .format(record["cwd"], whose, len(text), CAP)), ""


def show(cwd: str, home_dir: Optional[str] = None,
         now: Optional[float] = None, session: Optional[str] = None) -> str:
    """The goal this shell would be handed, read back as declared."""
    now = time.time() if now is None else now
    session = _session() if session is None else session
    record = _read(cwd, home_dir, session) or _read(cwd, home_dir)
    if record is None:
        return ("no goal declared for {}\n"
                "  declare one: agentlog goal \"the objective\""
                .format(_real(cwd)))
    whose = "this session" if record.get("session") else "every session"
    return "goal for {} ({})  ({})\n\n{}".format(
        record.get("cwd") or _real(cwd), whose, _set_at(record, now),
        record["goal"])


def clear(cwd: str, home_dir: Optional[str] = None,
          session: Optional[str] = None) -> str:
    """Forget the goal this shell would be handed.

    A session's own goal is cleared before the shared one -- the same order
    they are found in -- and clearing nothing is not an error.
    """
    session = _session() if session is None else session
    real = _real(cwd)
    for candidate in (session, ""):
        try:
            os.remove(_file_for(cwd, home_dir, candidate))
        except OSError:
            continue
        whose = "this session's" if _safe(candidate) else "the shared"
        return "cleared {} goal for {}".format(whose, real)
    return "no goal was declared for {}".format(real)


def anchor(cwd: str, session: str = "", home_dir: Optional[str] = None,
           now: Optional[float] = None) -> str:
    """The block a resuming session is handed, or "" when none is declared.

    Rides in front of the handover note, and alone when there is no note.
    Unlike the note it is not deleted on the way out: a note describes a
    moment and goes stale by the next compaction; a goal holds until it is
    redeclared or cleared, and replaying it is the entire point.

    The session's own goal outranks the directory's shared one, and another
    session's goal is never handed over at all: two sessions in one
    directory are two briefs, and replaying one into the other would be the
    drift this seam exists to prevent.

    The label carries the two things that keep a quotation from doing harm:
    when it was declared, and that it is not the last word.
    """
    now = time.time() if now is None else now
    if not cwd:
        return ""
    try:
        record = _read(cwd, home_dir, session) or _read(cwd, home_dir)
        if record is None:
            return ""
        return ("The declared goal for this project, {} with `agentlog "
                "goal`.  A declaration\nfrom that moment, not the last word: "
                "newer instructions from the user override\nit.\n\n{}"
                .format(_set_at(record, now), record["goal"]))
    except Exception:  # noqa: BLE001 -- a hook must never be the reason
        return ""      # an agent stops; see handover.handle's docstring
