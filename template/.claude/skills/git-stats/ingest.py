#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Git Commit Stats Ingest

Scans git repositories in configured superdirectories and records commit
stats (author, lines changed, co-authors) to data/git-commits.db.

Usage:
    uv run ingest.py                    # Last 7 days
    uv run ingest.py --since 2026-03-01
    uv run ingest.py --since 2026-03-01 --until 2026-04-01
    uv run ingest.py --all
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import (  # noqa: E402
    load_config,
    source_enabled,
    git_scopes,
    git_commits_db_path,
    expand,
    activity_log,
)

FIELD_SEP = "\x00"
# git log format: hash, author name, author email, ISO date, subject, decorations, co-author trailers
LOG_FORMAT = "%H%x00%an%x00%ae%x00%aI%x00%s%x00%D%x00%(trailers:key=Co-authored-by,valueonly)"

SHORTSTAT_RE = re.compile(
    r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?"
)

CO_AUTHOR_RE = re.compile(r"^(.+?)\s*<([^>]+)>$")


def find_repos(superdirectory: Path) -> list[Path]:
    """Find git repositories as immediate children of superdirectory."""
    repos = []
    if not superdirectory.is_dir():
        return repos
    try:
        for entry in sorted(superdirectory.iterdir()):
            if entry.is_dir() and (entry / ".git").exists():
                repos.append(entry)
    except PermissionError:
        pass
    return repos


def extract_branch(decorations: str) -> str | None:
    """Extract a branch name from git log %D decorations (best-effort)."""
    if not decorations:
        return None
    # Decorations look like: "HEAD -> main, tag: v1.0, origin/main"
    for part in decorations.split(","):
        part = part.strip()
        if part.startswith("HEAD -> "):
            return part[len("HEAD -> "):]
        if part.startswith("tag:"):
            continue
        # origin/branch-name → branch-name
        if "/" in part:
            return part.split("/", 1)[1]
        if part and part != "HEAD":
            return part
    return None


def parse_co_authors(trailer_value: str) -> list[tuple[str, str]]:
    """Parse co-author trailer values into (name, email) pairs."""
    co_authors = []
    for line in trailer_value.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        m = CO_AUTHOR_RE.match(line)
        if m:
            co_authors.append((m.group(1).strip(), m.group(2).strip()))
    return co_authors


def run_git_log(
    repo_path: Path,
    authors: list[str],
    since: str | None,
    until: str | None,
) -> list[dict[str, Any]]:
    """Run git log for a repo and return parsed commit dicts."""
    commits = []

    for author in authors:
        cmd = [
            "git", "log",
            f"--author={author}",
            f"--format={LOG_FORMAT}",
            "--shortstat",
        ]
        if since:
            cmd.append(f"--since={since}")
        if until:
            cmd.append(f"--until={until}")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, cwd=repo_path, timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue

        if result.returncode != 0:
            continue

        # Parse output: format line, blank line, optional shortstat line, blank line
        lines = result.stdout.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue

            # Should be a format line with null separators
            if FIELD_SEP not in line:
                i += 1
                continue

            parts = line.split(FIELD_SEP)
            if len(parts) < 7:
                i += 1
                continue

            hash_, author_name, author_email, date, subject, decorations, trailers = (
                parts[0], parts[1], parts[2], parts[3], parts[4], parts[5],
                FIELD_SEP.join(parts[6:]),  # trailers may contain our separator if multiple
            )

            # Look ahead for shortstat
            files_changed = insertions = deletions = 0
            j = i + 1
            while j < len(lines) and j <= i + 3:
                m = SHORTSTAT_RE.search(lines[j])
                if m:
                    files_changed = int(m.group(1))
                    insertions = int(m.group(2) or 0)
                    deletions = int(m.group(3) or 0)
                    i = j  # advance past shortstat
                    break
                if lines[j].strip() and FIELD_SEP in lines[j]:
                    break  # next commit, no shortstat for this one
                j += 1

            branch = extract_branch(decorations)
            co_authors = parse_co_authors(trailers)

            commits.append({
                "hash": hash_,
                "author_name": author_name,
                "author_email": author_email,
                "date": date,
                "subject": subject,
                "branch": branch,
                "files_changed": files_changed,
                "insertions": insertions,
                "deletions": deletions,
                "co_authors": co_authors,
            })

            i += 1

    return commits


