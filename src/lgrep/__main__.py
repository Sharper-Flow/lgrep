"""Run lgrep as a module: python -m lgrep"""

import sys

from lgrep.cli import main

if __name__ == "__main__":
    sys.exit(main())
