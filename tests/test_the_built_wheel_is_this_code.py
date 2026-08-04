"""A wheel sitting in `dist/` is the thing that goes to PyPI. Is it this code?

`test_the_version_it_reports` checks that `pyproject.toml` and the package
agree, and over in unedit it passed the whole time `unedit --version` was
printing 0.1.3 to everyone who installed 0.1.4 from PyPI. It passed because it
was right: the *source* agreed with itself. What went wrong was one step later.

`dist/` still held the wheel built before that fix. `twine upload
--skip-existing` decides by filename, so it saw `unedit-0.1.4-py3-none-any.whl`
already on PyPI and skipped — the corrected build could never replace it, and
PyPI filenames are immutable, so the fix could not ship under that number at
all. It took a version bump to get out.

Nothing about that is unedit's. Every tool in the family is built and uploaded
the same way, so every one of them can hold a stale wheel, and this repo has
no reason to be the one that finds out from a user's `--version`. The check is
the same: whether the artifact about to be uploaded contains the code in this
working tree.

There is usually no `dist/` — a clean checkout has none, and this skips. That
is not a hole: the failure only exists when a build exists, and it appears the
moment someone builds one.
"""

from __future__ import annotations

import glob
import os
import re
import sys
import unittest
import zipfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import stillworks  # noqa: E402

DIST = os.path.join(_ROOT, "dist")
PACKAGE = "stillworks"

# "stillworks-0.1.3-py3-none-any.whl", and "agentdiff_cli-0.1.4-..." elsewhere
# in the family, where the distribution name is not the import name. Sdists
# are spelled differently enough to need their own — "stillworks-0.1.3.tar.gz"
# has no tag after the version, so a pattern ending in a dash silently matches
# nothing and the sdist check passes over an empty list.
_WHEEL = re.compile(r"^[A-Za-z0-9_.]+-(\d[^-]*)-")
_SDIST = re.compile(r"^.+-(\d[^-]*)\.tar\.gz$")
_VERSION = re.compile(r"""^__version__ = ["']([^"']+)["']""", re.M)


def wheels():
    return sorted(glob.glob(os.path.join(DIST, "*.whl")))


def version_inside(path):
    """The `__version__` the wheel would install, read out of the wheel."""
    with zipfile.ZipFile(path) as archive:
        source = archive.read("{}/__init__.py".format(PACKAGE)).decode("utf-8")
    found = _VERSION.search(source)
    return found.group(1) if found else None


class TestTheBuiltWheelIsThisCode(unittest.TestCase):

    def setUp(self):
        self.built = wheels()
        if not self.built:
            self.skipTest("no dist/ — nothing built, so nothing to go stale")

    def test_the_wheel_carries_the_version_this_tree_says(self):
        # The filename is the metadata version, which is what PyPI serves and
        # what pip reports. The string inside is what `--version` prints. The
        # bug was those two disagreeing inside one file.
        for path in self.built:
            name = os.path.basename(path)
            found = _WHEEL.match(name)
            self.assertTrue(found, "cannot read a version out of {}".format(name))
            self.assertEqual(
                found.group(1), stillworks.__version__,
                "{} was built from a different version of this tree; delete "
                "dist/ and build again before uploading anything".format(name))

    def test_the_code_inside_agrees_with_the_name_outside(self):
        # This is the exact shape of the shipped bug: 0.1.4 on the tin, 0.1.3
        # in the tin. Nothing looked, so it reached PyPI and stayed there.
        for path in self.built:
            name = os.path.basename(path)
            self.assertEqual(
                version_inside(path), _WHEEL.match(name).group(1),
                "{} installs as one version and reports another when you run "
                "it — that is what shipped last time".format(name))

    def test_the_sdist_matches_too(self):
        # Same trap, one file over. pip falls back to the sdist wherever a
        # wheel will not do, so a stale one is not a lesser problem.
        sdists = sorted(glob.glob(os.path.join(DIST, "*.tar.gz")))
        self.assertTrue(sdists, "dist/ has wheels and no sdist, which is not "
                                "what `python -m build` leaves behind")
        for path in sdists:
            name = os.path.basename(path)
            found = _SDIST.match(name)
            self.assertTrue(found, "cannot read a version out of {}".format(name))
            self.assertEqual(
                found.group(1), stillworks.__version__,
                "{} is left over from an older build".format(name))


if __name__ == "__main__":
    unittest.main()
