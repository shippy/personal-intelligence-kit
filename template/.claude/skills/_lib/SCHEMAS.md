# Data Contracts — Ingest ↔ Analysis

This document specifies the SQLite schemas that **ingest skills produce** and **analysis skills read**. If you write a custom ingest skill, conform to these schemas so the analysis pipeline works.

All databases live in `data/` (relative to vault root).

---

## `data/journal.db` — Journal Entries

Produced by: `journal-ingest`
Read by: `weekly-reflection`, `cross-source-queries`

```sql
CREATE TABLE entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,          -- ISO 8601 date (e.g. "2026-01-15")
    filename TEXT,               -- Source file name
    speaker TEXT DEFAULT 'author', -- "author" (vault owner) or app tag (e.g. "rosebud")
    body TEXT NOT NULL,          -- Entry body (may be multi-paragraph)
    mood TEXT,                   -- Optional mood tag
    word_count INTEGER,
    UNIQUE(date, filename, speaker, body) -- Prevent duplicate imports
);

CREATE TABLE mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    name TEXT NOT NULL,          -- Proper noun (e.g. person name)
    context TEXT,                -- ~80 chars of surrounding text
    FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
);

CREATE TABLE intentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    text TEXT NOT NULL,          -- The full intention sentence
    FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
);

-- Indexes
CREATE INDEX idx_entries_date ON entries(date);
CREATE INDEX idx_mentions_name ON mentions(name);
```

### Query patterns (used by analysis skills)

```sql
-- Recent entries (last 7 days)
SELECT * FROM entries WHERE date >= ? ORDER BY date DESC;

-- Recent entries by the user only (for Rosebud which has both user + AI)
SELECT * FROM entries WHERE date >= ? AND speaker = 'author' ORDER BY date DESC;

-- People mentioned recently
SELECT name, COUNT(*) as freq FROM mentions m
JOIN entries e ON m.entry_id = e.id
WHERE e.date >= ?
GROUP BY name ORDER BY freq DESC;

-- Recent intentions
SELECT i.text, e.date FROM intentions i
JOIN entries e ON i.entry_id = e.id
WHERE e.date >= ?
ORDER BY e.date DESC;
```

---

## `data/tasks.db` — Task Manager Snapshot

Produced by: `tasks-import`
Read by: `weekly-reflection`, `cross-source-queries`

```sql
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,          -- Provider-prefixed: "todoist:123", "things:uuid"
    title TEXT NOT NULL,
    body TEXT,
    status TEXT CHECK(status IN ('open', 'done', 'cancelled')),
    list_name TEXT,               -- Project/list name
    created_at TIMESTAMP,         -- ISO 8601
    completed_at TIMESTAMP,       -- ISO 8601, NULL if not done
    due_date TIMESTAMP,           -- ISO 8601, NULL if no due date
    provider TEXT NOT NULL,       -- "todoist", "microsoft-todo", "things"
    raw_json TEXT                 -- Original API response (for debugging)
);

CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_due ON tasks(due_date);
CREATE INDEX idx_tasks_completed ON tasks(completed_at);
```

### Query patterns

```sql
-- Tasks created this week
SELECT * FROM tasks WHERE DATE(created_at) >= ? ORDER BY created_at DESC;

-- Tasks completed this week
SELECT * FROM tasks WHERE DATE(completed_at) >= ? ORDER BY completed_at DESC;

-- Overdue tasks
SELECT * FROM tasks
WHERE status = 'open' AND due_date IS NOT NULL AND DATE(due_date) < DATE('now')
ORDER BY due_date ASC;

-- Search by keyword
SELECT * FROM tasks WHERE title LIKE ? OR body LIKE ?;
```

---

## `data/browser-history.db` — Browser History Snapshot

Produced by: `browser-ingest`
Read by: `weekly-reflection`, `cross-source-queries`, `session-analyzer`

```sql
CREATE TABLE visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    title TEXT,
    visit_count INTEGER,
    last_visit_time TIMESTAMP,    -- ISO 8601
    domain TEXT                   -- Extracted hostname
);

CREATE INDEX idx_visits_domain ON visits(domain);
CREATE INDEX idx_visits_time ON visits(last_visit_time);
```

### Query patterns

```sql
-- Recent visits
SELECT * FROM visits WHERE last_visit_time >= ? ORDER BY last_visit_time DESC;

-- Top domains by visit count
SELECT domain, SUM(visit_count) as total, COUNT(*) as pages
FROM visits WHERE last_visit_time >= ?
GROUP BY domain ORDER BY total DESC;

-- Title search (for convergence detection)
SELECT title, domain FROM visits WHERE last_visit_time >= ? AND title LIKE ?;
```

