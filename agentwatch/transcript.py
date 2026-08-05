"""What a session transcript is, once, for the two tools that read one.

Claude Code and Codex each write their session to a JSONL file, and this family
has two tools that read those files: `agentlog`, which reads a finished session
and reports what happened in it, and `agentwatch`, which tails a live one and
reports what is happening now.  Different questions, different outputs, and for
a long time two independent readings of the same two file formats.

They had drifted the way copies do.  The timestamp reader was written twice,
once guarding on `isinstance(raw, str)` and once by catching `AttributeError`
-- the same answer for every input, arrived at two different ways, each with
its own paragraph explaining why it assumed UTC.  `_patched_files` appeared in
both files at twenty-one identical lines, `_js_unescape` at eleven, `_unquote`
at seven, `_script_failed` at fifteen with a docstring one of the two had grown
and the other had not.  Five regexes and dicts were declared twice.  Nobody
decided any of that: it is what two readings of one format do when nothing is
watching them.

The reason it matters is not the line count.  It is that a fix to how Codex
records a file write is a fix in one place or two, and which one it is depends
on whether whoever made it knew the other copy existed.  That is how the last
bug here got out.

So the facts of the formats live here and the views stay where they were.  What
this module knows:

  * how a stamp is written, in both formats;
  * which Claude tool calls touch a file, and where in the call the path is;
  * which Codex calls are work rather than chatter;
  * how a Codex exec snippet carries its commands, its working directory, and
    the file-patch envelope that is the only record Codex keeps of a write;
  * how a Codex call says it failed.

What it does not know: what any of that means, what to count, what to print,
what a session is, or what an event is.  Those are the two views, and they stay
two.

Nothing here imports outside the standard library, because this file is copied
into both packages rather than shared -- `pip install stillworks` promises no
package in the family reaches into another, and
`test_every_import_is_stdlib_or_the_packages_own` enforces it.  The copies are
pinned byte-identical by
`stillworks/tests/test_the_transcript_format_is_read_the_same_way_twice.py`, so
a fix made in one is a fix in both or it is a failing test.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def parse_time(raw) -> Optional[datetime]:
    """An ISO 8601 stamp as an *aware* datetime, or None if it is not one.

    Aware for every input, never naive.  Every real record ends in ``Z``, but
    these files are written by another program, and one that dropped its offset
    came back naive -- and then the first comparison against an aware datetime
    raised TypeError, halfway through a digest in one tool and taking the
    watcher down with a traceback and an exit 1 in the other.

    A naive stamp is read as UTC, which is the offset the format is written in.
    The alternative, letting Python resolve it as local, put the same log line
    nine hours from where the other tool put it when read in Tokyo: two tools in
    one family disagreeing about one line, quietly, and differently on each
    machine.  Assuming UTC can still be wrong, but it is wrong by the same
    amount everywhere -- which is the whole reason this function is one
    function.

    Anything that is not a usable string is None rather than an exception, for
    the same reason every other reader here is forgiving: the file is somebody
    else's output on a bad day.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        # The `Z` rewrite is not decoration and it is not dead.  Python 3.11
        # and later read `Z` themselves, so on a modern interpreter removing
        # this changes nothing and no test in either package fails -- a mutant
        # campaign proved that.  On 3.9 and 3.10, which the family supports,
        # `fromisoformat` raises ValueError on it, and every stamp in every
        # record of both formats ends in `Z`: the digest would be empty and the
        # watcher would show a session with no time in it.
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Claude Code: tool calls
# ---------------------------------------------------------------------------

# Which Claude tool calls write to a file, and the input field the path is in.
# `NotebookEdit` is the reason this is a mapping and not a set: it does not put
# its path under `file_path` like the other three, so knowing the tool's name is
# not enough to find what it wrote.  A day spent in a notebook read `0 files
# written`.  agentlog's tests/test_notebook_writes.py is where that is pinned.
_WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path",
                "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}


def tool_path(name: str, tool_input: Dict) -> str:
    """The file a Claude tool call names, or "" if it names none.

    Both readers want this before they know what to do with it -- a path is how
    a read is reported and how a write is reported -- so the lookup happens
    once, and neither caller has to know that one tool spells the field
    differently from the other three.

    "" for anything that is not a usable string: a `file_path` that arrived as
    a number is one blank field, not a crash.
    """
    if not isinstance(tool_input, dict):
        return ""
    value = tool_input.get(_WRITE_TOOLS.get(name, "file_path"))
    return value if isinstance(value, str) else ""


def is_write_tool(name: str) -> bool:
    """Does this Claude tool call change a file?"""
    return name in _WRITE_TOOLS


