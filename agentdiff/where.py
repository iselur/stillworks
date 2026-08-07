"""Which directory to work in.

Three of these commands are pointed at a directory.  `stillworks`, `agentdiff`
and `unedit` all do their work inside one, and all three take a flag saying
which.  Three declarations, and the one line of help everybody reads said three
different things:

    usage: stillworks [-h] [--version] [--project PROJECT] ...
    usage: agentdiff [-h] [--version] [--project DIR] COMMAND ...
    usage: unedit [-h] [--version] [--dir DIR] COMMAND ...

The first two are the same flag.  One asks for a DIR and the other for a
PROJECT, and a PROJECT is a thing you have a name for.  That reading is not a
stretch here, because two *other* commands in this family — `agentlog` and
`agentwatch` — really do take `--project NAME`, where a name is exactly the
right thing to type.  Somebody who met those first and then types

    stillworks --project relay

is answering the question the usage line asked them, and gets told there is no
such directory.

The third does not mention `--project` at all.  It accepts it — `--dir` is the
older spelling and the alias was added so the family would agree — but argparse
puts the *first* name in the usage line and `--project` was written second.  So
the one command whose whole job is to stand outside your project and snapshot it
is the one whose usage line never says the family's word for which project.

Nobody decided any of that.  Each command declared the flag where it needed it,
twice over — once before the subcommand and once after — and six copies of one
fact is five chances to say it differently.

So the flag is declared here, once, and all three add it by calling this.  What
goes after it, the sentence beneath it, and which spelling is the one shown are
now single facts, and there is no second place left to forget.

It has to stay copied: nothing in this family imports outside its own package —
the promise `pip install stillworks` makes, enforced by
`test_every_import_is_stdlib_or_the_packages_own` — so a shared module is not on
offer.  What is on offer is a copy that cannot drift, pinned byte-for-byte by
`test_the_directory_flag_is_one_flag_in_all_three.py` in the stillworks tree.
"""

from __future__ import annotations

#: What goes after the flag.  `DIR` and not `PROJECT`: it takes a path, and
#: `PROJECT` is what the two commands that take a *name* would rightly say.
_PLACEHOLDER = "DIR"

#: The one sentence all three print beneath it.
_WHAT_IT_TAKES = "project directory (default: current directory)"


def add_project_flag(parser, default, *older_spellings, dest=None):
    """Give ``parser`` the flag that says which directory to work in.

    ``default`` is what the flag is worth when nobody passes it.  It is asked
    for rather than assumed because each of these commands declares the flag
    twice: once on the top-level parser, where the default is the current
    directory, and once on the parser the subcommands share, where it has to be
    ``argparse.SUPPRESS``.  Anything else there is a default nobody typed
    overwriting a directory somebody did — `agentdiff review --json` after
    `agentdiff --project ../app` would quietly review the wrong tree.

    ``older_spellings`` are names a command used to have and still answers to.
    They come after ``--project``, never before: argparse shows the first name
    in the usage line, and that line is the whole of the help most people read.
    A command whose usage line advertises the spelling it has moved away from
    teaches it to everybody who arrives after the move.

    ``dest`` is for a command whose own code already reads the value under
    another name.  Nothing anybody types depends on it.
    """
    options = {"metavar": _PLACEHOLDER, "default": default,
               "help": _WHAT_IT_TAKES}
    if dest is not None:
        options["dest"] = dest
    parser.add_argument("--project", *older_spellings, **options)
