#!/usr/bin/env python3
"""Emit one JSON event per cycle: memory, swap, OOM and top-slab snapshot."""
import sys

from memwatch_common import discover, emit


def main():
    try:
        data = discover()
    except Exception:
        data = {"status": "absent"}
    emit(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