# ---------------------------------------------------------------------------
# Codex: work calls
# ---------------------------------------------------------------------------

# The two Codex function calls that do something, as against the several that
# only talk about doing something.
_WORK_CALLS = {"exec_command", "apply_patch"}


def is_work_call(name) -> bool:
    """Is this Codex function call work, rather than chatter?"""
    return name in _WORK_CALLS


# ---------------------------------------------------------------------------
# Codex: the exec snippet
# ---------------------------------------------------------------------------
#
# A current Codex session does not record a command as a field.  It records a
# line of JavaScript that calls one, and everything below is the cost of that:
# the command, the working directory and the patch envelope all have to be read
# back out of source text that is not JSON and cannot be parsed as any one
# thing.

_JS_COMMAND = re.compile(
    r'["\']?\b(?:cmd|command)\b["\']?\s*:\s*"((?:[^"\\]|\\.)*)"')

_JS_WORKDIR = re.compile(
    r'["\']?\bworkdir\b["\']?\s*:\s*"((?:[^"\\]|\\.)*)"')

_PATCH_LINE = re.compile(
    r"\*\*\* (?:Update|Add|Delete) File:[ \t]*([^\n]+)")

# How a Codex call says it failed: the first line of its output, and nowhere
# else in the record.  `Script failed` against `Script completed`.
_SCRIPT_FAILED = "script failed"


def _js_unescape(text: str) -> str:
    """Best-effort: a JavaScript string literal's contents, read as text.

    The whole snippet is not valid JSON, so it cannot simply be parsed.  Only
    the two escapes that matter for finding a patch envelope are undone; being
    wrong about the rest costs nothing, because all that is read back out of
    the result is the file paths.
    """
    if "\\" not in text:
        return text
    return text.replace("\\n", "\n").replace('\\"', '"')


def _unquote(raw: str) -> str:
    """A JSON string body, decoded — or returned as it stands if it will not."""
    try:
        value = json.loads('"' + raw + '"')
    except (json.JSONDecodeError, ValueError):
        return raw
    return value if isinstance(value, str) else raw


def script_commands(raw) -> List[str]:
    """Every command in a script call, in the order they appear.

    Every one, not the first: a Promise.all of four calls is four commands,
    and the old shape's one-command-per-record habit is what made taking the
    first look sufficient.
    """
    if not isinstance(raw, str) or not raw:
        return []
    out: List[str] = []
    for found in _JS_COMMAND.findall(raw):
        cmd = _unquote(found).strip()
        if cmd:
            out.append(cmd)
    return out


def script_workdir(raw) -> str:
    """The directory a script call ran in, or "" if it did not say.

    Codex does not always announce a cwd, but every exec snippet carries a
    workdir.  Only one of the two readers asks for this today -- it is here
    anyway, because the alternative is one regex about Codex's snippet format
    living somewhere other than the file about Codex's snippet format, which is
    exactly how the copies this module replaced got started.
    """
    if not isinstance(raw, str) or not raw:
        return ""
    found = _JS_WORKDIR.search(raw)
    return _unquote(found.group(1)).strip() if found else ""


def patched_files(text) -> List[str]:
    """File paths named in an ``apply_patch`` envelope.

    Codex has no structured file-write field — it edits by handing an envelope
    like ``*** Update File: src/app.py`` to a patch tool — so the only record of
    which file changed is the text of the call itself.

    The marker is not required to start its line: in a current session the
    envelope is embedded in a line of JavaScript, and insisting on a line start
    there finds nothing at all.
    """
    if not isinstance(text, str) or "*** " not in text:
        return []
    out: List[str] = []
    for found in _PATCH_LINE.findall(_js_unescape(text)):
        # Whatever follows the path is the rest of somebody's source line.
        path = found.strip().rstrip("\\").strip().strip("'\"")
        path = path.rstrip(" \t\\'\");,")
        if path:
            out.append(path)
    return out


def script_failed(output) -> bool:
    """Did a script call come back as a failure?

    Codex says so in the first line of the output — ``Script failed`` against
    ``Script completed`` — and nowhere else in the record.  The output arrives
    as a string in some records and as a list of content items in others, and
    as neither in a record that was cut off mid-write.
    """
    if isinstance(output, str):
        text = output
    elif isinstance(output, list):
        parts = [item.get("text", "") for item in output
                 if isinstance(item, dict) and isinstance(item.get("text"), str)]
        text = "\n".join(parts)
    else:
        return False
    return text.strip()[:40].lower().startswith(_SCRIPT_FAILED)
