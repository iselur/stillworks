"""The four vendored packages are byte-identical to their source repositories.

Since 0.2.0 this repository ships unedit, agentdiff, agentlog and agentwatch
inside the stillworks wheel.  Each of them still has its own repository, its
own test suite, and its own history — the copies here arrived by `rsync` and
are the thing pip installs, so the failure this file exists for is drift: a fix
landed in the source repo and never copied over, or worse, a fix made here in
the copy that the source repo's tests never saw.

So every `.py` file is compared byte-for-byte, in both directions — a file
edited in place fails, and so does a file added on one side only.  When the
sync is deliberate, the fix is mechanical:

    rsync -a --delete --exclude __pycache__ /home/val/<pkg>/<pkg>/ <pkg>/

The whole module skips on a machine that does not have the source checkouts —
a contributor's clone can run everything else, and a skip is honest there where
a pass would be vacuous.
"""

from __future__ import annotations

import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# vendored package -> its source checkout.  stillworks itself is not vendored;
# this repository is its source.
SOURCES = {
    "unedit": "/home/val/unedit",
    "agentdiff": "/home/val/agentdiff",
    "agentlog": "/home/val/agentlog",
    "agentwatch": "/home/val/agentwatch",
}


def _modules(package_dir):
    """{relative path: absolute path} for every .py file under a package."""
    found = {}
    for dirpath, dirnames, names in os.walk(package_dir):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in names:
            if name.endswith(".py"):
                path = os.path.join(dirpath, name)
                found[os.path.relpath(path, package_dir)] = path
    return found


class TestTheVendoredPackagesMatchTheirSourceRepos(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        missing = [repo for repo in SOURCES.values() if not os.path.isdir(repo)]
        if missing:
            raise unittest.SkipTest(
                "source checkouts not on this machine: %s" % ", ".join(missing))

    def test_every_vendored_file_matches_its_source_byte_for_byte(self):
        for package, repo in SOURCES.items():
            vendored = _modules(os.path.join(_ROOT, package))
            source = _modules(os.path.join(repo, package))
            self.assertEqual(
                sorted(vendored), sorted(source),
                "{}: the vendored copy and {} do not hold the same "
                "files".format(package, repo))
            for rel in sorted(vendored):
                with open(vendored[rel], "rb") as fh:
                    ours = fh.read()
                with open(source[rel], "rb") as fh:
                    theirs = fh.read()
                self.assertEqual(
                    ours, theirs,
                    "{}/{} differs from the copy in {} — sync it (rsync, "
                    "direction depends on where the fix landed)".format(
                        package, rel, repo))

    def test_the_guard_is_looking_at_real_packages(self):
        # Vacuity guard: a renamed directory would make _modules return {} on
        # both sides, and empty == empty is a pass that checked nothing.
        for package in SOURCES:
            self.assertTrue(
                _modules(os.path.join(_ROOT, package)),
                "no vendored .py files found for %s" % package)


if __name__ == "__main__":
    unittest.main()
