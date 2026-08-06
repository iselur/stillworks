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
  * which files a Codex command read, since Codex has no read tool and reads by
    running one;
  * how a Codex call says it failed, and what to call the failure.

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
import os
import re
import shlex
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

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


# ---------------------------------------------------------------------------
# Codex: what a failed call is called
# ---------------------------------------------------------------------------
#
# `script_failed` above answers whether a call failed.  These two answer what to
# write down when it did — which is a different question, and one both readers
# were answering for themselves, in nine identical lines each, down to the
# three-name truncation and the colon.  That is to say the truncation was a
# decision taken once and copied, and the second copy was free to become four
# names on any afternoon without anything noticing.
#
# The wording is here rather than at the call sites for the same reason the
# reading is: `agentlog` prints these labels in a report and `agentwatch` prints
# them in a live tail, and a person running both should not be told about the
# same failure in two different sentences.

#: When a patch did not apply, and when an MCP call came back an error.  Both
#: are what to show when nothing more specific is known — a patch that named no
#: files, a call whose invocation the log did not record.
_PATCH_FAILED = "patch did not apply"
_MCP_FAILED = "mcp call failed"


def patch_result(payload: Dict) -> Tuple[List[str], Optional[str]]:
    """What a Codex ``patch_apply_end`` record says happened.

    Returns the files it claims, sorted, and — if the patch failed — the line
    to show for it.  A patch that applied has no label; one that did not has no
    files, because files it did not write are not files it wrote.

    The end record rather than the call is deliberate, and is the whole reason
    this is read here at all: the envelope in the *call* lists edits that never
    reached the disk.  ``patched_files`` above is the other half of that, for
    the calls that did.

    Absence means success.  A record cut off mid-write, or one from a Codex
    that has not got round to saying, is a patch that applied — the failures
    are the ones that say so, and guessing the other way turns a truncated log
    into a session full of errors that never happened.
    """
    changes = payload.get("changes")
    paths = sorted(changes) if isinstance(changes, dict) else []
    if payload.get("success", True):
        return paths, None
    # Three names, because a patch across a dozen files still has to fit on the
    # one line a live tail gives it, and the first three say which change it
    # was.
    names = ", ".join(os.path.basename(p) for p in paths[:3])
    return [], _PATCH_FAILED + (": " + names if names else "")


def mcp_failure(payload: Dict) -> Optional[str]:
    """The line to show for a Codex ``mcp_tool_call_end``, if it failed.

    ``None`` when the call succeeded, which is most of them.  A successful MCP
    call is deliberately not news: it is not a shell command, and a reader that
    turned every one into a command line would trade a missing failure for a
    wrong command count.  A failed one is a failure exactly like a command that
    exited non-zero, and a session whose only failures were these used to read
    ``0 errors`` — a claim, not a partial count.

    The result is either ``{"Ok": ...}`` or ``{"Err": "..."}``.  Server and tool
    are both named where both are known: `read_mcp_resource` on its own does not
    say which server was down, and that is the part a person can act on.
    """
    result = payload.get("result")
    if not isinstance(result, dict) or "Err" not in result:
        return None
    inv = payload.get("invocation")
    inv = inv if isinstance(inv, dict) else {}
    server = inv.get("server") if isinstance(inv.get("server"), str) else ""
    tool = inv.get("tool") if isinstance(inv.get("tool"), str) else ""
    what = "/".join(p for p in (server, tool) if p)
    return "mcp " + what if what else _MCP_FAILED


# ---------------------------------------------------------------------------
# Codex: the files a command read
# ---------------------------------------------------------------------------
#
# Claude Code reads a file by calling a tool named `Read`, so the path is a
# field and finding it is a lookup.  Codex has no read tool.  It reads a file by
# running `sed -n '1,200p' notes.md`, and the only record of what it read is the
# text of the command — the same place its writes hide, and for the same reason.
#
# So both readers reported that a Codex session read nothing.  Every one of
# them, since the first release: `files read` was a Claude-only fact wearing a
# name that did not say so, and `agentwatch --reads` was a flag that did nothing
# on half the logs it accepts.
#
# The risk here is not missing a read.  It is inventing one — a path in a digest
# that was never opened is worse than a digest that stays quiet — so everything
# below is built to under-report.  A verb is listed only if it opens every
# argument it is given; nothing that searches, globs, or walks a directory is
# listed at all, because `rg pattern src/` puts a pattern, a glob and a
# directory in the same position a path goes and the text cannot say which is
# which.  Measured against the 1,217 Codex sessions on the machine this was
# written on: of 4,472 paths claimed, 87.5% are a file that still exists, one
# was a directory, and the rest are the temporary files a session makes and
# deletes.

