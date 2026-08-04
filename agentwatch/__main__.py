"""Allow `python3 -m agentwatch` from a checkout, with no install."""

import sys

from .cli import main

sys.exit(main())
