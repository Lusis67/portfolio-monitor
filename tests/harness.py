"""The whole test runner. There isn't one — these are plain scripts.

Each test file prints a line per check and exits non-zero on the first
failure, so `python tests/test_x.py` is the entire contract.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_count = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _count
    _count += 1
    if condition:
        print(f"  ok   {label}")
        return
    print(f"  FAIL {label}" + (f"\n       {detail}" if detail else ""))
    sys.exit(1)


def equal(label: str, got, want, tolerance: float = 0.0) -> None:
    if isinstance(got, float) or isinstance(want, float):
        ok = got is not None and abs(got - want) <= tolerance
    else:
        ok = got == want
    check(label, ok, f"got {got!r}, wanted {want!r}")


def heading(text: str) -> None:
    print(text)


def done() -> None:
    print(f"  {_count} checks passed")
