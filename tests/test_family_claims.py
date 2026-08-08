"""The four claims the family README makes, checked against all five packages.

    Five tools for working with coding agents, same house style: zero
    dependencies, MIT, no API key, nothing leaves your machine. None of them
    call a model — that is the point, since the thing being checked already is
    one.

That paragraph is the pitch.  Since 0.2.0 all five tools ship in this one
distribution, so the wheel this repository builds is the whole family — and a
claim made about five tools has to be checked against five packages, not
against the flagship alone.  Until this file existed the paragraph was checked
nowhere, which is the worst shape for a claim to be in: being written down
reads as being agreed, not as being asserted.

Each sentence is mechanical, so each gets a test:

    zero dependencies      every import in every package resolves to the
                           standard library or to the package's own modules;
                           `[project.dependencies]` is empty
    nothing leaves         nothing that can open a socket is imported, and
    your machine           nothing is imported by name at runtime either
    no API key             no environment variable that looks like a
                           credential is read
    none of them           no provider hostname or SDK appears anywhere in
    call a model           any package

The last one is the load-bearing claim of the whole family: these tools check
an agent's work, and a checker that phoned a model would be marking its own
homework.  It is also the one a maintainer could break by accident, in a single
convenience import, which is exactly why it belongs to the machine and not to
somebody's memory.
"""

from __future__ import annotations

import ast
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# Every import package this distribution ships — the five console scripts each
# have one.  Read from pyproject rather than written out here, so a package
# added to the wheel is scanned the day it arrives instead of when somebody
# remembers this list.
import tomllib

with open(os.path.join(_ROOT, "pyproject.toml"), "rb") as _fh:
    _CFG = tomllib.load(_fh)
PACKAGES = tuple(_CFG["tool"]["setuptools"]["packages"])

# Every stdlib module that can open a socket, plus the popular third-party
# clients.  A dependency-free tool that grew one of these would be the first
# place a reviewer looks, and nobody re-reads the imports by hand every release.
NETWORK_MODULES = {
    "asyncore", "ftplib", "http", "httplib", "httpx", "imaplib", "nntplib",
    "poplib", "requests", "smtplib", "socket", "socketserver", "ssl",
    "telnetlib", "urllib", "urllib2", "urllib3", "webbrowser", "xmlrpc",
    "aiohttp", "websockets",
}

# The SDKs and hostnames a tool would reach for if it did call a model.
MODEL_SDKS = {
    "anthropic", "openai", "cohere", "google", "vertexai", "litellm",
    "langchain", "llama_cpp", "transformers", "ollama", "mistralai",
    "groq", "replicate", "together", "huggingface_hub",
}
MODEL_HOSTS = (
    "api.anthropic.com", "api.openai.com", "generativelanguage.googleapis.com",
    "api.cohere.ai", "api.mistral.ai", "api.groq.com", "openrouter.ai",
    "api.together.xyz", "api-inference.huggingface.co",
)

# Substrings that mark an environment variable as a credential.  `HOME`,
# `COLUMNS`, `NO_COLOR` and the tool's own `*_HOME` are all fine.
#
# These are matched against the *names passed to os.environ / os.getenv*, and
# nowhere else.  Naming a credential and reading one are opposite acts, and
# only the second breaks the claim -- a tool that works on someone's code may
# have perfectly good reason to mention the word in a message or a pattern.
CREDENTIAL_MARKERS = ("API_KEY", "APIKEY", "SECRET", "TOKEN", "PASSWORD",
                     "CREDENTIAL", "_KEY")

# The paragraph at the foot of the README, with its line breaks removed so a
# re-wrap does not read as a retraction.
FAMILY_BLURB = (
    "Five tools for working with coding agents, same house style: zero "
    "dependencies, MIT, no API key, nothing leaves your machine. One command "
    "is the exception and says so on its own help line: `agentlog --brief` "
    "hands the day's evidence to the `claude` command already installed on "
    "your machine, because naming what a day's work was is a judgement and "
    "there is no arithmetic for it. Everything else here reads files and "
    "prints."
)

