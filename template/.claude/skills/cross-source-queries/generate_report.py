#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Cross-Source Report Generator

Runs all three analyses (intention-reality, commitments, convergence)
and produces a combined report.

Usage:
    uv run generate_report.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import load_config  # noqa: E402

from intention_reality_gaps import generate_report as intention_report
from commitment_accountability import generate_report as commitment_report
from serendipity_convergence import generate_report as convergence_report


def main():
    load_config()
    print("=" * 60)
    print("CROSS-SOURCE QUERIES")
    print("=" * 60)

    print("\n=== 1. Intention ↔ Reality Gaps ===\n")
    p1 = intention_report()

    print("\n=== 2. Commitment Accountability ===\n")
    p2 = commitment_report()

    print("\n=== 3. Serendipity & Convergence ===\n")
    p3 = convergence_report()

    print("\n" + "=" * 60)
    print("Reports generated:")
    for p in [p1, p2, p3]:
        if p:
            print(f"  {p}")


if __name__ == "__main__":
    main()
