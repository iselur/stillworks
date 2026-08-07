"""Where the agents keep their logs is one fact, and it lives in one file.

Claude Code writes a session under ``~/.claude/projects``; Codex writes one
under ``~/.codex/sessions``.  That is a fact about somebody else's software --
it can change without asking us -- and this family had written it out four
times: `agentlog`'s finder, the sentence `agentlog` prints when it finds none,
the guard that refuses to write a report on top of a log, and the walk
`agentwatch` does to discover what to follow.

The four fail differently, which is the part that made it worth fixing.  The
finder looking in the wrong place prints "no sessions": wrong, but audible.
The **guard** looking in the wrong place says nothing at all.  `agentlog`'s one
promise is that it never writes to the logs it reads; a guard holding a
directory the logs have left does not refuse a write, it waves it through, onto
a day's work.  A promise kept by a copy of a fact is kept only until somebody
updates the other copy.

It has to stay copied.  Nothing in this family imports outside its own package
-- the promise `pip install stillworks` makes, enforced by
`test_every_import_is_stdlib_or_the_packages_own` -- so a shared module is not
on offer.  What is on offer is a copy that cannot drift, which is what this
file is.

What it checks, and why each is not covered by the one before it:

  * the two `where_the_logs_are.py` are byte-identical, so a directory changed
    on one side is changed on both or it is a failing test;
  * the interface is the roster and the lookup, and nothing else -- a third
    name is a third thing both commands must be understood against;
  * all four call sites actually go through it, because the cheapest way to
    undo this is to leave the file sitting there unread;
  * **no module in either package spells a log directory by hand** -- not
    ``.claude``, not ``.codex``, not an `os.path.join` ending in ``projects``
    or ``sessions``.  This is the check that catches the copy coming back, and
    it is deliberately wider than the four callers: the fifth copy will be
    written somewhere nobody listed here; and
  * both commands, run for real against one home, find the same session --
    because everything above this line is satisfied by a module nobody calls.

What this file does not say is how either command decides *which* home.  That
differs on purpose -- ``AGENTLOG_HOME`` and ``AGENTWATCH_HOME``, and one of the
two stops when handed a directory that does not exist -- and each repository
tests its own.  This one says there is one layout.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

THE_MODULE = "where_the_logs_are.py"

# The packages that read agent session logs, and the modules in each that used
# to know the layout.  `cli.py` appears on both sides for different reasons:
# in `agentlog` it holds the write guard and the "no sessions" sentence, in
# `agentwatch` it holds the default list of agents to follow.
THE_CALLERS = {
    "agentlog": ("cli.py", "parser.py"),
    "agentwatch": ("cli.py", "follow.py"),
}

# What a caller may ask for.  `log_dirs` answers where; `SOURCES` is the roster
# in the order it is looked in and listed, which `agentwatch` needs to validate
# a `--source` against without knowing what an agent is.
THE_INTERFACE = {"SOURCES", "log_dirs"}

# The directory names.  Spelled here, by hand, once -- so this file is an
# independent statement of the fact rather than a comparison of the roster with
# itself, which would pass over a roster pointing anywhere at all.
THE_LAYOUT = {
    "claude": (".claude", "projects"),
    "codex": (".codex", "sessions"),
}

# The leading component of each: unmistakable, and banned outright at a call
# site.  The trailing component (`projects`, `sessions`) is an ordinary English
# word that turns up in dict keys and prose, so it is banned only where it is
# being made into a path -- see `_joins_a_log_directory`.
THE_AGENT_DOTDIRS = tuple(sorted(parts[0] for parts in THE_LAYOUT.values()))
THE_LOG_SUBDIRS = tuple(sorted(parts[1] for parts in THE_LAYOUT.values()))


def _path(package, module):
    return os.path.join(_ROOT, package, module)


def _source(package, module):
    with open(_path(package, module), encoding="utf-8") as fh:
        return fh.read()


def _modules(package):
    """Every module in the package except the shared one itself."""
    directory = os.path.join(_ROOT, package)
    return sorted(name for name in os.listdir(directory)
                  if name.endswith(".py") and name != THE_MODULE)


class TestTheyAreOneFile(unittest.TestCase):

    def test_both_packages_carry_it(self):
        missing = [p for p in sorted(THE_CALLERS)
                   if not os.path.exists(_path(p, THE_MODULE))]
        self.assertEqual(missing, [],
                         "packages with no {}: {}".format(THE_MODULE, missing))

    def test_byte_for_byte(self):
        digests = {}
        for package in THE_CALLERS:
            with open(_path(package, THE_MODULE), "rb") as fh:
                digests.setdefault(
                    hashlib.sha256(fh.read()).hexdigest(), []).append(package)
        self.assertEqual(
            len(digests), 1,
            "{} has drifted into {} versions:\n  {}\nA directory changed on "
            "one side belongs on both: rsync the source repo you changed, then "
            "the other.\n"
            .format(THE_MODULE, len(digests),
                    "\n  ".join(sorted(", ".join(sorted(group))
                                       for group in digests.values()))))

    def test_the_interface_is_the_two_names_both_commands_were_promised(self):
        # Read off the copy rather than imported, so this says the same thing
        # whether or not the packages are installed.
        tree = ast.parse(_source("agentlog", THE_MODULE))
        public = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    public.add(node.name)
            elif isinstance(node, ast.Assign):
                public |= {t.id for t in node.targets
                           if isinstance(t, ast.Name)
                           and not t.id.startswith("_")}
        self.assertEqual(
            public, THE_INTERFACE,
            "{}'s interface changed.  Adding a name is fine, but it is a name "
            "both commands now have to be understood against -- say so here."
            .format(THE_MODULE))

    def test_it_holds_the_layout_this_file_names_independently(self):
        # The roster is right, said by something that is not the roster.  Every
        # other check here would pass over two identical copies of a wrong
        # directory, and the guard would wave writes through in both.
        sys.path.insert(0, os.path.join(_ROOT, "agentlog"))
        try:
            import where_the_logs_are as roster       # noqa: N813
        finally:
            sys.path.pop(0)
        home = os.path.join("/tmp", "a-home")
        got = {source: directory
               for source, _shown, directory in roster.log_dirs(home)}
        for source, parts in sorted(THE_LAYOUT.items()):
            self.assertEqual(got.get(source), os.path.join(home, *parts),
                             "the roster reads {} logs from {}"
                             .format(source, got.get(source)))
        self.assertEqual(tuple(roster.SOURCES), tuple(sorted(THE_LAYOUT)),
                         "the roster's agents are {}".format(roster.SOURCES))


class TestBothCommandsGoThroughIt(unittest.TestCase):
    """The file existing is not the same as the file being used."""

    def _imported(self, package, module):
        tree = ast.parse(_source(package, module))
        return {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module == "where_the_logs_are"
                for alias in node.names}

    def test_each_caller_asks_the_shared_module(self):
        for package, modules in sorted(THE_CALLERS.items()):
            for module in modules:
                with self.subTest(package + "/" + module):
                    asked = self._imported(package, module)
                    self.assertTrue(
                        asked, "{}/{} stopped importing from {}"
                        .format(package, module, THE_MODULE))
                    self.assertLessEqual(
                        asked, THE_INTERFACE,
                        "{}/{} reaches past the interface into {}'s privates: "
                        "{}".format(package, module, THE_MODULE,
                                    sorted(asked - THE_INTERFACE)))


def _joins_a_log_directory(tree):
    """`os.path.join(..., "projects")` and friends, anywhere in a module.

    Only `os.path.join`: `"\\n".join(group["sessions"])` in agentlog's HTML is a
    string being glued together, not a directory being built, and a check that
    cannot tell them apart is one somebody switches off.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "join"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "path"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and arg.value in THE_LOG_SUBDIRS:
                found.append(arg.value)
    return found


