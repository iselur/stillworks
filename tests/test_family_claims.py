"""The four claims every README in this family makes, checked against the code.

    Five tools for working with coding agents, same house style: zero
    dependencies, MIT, no API key, nothing leaves your machine. None of them
    call a model — that is the point, since the thing being checked already is
    one.

That paragraph is the pitch.  It is repeated verbatim in all five READMEs, and
until this file existed it was checked in none of them — which is the worst
shape for a claim to be in, because being written five times reads as being
agreed five times rather than as being asserted five times.

Each sentence is mechanical, so each gets a test:

    zero dependencies      every import resolves to the standard library or to
                           this package; `[project.dependencies]` is empty
    nothing leaves         nothing that can open a socket is imported, and
    your machine           nothing is imported by name at runtime either
    no API key             no environment variable that looks like a
                           credential is read
    none of them           no provider hostname or SDK appears anywhere in the
    call a model           package

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

PACKAGE = "stillworks"

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

# The paragraph at the foot of all five READMEs, with its line breaks removed
# so a re-wrap does not read as a retraction.
FAMILY_BLURB = (
    "Five tools for working with coding agents, same house style: zero "
    "dependencies, MIT, no API key, nothing leaves your machine. None of them "
    "call a model — that is the point, since the thing being checked already "
    "is one."
)


def sources(package):
    pkg = os.path.join(_ROOT, package)
    for dirpath, dirnames, names in os.walk(pkg):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(names):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def imported_names(path):
    """(top-level module, full name, line) for every import in a file."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split(".")[0], a.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:          # a relative import, i.e. our own package
                yield PACKAGE, "." * node.level + (node.module or ""), node.lineno
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


class TestZeroDependencies(unittest.TestCase):

    def test_every_import_is_stdlib_or_our_own(self):
        stdlib = set(sys.stdlib_module_names)
        for path in sources(PACKAGE):
            for top, full, lineno in imported_names(path):
                if top in stdlib or top == PACKAGE or top == "":
                    continue
                self.fail("{}:{} imports {!r}, which is neither stdlib nor "
                          "{}".format(os.path.basename(path), lineno, full,
                                      PACKAGE))

    def test_the_package_metadata_declares_none(self):
        import tomllib
        with open(os.path.join(_ROOT, "pyproject.toml"), "rb") as fh:
            cfg = tomllib.load(fh)
        deps = cfg.get("project", {}).get("dependencies", [])
        self.assertEqual(deps, [],
                         "pyproject declares runtime dependencies: %r" % deps)

    def test_the_all_extra_names_only_the_family_and_stays_optional(self):
        # stillworks is the only one of the five with an extra: `pip install
        # 'stillworks[all]'` is how you get the whole family in one command.
        # An extra is not a dependency — `pip install stillworks` still pulls
        # nothing — but it is the one place a real dependency could arrive
        # while the claim above stayed true, so it gets pinned to the four
        # siblings by name.
        import re
        import tomllib
        with open(os.path.join(_ROOT, "pyproject.toml"), "rb") as fh:
            cfg = tomllib.load(fh)
        extras = cfg.get("project", {}).get("optional-dependencies", {})
        self.assertEqual(sorted(extras), ["all"],
                         "an extra other than [all]: %r" % sorted(extras))
        named = {re.split(r"[<>=!~\[ ]", s.strip())[0] for s in extras["all"]}
        self.assertEqual(
            named,
            {"agentdiff-cli", "agentlog-tool", "agentwatch", "unedit"},
            "[all] names something outside the family: %r" % sorted(named))

    def test_a_fresh_interpreter_can_import_it_with_no_site_packages(self):
        # The strongest form of the claim: run with `-S`, so nothing installed
        # into site-packages is importable, and see if the CLI still starts.
        import subprocess
        r = subprocess.run(
            [sys.executable, "-S", "-c",
             "import sys; sys.path.insert(0, %r); "
             "import %s" % (_ROOT, PACKAGE)],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0,
                         "importing without site-packages failed:\n" + r.stderr)


class TestNothingLeavesYourMachine(unittest.TestCase):

    def test_nothing_that_can_open_a_socket_is_imported(self):
        for path in sources(PACKAGE):
            for top, full, lineno in imported_names(path):
                self.assertNotIn(
                    top, NETWORK_MODULES,
                    "{}:{} imports {}".format(
                        os.path.basename(path), lineno, full))

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
        for path in sources(PACKAGE):
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name == "__import__":
                    self.fail("{}:{} imports by name at runtime".format(
                        os.path.basename(path), node.lineno))

    def test_the_module_it_imports_is_always_one_you_named(self):
        # The complement of the test above, and the reason the exception there
        # is safe to make.  `import_module` loads whatever string it is handed,
        # so what matters is where the string comes from: every call site
        # passes a value derived from the argument on the command line, never a
        # module this package picked.  A literal here would be a name stillworks
        # chose for itself, which is the thing the import scan exists to catch.
        for path in sources(PACKAGE):
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
                    "{}:{} imports a module this package named itself".format(
                        os.path.basename(path), node.lineno))

    def test_no_url_appears_in_the_package(self):
        # Not a network call by itself, but nothing in a tool that promises to
        # stay local has a reason to name a remote address, and a string is
        # where one would first appear.
        for path in sources(PACKAGE):
            for text, lineno in string_constants(path):
                for scheme in ("http://", "https://"):
                    if scheme in text and "github.com/iselur" not in text:
                        self.fail("{}:{} names a remote address: {!r}".format(
                            os.path.basename(path), lineno, text[:80]))


class TestNoAPIKey(unittest.TestCase):

    def test_no_credential_shaped_environment_variable_is_read(self):
        for path in sources(PACKAGE):
            for name, lineno in environment_names(path):
                for marker in CREDENTIAL_MARKERS:
                    self.assertNotIn(
                        marker, name.upper(),
                        "{}:{} reads {}, which reads as a credential".format(
                            os.path.basename(path), lineno, name))

    def test_the_environment_is_never_swept(self):
        # The check above reads the names one at a time, so code that walked
        # the whole environment looking for anything key-shaped would slip
        # past it.  Handing `os.environ` to a subprocess is not that and stays
        # allowed; enumerating it is, and has no use here.
        for path in sources(PACKAGE):
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.For) and _is_environ(node.iter):
                    self.fail("{}:{} iterates the environment".format(
                        os.path.basename(path), node.lineno))
                if (isinstance(node, ast.Attribute)
                        and node.attr in ("items", "keys", "values")
                        and _is_environ(node.value)):
                    self.fail("{}:{} enumerates the environment".format(
                        os.path.basename(path), node.lineno))


class TestNoneOfThemCallAModel(unittest.TestCase):
    """The claim the whole family rests on."""

    def test_no_model_sdk_is_imported(self):
        for path in sources(PACKAGE):
            for top, full, lineno in imported_names(path):
                self.assertNotIn(
                    top, MODEL_SDKS,
                    "{}:{} imports the {} SDK".format(
                        os.path.basename(path), lineno, full))

    def test_no_provider_hostname_appears(self):
        for path in sources(PACKAGE):
            for text, lineno in string_constants(path):
                low = text.lower()
                for host in MODEL_HOSTS:
                    self.assertNotIn(
                        host, low, "{}:{} names {}".format(
                            os.path.basename(path), lineno, host))

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
