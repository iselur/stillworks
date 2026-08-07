"""What markdown reads rather than shows, kept out of the report.

`terminal.py` exists because a screen obeys some of the text sent to it instead
of drawing it.  A markdown document has the same problem with a different list
of characters, and the report this tool writes is made almost entirely of
strings somebody else chose: the paths in a diff were put there by whoever
changed the tree, and an agent that names a file
``test_*prod*_[click here](somewhere-else).py`` gets that name copied into the
evidence document unaltered.

Rendered, it stops being the name of a file.  ``*prod*`` comes out in italics,
so the path a reviewer copies out of the report is not a path on disk; and the
bracketed half comes out as a link, so a filename has put a clickable
destination into a document that is supposed to be a record of what changed.  Neither shows up in the terminal view, which is why this was missed:
the two views were written from the same line of code and only one of the two
mediums was thought about.

Two functions, because the report says two kinds of thing:

``as_a_path``
    something the reader has to go and find -- a filename, or a filename and a
    line.  It goes in a code span, which is markdown's own answer: inside one,
    nothing at all is read as syntax, and what comes out is selectable as the
    single word it was.

``as_prose``
    a sentence the reader has to read -- a rule's reason.  A code span would be
    wrong (it is a sentence, not a name), so the characters markdown acts on
    are escaped one by one.

Both go through `terminal.quoted` first.  A control character in a filename is
not markdown's problem but it is still a problem here: a newline in a path ends
the list item and the rest of the name becomes a paragraph of the document.
Doing it inside these two functions rather than at the call sites is the point
of the module -- there is no way to ask for the markdown-safe form and get only
half of it.
"""

from __future__ import annotations

from .terminal import quoted

#: The ASCII punctuation that changes how the text around it renders.  Not the
#: whole of CommonMark's escapable set: `\.` and `\!` render as themselves and
#: escaping them turns an ordinary sentence into a thicket of backslashes,
#: which is its own kind of unreadable.  These are the ones that do something.
#:
#: The backslash is in the set, and `as_prose` reads the text once rather than
#: replacing one character at a time -- the backslashes it adds for `*` are
#: never looked at again, so they are never escaped a second time.
_ACTS_ON = "\\`*_[]<>&~|"


def as_a_path(text) -> str:
    """A name the reader has to go and find, as a markdown code span.

    The fence is as many backticks as it takes: a name containing one backtick
    is fenced with two, and so on, which is how markdown says a code span holds
    a backtick.  When the text begins or ends with one, a space goes inside the
    fence at each end -- a pair markdown drops again when it renders -- because
    otherwise the fence and the content run together and the reader is shown
    one backtick fewer than the file has.
    """
    text = quoted(text)
    longest = 0
    run = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return "{0}{1}{2}{1}{0}".format(fence, pad, text)


def as_prose(text) -> str:
    """A sentence the reader has to read, with markdown's characters defused.

    Escaped rather than fenced: a reason is prose, and prose in a code span is
    a sentence in a typewriter box that no longer wraps.
    """
    text = quoted(text)
    out = []
    for char in text:
        if char in _ACTS_ON:
            out.append("\\")
        out.append(char)
    return "".join(out)
