"""Parse Claude Code and Codex JSONL session files.

Each public function returns a session dict (or None for files that are empty
or metadata-only).  Every field is treated as optional; malformed lines are
silently skipped and counted in ``skipped_lines``.

Session dict keys
-----------------
id            str   — session identifier (from filename or record)
source        str   — 'claude' or 'codex'
project       str   — absolute working directory (best guess)
project_name  str   — basename of project directory
start         datetime | None — first timestamp seen
end           datetime | None — last timestamp seen
duration_s    float | None    — (end - start).total_seconds(), or None
models        list[str]       — unique model names observed
user_turns    int             — number of user-turn records
files_read    list[str]       — file paths from Read tool calls
files_written list[str]       — file paths from Write/Edit/MultiEdit and
                                NotebookEdit calls, and from Codex
                                patch_apply_end records
commands      list[str]       — shell commands from Bash, from Codex
                                custom_tool_call script snippets, and from
                                older exec_command / apply_patch calls
errors        int             — count of tool_result is_error records, plus
                                Codex commands that exited non-zero
failed_cmds   list[str]       — the commands those errors came from
tokens_in     int | None      — sum of input_tokens across assistant turns
tokens_out    int | None      — sum of output_tokens across assistant turns
ai_title      str | None      — auto-generated session title (Claude only)
version       str | None      — agent version string
skipped_lines int             — lines that could not be parsed
"""

from __future__ import annotations

