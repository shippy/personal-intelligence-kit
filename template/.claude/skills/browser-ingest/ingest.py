#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Browser History Ingest

Copies the live browser History SQLite file to a clean snapshot in
data/browser-history.db, filtering out noise.

Usage:
    uv run ingest.py              # Last 30 days
    uv run ingest.py --days 90
    uv run ingest.py --all
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import (  # noqa: E402
    load_config,
    source_enabled,
    browser_history_path,
    browser_type,
    vault_root,
    activity_log,
)


NOISE_DOMAINS = {
    "google.com", "www.google.com", "duckduckgo.com", "bing.com",
    "www.bing.com", "search.brave.com",
}
NOISE_PATH_MARKERS = ("/search?", "/oauth", "/login", "/auth/", "/sso/")


def is_noise(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return True
    if parsed.scheme not in ("http", "https"):
        return True
    if parsed.hostname in NOISE_DOMAINS and "/search" in parsed.path:
        return True
    if any(m in url for m in NOISE_PATH_MARKERS):
        return True
    return False


def chrome_time_to_datetime(chrome_ts: int) -> datetime:
    """Chrome stores time as microseconds since 1601-01-01."""
    if chrome_ts == 0:
        return datetime.fromtimestamp(0)
    return datetime.fromtimestamp(chrome_ts / 1_000_000 - 11644473600)


def main():
    parser = argparse.ArgumentParser(description="Browser history ingest")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    load_config()
    if not source_enabled("browser"):
        print("Browser source not enabled in vault.toml.")
        return 1

    src = browser_history_path()
    if not src or not src.exists():
        print(f"Browser history file not found: {src}")
        print(f"Browser type from config: {browser_type()}")
        print("On macOS you may need Full Disk Access for your terminal.")
        return 1

    # Copy live DB (it's locked while browser runs)
    tmp = Path("/tmp/pik_browser_ingest.db")
    try:
        shutil.copy2(src, tmp)
    except Exception as e:
        print(f"Failed to copy {src}: {e}")
        return 1

    # Read from copy
    src_conn = sqlite3.connect(tmp)
    src_conn.row_factory = sqlite3.Row

    if args.all:
        cursor = src_conn.execute(
            "SELECT url, title, visit_count, last_visit_time FROM urls"
        )
    else:
        cutoff_chrome = int(
            (datetime.now() - timedelta(days=args.days)).timestamp() * 1_000_000
            + 11644473600 * 1_000_000
        )
        cursor = src_conn.execute(
            "SELECT url, title, visit_count, last_visit_time FROM urls "
            "WHERE last_visit_time > ? ORDER BY last_visit_time DESC",
            (cutoff_chrome,),
        )

    # Prepare output DB
    out_path = vault_root() / "data" / "browser-history.db"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    out_conn = sqlite3.connect(out_path)
    out_conn.execute("""
        CREATE TABLE visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT,
            visit_count INTEGER,
            last_visit_time TIMESTAMP,
            domain TEXT
        )
    """)
    out_conn.execute("CREATE INDEX idx_visits_domain ON visits(domain)")
    out_conn.execute("CREATE INDEX idx_visits_time ON visits(last_visit_time)")

    imported = 0
    skipped = 0
    for row in cursor:
        url = row["url"]
        if is_noise(url):
            skipped += 1
            continue
        domain = urlparse(url).hostname or ""
        dt = chrome_time_to_datetime(row["last_visit_time"])
        out_conn.execute(
            "INSERT INTO visits (url, title, visit_count, last_visit_time, domain) "
            "VALUES (?, ?, ?, ?, ?)",
            (url, row["title"], row["visit_count"], dt.isoformat(), domain),
        )
        imported += 1

    out_conn.commit()
    out_conn.close()
    src_conn.close()
    tmp.unlink(missing_ok=True)

    print(f"Imported {imported} visits ({skipped} noise filtered) → {out_path}")

    # Log
    log = activity_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(f"\n## {datetime.now().strftime('%Y-%m-%dT%H:%M')}\n\n")
        f.write(f"**Action:** Browser history ingest ({browser_type()})\n")
        f.write(f"**Imported:** {imported}, **Filtered:** {skipped}\n")
        f.write(f"**Output:** `data/browser-history.db`\n\n---\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