class TestNobodySpellsALogDirectoryByHand(unittest.TestCase):
    """The fifth copy, refused before it is written.

    Every module in both packages, not just the four that used to have one:
    the copy that comes back will be written somewhere no list here mentions.
    """

    def test_no_module_names_an_agent_s_directory(self):
        for package in sorted(THE_CALLERS):
            for module in _modules(package):
                with self.subTest(package + "/" + module):
                    tree = ast.parse(_source(package, module))
                    spelled = {node.value for node in ast.walk(tree)
                               if isinstance(node, ast.Constant)
                               and node.value in THE_AGENT_DOTDIRS}
                    self.assertEqual(
                        spelled, set(),
                        "{}/{} spells {} out -- where the logs are is {}'s to "
                        "know, and a second copy of it is a guard that stops "
                        "refusing writes onto session logs"
                        .format(package, module, sorted(spelled), THE_MODULE))
                    joined = _joins_a_log_directory(tree)
                    self.assertEqual(
                        joined, [],
                        "{}/{} builds a path ending in {} -- see above"
                        .format(package, module, sorted(set(joined))))


def _a_stamp_that_is_recent_and_still_today(minutes_ago=10):
    """A time inside `--since 1h` that `today` also agrees is today.

    A fixture that writes "ten minutes ago" lands in yesterday for the first
    ten minutes of every day, and `agentlog today` then has nothing to print: a
    suite that goes red at 00:03 and green again on its own.  So the clock the
    offsets are taken from is moved forward off midnight instead.
    """
    now = datetime.now().astimezone()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    anchor = max(now, midnight + timedelta(minutes=minutes_ago))
    return anchor - timedelta(minutes=minutes_ago)