def init_db(db_path: Path) -> sqlite3.Connection:
    """Create or open the git-commits database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS commits (
            hash TEXT PRIMARY KEY,
            repo TEXT NOT NULL,
            repo_path TEXT NOT NULL,
            author_name TEXT NOT NULL,
            author_email TEXT NOT NULL,
            date TEXT NOT NULL,
            subject TEXT NOT NULL,
            branch TEXT,
            files_changed INTEGER,
            insertions INTEGER,
            deletions INTEGER,
            scope TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS co_authors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commit_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            FOREIGN KEY(commit_hash) REFERENCES commits(hash) ON DELETE CASCADE,
            UNIQUE(commit_hash, email)
        );

        CREATE INDEX IF NOT EXISTS idx_commits_date ON commits(date);
        CREATE INDEX IF NOT EXISTS idx_commits_repo ON commits(repo);
        CREATE INDEX IF NOT EXISTS idx_commits_author ON commits(author_email);
        CREATE INDEX IF NOT EXISTS idx_co_authors_email ON co_authors(email);
    """)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def main() -> int:
    parser = argparse.ArgumentParser(description="Git commit stats ingest")
    parser.add_argument("--since", type=str, help="Start date (ISO 8601 or git date spec)")
    parser.add_argument("--until", type=str, help="End date (ISO 8601 or git date spec)")
    parser.add_argument("--all", action="store_true", help="Ingest all history (no date filter)")
    args = parser.parse_args()

    load_config()
    if not source_enabled("git"):
        print("Git source not enabled in vault.toml.")
        return 1

    scopes = git_scopes()
    if not scopes:
        print("No git scopes configured in vault.toml. Add [[sources.git.scopes]] entries.")
        return 1

    # Default to last 7 days
    since = args.since
    until = args.until
    if not args.all and not since:
        since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    db_path = git_commits_db_path()
    conn = init_db(db_path)

    total_commits = 0
    total_repos = 0
    total_co_authored = 0
    co_author_counts: dict[str, int] = {}

    for scope in scopes:
        scope_path = expand(scope["path"])
        authors = scope.get("authors", [])
        if not authors:
            print(f"  Skipping scope {scope_path}: no authors configured")
            continue

        repos = find_repos(scope_path)
        for repo in repos:
            commits = run_git_log(repo, authors, since, until)
            if not commits:
                continue

            total_repos += 1
            repo_name = repo.name

            for c in commits:
                conn.execute(
                    """INSERT OR REPLACE INTO commits
                       (hash, repo, repo_path, author_name, author_email,
                        date, subject, branch, files_changed, insertions, deletions, scope)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        c["hash"], repo_name, str(repo), c["author_name"],
                        c["author_email"], c["date"], c["subject"], c["branch"],
                        c["files_changed"], c["insertions"], c["deletions"],
                        str(scope_path),
                    ),
                )

                for name, email in c["co_authors"]:
                    conn.execute(
                        """INSERT OR IGNORE INTO co_authors
                           (commit_hash, name, email) VALUES (?, ?, ?)""",
                        (c["hash"], name, email),
                    )
                    co_author_counts[name] = co_author_counts.get(name, 0) + 1

                if c["co_authors"]:
                    total_co_authored += 1
                total_commits += 1

            print(f"  {repo_name}: {len(commits)} commits")

    conn.commit()
    conn.close()

    # Terminal summary
    date_range = f"since {since}" if since else "all time"
    if until:
        date_range += f" until {until}"

    print(f"\n{'='*50}")
    print(f"Git Stats Ingest Complete ({date_range})")
    print(f"{'='*50}")
    print(f"Repos with commits: {total_repos}")
    print(f"Total commits:      {total_commits}")
    print(f"Co-authored:        {total_co_authored} ({_pct(total_co_authored, total_commits)})")

    if co_author_counts:
        print(f"\nCo-authors:")
        for name, count in sorted(co_author_counts.items(), key=lambda x: -x[1]):
            print(f"  {name}: {count} commits")

    print(f"\nOutput: {db_path}")

    # Activity log
    log = activity_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(f"\n## {datetime.now().strftime('%Y-%m-%dT%H:%M')}\n\n")
        f.write(f"**Action:** Git commit stats ingest ({date_range})\n")
        f.write(f"**Repos:** {total_repos}, **Commits:** {total_commits}, ")
        f.write(f"**Co-authored:** {total_co_authored}\n")
        f.write(f"**Output:** `data/git-commits.db`\n\n---\n")

    return 0


def _pct(part: int, total: int) -> str:
    if total == 0:
        return "0%"
    return f"{part * 100 // total}%"


if __name__ == "__main__":
    sys.exit(main())
