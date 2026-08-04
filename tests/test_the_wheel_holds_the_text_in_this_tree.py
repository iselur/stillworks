"""The wheel in `dist/` is this code — the text of it, not just its number.

`test_the_built_wheel_is_this_code` checks the version three ways and stops
there, which is one step short of the thing it is about.  A wheel built this
morning and a tree edited this afternoon carry the same `__version__`, agree
on every check in that file, and differ in what they contain.  That is not
hypothetical: all three parked dists in this family had it at once, and every
version check passed on all three.

What drifted was the README, and the README is not documentation here — the
long description in a wheel's METADATA *is* the project page on PyPI.  So the
page describing the tool loses whatever was added since the build: in
agentwatch's case three flags that exist, work, and are documented in the
repository and nowhere a user would look.

The number matters more than it looks.  A PyPI filename is immutable, so a
first release ships its page exactly once.  There is no corrected upload of
`agentwatch-0.1.0`; there is only a version bump, which for a page typo is a
silly thing to spend and for a first release is not available at all.

Modules are checked the same way for the same reason.  A version that agrees
with itself says nothing about whether the code under it moved, and a stale
module is the shipped bug the neighbouring file already tells the story of —
caught there by its number, invisible here when the number happens to match.

No `dist/` means nothing to compare, and this skips.  The failure only exists
once a build does.
"""

from __future__ import annotations

import email
import glob
import os
import posixpath
import sys
import tarfile
import unittest
import zipfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

PACKAGE = "stillworks"

DIST = os.path.join(_ROOT, "dist")


def wheels():
    return sorted(glob.glob(os.path.join(DIST, "*.whl")))


def long_description(archive):
    """The text PyPI renders as the project page, out of the wheel's METADATA.

    It is the message body, so it is read with an email parser rather than by
    splitting on the first blank line: a header can be folded across lines and
    a body can begin with one, and both spellings turn a wrong answer into a
    passing test.
    """
    name = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
    if not name:
        return None
    message = email.message_from_string(
        archive.read(name[0]).decode("utf-8"))
    return message.get_payload()


def modules_inside(archive):
    """Every source file the wheel would install, as {path: text}."""
    found = {}
    for name in archive.namelist():
        if name.startswith(PACKAGE + "/") and name.endswith(".py"):
            found[name] = archive.read(name).decode("utf-8")
    return found


def sdists():
    return sorted(glob.glob(os.path.join(DIST, "*.tar.gz")))


def _sdist_text(archive, member):
    handle = archive.extractfile(member)
    return handle.read().decode("utf-8") if handle is not None else None


def sdist_long_description(archive):
    """The same project page, out of the sdist's PKG-INFO.

    Both files are uploaded together and each carries its own copy of the
    description, so the two can disagree — and then which one is the project
    page depends on the order twine happened to send them.
    """
    for member in archive.getnames():
        if posixpath.basename(member) == "PKG-INFO" \
                and member.count("/") == 1:
            return email.message_from_string(
                _sdist_text(archive, member) or "").get_payload()
    return None


def sdist_modules(archive):
    """Every source file in the sdist, keyed the way the wheel keys them.

    An sdist is one directory deep — `agentwatch-0.1.0/agentwatch/cli.py` —
    and that prefix is stripped here so both halves of this file compare
    against the same paths in the tree.
    """
    found = {}
    for member in archive.getnames():
        parts = member.split("/", 1)
        if len(parts) != 2:
            continue
        inner = parts[1]
        if inner.startswith(PACKAGE + "/") and inner.endswith(".py"):
            found[inner] = _sdist_text(archive, member)
    return found


