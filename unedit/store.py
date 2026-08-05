"""
Core snapshot logic for unedit.

Layout under .unedit/ (relative to the project root):
  objects/<xx>/<rest>       content-addressed file blobs, SHA-256, stored once
  snapshots/<id>.json       manifest per snapshot (human-readable JSON)
  aside/<timestamp>/        new files moved here during restore
"""

from __future__ import annotations

import datetime
import fnmatch
import hashlib
import json
import os
import random
import re
import shutil
import stat
import string
import sys
import time
from typing import Dict, Iterator, List, Optional, Tuple

# What a terminal obeys rather than shows is a fact about terminals rather than
# about a snapshot store: it lives in `terminal.py`, which is the same file in
# the four tools that print.  Only the save-time use is here -- what a listing
# does with a value is the command layer's decision, and it asks `terminal`
# itself.
from .terminal import one_line


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_EXCLUDES: frozenset = frozenset({
    '.git', '.unedit', 'node_modules', '.venv', 'venv', '__pycache__',
    '.mypy_cache', '.pytest_cache', 'dist', 'build', 'target',
    '.next', '.DS_Store',
})

# Roots that are dangerous to snapshot
DANGER_ROOTS: frozenset = frozenset({
    '/', '/etc', '/usr', '/var', '/opt', '/System', '/Windows',
})

SIZE_LIMIT = 2 * 1024 ** 3   # 2 GB
FILE_LIMIT = 50_000

STORE_DIR_NAME = '.unedit'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_size(n: int) -> str:
    """Human-readable file size."""
    if n < 1024:
        return '{} B'.format(n)
    elif n < 1024 ** 2:
        return '{:.1f} KB'.format(n / 1024)
    elif n < 1024 ** 3:
        return '{:.1f} MB'.format(n / 1024 ** 2)
    else:
        return '{:.1f} GB'.format(n / 1024 ** 3)


# The shape `_new_id` makes, as a manifest filename.  Used to tell one of our
# files apart from anything else that happens to be in `snapshots/`.
_SNAPSHOT_NAME_RE = re.compile(r'^\d{8}-\d{6}-\d{6}-[a-z0-9]{4}\.json$')


