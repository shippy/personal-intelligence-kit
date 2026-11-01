"""Tests for journal-ingest date parsing.

Run: uv run --no-project --with pytest pytest test_ingest.py
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ingest


LEGACY_SCHEMA = """
    CREATE TABLE entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        filename TEXT,
        speaker TEXT DEFAULT 'author',
        body TEXT NOT NULL,
        mood TEXT,
        word_count INTEGER,
        UNIQUE(date, filename, speaker, body)
    );
    CREATE TABLE mentions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        context TEXT,
        FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
    );
    CREATE TABLE intentions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
    );
"""


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

**Alex Example:** At the reunion, i discovered the group chat.


## A Quieter Day
### Tuesday, June 2nd, 2026

#### Entry

**Rosebud:** What was the highlight of your day?

**Alex Example:** Picked up the TV gift for the family.
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


# --- parse_rosebud_file: newer exports append non-entry sections ---

# Rosebud's newer exporter emits an `Exported:` preamble and appends `# Drafts`,
# `# Goals`, and `# Weekly reports` after the entries. Those sections reuse the
# same heading depths as entries, so a naive chunk split ingests their
# timestamps and report prose as journal entries.
NEW_FORMAT_SAMPLE = """# \U0001f339 Rosebud data export

Exported: 2026-08-10T15:27:55.947Z

---

# \U0001f339 Rosebud entries

### December 23, 2024 - August 5, 2026

---


## \U0001f33f A Real Entry
### Wednesday, August 5th, 2026

#### Entry

**Rosebud:** How are you feeling?

**Alex Example:** Busy with work, but the community project is wonderful.

# Drafts

## Untitled draft

**Last edited:** 2026-08-04T19:03:09.151Z

---

## Untitled draft

**Journal day:** 2026-06-10

**Last edited:** 2026-06-10T20:15:57.100Z

### Draft conversation

**Rosebud:** How's the jetlag?

**Alex Example:** The travel plan is coming along slowly.

# Goals

## \U0001f4ca Learn Bayesian modeling

**Status:** Archived

**Created:** 2025-01-05T09:43:48.441Z

Explore Bayesian probabilistic programming.

# Weekly reports

### \U0001f327️ Getting Things Done While Feeling Bad

**Period:** 2026-07-06 to 2026-07-12

**Generated:** 2026-07-12T21:08:26.419Z

This week was shaped by a delayed workshop.
"""


def test_export_preamble_is_not_an_entry(tmp_path):
    f = tmp_path / "rosebud-2026-08-10_17-27-55.md"
    f.write_text(NEW_FORMAT_SAMPLE)

    bodies = [e.body for e in ingest.parse_rosebud_file(f)]
    assert not any("Rosebud data export" in b for b in bodies), (
        f"export header leaked in as an entry: {[b for b in bodies if 'Rosebud data export' in b]}"
    )


def test_weekly_report_prose_is_not_ingested_as_an_entry(tmp_path):
    # The worst failure mode: report prose lands under whatever ISO date appears
    # first in its chunk, so 2026-07 narrative gets filed under an unrelated day.
    f = tmp_path / "rosebud-2026-08-10_17-27-55.md"
    f.write_text(NEW_FORMAT_SAMPLE)

    bodies = [e.body for e in ingest.parse_rosebud_file(f)]
    assert not any("delayed workshop" in b for b in bodies), (
        "weekly-report prose was ingested as a journal entry"
    )


def test_drafts_and_goals_sections_are_not_ingested(tmp_path):
    f = tmp_path / "rosebud-2026-08-10_17-27-55.md"
    f.write_text(NEW_FORMAT_SAMPLE)

    bodies = [e.body for e in ingest.parse_rosebud_file(f)]
    assert not any("jetlag" in b for b in bodies), "draft conversation ingested as an entry"
    assert not any("Bayesian" in b for b in bodies), "goal description ingested as an entry"


def test_bare_timestamps_never_become_entries(tmp_path):
    f = tmp_path / "rosebud-2026-08-10_17-27-55.md"
    f.write_text(NEW_FORMAT_SAMPLE)

    for e in ingest.parse_rosebud_file(f):
        stripped = e.body.replace("-", "").replace("*", "").strip()
        assert not stripped.startswith("2026") or len(e.body.split()) > 5, (
            f"bare timestamp stored as an entry: {e.body!r}"
        )


def test_real_entries_survive_the_new_format(tmp_path):
    # The fix must not throw the baby out: real entries still parse, correctly dated.
    f = tmp_path / "rosebud-2026-08-10_17-27-55.md"
    f.write_text(NEW_FORMAT_SAMPLE)

    entries = ingest.parse_rosebud_file(f)
    real = [e for e in entries if "community project is wonderful" in e.body]
    assert real, "the actual August 5 entry was lost"
    assert real[0].date == "2026-08-05", f"got {real[0].date}"
    assert real[0].speaker == "author"


def test_legacy_format_without_extra_sections_still_parses(tmp_path):
    # Older exports have no Drafts/Goals/Weekly-reports sections; must be a no-op.
    f = tmp_path / "rosebud-2026-06-02_10-38-20.md"
    f.write_text(ROSEBUD_SAMPLE)

    entries = ingest.parse_rosebud_file(f)
    assert [e.date for e in entries if "reunion" in e.body.lower()] == ["2026-05-30"]
    assert any("TV gift" in e.body for e in entries)


# --- _migrate_legacy_dedup ---

def test_migrate_collapses_legacy_filename_duplicates(tmp_path):
    db = tmp_path / "journal.db"
    conn = sqlite3.connect(db)
    conn.executescript(LEGACY_SCHEMA)
    # Same entry re-exported under two filenames -> two rows under legacy key.
    conn.execute(
        "INSERT INTO entries (date, filename, speaker, body) VALUES (?,?,?,?)",
        ("2026-01-01", "export-a.md", "author", "hello"),
    )
    conn.execute(
        "INSERT INTO entries (date, filename, speaker, body) VALUES (?,?,?,?)",
        ("2026-01-01", "export-b.md", "author", "hello"),
    )
    conn.execute("INSERT INTO mentions (entry_id, name) VALUES (2, 'Bob')")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 2

    ingest._migrate_legacy_dedup(conn)

    rows = conn.execute("SELECT date, speaker, body FROM entries").fetchall()
    assert rows == [("2026-01-01", "author", "hello")]
    new_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='entries'"
    ).fetchone()[0]
    assert "UNIQUE(date, speaker, body)" in new_sql
    assert "filename, speaker, body" not in new_sql
    # child row orphaned by the collapse is pruned
    assert conn.execute("SELECT COUNT(*) FROM mentions").fetchone()[0] == 0


def test_migrate_is_noop_on_current_schema(tmp_path):
    db = tmp_path / "journal.db"
    conn = sqlite3.connect(db)
    conn.executescript(ingest.SCHEMA_SQL)
    ingest._migrate_legacy_dedup(conn)  # must not raise or alter the schema
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='entries'"
    ).fetchone()[0]
    assert "UNIQUE(date, speaker, body)" in sql
