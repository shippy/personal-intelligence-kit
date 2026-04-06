---
name: cross-source-queries
description: Cross-source analysis for detecting patterns across data sources. Includes intention-reality gap detection, commitment accountability tracking, and serendipity/convergence analysis. Reads vault.toml and queries normalized SQLite databases (see SCHEMAS.md).
---

# Cross-Source Queries

Analysis tools that detect patterns across multiple data sources. Each analysis reads `vault.toml` to discover which sources are enabled and skips missing ones gracefully.

All three analyses use **regex-based heuristics by default** (no LLM required). Each file has `EXTENSION POINT` comments marking where to add LLM calls for higher-quality extraction.

## Invocation

```bash
cd .claude/skills/cross-source-queries
uv run generate_report.py                 # Run all three analyses
uv run intention_reality_gaps.py          # Just intention-reality
uv run commitment_accountability.py       # Just commitments
uv run serendipity_convergence.py         # Just convergence
```

Or via Claude Code: `batch: cross-source-queries`

## Available Analyses

### 1. Intention ↔ Reality Gaps (`intention_reality_gaps.py`)

Compare stated intentions vs actual behavior.

**What it does:**
- Finds goal files in notes vault (e.g., "2026 Goals.md")
- Extracts unchecked goals via regex
- Cross-references with notes vault activity, email (notmuch), tasks (tasks.db)
- Queries journal.db `intentions` table for recent stated intentions
- Flags goals with zero evidence

**Output:** `output/reports/intention-reality-YYYY-MM-DD.md`

**Data sources:** notes vault, journal.db, tasks.db, email (notmuch)

### 2. Commitment Accountability (`commitment_accountability.py`)

Track commitments made in email and check follow-through.

**What it does:**
- Scans sent emails for commitment phrases (regex patterns)
- Checks for follow-up emails to same recipient
- Flags commitments without follow-through

**Output:** `output/alerts/commitment-accountability-YYYY-MM-DD.md`

**Requires:** email source enabled

### 3. Serendipity & Convergence (`serendipity_convergence.py`)

Detect topics and people appearing across multiple unrelated sources.

**What it does:**
- Extracts topics from journal.db, notes vault, browser-history.db, email
- Finds convergence (same topic/person in 2+ sources)
- Scores by source count × total mentions
- Separately detects person convergence using journal mentions table

**Output:** `output/reports/convergence-YYYY-MM-DD.md`

## Data Source Matrix

| Analysis | Journal DB | Notes Vault | Browser DB | Email | Tasks DB |
|----------|-----------|-------------|-----------|-------|---------|
| Intention-reality | ✓ (intentions) | ✓ (goals) | — | optional | optional |
| Commitments | — | — | — | **required** | — |
| Convergence | ✓ (topics, people) | ✓ (topics, people) | ✓ (titles) | ✓ (subjects) | — |

## Extension Points

Each analysis file has clearly marked `EXTENSION POINT` comments where regex extraction can be replaced with LLM calls. The recommended upgrade path:

1. **Keyword extraction** → Call Claude Haiku to return JSON keywords from goal text
2. **Commitment detection** → Call Claude Haiku to parse email for structured commitment JSON
3. **Topic extraction** → Call Claude Haiku to return semantic topic lists from text blocks

These extensions would add `pydantic-ai` and `anthropic` as dependencies and require `ANTHROPIC_API_KEY` in `.env`.

## Output Conventions

All outputs follow the terse + dense pattern:
- Terse alerts in `output/alerts/` (actionable findings)
- Dense reports in `output/reports/` (full analysis)
- Activity entries appended to `logs/activity.md`
