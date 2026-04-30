#!/usr/bin/env python3
"""Entry shim so you can run `python build_library.py …` from the project root."""
from dcs2hc.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