# Verbs that open every path they are handed.
_READS_ITS_ARGS = {
    "cat", "head", "tail", "nl", "less", "more", "od", "xxd", "strings",
    "wc", "md5sum", "sha256sum", "sha1sum", "cksum", "base64",
}

# `sed SCRIPT file...` — the first non-flag word is the script, not a path.
_SCRIPT_THEN_ARGS = {"sed", "awk"}

# Flags that swallow the word after them, per verb, because `-n` means "quiet"
# to sed and "how many lines" to head.  One shared table gets one of the two
# wrong: sharing it lost every `sed -n` read in the corpus, which is far and
# away the commonest way a Codex session reads a file.
#
# Only sed's row is load-bearing, and only by what it leaves out.  The rest are
# here because they are true, not because a test can see them: `-n`, `-c`, `-j`
# and `-w` take a number, a number has neither a separator nor an extension, and
# `_looks_like_a_path` turns it down whether it was swallowed or not.  A mutant
# that empties head's row survives for that reason and is not a gap — the row is
# load-bearing the day somebody adds a verb whose flag takes a filename.
_TAKES_A_VALUE = {
    "sed": {"-e", "-f", "--expression", "--file"},
    "awk": {"-v", "-f", "-F"},
    "head": {"-n", "-c", "--lines", "--bytes"},
    "tail": {"-n", "-c", "--lines", "--bytes"},
    "od": {"-A", "-t", "-j", "-N"},
    "nl": {"-b", "-s", "-w", "-v", "-i"},
}

# `sed -e SCRIPT file` and `awk -f prog.awk data.csv` put the script behind a
# flag, so the first non-flag word is a path after all.  Skipping it anyway lost
# the file: `sed -e 's/a/b/' notes.md` reported nothing read.
_SCRIPT_COMES_FROM_A_FLAG = {"-e", "-f", "--expression", "--file"}

# One line runs several commands, and a newline separates two as surely as a
# semicolon does.  A Codex snippet is full of both.
_STATEMENT_END = re.compile(r"\|\||&&|[|;\n]")

# Sending stderr somewhere is not writing a file anybody meant to write, and
# `2>/dev/null || true` is how this corpus reads a file that may not be there.
# Treating the `>` in it as a redirect discarded the whole statement.
_STDERR_REDIRECT = re.compile(r"\s\d*2>\s*(?:&\d|\S+)")

# Not a path worth reporting: a flag, a variable, a glob, a device file, or a
# bare word with neither a separator nor an extension — `dispatch` could be
# anything, and guessing is the one thing this must not do.
_NOT_A_PATH = re.compile(r"^-|^[$~]|[*?\[\]{}]|^/dev/|^/proc/|^/sys/")
_HAS_EXTENSION = re.compile(r"\.\w+$")


def _looks_like_a_path(word: str) -> bool:
    # An earlier draft also turned down a `VAR=value` prefix here.  It never
    # fired: `LC_ALL=C cat x.py` puts the prefix in the verb's position, where
    # the verb lookup drops the whole statement, and the only word that ever
    # reached this test with an `=` in it was an argument to a reading verb —
    # where a file really is named that and really was read.
    if not word or _NOT_A_PATH.search(word):
        return False
    return "/" in word or _HAS_EXTENSION.search(word) is not None


def _paths_handed_to(verb: str, words: List[str], skip: int) -> List[str]:
    """The path-shaped words past the first ``skip`` non-flag ones."""
    swallows = _TAKES_A_VALUE.get(verb, frozenset())
    out: List[str] = []
    seen = 0
    pending = False
    for word in words:
        if pending:
            pending = False
            continue
        if word.startswith("-") and word != "-":
            if word in swallows:
                pending = True
            continue
        seen += 1
        if seen <= skip:
            continue
        if _looks_like_a_path(word):
            out.append(word)
    return out


