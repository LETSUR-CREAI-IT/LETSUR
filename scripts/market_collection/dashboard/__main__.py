"""python -m scripts.market_collection.dashboard --root data/market_collection --output dashboard-site  (see docs/dashboard.md)"""

from __future__ import annotations

import sys

from scripts.market_collection.dashboard.build import main

if __name__ == "__main__":
    sys.exit(main())