# Running another program is not the line -- agentdiff runs `git` and
# stillworks runs the module under test in a child.  Running an *agent* is.
# These are the command names a package would reach for to get a model on the
# phone without importing an SDK or naming a host, which is how the exception
# was actually built and so how a second one would arrive.
MODEL_COMMANDS = ("claude", "codex", "gemini", "ollama", "llm", "aichat")
MAY_RUN_A_MODEL = os.path.join("agentlog", "asking_a_model.py")


_CHILD_PROCESS = {
    "run", "Popen", "call", "check_call", "check_output", "system", "popen",
    "spawnl", "spawnv", "spawnvp", "spawnlp", "execv", "execvp", "execl",
}


def _is_a_child_process(fn):
    """Does this call start another program?"""
    return (getattr(fn, "attr", None) in _CHILD_PROCESS
            or getattr(fn, "id", None) in _CHILD_PROCESS)


def sources():
    """(package, path) for every module of every package in the wheel."""
    for package in PACKAGES:
        pkg = os.path.join(_ROOT, package)
        for dirpath, dirnames, names in os.walk(pkg):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in sorted(names):
                if name.endswith(".py"):
                    yield package, os.path.join(dirpath, name)


def imported_names(path, package):
    """(top-level module, full name, line) for every import in a file."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split(".")[0], a.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:          # a relative import, i.e. the owning package
                yield package, "." * node.level + (node.module or ""), node.lineno
            else:
                mod = node.module or ""
                yield mod.split(".")[0], mod, node.lineno


def _is_environ(node):
    return (isinstance(node, ast.Attribute) and node.attr == "environ"
            and getattr(node.value, "id", None) == "os")


def environment_names(path):
    """(variable name, line) for every environment variable the code reads."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    for node in ast.walk(tree):
        # os.environ["X"] and os.environ.get("X") / os.getenv("X")
        if isinstance(node, ast.Subscript) and _is_environ(node.value):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                yield key.value, node.lineno
        elif isinstance(node, ast.Call):
            fn = node.func
            reads = (getattr(fn, "attr", None) == "getenv"
                     or (getattr(fn, "attr", None) == "get"
                         and _is_environ(getattr(fn, "value", None)))
                     or getattr(fn, "id", None) == "getenv")
            if reads and node.args:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    yield a.value, node.lineno


def string_constants(path):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value, node.lineno


class TestTheWheelShipsTheWholeFamily(unittest.TestCase):

    def test_five_packages_and_five_console_scripts_agree(self):
        # Everything below scans PACKAGES, so the first thing to pin is that
        # PACKAGES is the family: one console script per package, entry point
        # inside the package it names.
        scripts = _CFG["project"]["scripts"]
        self.assertEqual(sorted(scripts), sorted(PACKAGES),
                         "console scripts and packages disagree")
        for command, target in scripts.items():
            self.assertEqual(
                target.split(".")[0], command,
                "the {} command points into the {} package".format(
                    command, target.split(".")[0]))
        self.assertEqual(len(PACKAGES), 5,
                         "the family blurb says five and the wheel ships %d"
                         % len(PACKAGES))


