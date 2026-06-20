#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Session Analyzer

Reads browser history from data/browser-history.db (produced by browser-ingest)
and computes health metrics per day / domain cluster. A simplified analysis
that works across all Chromium browsers.

Usage:
    uv run analyze.py
    uv run analyze.py --dormant-days 14
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import (  # noqa: E402
    load_config,
    source_enabled,
    vault_root,
    output_dir,
    activity_log,
)
import okf  # noqa: E402


def write_session_docs(active, dormant, cluster_stats, date, dormant_days):
    terse_lines = [
        f"# Session Health — {date}",
        "",
        "## Summary",
        "",
        f"- 🟢 **{len(active)}** active domain clusters",
        f"- 🔴 **{len(dormant)}** dormant (no activity in {dormant_days} days)",
        "",
    ]
    if dormant[:5]:
        terse_lines.extend(["## Top Dormant Clusters", ""])
        for domain, stats in dormant[:5]:
            days = (datetime.now() - stats["last_visit"]).days if stats["last_visit"] else "?"
            terse_lines.append(f"- 🔴 **{domain}** — {stats['count']} visits, last {days}d ago")
        terse_lines.append("")
    if active[:5]:
        terse_lines.extend(["## Top Active Clusters", ""])
        for domain, stats in active[:5]:
            terse_lines.append(f"- 🟢 **{domain}** — {stats['count']} visits")
    terse_body = "\n".join(terse_lines) + "\n"

    dense_lines = [
        f"# Session Health Report — {date}",
        "",
        "## All Clusters",
        "",
        "| Domain | Visits | Last Seen | Status |",
        "|--------|--------|-----------|--------|",
    ]
    for domain, stats in sorted(cluster_stats.items(), key=lambda x: x[1]["count"], reverse=True)[:50]:
        if stats["last_visit"]:
            days = (datetime.now() - stats["last_visit"]).days
            status = "🔴 dormant" if days > dormant_days else "🟢 active"
            last_seen = stats["last_visit"].strftime("%Y-%m-%d")
        else:
            status, last_seen = "?", "?"
        dense_lines.append(f"| {domain} | {stats['count']} | {last_seen} | {status} |")
    dense_body = "\n".join(dense_lines) + "\n"

    terse_path = okf.write_concept(
        "alerts", f"session-health-{date}", type="alert",
        title=f"Session Health — {date}",
        description=f"{len(active)} active, {len(dormant)} dormant domain clusters",
        body=terse_body, timestamp=date, status="final", sources=["browser"], tags=["session-health"],
    )
    dense_path = okf.write_concept(
        "reports", f"session-health-{date}", type="report",
        title=f"Session Health Report — {date}",
        body=dense_body, timestamp=date, status="final", sources=["browser"], tags=["session-health"],
    )
    return [terse_path, dense_path]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dormant-days", type=int, default=14)
    args = parser.parse_args()

    load_config()
    if not source_enabled("browser"):
        print("Browser source not enabled.")
        return 1

    db = vault_root() / "data" / "browser-history.db"
    if not db.exists():
        print("No browser history data found. Run browser-ingest first.")
        return 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    # Domain clusters → treat as "workspaces"
    cluster_stats = defaultdict(lambda: {"count": 0, "last_visit": None, "titles": []})
    for row in conn.execute(
        "SELECT domain, title, last_visit_time FROM visits "
        "WHERE last_visit_time IS NOT NULL"
    ):
        domain = row["domain"] or "(unknown)"
        cluster_stats[domain]["count"] += 1
        try:
            ts = datetime.fromisoformat(row["last_visit_time"])
        except (TypeError, ValueError):
            continue
        if (cluster_stats[domain]["last_visit"] is None
                or ts > cluster_stats[domain]["last_visit"]):
            cluster_stats[domain]["last_visit"] = ts
        if row["title"] and len(cluster_stats[domain]["titles"]) < 5:
            cluster_stats[domain]["titles"].append(row["title"])

    # Top active, dormant, bloated
    now = datetime.now()
    dormant_cutoff = now - timedelta(days=args.dormant_days)

    active = []
    dormant = []
    for domain, stats in cluster_stats.items():
        if stats["count"] < 3:
            continue  # ignore one-offs
        if stats["last_visit"] and stats["last_visit"] < dormant_cutoff:
            dormant.append((domain, stats))
        else:
            active.append((domain, stats))

    active.sort(key=lambda x: x[1]["count"], reverse=True)
    dormant.sort(key=lambda x: x[1]["count"], reverse=True)

    date = now.strftime("%Y-%m-%d")

    terse_path, dense_path = write_session_docs(
        active, dormant, cluster_stats, date, args.dormant_days
    )
    print(f"Wrote {terse_path}")
    print(f"Wrote {dense_path}")

    try:
        subprocess.run([
            "terminal-notifier",
            "-title", "Claude",
            "-subtitle", "Session Health",
            "-message", f"{len(dormant)} dormant, {len(active)} active",
            "-group", "claude-session-health",
        ], check=False)
    except FileNotFoundError:
        pass

    log = activity_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(f"\n## {datetime.now().strftime('%Y-%m-%dT%H:%M')}\n\n")
        f.write("**Action:** Session health check\n")
        f.write(f"**Active clusters:** {len(active)}, **Dormant:** {len(dormant)}\n")
        f.write(f"**Output:** [[output/alerts/session-health-{date}]]\n\n---\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
