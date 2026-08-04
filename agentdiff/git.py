"""
agentdiff.git — git plumbing via subprocess.

All functions operate on an absolute repo_root path.
Nothing here modifies the repo.
"""

import os
import stat
import subprocess


class GitError(Exception):
    """Raised when git is unavailable, the directory is not a repo, or a ref is bad."""


DEFAULT_TIMEOUT = 60


def _as_argv(arg):
    """One argument, in the form git will read it back as.

    Handing git a path means handing the kernel bytes, and the encoding of
    those bytes is the locale's business — on a machine set to C it is ASCII,
    and passing back the very path git just gave us raises before git is even
    started.  On POSIX a filename *is* bytes, so we choose them ourselves and
    choose the ones git used.  Elsewhere the platform already speaks UTF-8 for
    paths and there is nothing to work around.
    """
    if os.name == "posix" and isinstance(arg, str) and not arg.isascii():
        return arg.encode("utf-8", "surrogateescape")
    return arg


def _git(args, cwd, timeout=DEFAULT_TIMEOUT):
    """Run git with the given args in cwd. Returns CompletedProcess. Never raises on non-zero.

    ``core.quotePath=false`` stops git escaping non-ASCII filenames into
    ``"caf\\303\\251.py"``, which is a display form and not a path any of this
    can open.

    It is load-bearing in exactly one place, and that place is easy to miss.
    Every call that lists paths asks for ``-z``, and NUL-separated output is
    never quoted whatever this setting says — so dropping it changes nothing
    there.  ``git diff --summary`` has no ``-z`` form, and that is the call the
    executable bit is read from (`_parse_exec_added`).  Without this setting a
    ``chmod +x`` on ``café.py`` is reported against a path that does not exist,
    while the file that actually changed goes unmentioned: not an error, just a
    file that quietly looks reviewed.  Keep both — the ``-z`` and this — and see
    tests/test_a_verdict_on_an_odd_name.py.

    A timeout because git is not always fast: a filter driver, a lock held by
    another process, a network remote.  agentdiff runs in pre-commit hooks,
    and a hook that never returns is a hook nobody can get out of.
    """
    try:
        return subprocess.run(
            [_as_argv(a) for a in
             ["git", "-c", "core.quotePath=false"] + list(args)],
            cwd=cwd,
            capture_output=True,
            text=True,
            # git speaks UTF-8 whatever the locale claims, and `text=True`
            # would otherwise believe the locale: on a machine set to C, one
            # file named in Japanese makes every git call raise.  `replace`
            # because a path we cannot decode is still a path worth reporting.
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        raise GitError("git not found in PATH")
    except subprocess.TimeoutExpired:
        raise GitError(
            "git took longer than {}s to answer: {}".format(timeout, " ".join(args[:2])))


def _must_have_worked(result, what):
    """Refuse to read a failed git call's empty output as an answer.

    `review` is a gate, and its quiet result -- "clean: 0 file(s) changed" --
    is also what an empty list looks like.  Each of these calls used to be
    guarded with `if returncode == 0:` and no else, so a git that could not
    read the index, or the working tree, or one file's diff, produced nothing
    and nothing was reported as clean.  Exit 0.  On the pre-commit path, with
    the change staged the whole time.

    Nothing found is a finding.  Nothing readable is not, and this is the
    difference between the two.
    """
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or "exit {}".format(result.returncode)
        raise GitError("{} failed: {}".format(what, detail))


def _split_z(text):
    """Fields from git's ``-z`` output.

    NUL is the one byte a filename cannot contain, which is the whole reason
    ``-z`` exists.  Splitting on newlines instead means a file called
    ``a\\nb.py`` arrives as two paths that do not exist.
    """
    return [field for field in text.split("\0") if field]


def find_repo_root(start=None):
    """Return the absolute git repo root path, or raise GitError."""
    r = _git(["rev-parse", "--show-toplevel"], cwd=start or os.getcwd())
    if r.returncode != 0:
        raise GitError("not a git repository (or git not available)")
    return r.stdout.strip()


def _has_commits(repo_root):
    return _git(["rev-parse", "HEAD"], cwd=repo_root).returncode == 0


def _ref_exists(repo_root, ref):
    return _git(["rev-parse", "--verify", "--quiet", ref], cwd=repo_root).returncode == 0


class FileChange:
    """One path that differs from the comparison ref."""

    __slots__ = ("status", "path", "old_path", "diff_text", "is_binary",
                 "new_exec", "unread")

    def __init__(self, status, path, old_path=None):
        self.status = status     # A added, M modified, D deleted, R renamed, U untracked
        self.path = path
        self.old_path = old_path
        self.diff_text = ""      # unified diff or "+lines" for untracked files
        self.is_binary = False
        self.new_exec = False    # executable bit was added
        # Why this file's contents never arrived, or "" if they did.  An empty
        # ``diff_text`` otherwise means "nothing was added", which is what a
        # file that could not be opened used to look like — and the rules then
        # found nothing in it, correctly, about nothing.
        self.unread = ""


def get_changes(repo_root, since_ref="HEAD", staged_only=False):
    """
    Return list[FileChange] for everything that differs from since_ref.

    Default mode: compares HEAD against working tree (staged + unstaged + untracked).
    staged_only=True: only staged changes (index vs since_ref); useful as a pre-commit hook.
    Raises GitError on bad repo or unknown ref.
    """
    if not _has_commits(repo_root):
        # A ref the person named still has to exist, first commit or not.
        # Silently ignoring it reviews something other than what was asked for
        # and reports success, which is the worst of the available outcomes.
        if since_ref not in ("HEAD", None) and not _ref_exists(repo_root, since_ref):
            raise GitError(f"unknown ref: {since_ref!r}")
        return _new_repo_changes(repo_root, staged_only=staged_only)

    if not _ref_exists(repo_root, since_ref):
        raise GitError(f"unknown ref: {since_ref!r}")

    cache = ["--cached"] if staged_only else []

    # 1. Tracked file changes
    r = _git(["diff"] + cache + ["-M", "--name-status", "-z", since_ref], cwd=repo_root)
    if r.returncode != 0:
        raise GitError(f"git diff failed: {r.stderr.strip()}")

    changes = []
    seen = set()

    # -z output is a flat NUL-separated stream: a status, then one path, except
    # for renames and copies which are followed by two.
    fields = _split_z(r.stdout)
    i = 0
    while i < len(fields):
        code = fields[i][:1]
        if code in ("R", "C") and i + 2 < len(fields):
            fc = FileChange("R" if code == "R" else code, fields[i + 2],
                            old_path=fields[i + 1])
            i += 3
        elif i + 1 < len(fields):
            fc = FileChange(code, fields[i + 1])
            i += 2
        else:
            break
        changes.append(fc)
        seen.add(fc.path)

    # 2. Untracked files (working-tree mode only)
    if not staged_only:
        r2 = _git(["ls-files", "--others", "--exclude-standard", "-z"], cwd=repo_root)
        _must_have_worked(r2, "git ls-files")
        for p in _split_z(r2.stdout):
            if p and p not in seen:
                changes.append(FileChange("U", p))
                seen.add(p)

    # 3. Executable-bit additions from diff summary
    r_sum = _git(["diff"] + cache + ["--summary", since_ref], cwd=repo_root)
    # A failure here used to fall back to an empty set, which quietly drops the
    # "this file became executable" flag -- one of the things review exists to
    # raise.  A missing flag looks exactly like nothing to flag.
    _must_have_worked(r_sum, "git diff --summary")
    exec_set = _parse_exec_added(r_sum.stdout)

    # 4. Fill diff_text, is_binary, new_exec
    for fc in changes:
        _fill_diff(fc, repo_root, since_ref, cache)
        if fc.path in exec_set:
            fc.new_exec = True
        # Untracked executables: check filesystem directly
        if fc.status == "U" and not fc.is_binary:
            full = os.path.join(repo_root, fc.path)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                fc.new_exec = True

    return changes


def _new_repo_changes(repo_root, staged_only=False):
    """
    For a brand-new repo with no commits, return staged and untracked files.

    git diff HEAD fails with no commits, so we query the index and the working
    tree separately and union the results.  ``staged_only`` still means staged
    only: a pre-commit hook on the very first commit must review what is about
    to be committed, not everything lying around next to it.
    """
    changes = []
    seen = set()

    # Staged files (git add-ed but not yet committed)
    r = _git(["ls-files", "--cached", "-z"], cwd=repo_root)
    _must_have_worked(r, "git ls-files")
    for p in _split_z(r.stdout):
        fc = FileChange("A", p)
        _read_as_added(fc, os.path.join(repo_root, p))
        changes.append(fc)
        seen.add(p)

    # Untracked files (not staged, not ignored)
    if not staged_only:
        r2 = _git(["ls-files", "--others", "--exclude-standard", "-z"], cwd=repo_root)
        _must_have_worked(r2, "git ls-files")
        for p in _split_z(r2.stdout):
            if p not in seen:
                fc = FileChange("U", p)
                _read_as_added(fc, os.path.join(repo_root, p))
                changes.append(fc)
                seen.add(p)

    return changes


def _fill_diff(fc, repo_root, since_ref, cache):
    if fc.status == "U":
        _read_as_added(fc, os.path.join(repo_root, fc.path))
        return
    r = _git(["diff"] + cache + [since_ref, "--", fc.path], cwd=repo_root)
    # An empty diff_text on a file we have already said changed is a file
    # reviewed as having changed nothing -- every rule reads this text.
    _must_have_worked(r, "git diff")
    fc.diff_text = r.stdout
    if "Binary files " in fc.diff_text:
        fc.is_binary = True
        fc.diff_text = ""


def _read_as_added(fc, full_path):
    """Read a file from disk and format each line as +line (for untracked/new files).

    Only regular files are opened.  Opening a FIFO blocks until somebody on the
    other end writes, and agentdiff runs in pre-commit hooks: a stray pipe in an
    untracked directory would stop the commit with no way to see why.
    """
    try:
        st = os.lstat(full_path)
    except OSError as exc:
        fc.unread = str(exc)
        return
    if not stat.S_ISREG(st.st_mode):
        fc.unread = "not a regular file"
        return
    try:
        with open(full_path, "rb") as f:
            raw = f.read(8192)
        if b"\x00" in raw:
            # Opened, and we know what it is: there is a rule about binaries
            # being added.  Not the same as not knowing anything about it.
            fc.is_binary = True
            return
        with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
            fc.diff_text = "".join(f"+{line}\n" for line in fh.read().splitlines())
    except OSError as exc:
        # Returning quietly here handed the rules an empty file.  They then
        # flagged nothing, which the run reported as `clean` — a verdict about
        # contents nobody ever saw.
        fc.unread = str(exc)


def _parse_exec_added(summary_text):
    """Return set of file paths that gained the executable bit in a diff --summary."""
    paths = set()
    for line in summary_text.splitlines():
        s = line.strip()
        # "create mode 100755 path/to/file"
        if s.startswith("create mode 100755 "):
            paths.add(s[len("create mode 100755 "):])
        # "mode change 100644 => 100755 path/to/file"
        elif s.startswith("mode change ") and "=> 100755 " in s:
            # Everything after the mode is the path.  Taking the last
            # whitespace-separated token instead loses every filename with a
            # space in it, and then the executable bit goes unreported.
            paths.add(s.split("=> 100755 ", 1)[1])
    return paths
