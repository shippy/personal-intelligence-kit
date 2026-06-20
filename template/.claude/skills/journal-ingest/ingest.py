#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Journal Ingest

Parses journal entries into a SQLite database. Supports rosebud, dayone (JSON),
and plain markdown formats. Configured via vault.toml → sources.journal.type.

Usage:
    uv run ingest.py
    uv run ingest.py --rebuild
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import (  # noqa: E402
    load_config,
    source_enabled,
    journal_path,
    journal_type,
    vault_root,
    activity_log,
)


INTENTION_RE = re.compile(
    r"\b(?:i want to|i should|i plan to|i will|i'?m going to|my goal is|need to)\b[^.!?\n]{5,200}[.!?]",
    re.IGNORECASE,
)
DATE_IN_FILENAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b")
STOPWORDS = {
    "The", "This", "That", "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday", "January", "February", "March", "April",
    "May", "June", "July", "August", "September", "October", "November",
    "December", "Today", "Yesterday", "Tomorrow",
}


ROSEBUD_SPEAKER_RE = re.compile(r"^\*\*(.+?):\*\*\s*", re.MULTILINE)

# Dedup is on content (date, speaker, body) — NOT filename. Rosebud exports are
# cumulative, so the same entry recurs across many export files; including
# filename in the key would store one copy per export (~15x inflation).
# `filename` records the first export an entry was seen in (informational only).
SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        filename TEXT,
        speaker TEXT DEFAULT 'author',
        body TEXT NOT NULL,
        mood TEXT,
        word_count INTEGER,
        UNIQUE(date, speaker, body)
    );
    CREATE TABLE IF NOT EXISTS mentions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        context TEXT,
        FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS intentions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date);
    CREATE INDEX IF NOT EXISTS idx_mentions_name ON mentions(name);