def _new_id() -> str:
    """Generate a sortable snapshot ID: YYYYMMDD-HHMMSS-uuuuuu-xxxx.

    The microsecond field (uuuuuu) ensures correct ordering within the same
    second.  The clock is UTC, because everything downstream depends on these
    sorting in the order they were taken — `scan_snapshots` sorts the directory
    listing, `resolve_snap_id(None)` takes the last one, `unedit back` restores
    it — and local time does not always go forwards.

    Let daylight saving end between two snapshots, or carry the laptop into
    another zone, and the newer one sorted first:

        20260804-183607-191573-jj8w  — A, taken first
        20260804-093608-241096-wz9e  — B, taken a second later

    `unedit back` then restored A and reported one file restored, which is a
    wrong restore reported as a right one.  UTC only ever goes forwards.  The
    manifest keeps local time for reading.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    ts = now.strftime('%Y%m%d-%H%M%S')
    us = '{:06d}'.format(now.microsecond)
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return '{}-{}-{}'.format(ts, us, suffix)


def _store_dir(root: str) -> str:
    return os.path.join(root, STORE_DIR_NAME)


def _objects_dir(store: str) -> str:
    return os.path.join(store, 'objects')


def _snapshots_dir(store: str) -> str:
    return os.path.join(store, 'snapshots')


def _object_path(objects_dir: str, sha256: str) -> str:
    return os.path.join(objects_dir, sha256[:2], sha256[2:])


def _snap_path(store: str, snap_id: str) -> str:
    return os.path.join(_snapshots_dir(store), snap_id + '.json')


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------

def check_safe_root(root: str) -> Optional[str]:
    """Return an error message if root is unsafe, else None."""
    resolved = os.path.realpath(root)
    if resolved in DANGER_ROOTS:
        return (
            "refusing to snapshot {} — snapshotting system directories could corrupt "
            "your system on restore. cd into a project directory first.".format(resolved)
        )
    home = os.path.realpath(os.path.expanduser('~'))
    if resolved == home:
        return (
            "refusing to snapshot your home directory — this would capture private "
            "files and consume large amounts of disk space. "
            "cd into a project directory first."
        )
    return None


# ---------------------------------------------------------------------------
# Ignore / exclusion logic
# ---------------------------------------------------------------------------

def load_ignore_patterns(root: str) -> List[str]:
    """Load patterns from .gitignore and .uneditignore in root."""
    patterns: List[str] = []
    for fname in ('.gitignore', '.uneditignore'):
        fpath = os.path.join(root, fname)
        if os.path.isfile(fpath):
            try:
                with open(fpath, 'r', encoding='utf-8',
                          errors='replace') as f:
                    for line in f:
                        line = line.rstrip('\n').rstrip('\r')
                        # Strip trailing slash (marks directory, but we treat same)
                        stripped = line.rstrip('/')
                        if stripped and not stripped.startswith('#'):
                            patterns.append(stripped)
            except OSError:
                pass
    return patterns


def _matches_pattern(rel_path: str, pattern: str) -> bool:
    """Check if rel_path matches a single ignore pattern."""
    rel_path = rel_path.replace('\\', '/')
    name = rel_path.rsplit('/', 1)[-1]
    if pattern.startswith('/'):
        # A leading slash anchors the pattern to the project root, which is how
        # git reads it.  Matching it as a literal path component means a line
        # somebody wrote to keep a secret out lets the secret straight in.
        return fnmatch.fnmatch(rel_path, pattern[1:])
    if '/' in pattern:
        # Pattern with slash: match against full path
        return fnmatch.fnmatch(rel_path, pattern)
    else:
        # No slash: match against basename only
        return fnmatch.fnmatch(name, pattern)


def is_excluded(name: str, rel_path: str, default_excludes: frozenset, patterns: List[str]) -> bool:
    """True if this file/dir should be skipped."""
    if name in default_excludes:
        return True
    for pat in patterns:
        if _matches_pattern(rel_path, pat):
            return True
    return False


# ---------------------------------------------------------------------------
# Tree scanning
# ---------------------------------------------------------------------------

def scan_tree(
    root: str,
    default_excludes: frozenset = DEFAULT_EXCLUDES,
    patterns: Optional[List[str]] = None,
    skipped: Optional[List[Tuple[str, str]]] = None,
) -> Iterator[Tuple[str, os.stat_result, bool, Optional[str]]]:
    """
    Walk root, yielding (rel_path, stat_result, is_symlink, symlink_target).
    rel_path is relative to root, using forward slashes.
    Symlinks are yielded as symlinks (not followed).

    Only regular files and symlinks are yielded.  A FIFO or a socket cannot be
    snapshotted and cannot be restored, and opening one blocks until somebody
    on the other end writes — a snapshot that hangs on a stray pipe gives the
    person nothing to look at while it does.

    Anything skipped is appended to ``skipped`` as (rel_path, reason) so the
    caller can say so.  A snapshot that quietly contains less than the project
    is worse than one that refuses: it is discovered at restore time.
    """
    if patterns is None:
        patterns = load_ignore_patterns(root)

    def note(path: str, reason: str) -> None:
        if skipped is not None:
            skipped.append((path, reason))

    def on_error(exc: OSError) -> None:
        # os.walk swallows these by default, and a directory that could not be
        # read looks exactly like a directory that was empty.
        target = getattr(exc, 'filename', None) or root
        try:
            rel = os.path.relpath(target, root).replace(os.sep, '/')
        except ValueError:
            rel = str(target)
        note(rel, str(exc))

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False,
                                                onerror=on_error):
        # Compute relative path of current directory
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == '.':
            rel_dir = ''

        # Prune excluded directories in-place
        pruned = []
        for d in dirnames:
            rel_d = '{}/{}'.format(rel_dir, d) if rel_dir else d
            if not is_excluded(d, rel_d, default_excludes, patterns):
                pruned.append(d)
        dirnames[:] = pruned

        # Yield files and symlinks in this directory
        for fname in filenames:
            full_path = os.path.join(dirpath, fname)
            rel_path = '{}/{}'.format(rel_dir, fname) if rel_dir else fname

            # Check exclusion for the file itself
            if is_excluded(fname, rel_path, default_excludes, patterns):
                continue

            try:
                st = os.lstat(full_path)
            except OSError as exc:
                note(rel_path, str(exc))
                continue

            if stat.S_ISLNK(st.st_mode):
                try:
                    target = os.readlink(full_path)
                except OSError as exc:
                    note(rel_path, str(exc))
                    continue
                yield rel_path, st, True, target
            elif stat.S_ISREG(st.st_mode):
                yield rel_path, st, False, None
            else:
                note(rel_path, 'not a regular file')

        # Also check for symlinks in dirnames that were not pruned
        # os.walk with followlinks=False lists dir-symlinks in dirnames
        # We need to yield them as symlinks, not follow them
        for d in list(dirnames):
            full_d = os.path.join(dirpath, d)
            try:
                st = os.lstat(full_d)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode):
                rel_d = '{}/{}'.format(rel_dir, d) if rel_dir else d
                try:
                    target = os.readlink(full_d)
                except OSError:
                    continue
                yield rel_d, st, True, target
                dirnames.remove(d)  # don't recurse into symlinked dirs


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def hash_file(path: str) -> str:
    """SHA-256 hex digest of file contents."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Object store