class TestBothCommandsFindTheSameSession(unittest.TestCase):
    """Everything above is satisfied by a module nobody calls.

    So both commands are run for real, against one home holding one session in
    each agent's directory, and what comes back on the two screens is read.
    """

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="stillworks-logs-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        project = os.path.join(self.home, "work", "myproj")
        os.makedirs(project)

        at = _a_stamp_that_is_recent_and_still_today().astimezone(timezone.utc)
        stamp = at.isoformat()

        # Spelled by hand, as everywhere else in this file: written through the
        # roster the fixture would follow it wherever it moved.
        claude_logs = os.path.join(self.home, ".claude", "projects", "p")
        os.makedirs(claude_logs)
        self._write(os.path.join(claude_logs, "s1.jsonl"), [
            {"type": "user", "sessionId": "s1", "cwd": project,
             "timestamp": stamp,
             "message": {"role": "user", "content": "go"}},
            {"type": "assistant", "sessionId": "s1", "cwd": project,
             "timestamp": (at + timedelta(seconds=1)).isoformat(),
             "message": {"role": "assistant", "model": "claude-opus-5",
                         "content": [{"type": "tool_use", "id": "t1",
                                      "name": "Bash",
                                      "input": {"command": "cargo build"}}]}},
        ])

        codex_logs = os.path.join(self.home, ".codex", "sessions")
        os.makedirs(codex_logs)
        self._write(os.path.join(codex_logs, "rollout-s2.jsonl"), [
            {"timestamp": stamp, "type": "session_meta",
             "payload": {"session_id": "s2", "id": "s2", "cwd": project,
                         "timestamp": stamp, "cli_version": "0.1.0"}},
            {"timestamp": stamp, "type": "event_msg",
             "payload": {"type": "user_message", "message": "go"}},
            {"timestamp": (at + timedelta(seconds=1)).isoformat(),
             "type": "response_item",
             "payload": {"type": "function_call", "call_id": "c1",
                         "name": "exec_command",
                         "arguments": json.dumps({"cmd": "pytest -x"})}},
        ])

    @staticmethod
    def _write(path, records):
        with open(path, "w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record) + "\n")

    def _run(self, *argv):
        env = dict(os.environ, PYTHONPATH=_ROOT, HOME=self.home,
                   COLUMNS="120", NO_COLOR="1")
        env.pop("AGENTLOG_HOME", None)
        env.pop("AGENTWATCH_HOME", None)
        return subprocess.run([sys.executable, "-m"] + list(argv),
                              cwd=_ROOT, env=env, capture_output=True,
                              text=True, timeout=120)

    def test_agentlog_reads_both_agents_out_of_one_home(self):
        # Read off `--json` rather than the digest: the digest is a summary and
        # would pass on the session count alone, which is the same number if
        # one agent's directory were read twice.  The commands are what say
        # both files were opened.
        result = self._run("agentlog.cli", "today", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("cargo build", result.stdout, result.stdout)
        self.assertIn("pytest -x", result.stdout, result.stdout)

    def test_agentwatch_walks_both_agents_out_of_the_same_home(self):
        result = self._run("agentwatch.cli", "--once", "--since", "1h")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        screen = result.stdout
        self.assertIn("cargo build", screen, screen)
        self.assertIn("pytest -x", screen, screen)

    def test_the_guard_refuses_to_write_into_either_directory(self):
        # The silent failure, end to end.  It is the one thing a wrong copy of
        # the layout breaks without saying anything.
        for parts in sorted(THE_LAYOUT.values()):
            target = os.path.join(self.home, *(parts + ("digest.md",)))
            with self.subTest("/".join(parts)):
                result = self._run("agentlog.cli", "today", "--md", target)
                self.assertNotEqual(
                    result.returncode, 0,
                    "wrote a digest into the session logs at {}".format(target))
                self.assertFalse(os.path.exists(target), target)
                self.assertIn("never writes to the logs it reads",
                              result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
