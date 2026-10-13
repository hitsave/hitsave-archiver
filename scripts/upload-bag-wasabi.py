#!/usr/bin/env python3
"""CLI entrypoint for Wasabi BagIt upload (see upload_bag_wasabi.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from upload_bag_wasabi import main  # noqa: E402

if __name__ == "__main__":
    main()
