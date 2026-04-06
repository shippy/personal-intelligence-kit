#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Email Query Wrapper

Thin wrapper over `notmuch search`/`notmuch show` with filters for
human-readable messages (vs bulk/newsletter noise).

Usage:
    uv run query.py "date:7d.. tag:sent"
    uv run query.py --recent 20
    uv run query.py --from "alice@example.com"
    uv run query.py --human date:7d..
    uv run query.py --stats
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import load_config, source_enabled  # noqa: E402


BULK_SENDERS = (
    "noreply@", "no-reply@", "notifications@", "updates@", "newsletter@",
    "marketing@", "mailer-daemon@", "bounce@", "info@",
)
BULK_MARKERS = ("unsubscribe", "view in browser", "you are receiving this")


def notmuch(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["notmuch", *args], capture_output=True, text=True, check=False
        )
        return result.stdout
    except FileNotFoundError:
        print("notmuch not installed", file=sys.stderr)
        sys.exit(127)


def search(query: str, limit: int = 50) -> list[dict]:
    """Return list of message summaries matching query."""
    output = notmuch([
        "search", "--format=json", f"--limit={limit}", query
    ])
    import json
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return []


def is_human(message_id: str) -> bool:
    """Check if a message is human-written (not bulk)."""
    hdrs = notmuch(["show", "--format=text", "--body=false", message_id])
    for line in hdrs.splitlines()[:20]:
        if line.lower().startswith("from:"):
            sender = line.split(":", 1)[1].lower()
            if any(b in sender for b in BULK_SENDERS):
                return False
    body = notmuch(["show", "--format=text", "--body=true", message_id])[:3000].lower()
    return not any(m in body for m in BULK_MARKERS)


def cmd_stats():
    print("Email volume stats:")
    for period in ("date:1d..", "date:7d..", "date:30d..", "date:365d.."):
        count = notmuch(["count", period]).strip()
        print(f"  {period:20s} → {count}")
    for tag in ("inbox", "sent", "unread", "flagged"):
        count = notmuch(["count", f"tag:{tag}"]).strip()
        print(f"  tag:{tag:15s} → {count}")


def cmd_search(query: str, human: bool, limit: int):
    results = search(query, limit=limit)
    if not results:
        print("(no results)")
        return
    shown = 0
    for msg in results:
        mid = msg.get("thread", "")
        if human and not is_human(f"thread:{mid}"):
            continue
        print(f"- **{msg.get('subject', '(no subject)')}**")
        print(f"  from: {msg.get('authors', '')}")
        print(f"  date: {msg.get('date_relative', '')}")
        print()
        shown += 1
    print(f"({shown} of {len(results)} shown)")


def main():
    load_config()
    if not source_enabled("email"):
        print("Email source not enabled in vault.toml.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Email query wrapper")
    parser.add_argument("query", nargs="?", default="", help="notmuch query string")
    parser.add_argument("--recent", type=int, help="show N most recent messages")
    parser.add_argument("--from", dest="from_addr", help="filter by sender")
    parser.add_argument("--human", action="store_true", help="filter out bulk senders")
    parser.add_argument("--stats", action="store_true", help="show volume stats")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    if args.stats:
        cmd_stats()
        return

    query = args.query
    if args.recent:
        query = "*"
        args.limit = args.recent
    if args.from_addr:
        query = f"from:{args.from_addr} {query}".strip()

    if not query:
        parser.print_help()
        return

    cmd_search(query, args.human, args.limit)


if __name__ == "__main__":
    main()
