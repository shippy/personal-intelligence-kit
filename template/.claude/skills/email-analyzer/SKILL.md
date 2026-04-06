---
name: email-analyzer
description: Query the notmuch email archive for messages, threads, and patterns. Used by other skills (cross-source-queries, weekly-reflection) or invoked directly. Requires email source enabled and notmuch indexed.
---

# Email Analyzer

Query your notmuch-indexed email archive. Used by other skills or directly.

## Invocation

Direct use via `notmuch` CLI is fastest, but this skill provides helpful wrappers:

```bash
cd .claude/skills/email-analyzer
uv run query.py "date:7d.. tag:sent"           # Last week's sent mail
uv run query.py --recent 20                    # 20 most recent messages
uv run query.py --from "alice@example.com"     # From a specific sender
uv run query.py --stats                         # Volume stats
```

## What It Does

Wraps `notmuch search` and `notmuch show` with conveniences:
- Filters out bulk/newsletter senders heuristically
- Extracts "human" messages (has personal tone, not auto-generated)
- Formats results as markdown for piping into other skills

## Example: Human Messages Only

```bash
uv run query.py --human date:7d..
```

Filters:
- Skips messages with `unsubscribe` in the body
- Skips messages from `noreply@` / `notifications@` senders
- Prefers messages with salutations (`Hi {{ owner_name }}`, etc.)

## Used By

- `cross-source-queries/commitments.py` — scans sent mail for promises
- `weekly-reflection/weekly_reflection.py` — extracts recent subjects
- Ad-hoc: `/briefing: Alice` — gather all Alice-related messages

## Requirements

- `notmuch` installed and indexed (see `email-ingest`)
- Email source enabled in `vault.toml`

## Philosophy

Email is a graph of relationships, not a to-do list. This skill treats it as such: filter noise, surface signal, find the humans.
