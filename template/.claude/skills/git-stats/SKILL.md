---
name: git-stats
description: Use when the user asks about their git commit activity, coding stats, co-authorship with Claude, or repository contribution history across projects
---

# Git Commit Stats

Tracks commit activity across git repositories in configured superdirectories, with per-directory author filtering and co-author (Claude) detection.

## Running the Ingest

```bash
# From the vault root directory:
uv run .claude/skills/git-stats/ingest.py              # Last 7 days (default)
uv run .claude/skills/git-stats/ingest.py --since 2026-03-01
uv run .claude/skills/git-stats/ingest.py --since 2026-03-01 --until 2026-04-01
uv run .claude/skills/git-stats/ingest.py --all         # Full history
```

## Configuration

The git source is configured in `vault.toml` under `[sources.git]`. Each scope maps a parent directory to the committer email(s) to track:

```toml
[sources.git]
enabled = true

[[sources.git.scopes]]
path = "~/Documents"
authors = ["user@personal.com"]

[[sources.git.scopes]]
path = "~/Work"
authors = ["user@company.com"]
```

## Querying the Data

The ingest writes to `data/git-commits.db`. Useful queries:

```sql
-- Commits per repo (last 7 days)
SELECT repo, COUNT(*) as commits, SUM(insertions) as added, SUM(deletions) as removed
FROM commits WHERE date >= date('now', '-7 days')
GROUP BY repo ORDER BY commits DESC;

-- Co-authorship rate (how many commits were made with Claude)
SELECT
    COUNT(*) as total,
    COUNT(ca.commit_hash) as co_authored,
    ROUND(COUNT(ca.commit_hash) * 100.0 / COUNT(*), 1) as pct
FROM commits c
LEFT JOIN co_authors ca ON c.hash = ca.commit_hash
WHERE c.date >= date('now', '-7 days');

-- Co-author breakdown
SELECT ca.name, COUNT(*) as commits
FROM co_authors ca
JOIN commits c ON ca.commit_hash = c.hash
WHERE c.date >= date('now', '-30 days')
GROUP BY ca.name ORDER BY commits DESC;

-- Daily commit volume
SELECT DATE(date) as day, COUNT(*) as commits,
       SUM(insertions) as added, SUM(deletions) as removed
FROM commits WHERE date >= date('now', '-30 days')
GROUP BY day ORDER BY day;

-- Repos by scope
SELECT scope, repo, COUNT(*) as commits
FROM commits WHERE date >= date('now', '-7 days')
GROUP BY scope, repo ORDER BY scope, commits DESC;
```

## Schema

See `_lib/SCHEMAS.md` for the full `data/git-commits.db` schema (tables: `commits`, `co_authors`).
