"""Tests for journal-ingest date parsing.

Run: uv run --no-project --with pytest pytest test_ingest.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ingest


# --- parse_rosebud_date: human-readable headers ---

def test_parses_human_header_with_weekday():
    assert ingest.parse_rosebud_date("### Saturday, May 30th, 2026") == "2026-05-30"


def test_parses_ordinal_suffix_st():
    assert ingest.parse_rosebud_date("Thursday, April 1st, 2026") == "2026-04-01"


def test_parses_ordinal_suffix_nd():
    assert ingest.parse_rosebud_date("Tuesday, June 2nd, 2026") == "2026-06-02"


def test_parses_ordinal_suffix_rd():
    assert ingest.parse_rosebud_date("Tuesday, March 3rd, 2026") == "2026-03-03"


def test_parses_full_month_double_digit_day():
    assert ingest.parse_rosebud_date("Monday, December 23rd, 2024") == "2024-12-23"


def test_iso_date_still_parsed():
    assert ingest.parse_rosebud_date("2026-05-30") == "2026-05-30"


def test_no_date_returns_none():
    assert ingest.parse_rosebud_date("#### Tags\n**Emotions:** Heartbroken") is None


# --- parse_rosebud_file: integration, dates come from per-entry headers ---

ROSEBUD_SAMPLE = """# \U0001f339 Rosebud entries

### December 23, 2024 - May 30, 2026

---


## \U0001f494 They Just Didn't Miss Me
### Saturday, May 30th, 2026

#### Entry

**Rosebud:** What's on your mind?

**Šimon Podhajský:** At the reunion, i discovered the group chat.


## A Quieter Day
### Tuesday, June 2nd, 2026

#### Entry

**Rosebud:** What was the highlight of your day?

**Šimon Podhajský:** Picked up the TV gift for the family.
"""


def test_same_entry_from_two_exports_dedupes_to_one_row():
    # Cumulative Rosebud exports re-emit the same entry under different filenames.
    # Dedup must be on content (date, speaker, body), not filename.
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.executescript(ingest.SCHEMA_SQL)
    row = ("2026-05-30", "author", "At the reunion, i discovered the group chat.", None, 7)
    conn.execute(
        "INSERT OR IGNORE INTO entries (date, filename, speaker, body, mood, word_count) "
        "VALUES (?, 'rosebud-2026-06-01.md', ?, ?, ?, ?)",
        row,
    )
    conn.execute(
        "INSERT OR IGNORE INTO entries (date, filename, speaker, body, mood, word_count) "
        "VALUES (?, 'rosebud-2026-06-02.md', ?, ?, ?, ?)",
        row,
    )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    conn.close()
    assert count == 1, f"expected dedup to 1 row across export files, got {count}"


def test_entries_dated_by_their_own_header(tmp_path):
    # Filename carries the EXPORT date (today), which must NOT win over per-entry headers.
    f = tmp_path / "rosebud-2026-06-02_10-38-20.md"
    f.write_text(ROSEBUD_SAMPLE)

    entries = ingest.parse_rosebud_file(f)
    author = [e for e in entries if e.speaker == "author" and "reunion" in e.body.lower()]
    assert author, "expected the reunion entry"
    assert author[0].date == "2026-05-30", f"got {author[0].date}, expected entry-header date not export date"

    later = [e for e in entries if e.speaker == "author" and "TV gift" in e.body]
    assert later, "expected the TV-gift entry"
    assert later[0].date == "2026-06-02"