class TestZeroDependencies(unittest.TestCase):

    def test_every_import_is_stdlib_or_the_packages_own(self):
        # Stricter than "inside this distribution": the tools were independent
        # for all of 0.1.x and stay independent inside the shared wheel — a
        # cross-package import would quietly weld two of them together.
        stdlib = set(sys.stdlib_module_names)
        for package, path in sources():
            for top, full, lineno in imported_names(path, package):
                if top in stdlib or top == package or top == "":
                    continue
                self.fail("{}/{}:{} imports {!r}, which is neither stdlib nor "
                          "{}".format(package, os.path.basename(path), lineno,
                                      full, package))

    def test_the_package_metadata_declares_none(self):
        deps = _CFG.get("project", {}).get("dependencies", [])
        self.assertEqual(deps, [],
                         "pyproject declares runtime dependencies: %r" % deps)

    def test_the_all_extra_is_empty_and_still_present(self):
        # `pip install 'stillworks[all]'` was the documented command for all
        # of 0.1.x, so the extra must keep existing — and since 0.2.0 the
        # whole family ships in the base wheel, so it must name nothing.  An
        # extra that named anything again would be the one door a real
        # dependency could arrive through while the claim above stayed true;
        # one that named a distribution that does not exist sent pip
        # backtracking to an older release once already.
        extras = _CFG.get("project", {}).get("optional-dependencies", {})
        self.assertEqual(
            extras, {"all": []},
            "the extras changed: %r — [all] must exist and stay empty" % extras)

    def test_a_fresh_interpreter_can_import_all_five_with_no_site_packages(self):
        # The strongest form of the claim: run with `-S`, so nothing installed
        # into site-packages is importable, and see if every package loads.
        import subprocess
        r = subprocess.run(
            [sys.executable, "-S", "-c",
             "import sys; sys.path.insert(0, %r); "
             "import %s" % (_ROOT, ", ".join(PACKAGES))],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0,
                         "importing without site-packages failed:\n" + r.stderr)


class TestNothingLeavesYourMachine(unittest.TestCase):

    def test_nothing_that_can_open_a_socket_is_imported(self):
        for package, path in sources():
            for top, full, lineno in imported_names(path, package):
                self.assertNotIn(
                    top, NETWORK_MODULES,
                    "{}/{}:{} imports {}".format(
                        package, os.path.basename(path), lineno, full))

    def test_no_import_is_hidden_behind_a_string(self):
        # The check above reads import statements, so a module named by a
        # string would walk straight past it.
        #
        # stillworks is the one tool in the family that must import by name at
        # runtime: `stillworks lock src/pricing.py` and `stillworks lock
        # myapp.pricing` both mean "load that module and watch it", and the
        # name only exists at run time.  So the sibling test below carries the
        # weight here instead — the argument is never a literal, which is what
        # makes those calls the user's choice rather than the tool's.
        for package, path in sources():
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name == "__import__":
                    self.fail("{}/{}:{} imports by name at runtime".format(
                        package, os.path.basename(path), node.lineno))

    def test_the_module_it_imports_is_always_one_you_named(self):
        # The complement of the test above, and the reason the exception there
        # is safe to make.  `import_module` loads whatever string it is handed,
        # so what matters is where the string comes from: every call site
        # passes a value derived from the argument on the command line, never a
        # module the package picked.  A literal here would be a name the tool
        # chose for itself, which is the thing the import scan exists to catch.
        for package, path in sources():
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, "attr", None) != "import_module":
                    continue
                if not node.args:
                    continue
                self.assertNotIsInstance(
                    node.args[0], ast.Constant,
                    "{}/{}:{} imports a module the package named itself".format(
                        package, os.path.basename(path), node.lineno))

    def test_no_url_appears_in_any_package(self):
        # Not a network call by itself, but nothing in a tool that promises to
        # stay local has a reason to name a remote address, and a string is
        # where one would first appear.
        for package, path in sources():
            for text, lineno in string_constants(path):
                for scheme in ("http://", "https://"):
                    if scheme in text and "github.com/iselur" not in text:
                        self.fail("{}/{}:{} names a remote address: {!r}".format(
                            package, os.path.basename(path), lineno, text[:80]))


