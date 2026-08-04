"""Find agent session logs and follow them as they are written.

The whole tool rests on one property of these logs: they are append-only JSONL.
So following them needs no inotify, no daemon and no dependency — remember a
byte offset per file, read from there, repeat.  That is why this works
identically on Linux, macOS and a network share.

Files are read as bytes and split on newlines by hand.  A log being appended to
right now will hand back a half-written final line; keeping it in a buffer until
its newline arrives is the difference between a watcher and a line-mangler.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .events import Tracker, events_from_line

CLAUDE_SUBDIR = os.path.join(".claude", "projects")
CODEX_SUBDIR = os.path.join(".codex", "sessions")

# How far back a file's mtime may be before it is assumed finished.  Generous:
# an agent that spends ten minutes on one tool call is thinking, not gone.
DEFAULT_STALE_S = 900.0

# A tree of session logs can hold tens of thousands of files; walking it every
# second would cost more than the watching does.
RESCAN_MIN_S = 2.0

_MAX_WALK_ENTRIES = 200000


def _walk_jsonl(root: str) -> List[str]:
    """Every ``.jsonl`` under a root, symlinks never followed.

    Not following links is a correctness choice, not just a safety one: a
    session log reached by two paths would be tailed twice and every event
    printed twice.
    """
    out: List[str] = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            if name.endswith(".jsonl"):
                out.append(os.path.join(dirpath, name))
        if len(out) > _MAX_WALK_ENTRIES:
            break
    return out


def _mtime_then_name(entry: Tuple[str, str]) -> Tuple[float, str]:
    """Sort key: oldest file first, ties by name, unstattable files last."""
    path = entry[0]
    try:
        return (os.path.getmtime(path), path)
    except OSError:
        return (float("inf"), path)


def discover(home: str, sources: Tuple[str, ...] = ("claude", "codex")) -> List[Tuple[str, str]]:
    """(path, source) for every session log under a home directory."""
    found: List[Tuple[str, str]] = []
    if "claude" in sources:
        for path in _walk_jsonl(os.path.join(home, CLAUDE_SUBDIR)):
            found.append((path, "claude"))
    if "codex" in sources:
        for path in _walk_jsonl(os.path.join(home, CODEX_SUBDIR)):
            found.append((path, "codex"))
    return found


def _subagent_session_dir(path: str) -> str:
    """The session directory a subagent transcript sits under, or "".

    Claude Code writes a subagent's whole transcript beside its parent's log:

        <project>/<session-id>.jsonl
        <project>/<session-id>/subagents/agent-<hex>.jsonl

    and one directory deeper when a workflow ran it:

        <project>/<session-id>/subagents/workflows/<run-id>/agent-<hex>.jsonl

    The file is named after the subagent, which names nothing a person can look
    up.  What names the sitting is the directory holding `subagents`, however
    many directories the transcript itself sits under — so the search walks up
    to the nearest `subagents` rather than looking only at the directory the
    file is in.
    """
    here = os.path.dirname(path)
    while here and here != os.path.dirname(here):
        if os.path.basename(here) == "subagents":
            return os.path.dirname(here)
        here = os.path.dirname(here)
    return ""


def _subagent_parent(path: str) -> str:
    """The session a subagent transcript belongs to, or "" if it is not one."""
    return os.path.basename(_subagent_session_dir(path))


def session_id_for(path: str, source: str) -> str:
    """A short, stable id for a session, taken from its filename.

    A subagent's transcript is part of a sitting rather than a sitting of its
    own, so it answers with the id of the session that spawned it — the work is
    that session's work, and a reader joining ``--json`` output by session must
    find it there.  Claude's layout only; Codex writes no subagent transcripts.
    """
    if source == "claude":
        parent = _subagent_parent(path)
        if parent:
            return parent
    base = os.path.splitext(os.path.basename(path))[0]
    if source == "codex":
        # rollout-<date>-<uuid>.jsonl — the uuid is the last five dash-parts.
        parts = base.split("-")
        if len(parts) >= 5:
            base = "-".join(parts[-5:])
    return base


def decode_claude_project(path: str) -> str:
    """Claude Code's encoded project directory, decoded back to a path.

    It stores the project's absolute path as a directory name with ``/``
    replaced by ``-``, which is ambiguous the moment the path itself contains a
    dash.  Treat the result as a label of last resort; a ``cwd`` seen in the log
    always wins.
    """
    session_dir = _subagent_session_dir(path)
    if session_dir:
        # A subagent transcript sits below its session's directory, so the
        # project is one further up again — not the directory beside the file,
        # which is a run id or the word `subagents`.
        holder = os.path.dirname(session_dir)
    else:
        holder = os.path.dirname(path)
    name = os.path.basename(holder)
    if name.startswith("-"):
        # The leading dash is the root slash.  Dropping it leaves a relative
        # path, which then fails to shorten anything in the output.
        return "/" + name[1:].replace("-", "/")
    return name


def _probe_project(path: str, source: str, max_lines: int = 60) -> str:
    """The working directory a log names near its top, if it names one.

    Worth the extra read: in live mode we seek straight to the end of the file,
    and the record that says which project this is went past long ago.
    """
    from .events import Tracker as _T, events_from_line as _ev
    tracker = _T("", source)
    try:
        with open(path, "rb") as fh:
            for _ in range(max_lines):
                line = fh.readline()
                if not line:
                    break
                _ev(line.decode("utf-8", "replace"), tracker)
                if tracker.project:
                    return tracker.project
    except OSError:
        return ""
    return ""


class _FileState:
    __slots__ = ("offset", "tracker", "buf", "inode")

    def __init__(self, offset: int, tracker: Tracker, inode: int) -> None:
        self.offset = offset
        self.tracker = tracker
        self.buf = b""
        self.inode = inode


def _label(project: str) -> str:
    """The last component of a project path — what the column shows."""
    return os.path.basename(project.rstrip("/")) if project else ""


class Watcher:
    """Follows every session log under a home directory.

    ``poll()`` returns the events appended since the previous call, oldest
    first.  It never blocks and never raises for a file that vanished, was
    truncated, or cannot be read — a watcher that dies when a log rotates is
    worse than no watcher.
    """

    def __init__(
        self,
        home: str,
        sources: Tuple[str, ...] = ("claude", "codex"),
        since: Optional[datetime] = None,
        stale_s: float = DEFAULT_STALE_S,
        project: str = "",
        clock=None,
    ) -> None:
        self.home = home
        self.sources = sources
        self.since = since
        self.stale_s = stale_s
        self.project = (project or "").lower()
        self._clock = clock or (lambda: datetime.now(timezone.utc).timestamp())
        self._files: Dict[str, _FileState] = {}
        self._unreadable: set = set()
        self._first_scan = True
        self._found = 0
        self._last_scan = 0.0
        self._project_names: Dict[str, str] = {}
        # Record uuids already shown.  Deliberately not bounded: it holds one
        # short id per record actually read, which is a fraction of the bytes
        # the watcher has already read to get them, and evicting the oldest
        # would drop exactly the records a resume is most likely to replay --
        # reintroducing the double-printing this exists to prevent, silently.
        self._seen_records: set = set()

    # -- scanning ---------------------------------------------------------

    def _fresh(self, path: str) -> bool:
        """Is this log recent enough to be worth following?"""
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            return False
        if self.since is not None:
            return mtime >= self.since.timestamp()
        return (self._clock() - mtime) <= self.stale_s

    def _adopt(self, path: str, source: str) -> None:
        try:
            st = os.stat(path)
        except OSError:
            return
        try:
            with open(path, "rb"):
                pass
        except OSError:
            # Not adopted, so `watching N` does not count it — that line is a
            # claim about coverage, and a file that will not open is not being
            # watched in any sense the word is used there.  Left out of
            # ``_files``, it is retried on every scan, so fixing the
            # permissions mid-run is enough to pick it up.
            self._unreadable.add(path)
            return
        self._unreadable.discard(path)
        project = _probe_project(path, source)
        # A cwd read out of the log is the truth; the directory name is a guess,
        # and a wrong one whenever the project's path contains a dash.
        guessed = not project
        if guessed and source == "claude":
            project = decode_claude_project(path)
        name = _label(project)
        if self.project and self.project not in name.lower():
            if guessed:
                # Excluded on a guess.  Follow it anyway and decide again in
                # ``poll`` once the log says what it really is — refusing here
                # is how asking for a project by its real name showed nothing.
                pass
            else:
                # Excluded on the log's own word, which will not change.
                # Remember the decision, so a filtered-out log is not re-probed
                # on every rescan for as long as the watcher runs.
                self._files[path] = _FileState(-1, Tracker("", source), st.st_ino)
                return
        # The tracker is seeded only with a project we actually read.  Handing it
        # the guess would settle the question for good — it fills ``project``
        # once and never overwrites it — and the real cwd, further down the
        # file, would arrive to find the slot already taken.  The guess lives in
        # ``_project_names`` instead, where it is a fallback rather than an
        # answer.
        tracker = Tracker(session_id_for(path, source), source,
                          "" if guessed else project)
        # A log that existed before we started is joined at its end; one that
        # appeared since is read whole, because all of it is new to the user.
        # With --since, everything is read whole and filtered by timestamp.
        start_at_end = self._first_scan and self.since is None
        self._files[path] = _FileState(
            st.st_size if start_at_end else 0, tracker, st.st_ino)
        self._project_names[path] = name

    def _scan(self) -> None:
        found = 0
        fresh = []
        for path, source in discover(self.home, self.sources):
            found += 1
            if path in self._files:
                continue
            if not self._fresh(path):
                continue
            fresh.append((path, source))
        # Oldest first.  When the same records are in two files -- a copied
        # project directory, most often -- the first one read is the one that
        # shows them, and that should be the original rather than whichever the
        # directory listing happened to name first.  Only the handful of files
        # not already followed are sorted, so the extra stat is not paid on
        # every file on every rescan.
        fresh.sort(key=_mtime_then_name)
        for path, source in fresh:
            self._adopt(path, source)
        self._found = found
        self._first_scan = False
        self._last_scan = self._clock()

    # -- reading ----------------------------------------------------------

    def _read_new(self, path: str, state: _FileState) -> List[Dict]:
        try:
            st = os.stat(path)
        except OSError:
            self._files.pop(path, None)
            return []
        if st.st_ino != state.inode or st.st_size < state.offset:
            # Rotated or truncated: the bytes we were pointing at are gone.
            state.offset = 0
            state.buf = b""
            state.inode = st.st_ino
        if st.st_size == state.offset:
            return []
        try:
            with open(path, "rb") as fh:
                fh.seek(state.offset)
                chunk = fh.read()
        except OSError:
            # A log can lose its permissions halfway through a watch, and the
            # bytes it just grew by are exactly the activity being missed.
            # Silently returning [] here made that indistinguishable from an
            # idle agent, which is the one thing this tool must not do.
            self._unreadable.add(path)
            return []
        self._unreadable.discard(path)
        state.offset += len(chunk)
        data = state.buf + chunk
        # Everything after the last newline is a line still being written.
        cut = data.rfind(b"\n")
        if cut < 0:
            state.buf = data
            return []
        state.buf = data[cut + 1:]
        out: List[Dict] = []
        for line in data[:cut].split(b"\n"):
            if not line:
                continue
            produced = events_from_line(line.decode("utf-8", "replace"),
                                        state.tracker)
            uuid = state.tracker.record_id
            if uuid:
                if uuid in self._seen_records:
                    # Already shown, from the file that had it first.  The line
                    # was still fed to the tracker above, because a replayed
                    # record is where a resumed session says which directory it
                    # is in — that is a property of the sitting, not of the
                    # record, and the new file has no other record that says it.
                    continue
                self._seen_records.add(uuid)
            out.extend(produced)
        # The project can turn up mid-stream, on the first record we happen to
        # read; backfill it so early events are not labelled blank.  It is also
        # preferred over the name settled at adoption, because that one may have
        # been decoded from the directory — see ``decode_claude_project``.
        name = _label(state.tracker.project) or self._project_names.get(path, "")
        for event in out:
            event["project"] = name
        return out

    def poll(self) -> List[Dict]:
        """Events appended since the last call, oldest first."""
        now = self._clock()
        if self._first_scan or (now - self._last_scan) >= RESCAN_MIN_S:
            self._scan()
        events: List[Dict] = []
        for path in list(self._files):
            state = self._files[path]
            if state.offset < 0:      # filtered out by --project
                continue
            events.extend(self._read_new(path, state))
        if self.project:
            # Checked again here, on the resolved name, because a log adopted on
            # a guessed one may since have said what project it really is — in
            # either direction.
            events = [e for e in events
                      if self.project in (e["project"] or "").lower()]
        if self.since is not None:
            events = [e for e in events
                      if e["at"] is None or e["at"] >= self.since]
        # Timestamps come from several files; None sorts first so an undated
        # record never jumps to the end of the stream.
        events.sort(key=lambda e: (e["at"] is not None,
                                   e["at"].timestamp() if e["at"] else 0.0))
        return events

    def watched(self) -> int:
        """How many sittings are actually being followed right now.

        Sessions, not files.  A session that handed work to twenty subagents
        has twenty-one logs open and is still one thing happening; counting the
        files said `watching 21 sessions` for a person sitting at one.
        """
        return len({s.tracker.session for s in self._files.values()
                    if s.offset >= 0})

    def unreadable(self) -> List[str]:
        """Session logs that exist and could not be opened, sorted.

        A live property, not a verdict stamped at startup: a watch runs for
        hours and permissions get fixed while it is running, so a file leaves
        this list as soon as a read of it succeeds.  It is only updated when
        there is a reason to open a file — a log that goes unreadable and then
        never grows has no activity being missed, so there is nothing to say.

        Deliberately only about files that would not *open*.  A file that opens
        and yields no events is the ordinary case on every log all day, since
        most records in a session file are not events.
        """
        return sorted(self._unreadable)

    def found(self) -> int:
        """How many session logs exist at all, before any filter.

        The difference between this and ``watched`` is the difference between
        "you have never run an agent" and "nothing happened in the window you
        asked for" — two messages a person needs told apart.
        """
        return self._found
