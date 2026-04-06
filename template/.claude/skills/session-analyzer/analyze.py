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

    # Terse
    terse_lines = [
        "---",
        f"created: {date}",
        "type: alert",
        "status: final",
        "sources: [browser]",
        "---",
        "",
        f"# Session Health — {date}",
        "",
        f"## Summary",
        "",
        f"- 🟢 **{len(active)}** active domain clusters",
        f"- 🔴 **{len(dormant)}** dormant (no activity in {args.dormant_days} days)",
        "",
    ]

    if dormant[:5]:
        terse_lines.extend(["## Top Dormant Clusters", ""])
        for domain, stats in dormant[:5]:
            days = (now - stats["last_visit"]).days if stats["last_visit"] else "?"
            terse_lines.append(
                f"- 🔴 **{domain}** — {stats['count']} visits, last {days}d ago"
            )
        terse_lines.append("")

    if active[:5]:
        terse_lines.extend(["## Top Active Clusters", ""])
        for domain, stats in active[:5]:
            terse_lines.append(f"- 🟢 **{domain}** — {stats['count']} visits")

    terse = "\n".join(terse_lines)

    # Dense
    dense_lines = [
        "---",
        f"created: {date}",
        "type: report",
        "status: final",
        "sources: [browser]",
        "---",
        "",
        f"# Session Health Report — {date}",
        "",
        "## All Clusters",
        "",
        "| Domain | Visits | Last Seen | Status |",
        "|--------|--------|-----------|--------|",
    ]
    all_clusters = sorted(
        cluster_stats.items(),
        key=lambda x: x[1]["count"],
        reverse=True,
    )
    for domain, stats in all_clusters[:50]:
        if stats["last_visit"]:
            days = (now - stats["last_visit"]).days
            status = "🔴 dormant" if days > args.dormant_days else "🟢 active"
            last_seen = stats["last_visit"].strftime("%Y-%m-%d")
        else:
            days = "?"
            status = "?"
            last_seen = "?"
        dense_lines.append(
            f"| {domain} | {stats['count']} | {last_seen} | {status} |"
        )

    dense = "\n".join(dense_lines)

    # Write
    alerts = output_dir("alerts")
    reports = output_dir("reports")
    alerts.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)

    terse_path = alerts / f"session-health-{date}.md"
    dense_path = reports / f"session-health-{date}.md"
    terse_path.write_text(terse)
    dense_path.write_text(dense)

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