class TestNoAPIKey(unittest.TestCase):

    def test_no_credential_shaped_environment_variable_is_read(self):
        for package, path in sources():
            for name, lineno in environment_names(path):
                for marker in CREDENTIAL_MARKERS:
                    self.assertNotIn(
                        marker, name.upper(),
                        "{}/{}:{} reads {}, which reads as a credential".format(
                            package, os.path.basename(path), lineno, name))

    def test_the_environment_is_never_swept(self):
        # The check above reads the names one at a time, so code that walked
        # the whole environment looking for anything key-shaped would slip
        # past it.  Handing `os.environ` to a subprocess is not that and stays
        # allowed; enumerating it is, and has no use here.
        for package, path in sources():
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.For) and _is_environ(node.iter):
                    self.fail("{}/{}:{} iterates the environment".format(
                        package, os.path.basename(path), node.lineno))
                if (isinstance(node, ast.Attribute)
                        and node.attr in ("items", "keys", "values")
                        and _is_environ(node.value)):
                    self.fail("{}/{}:{} enumerates the environment".format(
                        package, os.path.basename(path), node.lineno))


class TestNoneOfThemCallAModel(unittest.TestCase):
    """The claim the whole family rests on, and the one exception to it."""

    def test_only_the_one_named_module_can_both_name_an_agent_and_run_one(self):
        # The exception was built by running the `claude` binary already on the
        # machine -- no SDK, no hostname, so the two tests below would both
        # pass on a package that had quietly grown a second one.
        #
        # Reading the argv of each call would miss it: the real module runs
        # `[exe, "-p"]`, and `exe` is a variable, so the one instance this
        # exists to confine is invisible to that check.  What is visible is the
        # pair -- a module that can start a program *and* writes an agent's
        # name down somewhere.  Neither half alone is the tell.  agentlog's
        # parser says "claude" because it reads Claude's logs and has to call
        # them something; agentdiff runs `git` and stillworks runs the module
        # under test, and neither knows any agent's name.
        for package, path in sources():
            where = os.path.join(package, os.path.relpath(
                path, os.path.join(_ROOT, package)))
            if where == MAY_RUN_A_MODEL:
                continue
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            if not any(_is_a_child_process(n.func)
                       for n in ast.walk(tree) if isinstance(n, ast.Call)):
                continue
            for text, lineno in string_constants(path):
                self.assertNotIn(
                    os.path.basename(text.strip().split(" ")[0]),
                    MODEL_COMMANDS,
                    "{}:{} can start a program and names {!r}; only {} may "
                    "do both".format(where, lineno, text[:40], MAY_RUN_A_MODEL))

    def test_the_one_module_that_may_is_still_there_to_be_confined(self):
        # A package that lost the file would pass the test above by having
        # nothing to exempt, and the claim would go quiet instead of failing.
        self.assertTrue(
            os.path.exists(os.path.join(_ROOT, MAY_RUN_A_MODEL)),
            "{} is gone -- the exception above now guards nothing".format(
                MAY_RUN_A_MODEL))

    def test_no_model_sdk_is_imported(self):
        for package, path in sources():
            for top, full, lineno in imported_names(path, package):
                self.assertNotIn(
                    top, MODEL_SDKS,
                    "{}/{}:{} imports the {} SDK".format(
                        package, os.path.basename(path), lineno, full))

    def test_no_provider_hostname_appears(self):
        for package, path in sources():
            for text, lineno in string_constants(path):
                low = text.lower()
                for host in MODEL_HOSTS:
                    self.assertNotIn(
                        host, low, "{}/{}:{} names {}".format(
                            package, os.path.basename(path), lineno, host))

    def test_the_readme_still_makes_the_claim(self):
        # If the paragraph is ever dropped or softened, these tests should be
        # revisited rather than left guarding a sentence nobody makes any more.
        # Compared with the line breaks squeezed out, so re-wrapping the
        # paragraph does not read as retracting it.
        with open(os.path.join(_ROOT, "README.md"), encoding="utf-8") as fh:
            text = " ".join(fh.read().split())
        self.assertIn(FAMILY_BLURB, text,
                      "the README no longer makes the family claim this file "
                      "exists to check")


if __name__ == "__main__":
    unittest.main()
