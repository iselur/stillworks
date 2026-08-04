"""Allow `python3 -m unedit` without installing (stdlib-only, so a checkout works)."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
