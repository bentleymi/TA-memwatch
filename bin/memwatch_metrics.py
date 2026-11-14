#!/usr/bin/env python3
"""Emit flat numeric metrics for memory/swap/OOM/slab analytics."""
import sys

from memwatch_common import discover, emit, metrics_payload


def main():
    try:
        data = discover()
    except Exception:
        data = {}
    emit(metrics_payload(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
