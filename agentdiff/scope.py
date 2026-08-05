"""
Where a repository's declared scope lives, and what may be stored in it.

``agentdiff scope src/**`` writes a file; every later ``agentdiff review``
reads it and checks the diff against it.  Between those two moments the globs
are a file format, and the format used to be written down twice: once in the
reader, which strips each line and drops the ones that are comments, and once
as a list of things the writer refused.  The two lists were not the same list,
and the gap had exactly the shape the writer's guards were there to prevent:

    $ agentdiff scope '#urgent/**'
    scope saved: #urgent/**
      stored in /repo/.agentdiff/scope
    $ agentdiff review
    clean: 2 file(s) changed, nothing flagged

The scope was saved.  It read back as a comment, so no scope was declared, so
the rule that checks the scope did not run — and a review with no scope is
green on every file in the repository.  A newline in a glob was already refused
because it silently widens the scope; a ``#`` widens it just as silently,
through a door nobody had thought to close.

So the format lives here once, and the writer does not carry a second copy of
it.  It writes what it is about to write, reads it back through the same parser
the review will use, and refuses anything that does not come back unchanged.
The promise this module makes is one sentence:

    read(root) returns exactly what write(root, globs) was given, in order.

It is kept by construction rather than by two lists happening to agree.  If the
format ever changes, the check changes with it and only the wording of a
refusal can go stale — which prints a vaguer sentence, rather than storing a
scope that is not the scope somebody asked for.

``ignore`` is the same format in the same directory read by the same rules, so
it is read here too.  Nothing writes it: it is a file people keep by hand, and
a file kept by hand is the reason reading is not allowed to fail.  An
unreadable, missing, mis-encoded, or not-even-a-file config says nothing about
the diff, and refusing to review because of one would be this tool declining to
do its job over somebody else's directory.
"""

import os

from .shell import as_typed

DIR_NAME = ".agentdiff"

# How bytes become text here, at both ends.  A config file is UTF-8 whatever
# the machine's locale claims, because the repository is shared and the locale
# is not; and a byte that is not UTF-8 is kept as the byte it was rather than
# replaced, so it comes back out as what went in.  That is the same answer
# `shell` already gives for `argv` and for the output streams, for the same
# reason -- the bytes were never wrong, only the codec the locale picked.
_ENCODING = {"encoding": "utf-8", "errors": "surrogateescape"}


class ScopeError(ValueError):
    """A glob that cannot be stored, carrying the sentence to print.

    The caller prints ``str(exc)`` and exits 2.  It does not have to know which
    rule was broken, because the message already says which and why.
    """


def path(root, name="scope"):
    """Where ``name`` lives for the repository at ``root``.

    Callers name a repository, not a config directory: where ``.agentdiff``
    sits inside a checkout is this module's business, and a caller that works
    it out has to be told when it moves.
    """
    return os.path.join(root, DIR_NAME, name)


def _parse(text):
    """The patterns in a config file: one per line, blanks and comments out.

    The one place the format is stated.  ``#`` is tested after stripping, so a
    hand-edited file with an indented comment in it means what it looks like it
    means.
    """
    patterns = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            patterns.append(stripped)
    return patterns


def _read(config_path):
    """The patterns in the file at ``config_path``, or none if it cannot be read.

    Never raises, and never blocks.  ``ignore`` is kept by hand and ``scope``
    can be, so this reads a directory, a missing file and a file full of
    somebody else's bytes as "nothing declared" — refusing to review because of
    one would be this tool declining to do its job over a file it does not own.

    Only a regular file is opened.  ``except OSError`` alone would answer the
    missing file and the directory just as well; what it cannot answer is a
    named pipe, which ``open`` waits on for a writer that never comes.  A
    review that hangs is worse than one that crashes — there is no output to
    read afterwards saying why.
    """
    if not os.path.isfile(config_path):
        return []
    try:
        with open(config_path, **_ENCODING) as fh:
            text = fh.read()
    except OSError:
        return []
    return _parse(text)


def read(root):
    """The scope globs declared for the repository at ``root``, or ``[]``."""
    return _read(path(root, "scope"))


def read_ignore(root):
    """The ignore patterns declared for the repository at ``root``, or ``[]``."""
    return _read(path(root, "ignore"))


def _explain(glob):
    """Why this glob will not read back as itself, in words.

    Wording only.  Whether a glob may be stored is decided by the round trip in
    ``_refusal`` — these branches exist so the person is told which character
    to take out, and the last line is what is said about a case nobody has
    thought of yet.
    """
    if not glob.strip():
        return "an empty scope glob matches nothing — give a pattern"
    if "\n" in glob or "\r" in glob:
        # It would be stored as two globs, and the second one is usually `**`.
        return "a scope glob cannot contain a newline: {!r}".format(glob)
    if glob.strip().startswith("#"):
        return ("a scope glob cannot start with '#' — it would be read back as "
                "a comment, and a review with no scope flags nothing: "
                "{!r}".format(glob))
    if glob != glob.strip():
        return ("a scope glob cannot begin or end with a space — it would be "
                "read back without it: {!r}".format(glob))
    return "a scope glob that cannot be stored as written: {!r}".format(glob)


def _refusal(glob):
    """The sentence refusing this glob, or None if it may be stored.

    One rule: write it, read it back, and see whether it is still the thing
    that was asked for.
    """
    if _parse(glob + "\n") == [glob]:
        return None
    return _explain(glob)


def write(root, globs):
    """Store ``globs`` as the scope for ``root``.  Returns them as stored.

    Every glob is checked before any of them is written, so a bad one leaves
    the scope that was already there alone rather than half-replacing it.

    What comes back is what ``read`` will give back, which is not always what
    was passed in: an argument the locale mis-decoded is repaired first.  A
    caller confirming the save prints this rather than its own argument, so the
    confirmation describes the file instead of the command line.

    Raises ``ScopeError`` for a glob that would not read back as itself; its
    message is the sentence to print.  Raises ``OSError`` if the file cannot be
    written, which is the caller's to phrase — it already knows how it says
    "could not save".
    """
    # `as_typed` first, because a machine with no locale hands argparse a run
    # of surrogates rather than the word somebody typed, and every check below
    # is about the glob they typed.
    globs = [as_typed(g) for g in globs]
    for glob in globs:
        refusal = _refusal(glob)
        if refusal is not None:
            raise ScopeError(refusal)

    scope_path = path(root, "scope")
    os.makedirs(os.path.dirname(scope_path), exist_ok=True)
    # The same encoding the reader uses, spelled once above.  Left to the
    # locale this wrote ASCII, and `agentdiff scope 'café/**'` on a machine
    # with no locale raised UnicodeEncodeError from inside a command that had
    # already accepted its arguments and printed nothing.
    with open(scope_path, "w", **_ENCODING) as fh:
        for glob in globs:
            fh.write(glob + "\n")
    return globs
