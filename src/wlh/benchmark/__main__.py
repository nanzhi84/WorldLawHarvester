"""Direct execution module entrypoint: python -m wlh.benchmark."""

from __future__ import annotations

import sys

from wlh.benchmark.cli import main

if __name__ == "__main__":
    sys.exit(main())