"""

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
# Rosebud entry headers are human-readable, e.g. "Saturday, May 30th, 2026".
HUMAN_DATE_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?,\s*(\d{4})",
    re.IGNORECASE,
)


def parse_rosebud_date(text: str) -> str | None:
    """Extract an ISO date (YYYY-MM-DD) from a Rosebud header.

    Handles both ISO dates and human-readable headers like
    "Saturday, May 30th, 2026". Returns None if no date is found.
    """
    m = DATE_IN_FILENAME_RE.search(text)
    if m:
        return m.group(1)
    m = HUMAN_DATE_RE.search(text)
    if m:
        month = MONTHS[m.group(1).lower()]
        return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"
    return None


@dataclass
class Entry:
    date: str  # ISO
    filename: str
    body: str
    speaker: str = "author"  # "author" = vault owner, or app name like "Rosebud"
    mood: str | None = None


def parse_rosebud_file(path: Path) -> list[Entry]:
    """Rosebud markdown export: each session delimited by ## headers with dates.

    Within the #### Entry section, speaker turns are marked as **Name:** lines.
    We split these into separate entries so analysis can filter by speaker.
    """
    text = path.read_text(errors="ignore")
    entries = []
    # Rosebud format: sessions split by "## " headers
    chunks = re.split(r"\n(?=## )", text)
    for chunk in chunks:
        if not chunk.strip():
            continue
        # The date lives in the per-entry header (e.g. "### Saturday, May 30th,
        # 2026"), which sits above the first "#### " sub-section. Prefer that;
        # fall back to the export filename, then file mtime as a last resort.
        header_zone = chunk.split("#### ", 1)[0][:400]
        date = (
            parse_rosebud_date(header_zone)
            or parse_rosebud_date(path.name)
            or datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
        )

        # Try to split the Entry section into speaker turns
        entry_section = chunk
        entry_marker = "#### Entry"
        if entry_marker in chunk:
            entry_section = chunk[chunk.index(entry_marker) + len(entry_marker):]

        # Split by **Speaker:** pattern
        parts = ROSEBUD_SPEAKER_RE.split(entry_section)
        # parts = [preamble, speaker1, text1, speaker2, text2, ...]
        if len(parts) >= 3:
            for i in range(1, len(parts), 2):
                speaker_name = parts[i].strip()
                body = parts[i + 1].strip() if i + 1 < len(parts) else ""
                if not body:
                    continue
                # Normalize: "Rosebud" stays as-is, anything else is "author"
                speaker = "rosebud" if speaker_name.lower() == "rosebud" else "author"
                entries.append(Entry(date=date, filename=path.name, body=body, speaker=speaker))
        else:
            # No speaker turns found — treat whole chunk as author
            entries.append(Entry(date=date, filename=path.name, body=chunk.strip()))
    return entries


def parse_dayone_json(path: Path) -> list[Entry]:
    """Day One JSON export structure."""
    try:
        data = json.loads(path.read_text(errors="ignore"))
    except json.JSONDecodeError:
        return []
    entries = []
    for e in data.get("entries", []):
        date = (e.get("creationDate") or "")[:10] or "1970-01-01"
        body = e.get("text", "")
        if body:
            entries.append(Entry(date=date, filename=path.name, body=body))
    return entries


def parse_markdown_file(path: Path) -> list[Entry]:
    """Plain markdown: filename (or first heading) contains date."""
    m = DATE_IN_FILENAME_RE.search(path.name)
    if not m:
        return []
    date = m.group(1)
    return [Entry(date=date, filename=path.name, body=path.read_text(errors="ignore"))]


PARSERS = {
    "rosebud": parse_rosebud_file,
    "dayone": parse_dayone_json,
    "markdown": parse_markdown_file,
}


def extract_mentions(body: str) -> list[tuple[str, str]]:
    """Return list of (name, context) tuples from capitalized proper nouns."""
    mentions = []
    seen = set()
    for m in PROPER_NOUN_RE.finditer(body):
        name = m.group(0)
        if name in STOPWORDS or name in seen:
            continue
        seen.add(name)
        start = max(0, m.start() - 40)
        end = min(len(body), m.end() + 40)
        context = body[start:end].replace("\n", " ")
        mentions.append((name, context))
    return mentions[:20]  # cap


def extract_intentions(body: str) -> list[str]:
    return [m.group(0).strip() for m in INTENTION_RE.finditer(body)][:20]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()

    load_config()
    if not source_enabled("journal"):
        print("Journal source not enabled.")
        return 1

    jpath = journal_path()
    jtype = journal_type()
    if not jpath or not jpath.exists():
        print(f"Journal path not found: {jpath}")
        return 1
    if jtype not in PARSERS:
        print(f"Unknown journal type: {jtype}")
        return 1

    print(f"Ingesting {jtype} journal from {jpath}")
    parser_fn = PARSERS[jtype]

    # Collect entries
    all_entries: list[Entry] = []
    glob = "*.json" if jtype == "dayone" else "*.md"
    for f in sorted(jpath.rglob(glob)):
        try:
            all_entries.extend(parser_fn(f))
        except Exception as e:
            print(f"  skip {f.name}: {e}")

    print(f"Parsed {len(all_entries)} entries")

    # Write to DB
    db_path = vault_root() / "data" / "journal.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if args.rebuild and db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)

    imported = 0
    for entry in all_entries:
        try:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO entries (date, filename, speaker, body, mood, word_count) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (entry.date, entry.filename, entry.speaker, entry.body, entry.mood, len(entry.body.split())),
            )
            if cursor.rowcount == 0:
                continue
            entry_id = cursor.lastrowid
            for name, context in extract_mentions(entry.body):
                conn.execute(
                    "INSERT INTO mentions (entry_id, name, context) VALUES (?, ?, ?)",
                    (entry_id, name, context),
                )
            for intent in extract_intentions(entry.body):
                conn.execute(
                    "INSERT INTO intentions (entry_id, text) VALUES (?, ?)",
                    (entry_id, intent),
                )
            imported += 1
        except Exception as e:
            print(f"  skip entry {entry.filename}: {e}")

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    conn.close()

    print(f"Imported {imported} new entries (total: {total})")

    log = activity_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(f"\n## {datetime.now().strftime('%Y-%m-%dT%H:%M')}\n\n")
        f.write(f"**Action:** Journal ingest ({jtype})\n")
        f.write(f"**New entries:** {imported}, **Total in DB:** {total}\n")
        f.write(f"**Output:** `data/journal.db`\n\n---\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
