"""Turn one line of an agent's session log into events.

Two log formats are read: Claude Code (``~/.claude/projects/**/*.jsonl``) and
Codex (``~/.codex/sessions/**/*.jsonl``).  Both are append-only JSONL, which is
what makes tailing them possible at all — a line, once written, never changes.

Only *activity* is extracted: the commands the agent ran, the files it wrote,
and the calls that came back as errors.  Message text is never read.  That is
the same promise the rest of this family makes, and here it is also what keeps
the output narrow enough to watch in real time.

An event is a small dict:

    at       datetime | None  — when it happened
    kind     str              — 'turn' | 'cmd' | 'write' | 'read' | 'error'
    text     str              — the command, the path, or the failed call
    session  str              — session id (short form of the filename)
    source   str              — 'claude' | 'codex'
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from .transcript import (
    files_a_command_reads,
    is_work_call,
    is_write_tool,
    parse_time,
    patched_files,
    script_commands,
    script_failed,
    script_workdir,
    tool_path,
)

# Reads are excluded from the default view: an agent reads far more than it
# writes, and a stream that is 90% reads is a stream nobody watches.
KINDS = ("turn", "cmd", "write", "read", "error")

# How close together two reports of the same file have to be to be one write.
_WRITE_ECHO = timedelta(seconds=30)


class Tracker:
    """Per-file state that single lines cannot carry on their own.

    A failed call arrives as its own record and names only the id of the call
    that failed, so the command has to be remembered from when it was issued.
    The same object also holds the working directory, which appears once near
    the top of the file and never again.
    """

    def __init__(self, session: str, source: str, project: str = "") -> None:
        self.session = session
        self.source = source
        self.project = project
        # The uuid of the record just read, or "" for a format that has none.
        # Claude Code writes the same record into two files whenever a session
        # is resumed or a project directory is copied, so the caller needs a
        # way to tell a record it has already shown from one that is new.  It
        # lives here because this is where the record is parsed; see
        # ``Watcher._read_new``.
        self.record_id = ""
        self._labels: Dict[str, str] = {}
        # Bounded: a long session issues thousands of calls, and a watcher that
        # grows without limit is a watcher that gets killed overnight.
        self._order: List[str] = []
        self._max_labels = 2000
        # One patch is announced twice — once by the call that sends it, once by
        # the result that says it applied.  See ``confirms_envelope``.
        self._pending: Dict[str, List[datetime]] = {}
        self._pending_order: List[str] = []
        # Calls whose patch has already been reported as failed, so that the
        # script's own result does not say the same thing again with less in
        # it.  See ``patch_failed``.
        self._failed_patches: List[str] = []
        # The script that is running right now, if one is.  A patch result
        # names its own call id, which is usually from a different namespace
        # than the script's, so the script it belongs to is the one it
        # interrupted.  See ``patch_failure``.
        self._open_call = ""

    def remember(self, call_id: str, label: str) -> None:
        if not call_id or not label:
            return
        if call_id not in self._labels:
            self._order.append(call_id)
        self._labels[call_id] = label
        while len(self._order) > self._max_labels:
            self._labels.pop(self._order.pop(0), None)

    def recall(self, call_id: str) -> str:
        return self._labels.get(call_id, "")

    def running(self, call_id: str) -> None:
        """A script has been issued and has not reported back yet."""
        self._open_call = call_id

    def patch_failure(self, call_id: str) -> None:
        """Remember that a patch failure has been reported, and for whom.

        Codex sends a patch by running a script, so one failed patch can
        surface twice: the ``patch_apply_end`` names the files and says why,
        and the script's own result says only that something failed.  Dropping
        the second needs to know which script it belongs to.

        The end record carries a call id, but usually not the script's: of 713
        real ``patch_apply_end`` records 646 are named ``exec-<uuid>``, an id
        that appears nowhere else in the file, and only 67 share the
        ``call_<...>`` namespace the scripts use.  So the id is taken when it
        matches and the running script is used when it does not — the patch was
        applied by that script, and its end record arrives between the call and
        its result every time (53 of 53 where both ids matched, and all 5 real
        patch failures fall inside a script that had not yet reported back).
        """
        for cid in (call_id, self._open_call):
            if cid:
                self._failed_patches.append(cid)
        while len(self._failed_patches) > self._max_labels:
            self._failed_patches.pop(0)

    def patch_failed(self, call_id: str) -> bool:
        """Has a patch failure already been reported for this script?"""
        return bool(call_id) and call_id in self._failed_patches

    def envelope_sent(self, path: str, at: Optional[datetime]) -> None:
        """Remember that a patch for this file was reported from its envelope.

        Only the older ``function_call`` shape reports from the envelope at
        all; current builds say nothing until the patch lands, so in those
        sessions nothing is ever remembered here and nothing is ever dropped.
        """
        if not path or at is None:
            return
        if path not in self._pending:
            self._pending_order.append(path)
        self._pending.setdefault(path, []).append(at)
        while len(self._pending_order) > self._max_labels:
            self._pending.pop(self._pending_order.pop(0), None)

    def confirms_envelope(self, path: str, at: Optional[datetime]) -> bool:
        """Is this the confirmation of a patch already reported from its call?

        Codex can name a patched file twice: in the call that sends the
        envelope, and again in the result that confirms it applied.  Both are
        worth reading — the call is the earliest sight of it, the result is the
        only sight of it when the envelope was built somewhere we cannot follow
        — so both are parsed and the echo is dropped here instead.

        What makes it an echo is the pairing, not the clock.  One envelope buys
        exactly one suppression, so an agent that fixes a file, runs the tests
        and fixes it again eight seconds later has two envelopes, two results
        and two lines in the feed.  Judging by the clock alone dropped 133 of
        742 successfully patched paths on the corpus this was measured against,
        across 87 sessions, with the gaps spread evenly from five seconds to
        thirty — ordinary consecutive work, read as duplication because it was
        quick.  A real echo lands in well under a second.

        The window is still here as an expiry: an envelope whose result never
        came must not sit waiting to swallow the next real edit to that file.
        """
        if not path or at is None:
            return False
        waiting = self._pending.get(path)
        while waiting and (at - waiting[0]) > _WRITE_ECHO:
            waiting.pop(0)
        # A pending entry stamped later than the confirmation is not the one
        # being confirmed, and dropping it would leave the edit it belongs to
        # unpaired.  Records that arrive out of order are left alone.
        if not waiting or waiting[0] > at:
            if not waiting:
                self._pending.pop(path, None)
            return False
        waiting.pop(0)
        if not waiting:
            self._pending.pop(path, None)
        return True

    def _event(self, at, kind: str, text: str) -> Dict:
        return {
            "at": at,
            "kind": kind,
            "text": text,
            "session": self.session,
            "source": self.source,
            "project": self.project,
        }


def events_from_line(raw: str, tracker: Tracker) -> List[Dict]:
    """Zero or more events from one JSONL line.

    Anything unparseable yields nothing.  A log being written to right now will
    hand us half a line eventually; that is expected, not an error.
    """
    raw = raw.strip()
    if not raw:
        return []
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(obj, dict):
        return []
    uuid = obj.get("uuid")
    tracker.record_id = uuid if isinstance(uuid, str) else ""
    try:
        if tracker.source == "codex":
            return _codex_events(obj, tracker)
        return _claude_events(obj, tracker)
    except Exception:
        # These formats are written by other programs and change without
        # notice, so a record shaped in a way no branch here expects is a
        # question of when.  Losing that one line is a fair price; taking the
        # watcher down in the middle of a run is not.
        return []


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------

def _claude_events(obj: Dict, tr: Tracker) -> List[Dict]:
    at = parse_time(obj.get("timestamp", ""))
    kind = obj.get("type", "")
    out: List[Dict] = []

    if kind == "user":
        cwd = obj.get("cwd")
        if not tr.project and isinstance(cwd, str) and cwd:
            tr.project = cwd
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        saw_result = False
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict) or item.get("type") != "tool_result":
                continue
            saw_result = True
            if item.get("is_error"):
                label = tr.recall(item.get("tool_use_id", ""))
                out.append(tr._event(at, "error", label))
        # A user record carrying tool results is the agent's own loop feeding
        # itself; a sidechain record is a prompt the agent wrote for a
        # subagent; an `isMeta` record is Claude Code putting text into the
        # conversation on its own account — the caveat before a slash
        # command's output, the body of a skill being loaded, a message
        # relayed from another session, a nudge to continue, the placeholder
        # standing in for a pasted image.  None is a person typing, and `»` is
        # the mark that means you — in a live feed it is the one a person uses
        # to find where they left off, so pointing it at machine text points
        # at the wrong place.  On 896 real logs the first two were 678 of 2992
        # marks, and injected text a further 210.
        #
        # The commands, writes and failures around them still stream: they ran
        # on this machine in this session.  Only the claim that you spoke goes.
        #
        # Only an explicit true counts as either — older logs have neither
        # field, and dropping a real turn is the opposite mistake.
        if (not saw_result
                and obj.get("isSidechain") is not True
                and obj.get("isMeta") is not True):
            out.append(tr._event(at, "turn", ""))

    elif kind == "assistant":
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict) or item.get("type") != "tool_use":
                continue
            call_id = item.get("id", "")
            name = item.get("name", "")
            inp = item.get("input")
            if not isinstance(inp, dict):
                inp = {}
            path = tool_path(name, inp)
            if name == "Bash":
                cmd = inp.get("command", "")
                if isinstance(cmd, str) and cmd:
                    tr.remember(call_id, cmd)
                    out.append(tr._event(at, "cmd", cmd))
            elif is_write_tool(name) and path:
                tr.remember(call_id, "edit " + os.path.basename(path))
                out.append(tr._event(at, "write", path))
            elif name == "Read" and path:
                tr.remember(call_id, "read " + os.path.basename(path))
                out.append(tr._event(at, "read", path))
            elif name:
                tr.remember(call_id, name)

    return out


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------

def _codex_events(obj: Dict, tr: Tracker) -> List[Dict]:
    at = parse_time(obj.get("timestamp", ""))
    kind = obj.get("type", "")
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return []
    ptype = payload.get("type", "")
    out: List[Dict] = []

    if kind in ("session_meta", "turn_context"):
        cwd = payload.get("cwd")
        if isinstance(cwd, str) and cwd and not tr.project:
            tr.project = cwd
        # Codex spawns subagents, and each one gets a rollout file of its own,
        # named after the thread rather than the sitting.  This record is the
        # first line of that file and carries both: ``id`` is the thread, which
        # names nothing a person can look up, and ``session_id`` is the sitting
        # that asked for the work.  For a top-level session the two are equal,
        # so preferring the sitting costs nothing there.  A file joined partway
        # through never sees this record and keeps the name it was opened with,
        # which is the best that is left.
        sitting = payload.get("session_id")
        if kind == "session_meta" and isinstance(sitting, str) and sitting:
            tr.session = sitting

    elif kind == "event_msg":
        if ptype == "user_message":
            out.append(tr._event(at, "turn", ""))
        elif ptype == "patch_apply_end":
            out.extend(_codex_patch_result(payload, tr, at))
        elif ptype == "mcp_tool_call_end":
            out.extend(_codex_mcp_result(payload, tr, at))

    elif kind == "response_item":
        if ptype == "custom_tool_call":
            out.extend(_codex_script(payload, tr, at))
        elif ptype == "function_call" and is_work_call(payload.get("name")):
            out.extend(_codex_call(payload, tr, at))
        elif ptype == "custom_tool_call_output":
            # A patch that failed to apply has already been reported, in more
            # detail, by the patch_apply_end for this same call, so this line
            # would be a second and worse account of it.  That is the whole of
            # the silence, and it is decided by call id.
            #
            # It used to be decided by whether a command had been remembered,
            # which is not the same question and is wrong far more often than
            # it is right: of 67 failing snippets with no command to name on
            # the developer's 1189 rollouts, not one shared a call id with a
            # failing patch_apply_end.  Their patches had failed *inside* the
            # script, so no end record was ever written and nothing said
            # anything at all — one real session showed `0 errors` against six
            # failed patch attempts.
            call_id = payload.get("call_id") or ""
            if script_failed(payload.get("output")) \
                    and not tr.patch_failed(call_id):
                out.append(tr._event(at, "error",
                                     tr.recall(call_id) or "script failed"))
        elif ptype == "function_call_output":
            output = payload.get("output")
            if isinstance(output, dict):
                meta = output.get("metadata")
                if isinstance(meta, dict) and meta.get("exit_code", 0) not in (0, None):
                    out.append(tr._event(
                        at, "error", tr.recall(payload.get("call_id") or "")))

    return out


def _codex_writes(paths, root, tr: Tracker, at, sent=False) -> List[Dict]:
    """Write events for patched paths, made absolute and de-echoed.

    ``sent`` marks the reports that come from the call carrying the envelope,
    as opposed to the result confirming it landed.  Each of the former lets
    exactly one of the latter be dropped as its echo; see
    ``Tracker.confirms_envelope``.
    """
    out: List[Dict] = []
    for path in paths:
        if not os.path.isabs(path) and isinstance(root, str) and root:
            path = os.path.normpath(os.path.join(root, path))
        if sent:
            tr.envelope_sent(path, at)
        elif tr.confirms_envelope(path, at):
            continue
        out.append(tr._event(at, "write", path))
    return out


def _codex_reads(command, root, tr: Tracker, at) -> List[Dict]:
    """The reads a Codex command names, resolved the way its writes are.

    Claude reads a file by calling a tool named ``Read``, so ``--reads`` was
    handed a path.  Codex has no read tool: it runs ``sed -n '1,200p' x.py``
    and the command text is the only record.  Without this the flag accepts a
    Codex log and prints nothing, which reads as a quiet session rather than
    as a flag that was never wired up.
    """
    out: List[Dict] = []
    for path in files_a_command_reads(command):
        if not os.path.isabs(path) and root:
            path = os.path.normpath(os.path.join(root, path))
        out.append(tr._event(at, "read", path))
    return out


def _codex_script(payload: Dict, tr: Tracker, at) -> List[Dict]:
    """A ``custom_tool_call`` — how current Codex runs everything.

    The call carries a snippet of JavaScript rather than arguments, so the
    command and any patch envelope have to be read back out of it.  A build
    that stops sending these will simply stop matching; nothing here assumes
    the snippet is well formed.
    """
    raw = payload.get("input")
    if not isinstance(raw, str) or not raw:
        return []
    call_id = payload.get("call_id") or ""
    tr.running(call_id)
    out: List[Dict] = []

    where = script_workdir(raw)
    for cmd in script_commands(raw):
        # The first command in a snippet is the one the failure is named after:
        # by the time the result arrives, several may have run.
        if not tr.recall(call_id):
            tr.remember(call_id, cmd)
        out.append(tr._event(at, "cmd", cmd))
        # Claude reads a file by calling a tool named Read, so `--reads` had a
        # path handed to it.  Codex has no read tool: it runs `sed -n` and the
        # only record is the command.  Without this the flag accepts a Codex
        # log and shows nothing, which reads as a quiet session rather than an
        # unimplemented flag.
        out.extend(_codex_reads(cmd, where or tr.project or "", tr, at))

    # A snippet that only sends a patch has no command in it to be named after,
    # and the failure that may follow carries nothing but the call id.  The
    # envelope names the files it was trying to change, so remember that much:
    # `patch server.py` is a line a person can act on where a bare "something
    # failed" is not.  This remembers a label and nothing else — no write is
    # reported from the envelope, for the reason set out below.
    if not tr.recall(call_id):
        touched = patched_files(raw)
        if touched:
            tr.remember(call_id, "patch " + ", ".join(
                os.path.basename(p) for p in touched[:3]))

    # Codex does not always announce a cwd, but every exec snippet carries a
    # workdir.  Without reading it, the project column stays empty for a whole
    # session that was never in doubt.
    if not tr.project and where:
        tr.project = where

    # Deliberately no write here.  The envelope in the snippet only proves one
    # was sent; the patch_apply_end that usually follows says whether it
    # applied and names the files absolutely.  Reporting from the call instead
    # would announce edits that failed, and a scrolling feed has no retraction.
    #
    # "Usually" is the honest word: on 1189 real session files, 44 of them sent
    # an envelope and no end record ever came — 56 calls, against 713 end
    # records elsewhere.  Those edits are not shown, and that is the price.
    # There is no trigger that would recover them: the end record is the very
    # next record 711 times out of 763, so flushing on the next record would
    # fire early for 35 calls still in flight.  agentlog reads whole files and
    # can decide per session, so it covers this; a stream cannot.
    # tests/test_patch_envelope_silence.py pins both halves.
    return out


def _codex_call(payload: Dict, tr: Tracker, at) -> List[Dict]:
    """The older ``function_call`` shape, still on disk in older sessions."""
    try:
        args = json.loads(payload.get("arguments", "{}"))
    except (json.JSONDecodeError, ValueError, TypeError):
        args = {}
    if not isinstance(args, dict):
        args = {}
    call_id = payload.get("call_id") or ""
    cmd = args.get("cmd") or args.get("command") or ""
    patch = args.get("patch") or args.get("input") or ""
    if not isinstance(cmd, str):
        cmd = ""
    if not isinstance(patch, str):
        patch = ""
    out: List[Dict] = []
    root = args.get("workdir") or tr.project or ""
    if cmd:
        tr.remember(call_id, cmd)
        out.append(tr._event(at, "cmd", cmd))
        out.extend(_codex_reads(cmd, root, tr, at))
    out.extend(_codex_writes(patched_files(patch or cmd), root, tr, at,
                             sent=True))
    return out


def _codex_patch_result(payload: Dict, tr: Tracker, at) -> List[Dict]:
    """``patch_apply_end`` — the only place a failed patch is ever admitted."""
    changes = payload.get("changes")
    paths = sorted(changes) if isinstance(changes, dict) else []
    if payload.get("success", True):
        return _codex_writes(paths, tr.project or "", tr, at)
    tr.patch_failure(payload.get("call_id") or "")
    names = ", ".join(os.path.basename(p) for p in paths[:3])
    return [tr._event(at, "error", "patch did not apply" + (": " + names if names else ""))]


def _codex_mcp_result(payload: Dict, tr: Tracker, at) -> List[Dict]:
    """``mcp_tool_call_end`` — the only place an MCP call reports itself.

    The result is either ``{"Ok": ...}`` or ``{"Err": "..."}``, and only the
    failure is news: an MCP call is not a shell command, and a stream that
    turned every one of them into a ``cmd`` line would trade a missing failure
    for a wrong command count.  A failed one is what the watcher is for — an
    agent retrying a server that is not running otherwise looks like an agent
    thinking.
    """
    result = payload.get("result")
    if not isinstance(result, dict) or "Err" not in result:
        return []
    inv = payload.get("invocation")
    inv = inv if isinstance(inv, dict) else {}
    server = inv.get("server") if isinstance(inv.get("server"), str) else ""
    tool = inv.get("tool") if isinstance(inv.get("tool"), str) else ""
    # Both, where both are known: `read_mcp_resource` on its own does not say
    # which server was down, and that is the part a person can act on.
    what = "/".join(p for p in (server, tool) if p)
    return [tr._event(at, "error", "mcp " + what if what else "mcp call failed")]