# ---------------------------------------------------------------------------

def store_object(objects_dir: str, src_path: str, sha256: str) -> None:
    """Copy src_path into the object store at the correct content-addressed path."""
    dest = _object_path(objects_dir, sha256)
    if os.path.exists(dest):
        return  # already stored — this is the deduplication
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        shutil.copy2(src_path, dest)
    except PermissionError:
        # Another concurrent writer beat us to it; the object is already there.
        if os.path.exists(dest):
            return
        raise
    # Make object read-only to signal immutability
    try:
        os.chmod(dest, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save(root: str, message: str = '', force: bool = False) -> Dict:
    """
    Snapshot the current directory tree.
    Returns the manifest dict.
    Raises RuntimeError on refusal conditions.
    """
    err = check_safe_root(root)
    if err:
        raise RuntimeError(err)

    store = _store_dir(root)
    objects = _objects_dir(store)
    patterns = load_ignore_patterns(root)

    # First pass: count files and total size for guard-rail checks
    total_size = 0
    total_files = 0
    entries = []
    skipped: List[Tuple[str, str]] = []
    for rel_path, st, is_lnk, lnk_target in scan_tree(root, DEFAULT_EXCLUDES, patterns,
                                                      skipped=skipped):
        total_files += 1
        if not is_lnk:
            total_size += st.st_size
        if not force:
            if total_files > FILE_LIMIT:
                raise RuntimeError(
                    'tree has more than {:,} files — refusing to snapshot '
                    '(use --force to override)'.format(FILE_LIMIT)
                )
            if total_size > SIZE_LIMIT:
                raise RuntimeError(
                    'tree exceeds 2 GB — refusing to snapshot '
                    '(use --force to override)'
                )
        entries.append((rel_path, st, is_lnk, lnk_target))

    os.makedirs(objects, exist_ok=True)
    os.makedirs(_snapshots_dir(store), exist_ok=True)

    snap_id = _new_id()
    files = []

    for rel_path, st, is_lnk, lnk_target in entries:
        if is_lnk:
            files.append({
                'path': rel_path,
                'type': 'symlink',
                'target': lnk_target,
                'mtime': st.st_mtime,
            })
        else:
            full_path = os.path.join(root, rel_path)
            try:
                sha = hash_file(full_path)
            except OSError as exc:
                skipped.append((rel_path, str(exc)))
                continue
            try:
                store_object(objects, full_path, sha)
            except OSError as exc:
                skipped.append((rel_path, str(exc)))
                continue
            files.append({
                'path': rel_path,
                'type': 'file',
                'hash': sha,
                'mode': stat.S_IMODE(st.st_mode),
                'mtime': st.st_mtime,
                'size': st.st_size,
            })

    manifest = {
        'id': snap_id,
        # Local time, because a person reads it, and with its offset, because
        # a stamp without one means a different instant on every machine.
        'timestamp': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
        'message': one_line(message),
        'file_count': len(files),
        'total_size': sum(f.get('size', 0) for f in files),
        'files': files,
    }

    if skipped:
        # Recorded in the snapshot itself, not just printed once: what a
        # snapshot does not contain is the thing you need to know at restore
        # time, which is months after the sentence scrolled off the screen.
        manifest['skipped'] = [{'path': p, 'reason': r} for p, r in skipped]

    snap_file = _snap_path(store, snap_id)
    # A manifest is the thing that has to be readable later, and "later" is
    # often a different machine with a different locale.  Naming the encoding
    # at both ends is what stops a snapshot taken on a laptop from being
    # unreadable in the container that needs to restore it.
    with open(snap_file, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
        f.write('\n')

    return manifest


# ---------------------------------------------------------------------------
# List / load
# ---------------------------------------------------------------------------

def load_manifest(store: str, snap_id: str) -> Dict:
    """Load a snapshot manifest by ID. Raises FileNotFoundError if missing."""
    path = _snap_path(store, snap_id)
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def scan_snapshots(store: str) -> Tuple[List[Dict], List[Dict]]:
    """The snapshots that can be read, and the ones that cannot.

    Good manifests come back oldest-first.  Damaged ones come back as
    ``{'id', 'file', 'why'}`` — the id is the filename, because that is how
    these are named and it is the only thing about a damaged one still known
    to be true.

    They used to be skipped without a word, so a store holding one snapshot
    could answer `no snapshots`.  For an undo tool that is the worst sentence
    available: you conclude you never saved and stop looking, when a manifest
    is only an index and the content it points at is still sitting in
    ``objects/``.  `.unedit/` lives in the working tree an agent is editing,
    so this is not a rare state — a save that ran out of disk, an editor that
    saved over one, a merge conflict in a store somebody committed.

    Only a file *named* like a snapshot can be damaged.  Anything else in the
    directory is somebody else's file, and reporting it as damage would cry
    wolf in every store forever.
    """
    snaps_dir = _snapshots_dir(store)
    if not os.path.isdir(snaps_dir):
        return [], []
    try:
        names = sorted(os.listdir(snaps_dir))
    except OSError:
        return [], []
    good: List[Dict] = []
    damaged: List[Dict] = []

    def _damaged(fname: str, why: str) -> None:
        damaged.append({'id': fname[:-len('.json')], 'file': fname,
                        'why': why})

    for fname in names:
        if not fname.endswith('.json'):
            continue
        try:
            with open(os.path.join(snaps_dir, fname), encoding='utf-8') as f:
                manifest = json.load(f)
        except ValueError as e:
            if _looks_like_a_snapshot_name(fname):
                _damaged(fname, 'not readable as JSON — {}'.format(e))
            continue
        except OSError as e:
            if _looks_like_a_snapshot_name(fname):
                _damaged(fname, e.strerror or str(e))
            continue
        # A manifest is an object with an id.  Anything else in this directory
        # is somebody else's file, and treating it as a snapshot turns every
        # later `.get` into a TypeError halfway down the listing.
        if isinstance(manifest, dict) and isinstance(manifest.get('id'), str):
            manifest.setdefault('files', [])
            if not isinstance(manifest['files'], list):
                manifest['files'] = []
            good.append(manifest)
        elif _looks_like_a_snapshot_name(fname):
            _damaged(fname, 'readable, but not a snapshot manifest')
    return good, damaged


def _looks_like_a_snapshot_name(fname: str) -> bool:
    """Is this file one of ours?

    Snapshot ids are `<date>-<time>-<micros>-<four random chars>`, and the
    manifest is that plus `.json`.  Matching the shape rather than trying to
    parse the file is what keeps a README or an editor's scratch file in this
    directory from being announced as a damaged snapshot.
    """
    return _SNAPSHOT_NAME_RE.match(fname) is not None


def list_snapshots(store: str) -> List[Dict]:
    """Return manifests sorted oldest-first. Returns [] if store doesn't exist.

    Damaged manifests are not in here — see `scan_snapshots` for those.  Every
    caller that reports to a person should use that instead, because an
    unreadable snapshot missing from a list reads as a snapshot that was never
    taken.
    """
    return scan_snapshots(store)[0]


def describe_damage(damaged: List[Dict], store: str) -> str:
    """What to print about manifests that could not be read."""
    head = '{} damaged snapshot{} in {}:'.format(
        len(damaged), '' if len(damaged) == 1 else 's', _snapshots_dir(store))
    lines = [head]
    for d in damaged:
        lines.append('  {}  — {}'.format(d['file'], d['why']))
    lines.append('a manifest is only an index: the file contents it points at '
                 'are still in {}'.format(_objects_dir(store)))
    return '\n'.join(lines)


def resolve_snap_id(store: str, snap_id: Optional[str]) -> str:
    """Resolve a snapshot ID (or None → newest). Raises RuntimeError if not found."""
    snaps, damaged = scan_snapshots(store)
    if not snaps:
        if damaged:
            raise RuntimeError(describe_damage(damaged, store))
        raise RuntimeError('no snapshots found')
    if snap_id is None:
        # "The newest" has to mean the newest, including when the newest is the
        # broken one.  Ids sort by time, so a damaged file sorting after the
        # newest good manifest *is* the newest.  Quietly reaching past it and
        # restoring the one before would be a wrong restore reported as a right
        # one — the failure this whole function exists to prevent.
        newer = [d for d in damaged if d['id'] > snaps[-1]['id']]
        if newer:
            raise RuntimeError(
                '{}\nthe newest snapshot is one of these — name an older id to '
                'restore it instead'.format(describe_damage(newer, store)))
        return snaps[-1]['id']
    if not snap_id.strip():
        # An empty prefix matches every snapshot.  With one in the store that
        # reads as a precise hit, and `unedit drop ''` deletes it.
        raise RuntimeError('empty snapshot id — give an id, or use --all')
    # Allow prefix matching
    matches = [s['id'] for s in snaps if s['id'].startswith(snap_id)]
    if not matches:
        hit = [d for d in damaged if d['id'].startswith(snap_id)]
        if hit:
            raise RuntimeError(describe_damage(hit, store))
        raise RuntimeError('no snapshot matching {!r}'.format(snap_id))
    if len(matches) > 1:
        raise RuntimeError('ambiguous id {!r} matches: {}'.format(snap_id, ', '.join(matches)))
    return matches[0]


# ---------------------------------------------------------------------------
# Restore (back)
# ---------------------------------------------------------------------------

def _lands_inside(root: str, rel_path: str) -> bool:
    """Whether restoring ``rel_path`` would write inside ``root``.

    Resolved rather than compared as text, because there are three ways out and
    only one of them is visible in the string.  ``../`` climbs; an absolute path
    makes ``os.path.join`` discard the root entirely, which is easy to not
    notice; and a directory symlink is an escape that lives on disk, where
    reading the path tells you nothing.  ``realpath`` answers all three, and
    answers them for a leaf that does not exist yet, which is the usual case
    during a restore.
    """
    root_real = os.path.realpath(root)
    dest = os.path.realpath(os.path.join(root_real, rel_path))
    return dest == root_real or dest.startswith(root_real + os.sep)


def _climbs_out(rel_path: str) -> bool:
    """Whether the path itself leaves the project, read as text.

    The two ways a path can say so on its own: it is absolute, which makes
    ``os.path.join`` discard the root entirely, or it climbs with ``..``.  A
    path that does neither is an ordinary relative path and the manifest
    holding it has nothing wrong with it — see `_redirected_by`.
    """
    if not rel_path or os.path.isabs(rel_path):
        return True
    first = os.path.normpath(rel_path).replace(os.sep, '/').split('/')[0]
    return first == '..'


def _redirected_by(root: str, rel_path: str) -> Optional[Tuple[str, str]]:
    """The symlink in the working tree that sends ``rel_path`` out, if any.

    Returns ``(the path of the link, where it points)``, walking from the
    project root down so the answer is the outermost redirect — the one to
    remove.  ``None`` if nothing on disk explains it.
    """
    parts = os.path.normpath(rel_path).replace(os.sep, '/').split('/')
    here = root
    for i, part in enumerate(parts):
        here = os.path.join(here, part)
        if os.path.islink(here):
            return ('/'.join(parts[:i + 1]), os.path.realpath(here))
    return None


def _refuse_to_write_outside(root: str, manifest: Dict) -> None:
    """A manifest is not trusted input, so check it before touching anything.

    The store is a directory in the working tree — it gets committed and cloned
    with the repo, and it sits in a tree something else is editing, which is the
    entire premise of this tool.  Paths in it are meant to be relative to the
    project root and nothing was making sure they stayed there.

    Checked in full, up front.  Stopping partway would mean the safety snapshot
    is already taken and some files are already overwritten, which leaves the
    tree in a state nobody asked for.

    Two different things get stopped here and they are reported separately.  A
    manifest naming `../../.ssh/authorized_keys` is a snapshot that is lying.  A
    manifest naming `notes.txt`, where `notes.txt` has since been replaced by a
    symlink to somewhere else, is an innocent snapshot and an interfered-with
    working tree.  Told the first sentence for the second case, a person goes
    and inspects a snapshot that has nothing wrong with it, while the symlink
    that actually stopped the restore sits unmentioned.
    """
    lying, redirected = [], []
    for entry in manifest.get('files', []):
        rel = entry.get('path', '')
        if _lands_inside(root, rel):
            continue
        link = None if _climbs_out(rel) else _redirected_by(root, rel)
        if link is None:
            lying.append(rel)
        else:
            redirected.append((rel, link[0], link[1]))

    if not lying and not redirected:
        return

    parts = []
    if lying:
        parts.append(
            'snapshot names {} path(s) outside the project and was not '
            'applied:\n  {}\n'
            'a snapshot only ever restores files under {}'.format(
                len(lying), '\n  '.join(sorted(lying)[:10]), root))
    if redirected:
        parts.append(
            '{} path(s) in the project now lead outside it, and nothing was '
            'restored:\n  {}\n'
            'a symlink was put there after the snapshot was taken. remove it '
            'and run the restore again — unedit only ever writes under '
            '{}'.format(
                len(redirected),
                '\n  '.join(
                    '{} (symlink {} -> {})'.format(rel, link, dest)
                    if link != rel else '{} (symlink -> {})'.format(rel, dest)
                    for rel, link, dest in sorted(redirected)[:10]),
                root))
    raise RuntimeError('\n\n'.join(parts))


def restore(
    root: str,
    snap_id: Optional[str],
    yes: bool = False,
    hard: bool = False,
    force: bool = False,
    print_fn=print,
) -> Dict:
    """
    Restore a snapshot.
    1. Compute plan.
    2. Confirm (unless --yes).
    3. Auto-save current state.
    4. Execute.
    Returns summary dict.
    """
    err = check_safe_root(root)
    if err:
        raise RuntimeError(err)

    store = _store_dir(root)
    resolved_id = resolve_snap_id(store, snap_id)
    manifest = load_manifest(store, resolved_id)
    _refuse_to_write_outside(root, manifest)
    objects = _objects_dir(store)

    # Build index of snapshot files
    snap_index: Dict[str, Dict] = {f['path']: f for f in manifest['files']}

    # Scan current tree
    patterns = load_ignore_patterns(root)
    current_index: Dict[str, Tuple[os.stat_result, bool, Optional[str]]] = {}
    for rel_path, st, is_lnk, lnk_target in scan_tree(root, DEFAULT_EXCLUDES, patterns):
        current_index[rel_path] = (st, is_lnk, lnk_target)

    # Files to restore: in snapshot but not matching current state
    to_restore = []
    for path, snap_file in snap_index.items():
        if snap_file['type'] == 'symlink':
            cur = current_index.get(path)
            if cur is None or not cur[1] or cur[2] != snap_file['target']:
                to_restore.append(path)
        else:
            cur = current_index.get(path)
            if cur is None:
                to_restore.append(path)
            else:
                cur_st, cur_lnk, _ = cur
                if cur_lnk:
                    to_restore.append(path)
                else:
                    try:
                        cur_hash = hash_file(os.path.join(root, path))
                        if cur_hash != snap_file['hash']:
                            to_restore.append(path)
                    except OSError:
                        to_restore.append(path)

    # New files: in current but NOT in snapshot
    new_files = [p for p in current_index if p not in snap_index]

    # Summary (before confirmation — no side effects yet)
    aside_action = 'delete' if hard else 'move aside'
    print_fn('plan:')
    print_fn('  {} files to restore'.format(len(to_restore)))
    print_fn('  {} new files to {} (created since snapshot)'.format(len(new_files), aside_action))
    if not snap_index:
        # `back` to an empty snapshot is not a no-op: everything present is new
        # relative to it, so all of it gets moved aside or deleted and nothing
        # arrives to replace it.  "done. 0 restored" was the only thing said
        # about that, after the fact.  Older versions wrote empty snapshots
        # freely whenever an ignore rule happened to match the whole tree, so
        # one can be sitting in any store, and the person choosing it is
        # choosing it because they believe their work is in it.
        print_fn('')
        print_fn('note: this snapshot is empty — it holds no files at all, so')
        print_fn('      nothing will come back; this only clears what is here.')
    print_fn('')

    if not yes:
        try:
            answer = input('proceed? [y/N] ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ''
        if answer not in ('y', 'yes'):
            print_fn('aborted.')
            return {'aborted': True}

    # Auto-save happens AFTER confirmation so aborts do not leave phantom snapshots.
    print_fn('auto-saving current state before restore...')
    safety_snap = save(root, message='[auto] before restore of {}'.format(resolved_id), force=force)
    safety_id = safety_snap['id']
    print_fn('safety snapshot: {}  (run: unedit back {} to undo this restore)'.format(
        safety_id, safety_id
    ))
    print_fn('')

    # Execute restore
    aside_dir = None
    if new_files and not hard:
        # UTC, for the same reason snapshot ids are: these directories sit
        # beside each other and the newest has to be the last one.
        ts = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
        aside_dir = os.path.join(store, 'aside', ts)

    deleted = []
    moved_aside = []

    # Track parent dirs of new_files so we can clean up empty dirs afterward.
    new_file_parent_dirs: set = set()

    for path in new_files:
        full = os.path.join(root, path)
        parent = os.path.dirname(full)
        if parent != root:
            new_file_parent_dirs.add(parent)
        if hard:
            try:
                os.unlink(full)
                deleted.append(path)
                print_fn('  deleted: {}'.format(path))
            except OSError as e:
                print_fn('warning: could not delete {}: {}'.format(path, e))
        else:
            dest = os.path.join(aside_dir, path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                shutil.move(full, dest)
                moved_aside.append(path)
            except OSError as e:
                print_fn('warning: could not move {}: {}'.format(path, e))

    # Remove empty directories left behind by moved/deleted new_files.
    # Sort deepest first so we remove children before parents.
    for dir_path in sorted(new_file_parent_dirs, key=lambda d: d.count(os.sep), reverse=True):
        # Only remove if it is not part of the snapshot and is now empty.
        rel_dir = os.path.relpath(dir_path, root)
        in_snap = any(
            f['path'].startswith(rel_dir + '/') or f['path'] == rel_dir
            for f in manifest['files']
        )
        if not in_snap:
            try:
                if os.path.isdir(dir_path) and not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    # Also try parent, walking upward
                    parent = os.path.dirname(dir_path)
                    while parent != root and os.path.isdir(parent) and not os.listdir(parent):
                        rel_p = os.path.relpath(parent, root)
                        in_snap_p = any(
                            f['path'].startswith(rel_p + '/') or f['path'] == rel_p
                            for f in manifest['files']
                        )
                        if in_snap_p:
                            break
                        os.rmdir(parent)
                        parent = os.path.dirname(parent)
            except OSError:
                pass

    restored = 0
    for path in to_restore:
        snap_file = snap_index[path]
        full = os.path.join(root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        if snap_file['type'] == 'symlink':
            try:
                if os.path.lexists(full):
                    if os.path.isdir(full) and not os.path.islink(full):
                        shutil.rmtree(full)
                    else:
                        os.unlink(full)
                os.symlink(snap_file['target'], full)
                restored += 1
            except OSError as e:
                print_fn('warning: could not restore symlink {}: {}'.format(path, e))
        else:
            obj = _object_path(objects, snap_file['hash'])
            if not os.path.exists(obj):
                print_fn('warning: object missing for {} — skipping'.format(path))
                continue
            try:
                if os.path.lexists(full):
                    if os.path.isdir(full) and not os.path.islink(full):
                        shutil.rmtree(full)
                    else:
                        os.unlink(full)
                shutil.copy2(obj, full)
                restored += 1
                try:
                    os.chmod(full, snap_file['mode'])
                except OSError:
                    pass
            except OSError as e:
                print_fn('warning: could not restore {}: {}'.format(path, e))

    # What was planned and what happened are two different numbers.  Reporting
    # the plan as the outcome tells somebody their work came back when it did
    # not, which is the one lie this tool cannot afford.
    result = {
        'restored_from': resolved_id,
        'safety_snapshot': safety_id,
        'restored': restored,
        'planned': len(to_restore),
        'moved_aside': len(moved_aside),
        'aside_dir': aside_dir,
        'deleted': len(deleted),
    }

    if moved_aside:
        print_fn('')
        print_fn('new files moved aside to: {}/'.format(aside_dir))

    print_fn('')
    print_fn('done. {} restored, {} moved aside, {} deleted.'.format(
        restored, len(moved_aside), len(deleted)
    ))
    if restored < len(to_restore):
        print_fn('warning: {} of {} planned files could not be restored.'.format(
            len(to_restore) - restored, len(to_restore)))
    print_fn('to undo: unedit back {}'.format(safety_id))

    return result


# ---------------------------------------------------------------------------
# Show
# ---------------------------------------------------------------------------

def show_snapshot(store: str, snap_id: Optional[str]) -> Tuple[Dict, List[Dict]]:
    """Return (manifest_header, file_list) for a snapshot."""
    resolved_id = resolve_snap_id(store, snap_id)
    manifest = load_manifest(store, resolved_id)
    return manifest, manifest.get('files', [])


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def diff_snapshot(root: str, snap_id: Optional[str], patch: bool = False) -> Dict:
    """
    Compare current tree against a snapshot.
    Returns dict with added/modified/removed lists, and optionally patch text.
    """
    err = check_safe_root(root)
    if err:
        raise RuntimeError(err)

    store = _store_dir(root)
    resolved_id = resolve_snap_id(store, snap_id)
    manifest = load_manifest(store, resolved_id)
    objects = _objects_dir(store)

    snap_index: Dict[str, Dict] = {f['path']: f for f in manifest['files']}

    patterns = load_ignore_patterns(root)
    current_index: Dict[str, Tuple] = {}
    for rel_path, st, is_lnk, lnk_target in scan_tree(root, DEFAULT_EXCLUDES, patterns):
        current_index[rel_path] = (st, is_lnk, lnk_target)

    added = []
    modified = []
    removed = []

    for path, snap_file in snap_index.items():
        if path not in current_index:
            removed.append({
                'path': path,
                'size': snap_file.get('size', 0),
                'type': snap_file['type'],
            })
        else:
            cur_st, cur_lnk, cur_target = current_index[path]
            if snap_file['type'] == 'symlink':
                if not cur_lnk or cur_target != snap_file['target']:
                    modified.append({
                        'path': path,
                        'type': 'symlink',
                        'old_target': snap_file['target'],
                        'new_target': cur_target,
                    })
            else:
                if cur_lnk:
                    modified.append({'path': path, 'type': 'file',
                                     'old_size': snap_file.get('size', 0),
                                     'new_size': cur_st.st_size})
                else:
                    try:
                        cur_hash = hash_file(os.path.join(root, path))
                        if cur_hash != snap_file['hash']:
                            modified.append({
                                'path': path,
                                'type': 'file',
                                'old_size': snap_file.get('size', 0),
                                'new_size': cur_st.st_size,
                            })
                    except OSError:
                        pass

    for path in current_index:
        if path not in snap_index:
            cur_st, cur_lnk, _ = current_index[path]
            added.append({
                'path': path,
                'size': cur_st.st_size if not cur_lnk else 0,
                'type': 'symlink' if cur_lnk else 'file',
            })

    result: Dict = {
        'snapshot_id': resolved_id,
        'snapshot_timestamp': manifest.get('timestamp', ''),
        'snapshot_message': manifest.get('message', ''),
        'added': sorted(added, key=lambda x: x['path']),
        'modified': sorted(modified, key=lambda x: x['path']),
        'removed': sorted(removed, key=lambda x: x['path']),
    }

    if patch:
        import difflib
        patches = []
        for entry in result['modified']:
            if entry['type'] != 'file':
                continue
            path = entry['path']
            obj_path = _object_path(objects, snap_index[path]['hash'])
            cur_path = os.path.join(root, path)
            try:
                # Source files are UTF-8; the locale does not get a say.  On a
                # machine set to C, letting it decide turns every accented word
                # in the patch into question marks, and the diff is then read
                # to make a decision about a file that does not exist.
                with open(obj_path, 'r', encoding='utf-8',
                          errors='replace') as f:
                    old_lines = f.readlines()
                with open(cur_path, 'r', encoding='utf-8',
                          errors='replace') as f:
                    new_lines = f.readlines()
                diff = list(difflib.unified_diff(
                    old_lines, new_lines,
                    fromfile='a/' + path,
                    tofile='b/' + path,
                ))
                if diff:
                    patches.append(''.join(diff))
            except OSError:
                pass
        result['patch'] = ''.join(patches)

    return result


# ---------------------------------------------------------------------------
# Drop
# ---------------------------------------------------------------------------

def drop_snapshots(store: str, snap_ids: List[str], all_snaps: bool = False) -> Dict:
    """Delete snapshots and GC orphaned objects."""
    snaps = list_snapshots(store)
    if not snaps:
        raise RuntimeError('no snapshots to drop')

    if all_snaps:
        if snap_ids:
            # Silently ignoring the ids means `unedit drop wrong-id --all`
            # deletes the whole store and reports success.  The person typed
            # two things; if they disagree, neither is safe to guess at.
            raise RuntimeError(
                'give snapshot ids or --all, not both '
                '(--all already means every snapshot)')
        to_drop = {s['id'] for s in snaps}
    else:
        to_drop = set()
        for sid in snap_ids:
            resolved = resolve_snap_id(store, sid)
            to_drop.add(resolved)

    # Delete snapshot files
    dropped = 0
    for snap_id in to_drop:
        try:
            os.unlink(_snap_path(store, snap_id))
            dropped += 1
        except FileNotFoundError:
            # Something else removed it first.  The end state is the one asked
            # for, so this is not a failure — it just was not us who did it.
            pass

    # GC: find all hashes still in use
    remaining = list_snapshots(store)
    in_use = set()
    for s in remaining:
        for f in s.get('files', []):
            if isinstance(f, dict) and f.get('type') == 'file' and 'hash' in f:
                in_use.add(f['hash'])

    # Remove orphaned objects
    gc_count = 0
    objects_dir = _objects_dir(store)
    if os.path.isdir(objects_dir):
        for prefix in sorted(os.listdir(objects_dir)):
            prefix_dir = os.path.join(objects_dir, prefix)
            if not os.path.isdir(prefix_dir):
                continue
            try:
                names = os.listdir(prefix_dir)
            except OSError:
                continue
            for fname in names:
                sha = prefix + fname
                if sha not in in_use:
                    try:
                        os.unlink(os.path.join(prefix_dir, fname))
                        gc_count += 1
                    except OSError:
                        pass
            # Remove empty prefix dir
            try:
                if not os.listdir(prefix_dir):
                    os.rmdir(prefix_dir)
            except OSError:
                pass

    return {'dropped': dropped, 'gc_objects': gc_count}


# ---------------------------------------------------------------------------
# Where
# ---------------------------------------------------------------------------

def where_info(root: str) -> Dict:
    """Return info about the snapshot storage location."""
    store = _store_dir(root)
    total_bytes = 0
    snap_count = len(list_snapshots(store))

    if os.path.isdir(store):
        for dirpath, _, filenames in os.walk(store):
            for fname in filenames:
                try:
                    total_bytes += os.path.getsize(os.path.join(dirpath, fname))
                except OSError:
                    pass

    return {
        'store_dir': store,
        'snap_count': snap_count,
        'total_bytes': total_bytes,
        'total_size': _fmt_size(total_bytes),
    }