---

## `data/git-commits.db` — Git Commit Stats

Produced by: `git-stats`
Read by: `cross-source-queries`

```sql
CREATE TABLE commits (
    hash TEXT PRIMARY KEY,           -- Full commit SHA
    repo TEXT NOT NULL,              -- Repository directory name
    repo_path TEXT NOT NULL,         -- Absolute path to repository
    author_name TEXT NOT NULL,
    author_email TEXT NOT NULL,
    date TEXT NOT NULL,              -- ISO 8601 datetime
    subject TEXT NOT NULL,           -- First line of commit message
    branch TEXT,                     -- Best-effort branch from decorations
    files_changed INTEGER,
    insertions INTEGER,
    deletions INTEGER,
    scope TEXT NOT NULL              -- Superdirectory path that matched
);

CREATE TABLE co_authors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commit_hash TEXT NOT NULL,
    name TEXT NOT NULL,              -- e.g. "Claude Opus 4.6 (1M context)"
    email TEXT NOT NULL,             -- e.g. "noreply@anthropic.com"
    FOREIGN KEY(commit_hash) REFERENCES commits(hash) ON DELETE CASCADE,
    UNIQUE(commit_hash, email)
);

CREATE INDEX idx_commits_date ON commits(date);
CREATE INDEX idx_commits_repo ON commits(repo);
CREATE INDEX idx_commits_author ON commits(author_email);
CREATE INDEX idx_co_authors_email ON co_authors(email);
```

### Query patterns

```sql
-- Commits per repo
SELECT repo, COUNT(*) as commits, SUM(insertions) as added, SUM(deletions) as removed
FROM commits WHERE date >= ? GROUP BY repo ORDER BY commits DESC;

-- Co-authorship rate
SELECT COUNT(*) as total, COUNT(ca.commit_hash) as co_authored
FROM commits c LEFT JOIN co_authors ca ON c.hash = ca.commit_hash
WHERE c.date >= ?;

-- Co-author breakdown
SELECT ca.name, COUNT(*) as commits FROM co_authors ca
JOIN commits c ON ca.commit_hash = c.hash
WHERE c.date >= ? GROUP BY ca.name ORDER BY commits DESC;

-- Daily volume
SELECT DATE(date) as day, COUNT(*) as commits
FROM commits WHERE date >= ? GROUP BY day ORDER BY day;
```

---

## Email — No SQLite; via `notmuch` CLI

Produced by: `email-ingest` (mbsync + notmuch)
Read by: `weekly-reflection`, `cross-source-queries`, `email-analyzer`

Email is queried via the `notmuch` CLI, not a custom SQLite database. The interface:

```bash
# Count messages in a time range
notmuch count 'date:7d..'

# Search (returns JSON)
notmuch search --format=json --limit=50 'date:7d..'

# Show message details (JSON)
notmuch show --format=json 'id:message-id-here'

# Sent mail
notmuch search 'date:30d.. AND folder:"[Gmail]/Sent Mail"'
```

### Key query patterns for analysis

| Query | Purpose |
|-------|---------|
| `date:7d..` | Messages from last 7 days |
| `tag:sent date:30d..` | Sent messages (last 30 days) |
| `from:alice@example.com` | From a specific person |
| `subject:"keyword"` | Subject line search |

---

## Notes Vault — Filesystem (Markdown)

Read directly from the notes vault path configured in `vault.toml → notes.vault_path`. No ingestion step.

### Access pattern

```python
from vault_config import notes_vault_path

nvault = notes_vault_path()
for md in nvault.rglob("*.md"):
    # Check mtime, read content, extract topics
    ...
```

Analysis skills should:
- Read all top-level directories (not assume PARA structure)
- Skip hidden directories (`.obsidian/`, `.git/`)
- Never write to the notes vault

---

## Extension Guide

To add a new data source:

1. Create an ingest skill in `.claude/skills/<name>-ingest/`
2. Write data to `data/<name>.db` following a contract like the ones above
3. Add an entry to `vault.toml` under `[sources]`
4. Register the SQLite path in `_lib/vault_config.py` (add a `<name>_db_path()` function)
5. Update analysis skills to query the new source (check `source_enabled("<name>")` first)

The analysis skills (cross-source-queries, weekly-reflection) are designed to **gracefully skip** any source that isn't enabled or whose database doesn't exist.