def files_a_command_reads(command) -> List[str]:
    """Every file a Codex command plainly read, in the order it named them.

    Plainly: the verb is one that opens whatever it is handed, and the word is
    shaped like a path.  Anything short of that is left out.  The caller is a
    digest a person reads, and a file listed there that was never opened costs
    more than one that is missing.

    Paths come back exactly as the command wrote them, which is usually
    relative to the directory the command ran in — ``script_workdir`` is where
    that is, for a caller that wants to resolve them.
    """
    if not isinstance(command, str) or not command:
        return []
    out: List[str] = []
    for statement in _STATEMENT_END.split(command):
        statement = _STDERR_REDIRECT.sub("", statement).strip()
        # A redirect means the statement is writing, and the write list is
        # where its file belongs.  A heredoc used to be turned down here too;
        # it never mattered, because the shapes this corpus actually writes
        # (`cat > x.py <<'EOF'`, `python3 - <<'PY'`) are already turned down by
        # the redirect or by the verb, and the one shape the heredoc test could
        # see — `cat notes.md <<EOF` — really did read notes.md.
        if not statement or ">" in statement:
            continue
        try:
            words = shlex.split(statement)
        except ValueError:             # unbalanced quotes; not worth guessing at
            continue
        if not words:
            continue
        verb = words[0].split("/")[-1]
        rest = words[1:]
        if verb in _READS_ITS_ARGS:
            out.extend(_paths_handed_to(verb, rest, 0))
        elif verb in _SCRIPT_THEN_ARGS:
            # `sed -i` rewrites the file where it sits.  That is a write, and
            # the session's write list is where it belongs.
            if any(word == "-i" or word.startswith("-i") for word in rest):
                continue
            # The script is the first non-flag word — unless a flag supplied
            # it, in which case there is no inline script and the first word is
            # a file like any other.
            gave_the_script = any(word in _SCRIPT_COMES_FROM_A_FLAG
                                  for word in rest)
            out.extend(_paths_handed_to(verb, rest, 0 if gave_the_script else 1))
    seen = set()
    uniq: List[str] = []
    for path in out:
        if path not in seen:
            seen.add(path)
            uniq.append(path)
    return uniq


# ---------------------------------------------------------------------------
# Where a transcript sits, and what that says about it
# ---------------------------------------------------------------------------
#
# A transcript's own filename and location carry two facts nothing inside the
# file reliably repeats: which sitting it belongs to, and which project that
# sitting was in.  Both readers need both, and both had been working them out
# on their own -- agentwatch correctly, agentlog wrongly in three separate
# ways at once.  On a real subagent transcript agentlog reported the project
# as the literal word `subagents`; on an ordinary one it reported
# `home/val/x`, a relative path, because it dropped the leading dash without
# putting the root slash back.  Neither is a wrong answer a reader could
# notice -- they are plausible-looking labels -- which is why they survived.
#
# The layout is Claude Code's, and it is the kind of fact this module exists
# to hold: one place, so a correction lands in both tools or in neither.


def _subagent_session_dir(path: str) -> str:
    """The directory of the session that spawned this subagent, or "".

    Claude Code writes a session as::

        <project>/<session-id>.jsonl
        <project>/<session-id>/subagents/agent-<hex>.jsonl

    and one directory deeper when a workflow ran it::

        <project>/<session-id>/subagents/workflows/<run-id>/agent-<hex>.jsonl

    The file is named after the subagent, which names nothing a person can look
    up.  What names the sitting is the directory holding `subagents`, however
    many directories the transcript itself sits under -- so the search walks up
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
    own, so it answers with the id of the session that spawned it -- the work is
    that session's work, and a reader joining ``--json`` output by session must
    find it there.  Claude's layout only; Codex writes no subagent transcripts.
    """
    if source == "claude":
        parent = _subagent_parent(path)
        if parent:
            return parent
    base = os.path.splitext(os.path.basename(path))[0]
    if source == "codex":
        # rollout-<date>-<uuid>.jsonl -- the uuid is the last five dash-parts.
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
        # project is one further up again -- not the directory beside the file,
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
