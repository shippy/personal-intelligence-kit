#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Email Ingest — mbsync + notmuch

Calls mbsync to sync IMAP mailboxes, then notmuch to index.

Usage:
    uv run ingest.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import load_config, source_enabled, email_path  # noqa: E402


def run(cmd: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return result.returncode, (result.stdout + result.stderr)
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"


def main():
    load_config()
    if not source_enabled("email"):
        print("Email source not enabled in vault.toml. Skipping.")
        return 0

    epath = email_path()
    if not epath or not epath.exists():
        print(f"Email path {epath} does not exist. Run setup first (see SETUP.md).")
        return 1

    start = time.time()
    print("Running mbsync -a ...")
    rc, out = run(["mbsync", "-a"])
    if rc != 0:
        print(f"mbsync failed ({rc}):")
        print(out[:2000])
        return rc

    print("Running notmuch new ...")
    rc, out = run(["notmuch", "new"])
    if rc != 0:
        print(f"notmuch new failed ({rc}):")
        print(out[:2000])
        return rc

    print(out.strip())
    print(f"Done in {time.time() - start:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
