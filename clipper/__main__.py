"""Allow ``python -m clipper``."""

import sys

from .cli import main

sys.exit(main())
