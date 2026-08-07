"""
unedit command-line interface.

Commands:
  unedit save [-m MSG] [--force]
  unedit list
  unedit show [ID]
  unedit back [ID] [--yes] [--hard] [--force]
  unedit diff [ID] [--patch]
  unedit drop ID [ID ...] | --all
  unedit where

Every one of them also takes --json and --project DIR (--dir DIR is the older
spelling of the same flag), so they are not repeated on each line.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from . import store as _store
from .shell import run_as_a_command
from .where import add_project_flag
# What a terminal obeys rather than shows is a fact about terminals rather than
# about a snapshot store, so it does not come through `store`: `terminal.py` is
# the same file in the four tools that print, and which of its answers a
# particular column wants is this layer's decision to make.
from .terminal import block, one_line, pad, row


def _failed(args, msg: str, code: int = 2) -> int:
    """Say why unedit stopped, in whichever way the caller asked to be told.

    A script that passes `--json` reads stdout and parses it.  Every failure
    here used to write a sentence to stderr and nothing at all to stdout, so
    that script got an empty string and `json.loads` raised -- the one place a
    program most needs to be told what happened is the one place it was told in
    a shape it cannot read, and the traceback it gets names our output, not our
    error.

    The error document is an object with `error` in it, on every command,
    including the two whose success output is a list.  Which of the two you are
    holding is what the exit code is for, and it is never 0 here.

    The word in front is `unedit`, not `error`.  Five commands install together
    under one `pip install`, and in a build log with several of them running, a
    line beginning `error:` does not say who is talking.

    `args.json` plainly, and not `getattr(args, 'json', False)`: every one of the
    seven subcommands declares the flag, so there is no namespace that arrives
    here without it and a default here would be a branch no test can reach.  The
    fact it leans on is pinned by `test_every_command_takes_the_flag_this_reads`.
    """
    if args.json:
        _print_json({'error': msg})
        return code
    print('unedit: {}'.format(msg), file=sys.stderr)
    return code


# What a moment looks like when this tool prints one is `store.fmt_time` and
# `store.fmt_mtime`, next to `fmt_size` and public for the same reason.  It
# used to be here, cutting the offset off the end of the stored string with
# string surgery -- which reads the stamp as a shape rather than as a moment,
# and prints a wall clock that is only the reader's if the store was written
# on the reader's machine.


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_save(args) -> int:
    root = os.path.abspath(args.dir)
    try:
        manifest = _store.save(root, message=args.message or '', force=args.force)
    except RuntimeError as e:
        return _failed(args, str(e))
    except OSError as e:
        return _failed(args, 'could not save snapshot: {}'.format(e))

    snap_id = manifest['id']
    fc = manifest['file_count']
    sz = _store.fmt_size(manifest['total_size'])

    skipped = manifest.get('skipped') or []

    # A snapshot with no files in it is either an honest baseline or a safety
    # net with no floor, and until now both printed `saved  … (0 files, 0 B)`.
    # The count was on screen the whole time, which is exactly the sort of true
    # detail nobody reads next to a success word: the person ran `save` so that
    # they would have something to go back to, saw `saved`, and had nothing.
    # Which of the two it was is the walk's to know, and the walk already
    # recorded it: asking again here would be a second set of exclusion rules
    # that can disagree with the first, and the old one did.
    uncaptured = manifest.get('nothing_captured') or {}

    if args.json:
        _print_json({'id': snap_id, 'file_count': fc, 'total_size': manifest['total_size'],
                     'message': manifest['message'], 'timestamp': manifest['timestamp'],
                     # `empty` is true of both cases — the snapshot holds no
                     # files either way, and `back` to it restores nothing.
                     # `nothing_captured` is the one that means the directory
                     # was not empty and the snapshot is anyway.
                     'empty': fc == 0,
                     'nothing_captured': bool(uncaptured),
                     'skipped': skipped})
    elif uncaptured:
        print('nothing captured: this directory has files in it and the '
              'snapshot has none.')
        print('       e.g. {}  ({})'.format(
            row(uncaptured.get('path', '')), row(uncaptured.get('reason', ''))))
        print('       {} exists but holds nothing, so `unedit back` to it '
              'restores'.format(snap_id))
        print('       nothing — and clears whatever is here now.')
    else:
        print('saved  {}  ({} files, {})'.format(snap_id, fc, sz))
        if args.message:
            print('       {}'.format(row(args.message)))
        for entry in skipped[:10]:
            # Named, not counted: "3 files skipped" is not something you can
            # act on, and the whole point is deciding whether it mattered.
            print('       not captured: {}  ({})'.format(
                row(entry.get('path', '')), row(entry.get('reason', ''))))
        if len(skipped) > 10:
            print('       ... and {} more not captured'.format(len(skipped) - 10))
        gitignore_hint = os.path.join(root, '.gitignore')
        if os.path.isfile(gitignore_hint):
            # Check if .unedit is already in .gitignore
            try:
                with open(gitignore_hint, encoding='utf-8',
                          errors='replace') as f:
                    content = f.read()
                if '.unedit' not in content:
                    print('hint: add .unedit/ to your .gitignore')
            except OSError:
                pass
        elif os.path.isdir(os.path.join(root, '.git')):
            print('hint: add .unedit/ to your .gitignore')
    if uncaptured:
        # 1, this tool's word for "the command did not do what you asked" — the
        # same code a restore returns when it puts back fewer files than it
        # planned to.  Not 2: nothing was mistyped, and the snapshot was
        # written.  A wrapper like `unedit save && npm test` should stop here,
        # because the thing it was saving against is not saved.
        return 1
    return 0


def cmd_list(args) -> int:
    root = os.path.abspath(args.dir)
    snaps, damaged = _store.scan_snapshots(root)

    if not snaps and not damaged:
        if args.json:
            _print_json([])
        else:
            print('no snapshots. run: unedit save')
        # An empty store is not a finding.  Exit 1 means "something to report",
        # and a script that branches on it should not see "nothing here yet"
        # and conclude that something went wrong.
        return 0

    if args.json:
        out = []
        # Damaged manifests are listed too, not omitted.  A wrapper reading
        # this would otherwise draw exactly the wrong conclusion a person
        # drew — an empty array where a snapshot is sitting on disk.  The id
        # is real (it is the filename), so `unedit show <id>` still works and
        # still explains itself; the fields that came from the file are null.
        for d in reversed(damaged):
            out.append({
                'id': d['id'],
                'timestamp': None,
                'message': None,
                'file_count': None,
                'total_size': None,
                'damaged': d['why'],
            })
        for s in reversed(snaps):
            out.append({
                'id': s['id'],
                'timestamp': s.get('timestamp', ''),
                'message': one_line(s.get('message', '')),
                'file_count': s.get('file_count', 0),
                'total_size': s.get('total_size', 0),
            })
        _print_json(out)
        if damaged:
            print(_store.describe_damage(damaged, root), file=sys.stderr)
            return 1
        return 0

    if damaged:
        print(_store.describe_damage(damaged, root), file=sys.stderr)

    # Human-readable table
    # newest first
    for s in reversed(snaps):
        ts = _store.fmt_time(s.get('timestamp', ''))
        msg = row(s.get('message', ''))
        fc = s.get('file_count', 0)
        sz = _store.fmt_size(s.get('total_size', 0))
        line = '{}  {}  {} files  {}'.format(s['id'], ts, fc, sz)
        if msg:
            line += '  — {}'.format(msg)
        print(line)
    # A store with something unreadable in it is a finding, whether or not the
    # readable ones listed fine.
    return 1 if damaged else 0


def cmd_show(args) -> int:
    root = os.path.abspath(args.dir)
    try:
        manifest, files = _store.show_snapshot(root, args.id)
    except RuntimeError as e:
        msg = str(e)
        # Same as `back` and `diff`: an empty store is a normal condition,
        # not a usage error.  Nothing was typed wrong — there is nothing
        # saved yet, and `unedit show || bail` has to read the same here as
        # it does on the other two.
        if msg == 'no snapshots found':
            return _failed(args, msg, code=1)
        return _failed(args, msg)
    except (OSError, ValueError) as e:
        # The manifest was there a moment ago when it was listed.  Something
        # else moved it, or it is not the JSON it claims to be.
        return _failed(args, 'could not read that snapshot: {}'.format(e))

    if args.json:
        _print_json({
            'id': manifest['id'],
            'timestamp': manifest.get('timestamp', ''),
            'message': manifest.get('message', ''),
            'files': files,
        })
        return 0

    # Both come out of the manifest on disk, so both are rows, not text:
    # a newline in the message printed the rest of itself above the file
    # list, in the same shape as the file list.
    print('snapshot: {}'.format(row(manifest['id'])))
    ts = _store.fmt_time(manifest.get('timestamp', ''))
    msg = row(manifest.get('message', ''))
    print('  when: {}{}'.format(ts, '  — ' + msg if msg else ''))
    print('  {} files'.format(manifest.get('file_count', len(files))))
    print('')
    for f in sorted(files, key=lambda x: x['path']):
        path = row(f['path'])
        if f['type'] == 'symlink':
            print('  {} -> {}'.format(path, row(f.get('target', ''))))
        else:
            sz = _store.fmt_size(f.get('size', 0))
            # The same clock as the `when:` row above these, which it was
            # not: that one showed seconds and this one did not, so a snapshot
            # read as having happened before the files it holds.
            shown = _store.fmt_mtime(f['mtime']) if 'mtime' in f else ''
            ts_str = '  ' + shown if shown else ''
            # Padded in cells, not characters: a CJK filename is drawn twice as
            # wide as it is long, and `ljust` would put the size column two
            # places right of where it is on every other row.
            print('  {}  {:>10s}{}'.format(pad(path, 40), sz, ts_str))
    return 0


def cmd_back(args) -> int:
    root = os.path.abspath(args.dir)

    lines = []
    def out(msg=''):
        if not args.json:
            print(msg)
        lines.append(msg)

    # --json implies non-interactive: skip the confirmation prompt so agents
    # that parse structured output do not see a prompt mixed into their JSON.
    yes = args.yes or args.json

    try:
        result = _store.restore(
            root=root,
            snap_id=args.id,
            yes=yes,
            hard=args.hard,
            force=args.force,
            print_fn=out,
        )
    except RuntimeError as e:
        msg = str(e)
        # "no snapshots found" is a normal empty-store condition, not a usage error.
        if msg == 'no snapshots found':
            return _failed(args, msg, code=1)
        return _failed(args, msg)
    except OSError as e:
        return _failed(args, 'restore failed: {}'.format(e))

    if args.json:
        _print_json(result)

    if result.get('aborted'):
        return 1

    # The store counts what it planned and what it managed separately, on
    # purpose.  A file can refuse to come back — a read-only directory, another
    # owner, a full disk — and it prints a warning when that happens.  But a
    # warning is for a human, and the person hitting undo is usually not
    # reading; `unedit back --yes && npm test` reads the exit code.  A tree that
    # was not put back is a failed command, whether it was one file or all.
    if result.get('restored', 0) < result.get('planned', 0):
        return 1
    return 0


def cmd_diff(args) -> int:
    root = os.path.abspath(args.dir)
    try:
        result = _store.diff_snapshot(root, args.id, patch=args.patch)
    except RuntimeError as e:
        msg = str(e)
        if msg == 'no snapshots found':
            return _failed(args, msg, code=1)
        return _failed(args, msg)

    added = result['added']
    modified = result['modified']
    removed = result['removed']
    snap_id = result['snapshot_id']

    if args.json:
        _print_json(result)
        return 0

    ts = _store.fmt_time(result.get('snapshot_timestamp', ''))
    msg = result.get('snapshot_message', '')
    header = 'diff vs {}  {}'.format(snap_id, ts)
    if msg:
        header += '  — {}'.format(row(msg))
    print(header)
    print('')

    if not added and not modified and not removed:
        print('no changes')
        return 0

    if added:
        print('added ({})'.format(len(added)))
        for f in added:
            sz = _store.fmt_size(f.get('size', 0)) if f['type'] == 'file' else 'symlink'
            print('  + {}  ({})'.format(row(f['path']), sz))

    if modified:
        print('modified ({})'.format(len(modified)))
        for f in modified:
            if f['type'] == 'symlink':
                print('  ~ {}  (symlink: {} -> {})'.format(
                    row(f['path']),
                    row(f.get('old_target', '?')),
                    row(f.get('new_target', '?'))))
            else:
                old = _store.fmt_size(f.get('old_size', 0))
                new = _store.fmt_size(f.get('new_size', 0))
                print('  ~ {}  ({} -> {})'.format(row(f['path']), old, new))

    if removed:
        print('removed ({})'.format(len(removed)))
        for f in removed:
            sz = _store.fmt_size(f.get('size', 0)) if f['type'] == 'file' else 'symlink'
            print('  - {}  ({})'.format(row(f['path']), sz))

    if args.patch and 'patch' in result and result['patch']:
        print('')
        print(block(result['patch']))

    return 0


def cmd_drop(args) -> int:
    root = os.path.abspath(args.dir)

    if not args.all and not args.ids:
        return _failed(args, 'specify a snapshot ID or --all')

    try:
        result = _store.drop_snapshots(root, args.ids or [], all_snaps=args.all)
    except RuntimeError as e:
        return _failed(args, str(e))

    n = result['dropped']
    gc = result['gc_objects']
    if args.json:
        _print_json(result)
    else:
        print('dropped {} snapshot{}.  {} object{} removed from store.'.format(
            n, 's' if n != 1 else '',
            gc, 's' if gc != 1 else '',
        ))
    return 0


def cmd_where(args) -> int:
    root = os.path.abspath(args.dir)
    info = _store.where_info(root)
    if args.json:
        _print_json(info)
    else:
        print(info['store_dir'])
        print('{} snapshot{}, {}'.format(
            info['snap_count'],
            's' if info['snap_count'] != 1 else '',
            info['total_size'],
        ))
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='unedit',
        description='A safety net for letting an agent loose on your files.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'examples:\n'
            '  unedit save -m "before agent refactor"\n'
            '  unedit list\n'
            '  unedit back\n'
            '  unedit diff\n'
            '  unedit where\n'
        ),
    )
    p.add_argument(
        '--version',
        action='version',
        version='unedit {}'.format(__version__),
    )
    # Two spellings of one flag, worded in where.py so all three commands
    # that take a directory word it the same way.  `--dir` is the older name
    # and keeps working; it goes second because argparse puts the first name
    # in the usage line, and that line is the whole of the help most people
    # read.
    add_project_flag(p, '.', '--dir', dest='dir')

    # The same pair again, accepted after the subcommand.  `unedit save --dir
    # build` used to be "unrecognized arguments", which reads as a flag that
    # does not exist rather than one written a word too late.  SUPPRESS is what
    # keeps an unnamed flag here from overwriting one given before the
    # subcommand with the default.
    common = argparse.ArgumentParser(add_help=False)
    add_project_flag(common, argparse.SUPPRESS, '--dir', dest='dir')

    sub = p.add_subparsers(dest='command', metavar='COMMAND')
    sub.required = True

    def add_json(sp):
        sp.add_argument('--json', action='store_true', help='output JSON')

    # save
    ps = sub.add_parser('save', parents=[common], help='snapshot the current directory tree')
    ps.add_argument('-m', '--message', metavar='MSG', default='', help='short description')
    ps.add_argument('--force', action='store_true',
                    help='override size/file-count guard rails')
    add_json(ps)

    # list
    pl = sub.add_parser('list', parents=[common], help='show all snapshots')
    add_json(pl)

    # show
    psh = sub.add_parser('show', parents=[common], help='list files captured in a snapshot')
    psh.add_argument('id', nargs='?', metavar='ID', help='snapshot ID (default: newest)')
    add_json(psh)

    # back
    pb = sub.add_parser('back', parents=[common], help='restore a snapshot')
    pb.add_argument('id', nargs='?', metavar='ID', help='snapshot ID (default: newest)')
    pb.add_argument('--yes', '-y', action='store_true', help='skip confirmation prompt')
    pb.add_argument('--hard', action='store_true',
                    help='delete new files instead of moving them aside')
    pb.add_argument('--force', action='store_true',
                    help='override guard rails for the auto-save step')
    add_json(pb)

    # diff
    pd = sub.add_parser('diff', parents=[common], help='what changed since a snapshot')
    pd.add_argument('id', nargs='?', metavar='ID', help='snapshot ID (default: newest)')
    pd.add_argument('--patch', action='store_true', help='include unified diff for changed files')
    add_json(pd)

    # drop
    pdr = sub.add_parser('drop', parents=[common], help='delete snapshots')
    pdr.add_argument('ids', nargs='*', metavar='ID', help='snapshot IDs to drop')
    pdr.add_argument('--all', action='store_true', help='drop all snapshots')
    add_json(pdr)

    # where
    pw = sub.add_parser('where', parents=[common], help='print snapshot directory and disk usage')
    add_json(pw)

    return p



def main(argv=None) -> int:
    """Entry point.  Returns the code the process should exit with.

    Snapshotting a large tree takes a moment, and interrupting a command that
    is taking longer than you expected is ordinary.  A traceback in reply reads
    especially badly out of this tool: people reach for it when something has
    already gone wrong, so a crash from the safety net is the last thing they
    need to see.  Answering an interrupted run with 130 also keeps `unedit save
    && rm -rf build` from deleting anything on the strength of a snapshot that
    was never finished.

    A closed pipe matters as much.  `unedit diff | head` and `unedit list |
    less` quit with `q` are ordinary, and unhandled they printed `Exception
    ignored in: <_io.TextIOWrapper ...>` over the output, or a whole traceback
    out of `diff` — the last thing a safety net should ever show you.  A
    listing that got cut off told you nothing about your snapshots, so it must
    not come back as one of the answers either.

    Both of those are `shell.run_as_a_command`, which is where the mechanism
    lives and where the codes are named.  What is here is the reason this tool
    in particular cannot afford to get them wrong.
    """
    return run_as_a_command(_run, argv)



def _run(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Propagate --json and --dir to subcommands
    # (argparse places them on the root namespace, shared with subcommands)

    cmd_map = {
        'save': cmd_save,
        'list': cmd_list,
        'show': cmd_show,
        'back': cmd_back,
        'diff': cmd_diff,
        'drop': cmd_drop,
        'where': cmd_where,
    }

    fn = cmd_map.get(args.command)
    if fn is None:
        parser.print_help()
        sys.exit(2)

    # A project root that does not exist is a typo, not a request to create
    # one.  Every command starts by joining `.unedit` onto this path, so the
    # old behaviour was to build the whole missing tree and snapshot the empty
    # directory it had just made — reported as a success.  Where the path was
    # not writable it failed instead, naming the topmost missing component
    # (`Permission denied: '/no'`), which is not a path anybody typed.
    #
    # Only a directory somebody named can be wrong this way; the default is the
    # current directory, which exists by definition.
    if not os.path.isdir(args.dir):
        what = 'not a directory' if os.path.exists(args.dir) else 'no such directory'
        # Through `_failed` like the rest: a mistyped `--project` is the most
        # likely way a script gets stopped here, and it is the one that used to
        # leave `--json` with nothing on stdout to parse.
        sys.exit(_failed(args, '{}: {}'.format(what, args.dir)))

    sys.exit(fn(args))


if __name__ == '__main__':
    main()