class TestTheWheelHoldsTheTextInThisTree(unittest.TestCase):

    def setUp(self):
        self.built = wheels()
        if not self.built:
            self.skipTest("no dist/ — nothing built, so nothing to go stale")

    def test_the_project_page_inside_is_this_readme(self):
        with open(os.path.join(_ROOT, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        for path in self.built:
            with zipfile.ZipFile(path) as archive:
                described = long_description(archive)
            self.assertIsNotNone(
                described,
                "{} has no METADATA to render as a project page".format(
                    os.path.basename(path)))
            self.assertEqual(
                described.strip(), readme.strip(),
                "{} would publish a different README than this tree has, and "
                "a PyPI filename cannot be uploaded twice — build again before "
                "you upload".format(os.path.basename(path)))

    def test_every_module_inside_is_the_module_in_this_tree(self):
        for path in self.built:
            with zipfile.ZipFile(path) as archive:
                inside = modules_inside(archive)
            self.assertTrue(
                inside,
                "{} contains no {}/ sources at all".format(
                    os.path.basename(path), PACKAGE))
            for name, packaged in sorted(inside.items()):
                on_disk = os.path.join(_ROOT, name)
                self.assertTrue(
                    os.path.exists(on_disk),
                    "{} ships {}, which is not in this tree".format(
                        os.path.basename(path), name))
                with open(on_disk, encoding="utf-8") as handle:
                    self.assertEqual(
                        handle.read(), packaged,
                        "{} ships a stale {} — build again before you "
                        "upload".format(os.path.basename(path), name))

    def test_the_tree_has_something_to_be_stale_against(self):
        # Vacuity guard.  Both checks above compare the wheel to files read
        # off disk, and both would pass quietly against an empty README or a
        # package with no modules — the two ways this could look green while
        # comparing nothing to nothing.
        with open(os.path.join(_ROOT, "README.md"), encoding="utf-8") as handle:
            self.assertGreater(len(handle.read().strip()), 200)
        with zipfile.ZipFile(self.built[0]) as archive:
            self.assertGreaterEqual(len(modules_inside(archive)), 2)


class TestTheSdistHoldsItToo(unittest.TestCase):
    """The other half of an upload, with its own copy of everything.

    `twine upload dist/*` sends both files.  pip builds from the sdist wherever
    a wheel will not do, so a stale one is a stale install — and PyPI takes the
    project page from the metadata it was handed, so a fresh wheel beside a
    stale sdist publishes whichever arrived in a way that no local check would
    have shown either.
    """

    def setUp(self):
        self.built = sdists()
        if not self.built:
            self.skipTest("no dist/ — nothing built, so nothing to go stale")

    def test_the_project_page_inside_is_this_readme(self):
        with open(os.path.join(_ROOT, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        for path in self.built:
            with tarfile.open(path) as archive:
                described = sdist_long_description(archive)
            self.assertIsNotNone(
                described,
                "{} has no PKG-INFO".format(os.path.basename(path)))
            self.assertEqual(
                described.strip(), readme.strip(),
                "{} would publish a different README than this tree has — "
                "build again before you upload".format(os.path.basename(path)))

    def test_every_module_inside_is_the_module_in_this_tree(self):
        for path in self.built:
            with tarfile.open(path) as archive:
                inside = sdist_modules(archive)
            self.assertTrue(
                inside,
                "{} contains no {}/ sources at all".format(
                    os.path.basename(path), PACKAGE))
            for name, packaged in sorted(inside.items()):
                on_disk = os.path.join(_ROOT, name)
                self.assertTrue(
                    os.path.exists(on_disk),
                    "{} ships {}, which is not in this tree".format(
                        os.path.basename(path), name))
                with open(on_disk, encoding="utf-8") as handle:
                    self.assertEqual(
                        handle.read(), packaged,
                        "{} ships a stale {} — build again before you "
                        "upload".format(os.path.basename(path), name))

    def test_the_two_halves_of_the_upload_agree_with_each_other(self):
        # Vacuity guard, and the check that needs neither file to be right.
        # Both tests above compare against the tree; if the sdist reader were
        # quietly finding nothing, both would pass by comparing nothing.  This
        # one compares the two archives, so it can only pass if each was read.
        if not wheels():
            self.skipTest("an sdist with no wheel beside it — nothing to "
                          "cross-check against")
        with tarfile.open(self.built[0]) as archive:
            from_sdist = sdist_modules(archive)
            page = sdist_long_description(archive)
        with zipfile.ZipFile(wheels()[0]) as archive:
            from_wheel = modules_inside(archive)
        self.assertTrue(page and page.strip())
        self.assertEqual(sorted(from_sdist), sorted(from_wheel),
                         "the sdist and the wheel do not even ship the same "
                         "set of modules")
        self.assertEqual(from_sdist, from_wheel)


if __name__ == "__main__":
    unittest.main()