import glob
import json
import os
import re
import stat
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .transcript import (
    decode_claude_project,
    files_a_command_reads,
    is_work_call,
    is_write_tool,
    parse_time,
    patched_files,
    script_commands,
    script_failed,
    script_workdir,
    session_id_for,
    tool_path,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _text(value) -> str:
    """A field that ought to be a string, as a string.  Anything else is empty.

    Session logs are written by another program having a bad day; a ``cwd``
    that arrives as a number should cost the digest one blank field, not the
    whole run.
    """
    return value if isinstance(value, str) else ""


def _obj(value) -> Dict:
    """A field that ought to be an object, as an object."""
    return value if isinstance(value, dict) else {}


def _items(value) -> List:
    """A field that ought to be a list, as a list."""
    return value if isinstance(value, list) else []


def _count(value) -> int:
    """A token count, however the log spelled it.  Anything else is zero."""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (ValueError, OverflowError):
            return 0
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return 0
    return 0


def _failed(value) -> bool:
    """Did a command exit non-zero?  Missing or unreadable counts as success.

    Guessing "failed" from a field nobody can parse would invent errors that
    never happened, which is worse than missing a real one.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        try:
            return int(value.strip()) != 0
        except ValueError:
            return False
    return False


def _compaction(ts, metadata) -> Optional[Dict]:
    """One `compact_boundary` record, or None if it says nothing usable.

    ``dropped`` is this compaction's own loss, ``pre - post``.  The record also
    carries ``cumulativeDroppedTokens``, which is a *running total* — on every
    real record it equals the running sum of ``pre - post`` — so adding that
    field up across a session counts the first compaction once for every
    compaction after it.  A session with three of them would report roughly
    three times what was actually lost, and the only thing wrong with the
    number is that it is too big, which is not something a reader can catch.

    A record with no metadata, or with token counts that are not numbers, is
    dropped rather than guessed at: a compaction reported with invented sizes
    is worse than one not reported, because the sizes are the whole point.
    """
    meta = _obj(metadata)
    pre, post = meta.get("preTokens"), meta.get("postTokens")
    if not _is_number(pre) or not _is_number(post):
        return None
    pre, post = int(pre), int(post)
    return {
        "at": ts,
        # Whatever the log said.  Guessing "auto" for a trigger this tool has
        # not seen before would report a wall the session never hit.
        "trigger": _text(meta.get("trigger")) or "?",
        "pre": pre,
        "post": post,
        # Never negative: a post larger than pre is not a compaction that
        # gained context, it is a record we cannot read.
        "dropped": max(0, pre - post),
        # Nor is a clock that stepped backwards mid-compaction time refunded.
        "duration_s": max(0.0, _count(meta.get("durationMs")) / 1000.0),
    }


def _is_number(value) -> bool:
    """A real number, written as one.  `True` is not a token count."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _dedup(items: List[str]) -> List[str]:
    """Deduplicate a list while preserving first-seen order."""
    seen: set = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# A silence longer than this is not work.  Five minutes, because that is the
# threshold that matches the ground truth: Claude Code writes a `turn_duration`
# record saying how long each turn really took, and against the 76 sessions on
# this machine that carry them, splitting at five minutes comes to 0.93 of the
# recorded time in aggregate and a median of 1.10 per session.  Ten minutes
# overshoots on both counts, and measuring first-event-to-last — which is what
# this replaced — came to 14x.  See tests/test_idle_gaps.py.
IDLE_GAP_S = 300


def active_spans(s: Dict, start: Optional[datetime] = None,
                 end: Optional[datetime] = None,
                 end_open: bool = False) -> List[tuple]:
    """The stretches during which a session was actually busy.

    A session is not one continuous piece of work.  It is bursts of it with
    nothing in between, and the gaps are somebody at lunch or asleep — a session
    left open overnight did nothing at 3am.  Measuring from its first event to
    its last therefore billed the night as work, and on real logs that came to
    fourteen times the truth.  Each returned pair is a stretch with no silence
    longer than ``IDLE_GAP_S`` in it.

    A session with no timestamped events keeps its whole span as one stretch:
    that is the same fallback the counting takes, for the same reason — we
    cannot see inside it, and a lifetime total is a worse answer than a clipped
    one but a made-up one is worse than both.

    But only a session we genuinely cannot see inside.  A session with four
    thousand events, none of them on the day being asked for, is one we can see
    inside perfectly well, and what we can see is that it slept through that
    day — the answer is nothing, not the width of the window.  Getting that
    wrong made a week come out shorter than the days inside it added up to.
    See tests/test_quiet_days.py.
    """
    stamped = [ts for ts, _kind, _value in (s.get("events") or []) if ts is not None]
    times = sorted(
        ts for ts in stamped
        if (start is None or ts >= start)
        and (end is None or (ts < end if end_open else ts <= end))
    )
    if not times:
        if stamped:
            return []
        first = start if start is not None else s.get("start")
        last = end if end is not None else (s.get("end") or first)
        if first is None or last is None or last < first:
            return []
        return [(first, last)]

    spans: List[tuple] = []
    opened = previous = times[0]
    for ts in times[1:]:
        if (ts - previous).total_seconds() > IDLE_GAP_S:
            spans.append((opened, previous))
            opened = ts
        previous = ts
    spans.append((opened, previous))
    return spans


def _empty_session(session_id: str, source: str) -> Dict:
    return {
        "id": session_id,
        "source": source,
        "project": "",
        "project_name": "",
        "events": [],
        "token_events": [],
        "start": None,
        "end": None,
        "duration_s": None,
        "models": [],
        "user_turns": 0,
        "files_read": [],
        "files_written": [],
        "commands": [],
        "errors": 0,
        "failed_cmds": [],
        "write_counts": {},
        "tokens_in": None,
        "tokens_out": None,
        "ai_title": None,
        "recaps": [],
        "compactions": [],
        "version": None,
        "skipped_lines": 0,
    }


def _read_lines(path: str) -> tuple[List[str], int]:
    """Read lines from a JSONL file.  Returns (lines, read_error_count).

    Only regular files are opened.  A FIFO or a socket that happens to be named
    ``*.jsonl`` blocks *at open* until somebody writes to it, and a digest tool
    that hangs forever on a stray pipe is worse than one that crashes — there is
    nothing on screen to explain the wait.
    """
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return [], 1
    except OSError:
        return [], 1
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.readlines(), 0
    except OSError:
        return [], 1


# ---------------------------------------------------------------------------
# Claude Code parser
# ---------------------------------------------------------------------------

_RECAP_TRAILER = re.compile(r"\s*\([^()]*/config[^()]*\)\s*$")


def _strip_recap_trailer(text: str) -> str:
    """A recap, with the note to the watcher taken off the end."""
    return _RECAP_TRAILER.sub("", text or "").strip()


def _claude_tool_items(assistant_obj: Dict):
    """Yield (tool_id, tool_name, tool_input) for each tool_use in an assistant record."""
    msg = _obj(assistant_obj.get("message"))
    for item in _items(msg.get("content")):
        if not isinstance(item, dict):
            continue
        if item.get("type") != "tool_use":
            continue
        yield (
            _text(item.get("id")),
            _text(item.get("name")),
            _obj(item.get("input")),
        )


#: Returned instead of ``None`` for a file whose every record was already
#: counted from an earlier file.  ``None`` means "this file contributed
#: nothing", which is what the ``unusable`` note is for; this means "this file
#: contributed nothing *because it was all already in the report*", which the
#: reader does not need to hear about.  Identity-compared, never inspected.
REPLAY: Dict = {"replay": True}


def parse_claude_session(
    path: str, seen_uuids: Optional[set] = None
) -> Optional[Dict]:
    """Parse one Claude Code JSONL file.  Returns a session dict or None.

    ``seen_uuids`` is the set of record uuids already counted, and is added to
    as this file is read.  Claude Code writes the same record into two files in
    two ordinary situations — ``--resume`` copies the earlier transcript into
    the new session verbatim, and a copied or moved project directory leaves
    the whole log under both names — so a uuid is the identity of an event and
    an event is counted once.  Pass a shared set across the files of one run to
    get that; the default is a fresh set, which still protects against a record
    repeated inside a single file.

    Callers that pass a shared set must read the files oldest-first, so that
    the sitting where the work actually happened is the one that reports it.
    """
    if seen_uuids is None:
        seen_uuids = set()
    replayed = 0
    session_id = session_id_for(path, "claude")
    lines, read_err = _read_lines(path)

    s = _empty_session(session_id, "claude")
    s["skipped_lines"] = read_err

    seen_tool_ids: set = set()
    seen_msg_ids: set = set()
    # tool_use id -> a short label for what that call did, so a failure can be
    # reported as the command that failed rather than as an anonymous count.
    tool_labels: Dict[str, str] = {}
    tok_in = 0
    tok_out = 0
    files_read: List[str] = []
    files_written: List[str] = []
    commands: List[str] = []

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            s["skipped_lines"] += 1
            continue
        if not isinstance(obj, dict):
            s["skipped_lines"] += 1
            continue

        record_type = obj.get("type", "")
        ts = parse_time(obj.get("timestamp", ""))

        uuid = _text(obj.get("uuid"))
        if uuid and uuid in seen_uuids:
            replayed += 1
            # The directory and the version belong to the sitting rather than
            # to the record, so a replayed record can still say which sitting
            # this is; taking them costs nothing and keeps the row readable.
            # Everything below this point is a count, and is skipped -- the
            # timestamp included, because a resume that inherited the earlier
            # transcript's timestamps would otherwise look like a session that
            # had been running since the morning.
            if record_type == "user":
                if not s["project"]:
                    s["project"] = _text(obj.get("cwd"))
                if not s["version"]:
                    s["version"] = _text(obj.get("version")) or None
            continue
        if uuid:
            seen_uuids.add(uuid)

        if ts:
            if s["start"] is None or ts < s["start"]:
                s["start"] = ts
            if s["end"] is None or ts > s["end"]:
                s["end"] = ts

        if record_type == "user":
            if not s["project"]:
                s["project"] = _text(obj.get("cwd"))
            if not s["version"]:
                s["version"] = _text(obj.get("version")) or None
            # Prefer the sessionId embedded in the record over the filename
            if s["id"] == session_id and _text(obj.get("sessionId")):
                s["id"] = _text(obj["sessionId"])
            # Count tool errors embedded in user content
            msg = _obj(obj.get("message"))
            saw_result = False
            for item in _items(msg.get("content")):
                if not isinstance(item, dict) or item.get("type") != "tool_result":
                    continue
                saw_result = True
                if item.get("is_error"):
                    s["errors"] += 1
                    label = tool_labels.get(_text(item.get("tool_use_id")), "")
                    s["failed_cmds"].append(label)
                    s["events"].append((ts, "error", label))

            # A `user` record is written for four different things and only
            # one of them is a person: a tool result is the agent feeding
            # itself, a sidechain record is a prompt the agent wrote for a
            # subagent, and an `isMeta` record is Claude Code putting text into
            # the conversation on its own account — the caveat before a slash
            # command's output, the body of a skill being loaded, a message
            # relayed from another session, a nudge to continue, the
            # placeholder standing in for a pasted image.  Counting all four
            # said 38318 turns on 896 real logs where 2314 were typed — and an
            # over-count is the one a reader cannot catch, because there is
            # nothing to check it against.
            #
            # Only an explicit true counts as either: both fields are absent on
            # older logs, and dropping those turns would be the opposite error.
            if (saw_result
                    or obj.get("isSidechain") is True
                    or obj.get("isMeta") is True):
                continue
            s["user_turns"] += 1
            s["events"].append((ts, "turn", ""))

        elif record_type == "assistant":
            msg = _obj(obj.get("message"))
            msg_id = _text(msg.get("id"))

            # Token counts — deduplicate by message id.
            # Include cache_creation_input_tokens and cache_read_input_tokens:
            # Claude Code's prompt caching means input_tokens alone is almost
            # zero on most turns; the cache fields carry the real load.
            if msg_id and msg_id not in seen_msg_ids:
                seen_msg_ids.add(msg_id)
                usage = _obj(msg.get("usage"))
                spent_in = (
                    _count(usage.get("input_tokens"))
                    + _count(usage.get("cache_creation_input_tokens"))
                    + _count(usage.get("cache_read_input_tokens"))
                )
                spent_out = _count(usage.get("output_tokens"))
                tok_in += spent_in
                tok_out += spent_out
                # And when it was spent, so a day can add up its own.  See
                # tests/test_window_tokens.py.
                if spent_in or spent_out:
                    s["token_events"].append((ts, spent_in, spent_out))

            model = _text(msg.get("model"))
            if model and model not in s["models"]:
                s["models"].append(model)

            # Tool calls — deduplicate by tool_use id
            for tool_id, name, inp in _claude_tool_items(obj):
                if tool_id and tool_id in seen_tool_ids:
                    continue
                if tool_id:
                    seen_tool_ids.add(tool_id)
                fp = tool_path(name, inp)
                if name == "Read" and fp:
                    files_read.append(fp)
                    s["events"].append((ts, "read", fp))
                    tool_labels[tool_id] = f"read {os.path.basename(fp)}"
                elif is_write_tool(name) and fp:
                    files_written.append(fp)
                    s["write_counts"][fp] = s["write_counts"].get(fp, 0) + 1
                    s["events"].append((ts, "write", fp))
                    tool_labels[tool_id] = f"edit {os.path.basename(fp)}"
                elif name == "Bash":
                    cmd = _text(inp.get("command"))
                    if cmd:
                        commands.append(cmd)
                        s["events"].append((ts, "cmd", cmd))
                        tool_labels[tool_id] = cmd
                elif name:
                    tool_labels[tool_id] = name

        elif record_type == "ai-title":
            s["ai_title"] = _text(obj.get("aiTitle")) or None

        elif record_type == "system" and obj.get("subtype") == "away_summary":
            text = _strip_recap_trailer(_text(obj.get("content")))
            if text:
                s["recaps"].append((ts, text))

        elif record_type == "system" and obj.get("subtype") == "compact_boundary":
            entry = _compaction(ts, obj.get("compactMetadata"))
            if entry is not None:
                s["compactions"].append(entry)

    # Require at least one user turn or timestamp to be a real session
    if s["user_turns"] == 0 and s["start"] is None:
        return REPLAY if replayed else None

    if not s["project"]:
        s["project"] = decode_claude_project(path)
    s["project_name"] = os.path.basename(s["project"]) if s["project"] else session_id[:8]
    if s["start"] and s["end"]:
        s["duration_s"] = (s["end"] - s["start"]).total_seconds()
    s["files_read"] = _dedup(files_read)
    s["files_written"] = _dedup(files_written)
    s["commands"] = _dedup(commands)
    if tok_in > 0:
        s["tokens_in"] = tok_in
    if tok_out > 0:
        s["tokens_out"] = tok_out
    return s


# ---------------------------------------------------------------------------
# Codex parser
# ---------------------------------------------------------------------------

# Calls that represent work done on the machine.  Everything else Codex emits
# (update_plan, spawn_agent, wait, send_message) is coordination, not activity.


def _reads_in(command: str, root) -> List[str]:
    """The files a command read, as absolute paths where that can be known.

    A command names its reads relative to the directory it ran in, the same way
    an apply_patch envelope names its writes, and for the same reason the
    writes are resolved here: without it one file appears twice under two
    spellings and the digest reports two.
    """
    out = []
    for path in files_a_command_reads(command):
        if not os.path.isabs(path) and isinstance(root, str) and root:
            path = os.path.normpath(os.path.join(root, path))
        out.append(path)
    return out


def parse_codex_session(path: str) -> Optional[Dict]:
    """Parse one Codex JSONL file.  Returns a session dict or None."""
    session_id = session_id_for(path, "codex")
    lines, read_err = _read_lines(path)
    s = _empty_session(session_id, "codex")
    s["skipped_lines"] = read_err

    tok_in = 0
    tok_out = 0
    # The per-turn fallback, used only for a log with no total_token_usage.
    turn_in = 0
    turn_out = 0
    saw_total = False
    commands: List[str] = []
    files_read: List[str] = []
    files_written: List[str] = []
    # call_id -> command, so a non-zero exit names the command that failed
    call_cmds: Dict[str, str] = {}
    # Files named in a patch envelope, held back in case this session turns out
    # not to report its patches any other way.  See the fallback below.
    envelope_writes: List[tuple] = []
    saw_patch_end = False

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            s["skipped_lines"] += 1
            continue
        if not isinstance(obj, dict):
            s["skipped_lines"] += 1
            continue

        record_type = obj.get("type", "")
        ts = parse_time(obj.get("timestamp", ""))
        if ts:
            if s["start"] is None or ts < s["start"]:
                s["start"] = ts
            if s["end"] is None or ts > s["end"]:
                s["end"] = ts

        payload = obj.get("payload") or {}
        if not isinstance(payload, dict):
            continue

        if record_type == "session_meta":
            s["id"] = (_text(payload.get("session_id"))
                       or _text(payload.get("id")) or session_id)
            s["project"] = _text(payload.get("cwd"))
            s["version"] = _text(payload.get("cli_version")) or None

        elif record_type == "turn_context":
            if not s["project"]:
                s["project"] = _text(payload.get("cwd"))

        elif record_type == "event_msg":
            pt = _text(payload.get("type"))
            if pt == "user_message":
                s["user_turns"] += 1
                s["events"].append((ts, "turn", ""))
            elif pt == "patch_apply_end":
                saw_patch_end = True
                # The only record that says which files a patch actually
                # changed, and the only one that admits a patch that did not
                # apply.  Reading the envelope in the call instead would list
                # edits that never reached the disk.  Paths here are absolute.
                changes = payload.get("changes")
                paths = sorted(changes) if isinstance(changes, dict) else []
                if payload.get("success", True):
                    for path in paths:
                        files_written.append(path)
                        s["write_counts"][path] = \
                            s["write_counts"].get(path, 0) + 1
                        s["events"].append((ts, "write", path))
                else:
                    names = ", ".join(os.path.basename(p) for p in paths[:3])
                    label = "patch did not apply" + (": " + names if names else "")
                    s["errors"] += 1
                    s["failed_cmds"].append(label)
                    s["events"].append((ts, "error", label))
            elif pt == "mcp_tool_call_end":
                # An MCP call reports itself here and nowhere else, and its
                # result is either {"Ok": ...} or {"Err": "..."}.  A failed one
                # is a failure exactly like a command that exited non-zero, and
                # a session whose only failures were these used to read
                # `0 errors` — a claim, not a partial count.
                #
                # A successful call is deliberately not turned into a command:
                # an MCP call is not a shell command, and `commands` means one
                # thing.  Only the failure is news.
                result = payload.get("result")
                if isinstance(result, dict) and "Err" in result:
                    inv = _obj(payload.get("invocation"))
                    server = _text(inv.get("server"))
                    tool = _text(inv.get("tool"))
                    # The tool name alone does not say which server was down,
                    # so both are shown where both are known.
                    what = "/".join(p for p in (server, tool) if p)
                    label = "mcp " + what if what else "mcp call failed"
                    s["errors"] += 1
                    s["failed_cmds"].append(label)
                    s["events"].append((ts, "error", label))
            elif pt == "token_count":
                # Two usage blocks sit side by side in this record and only
                # one of them is the session.  `last_token_usage` is the turn
                # that just finished; `total_token_usage` is everything so
                # far.  Reading the first as if it were the second reported a
                # session's most expensive single turn as its whole total —
                # 10.8x low on input across the Codex sessions on this
                # machine, and 97x on the worst of them.  It hid well: the
                # number it printed was the size of a plausible turn.
                #
                # The total is monotonic, so the high-water mark is the final
                # snapshot.  Where the field is missing — a log older than it
                # — the per-turn numbers add up to exactly the same thing,
                # which is what the fallback below does.
                # tests/test_codex_tokens.py
                info = _obj(payload.get("info"))
                total = info.get("total_token_usage")
                if isinstance(total, dict):
                    saw_total = True
                    # The total is a high-water mark, so what this turn cost is
                    # how much it moved.  Recording the difference lets a day
                    # add up its own share; the differences still sum to the
                    # final total, which is what the session reports.
                    was_in, was_out = tok_in, tok_out
                    tok_in = max(tok_in, _count(total.get("input_tokens")))
                    tok_out = max(tok_out, _count(total.get("output_tokens")))
                    if tok_in > was_in or tok_out > was_out:
                        s["token_events"].append(
                            (ts, tok_in - was_in, tok_out - was_out))
                else:
                    last = _obj(info.get("last_token_usage"))
                    spent_in = _count(last.get("input_tokens"))
                    spent_out = _count(last.get("output_tokens"))
                    turn_in += spent_in
                    turn_out += spent_out
                    if spent_in or spent_out:
                        s["token_events"].append((ts, spent_in, spent_out))

        elif record_type == "response_item":
            pt = _text(payload.get("type"))
            if pt == "custom_tool_call":
                # How current Codex runs everything.  See transcript.script_commands.
                raw_input = payload.get("input")
                found = script_commands(raw_input)
                call_id = _text(payload.get("call_id"))
                # A command names its reads the way it names its writes: in
                # its own text.  Codex has no read tool to look them up in.
                where = (script_workdir(raw_input)
                         if isinstance(raw_input, str) else "")
                for cmd in found:
                    commands.append(cmd)
                    s["events"].append((ts, "cmd", cmd))
                    for path in _reads_in(cmd, where or s["project"]):
                        files_read.append(path)
                        s["events"].append((ts, "read", path))
                # The same call can carry a patch envelope instead of a
                # command.  The files are remembered rather than recorded:
                # patch_apply_end says whether the patch actually landed, and
                # if this session sends those records it is the one to believe.
                patched = (patched_files(raw_input)
                           if isinstance(raw_input, str) else [])
                if patched:
                    envelope_writes.append((ts, patched))
                # The first command in a snippet is the one a failure is named
                # after: by the time the result arrives several may have run.
                # A patch call has no command in it at all, and an error
                # counted under a blank name is one the reader cannot act on —
                # the digest drops nameless failures from the list it prints
                # while still counting them.
                if call_id and call_id not in call_cmds:
                    if found:
                        call_cmds[call_id] = found[0]
                    elif patched:
                        call_cmds[call_id] = "patch " + ", ".join(
                            os.path.basename(p) for p in patched[:3])
            elif pt == "custom_tool_call_output":
                if script_failed(payload.get("output")):
                    s["errors"] += 1
                    label = call_cmds.get(_text(payload.get("call_id")), "")
                    s["failed_cmds"].append(label)
                    s["events"].append((ts, "error", label))
            elif pt == "function_call" and is_work_call(payload.get("name")):
                args_str = payload.get("arguments")
                if not isinstance(args_str, str):
                    args_str = "{}"
                try:
                    args = json.loads(args_str)
                except (json.JSONDecodeError, ValueError):
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                # exec_command carries `cmd`; apply_patch carries either a
                # `command` (a shell one-liner) or a `patch` envelope.
                cmd = args.get("cmd") or args.get("command") or ""
                patch = args.get("patch") or ""
                if not isinstance(cmd, str):
                    cmd = ""
                if not isinstance(patch, str):
                    patch = ""
                if cmd:
                    commands.append(cmd)
                    s["events"].append((ts, "cmd", cmd))
                    for path in _reads_in(
                            cmd, _text(args.get("workdir")) or s["project"]):
                        files_read.append(path)
                        s["events"].append((ts, "read", path))
                    call_id = _text(payload.get("call_id"))
                    if call_id:
                        call_cmds[call_id] = cmd
                # Codex edits files by piping an apply_patch envelope through
                # the shell, so the written paths are inside the command text
                # rather than in a structured field.
                # Envelopes name files relative to the working directory as
                # often as absolutely; without this the same file shows up
                # twice under two spellings.
                root = _text(args.get("workdir")) or s["project"] or ""
                for path in patched_files(patch or cmd):
                    if not os.path.isabs(path) and isinstance(root, str) and root:
                        path = os.path.normpath(os.path.join(root, path))
                    files_written.append(path)
                    s["write_counts"][path] = s["write_counts"].get(path, 0) + 1
                    s["events"].append((ts, "write", path))
            elif pt == "function_call_output":
                output = _obj(payload.get("output"))
                meta = _obj(output.get("metadata"))
                if _failed(meta.get("exit_code")):
                    s["errors"] += 1
                    label = call_cmds.get(_text(payload.get("call_id")), "")
                    s["failed_cmds"].append(label)
                    s["events"].append((ts, "error", label))

    if s["user_turns"] == 0 and s["start"] is None:
        return None

    # A session that never sent a patch_apply_end is from a build that does not
    # report its own patches, so the envelope in the call is the only record of
    # the edit and the session otherwise reads as having written nothing.  Where
    # even one end record exists the build does report them and the envelopes
    # are ignored — the end record knows which patches failed and the envelope
    # does not, and listing an edit that never reached the disk is the worse
    # error of the two, because nothing in the report contradicts it.
    if envelope_writes and not saw_patch_end:
        root = s["project"] or ""
        for ts, paths in envelope_writes:
            for path in paths:
                if not os.path.isabs(path) and root:
                    path = os.path.normpath(os.path.join(root, path))
                files_written.append(path)
                s["write_counts"][path] = s["write_counts"].get(path, 0) + 1
                s["events"].append((ts, "write", path))
        s["events"].sort(key=lambda e: e[0] or _EPOCH)

    s["project_name"] = os.path.basename(s["project"]) if s["project"] else session_id[:8]
    if s["start"] and s["end"]:
        s["duration_s"] = (s["end"] - s["start"]).total_seconds()
    s["commands"] = _dedup(commands)
    s["files_read"] = _dedup(files_read)
    s["files_written"] = _dedup(files_written)
    if not saw_total:
        tok_in, tok_out = turn_in, turn_out
    if tok_in > 0:
        s["tokens_in"] = tok_in
    if tok_out > 0:
        s["tokens_out"] = tok_out
    return s


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

UNREADABLE = "could not be read"
NO_RECORDS = "had no readable records"


def _why_unusable(path: str, sess: Optional[Dict]) -> str:
    """Why this file will not appear in any report, or '' if it will.

    A session with no start time cannot fall inside any day window, so it is
    dropped by the time filter before anything is rendered — silently, and
    including the count of what it skipped.  This asks the file itself why,
    so the report can say so instead.

    Only ever asked about files that produced nothing, so the extra stat calls
    fall on the handful that are already broken.

    A zero-byte file is deliberately not one of these: a session that has just
    started is empty on disk, nothing has been lost, and warning about it would
    make the note fire on an ordinary morning.
    """
    if sess is not None and sess["start"] is not None:
        return ""
    try:
        st = os.stat(path)
    except OSError:
        return UNREADABLE
    if not stat.S_ISREG(st.st_mode):
        return UNREADABLE
    if st.st_size == 0:
        return ""
    try:
        with open(path, "rb") as fh:
            fh.read(1)
    except OSError:
        return UNREADABLE
    # It opened and it has bytes in it, so the bytes are the problem: truncated
    # mid-write, a different format, or a file that only happens to end .jsonl.
    return NO_RECORDS


def _merge_sessions(group: List[Dict]) -> Dict:
    """Fold several files of one session into the session they describe.

    Codex writes one file per parallel worker and gives them all the same
    session_id.  Every one of them is a different part of the same session's
    work, so the lists are unioned and the counts are added up.

    The lists are unioned and not concatenated: ``commands`` and the two file
    lists have always been sets-in-order within a single file, and a merge that
    changed what they mean would trade this bug for an over-count, which is
    worse — an under-count can be caught by adding up the source files, an
    over-count cannot be caught at all.  ``errors``, ``user_turns``, the tokens
    and ``write_counts`` are genuine per-worker tallies, and do add.
    """
    if len(group) == 1:
        return group[0]
    # Oldest first, so the merged lists read in the order the work happened
    # rather than in the order the filesystem handed the files over.
    ordered = sorted(group, key=lambda s: (s["start"] or _EPOCH, s["end"] or _EPOCH))
    merged = dict(ordered[0])
    merged["events"] = []
    merged["token_events"] = []
    merged["models"] = []
    merged["files_read"] = []
    merged["files_written"] = []
    merged["commands"] = []
    merged["failed_cmds"] = []
    merged["compactions"] = []
    merged["write_counts"] = {}
    merged["user_turns"] = 0
    merged["errors"] = 0
    merged["skipped_lines"] = 0
    tok_in = tok_out = None
    for s in ordered:
        merged["events"].extend(s["events"])
        merged["token_events"].extend(s.get("token_events") or [])
        merged["models"].extend(s["models"])
        merged["files_read"].extend(s["files_read"])
        merged["files_written"].extend(s["files_written"])
        merged["commands"].extend(s["commands"])
        merged["failed_cmds"].extend(s["failed_cmds"])
        # Concatenated, not unioned: two workers that both had to compact are
        # two compactions, and each one really cost its own time.
        merged["compactions"].extend(s.get("compactions") or [])
        for path, n in s["write_counts"].items():
            merged["write_counts"][path] = merged["write_counts"].get(path, 0) + n
        merged["user_turns"] += s["user_turns"]
        merged["errors"] += s["errors"]
        merged["skipped_lines"] += s["skipped_lines"]
        if s["tokens_in"] is not None:
            tok_in = (tok_in or 0) + s["tokens_in"]
        if s["tokens_out"] is not None:
            tok_out = (tok_out or 0) + s["tokens_out"]
        # A worker that never learned the project or the version leaves the
        # field empty; one that did fills it in for the session.
        for field in ("project", "project_name", "version", "ai_title"):
            if not merged.get(field) and s.get(field):
                merged[field] = s[field]
    # A record whose timestamp would not parse still has an event; it sorts to
    # the front rather than raising halfway through the merge.
    merged["compactions"].sort(key=lambda c: c["at"] or _EPOCH)
    merged["events"].sort(key=lambda e: e[0] or _EPOCH)
    merged["token_events"].sort(key=lambda e: e[0] or _EPOCH)
    merged["models"] = _dedup(merged["models"])
    merged["files_read"] = _dedup(merged["files_read"])
    merged["files_written"] = _dedup(merged["files_written"])
    merged["commands"] = _dedup(merged["commands"])
    merged["tokens_in"] = tok_in
    merged["tokens_out"] = tok_out
    starts = [s["start"] for s in ordered if s["start"]]
    ends = [s["end"] for s in ordered if s["end"]]
    merged["start"] = min(starts) if starts else None
    merged["end"] = max(ends) if ends else None
    merged["duration_s"] = (
        (merged["end"] - merged["start"]).total_seconds()
        if merged["start"] and merged["end"] else None)
    return merged


def _oldest_first(paths: List[str]) -> List[str]:
    """Oldest file first, ties broken by name, unstattable files last.

    When the same record is in two files, the first file read is the one that
    reports it, so this is what decides that a resume shows the work done in
    the resume and the earlier sitting keeps its own.  Without it the answer
    would depend on the order the filesystem happened to list the directory in.
    """
    def key(p: str) -> tuple:
        try:
            return (os.path.getmtime(p), p)
        except OSError:
            return (float("inf"), p)
    return sorted(paths, key=key)


def find_sessions(
    home_dir: Optional[str] = None,
) -> tuple[List[Dict], List[str], List[tuple]]:
    """Find all sessions from Claude Code and Codex.

    Returns ``(sessions, sources, unusable)``.  ``sources`` is a list of names
    of agents whose logs were found.  ``sessions`` is sorted newest-first.
    ``unusable`` is a list of ``(path, reason)`` for log files that exist and
    contributed nothing — the caller is expected to say so, because a report
    computed from fewer files than are on disk looks exactly like a complete
    one.  If neither log directory exists every list is empty.
    """
    if home_dir is None:
        home_dir = os.path.expanduser("~")

    claude_dir = os.path.join(home_dir, ".claude", "projects")
    codex_dir = os.path.join(home_dir, ".codex", "sessions")

    sessions: List[Dict] = []
    sources: List[str] = []
    unusable: List[tuple] = []

    # Track real (resolved) file paths to skip symlink duplicates.
    seen_real_paths: set = set()
    # Track record uuids, so that a record written into two files -- a resume,
    # or a copied project directory -- is counted once.  See parse_claude_session.
    seen_uuids: set = set()

    def _add(sess: Optional[Dict], path: str) -> None:
        # The duplicate check now happens before the None check, so a symlink
        # to an unreadable file is reported once rather than once per link.
        real = os.path.realpath(path)
        if real in seen_real_paths:
            return
        seen_real_paths.add(real)
        if sess is REPLAY:
            # Every record in it is already in the report, under the sitting
            # that did the work.  Nothing is missing, so nothing is said.
            return
        reason = _why_unusable(path, sess)
        if reason:
            unusable.append((path, reason))
        if sess is None:
            return
        sessions.append(sess)

    if os.path.isdir(claude_dir):
        sources.append("Claude Code")
        for path in _oldest_first(
                glob.glob(os.path.join(claude_dir, "**", "*.jsonl"), recursive=True)):
            _add(parse_claude_session(path, seen_uuids), path)

    if os.path.isdir(codex_dir):
        sources.append("Codex")
        # Codex records carry no uuid, and its duplicate files are its parallel
        # workers, whose separate work does add up -- so nothing here is deduped
        # by record.  It is read in the same order only so the report is the
        # same however the filesystem hands the paths over.
        for path in _oldest_first(
                glob.glob(os.path.join(codex_dir, "**", "*.jsonl"), recursive=True)):
            _add(parse_codex_session(path), path)

    # Collapse by session ID: Codex parallel-worker files all carry the same
    # session_id in their session_meta record, so they are one session and
    # belong on one row.  They are merged rather than chosen between, because
    # each worker file records that worker's own work: on the machine this was
    # written for, 21 of 1147 Codex sessions had more than one file, 38 of the
    # 42 extra files held commands the richest file did not, and keeping only
    # the richest dropped 299 of those sessions' 616 commands.  None of the
    # extra files was a byte-for-byte copy of another.
    #
    # Claude Code is the opposite case and is handled before this point, by
    # record uuid: there a second file holding the same records is a resume or
    # a copied project directory, and adding it up would count the same work
    # twice.  Merging is only ever safe once the records are known distinct.
    by_id: Dict[str, List[Dict]] = {}
    for s in sessions:
        by_id.setdefault(s["id"], []).append(s)
    sessions = [_merge_sessions(group) for group in by_id.values()]

    # Sort newest-first; sessions without a start time go to the end
    sessions.sort(key=lambda s: s["start"] or _EPOCH, reverse=True)
    unusable.sort()
    return sessions, sources, unusable
