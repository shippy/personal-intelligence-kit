# Activity log split — design

**Date:** 2026-11-16 · **Status:** approved by Šimon ("Sounds good") on the three-point proposal below; details marked *(Claude's call)* were filled in afterwards.

## Why

The generated vault's `logs/activity.md` reached 22k lines / 731 KB and 1,750 entries in eleven months (measured in pki-simon, 2026-11-16).

- **Volume:** about 70% of entries (≈1,230) are nightly job heartbeats: journal ingest, git-stats ingest, browser ingest, data sync, tasks import, legacy "To-Do sync". They bury the ≈500 entries that record real work.
- **Inconsistent format:** every entry has `**Action:**`, but the other fields vary (`Output`/`Outputs`, `Sources`/`Sources used`). About 87 headings don't follow `## YYYY-MM-DDTHH:MM`: bare dates, `T (later)`, `T (evening)`, `T` with no time, seconds, offsets. Dates go backwards 9 times.
- **Cost:** every append and every search touches one ever-growing file.

## Goals

1. **Job runs go to `logs/sync.jsonl`,** one JSON object per line. `activity` then holds only session and analysis work.
2. **One activity file per month:** `logs/activity/YYYY-MM.md`.
3. **A fixed entry format,** written by one helper that skills import and host sessions call from the CLI.
4. **A one-off migration** that splits an existing `logs/activity.md` into the new layout without losing an entry.

## Non-goals

- No rotation for `sync.jsonl` (≈1,200 lines/year).
- No query tool for either log. `jq` and `grep` suffice.
- No change to `logs/errors.md`.
- No parsing of legacy job entries into structured stats. They're kept as raw text.

## Anti-goals

- Losing or reordering any legacy entry's text.
- A write path that can land in the notes vault (every write goes through `assert_writable` / `safe_write`).
- Two copies of a log entry (the migration moves the old file aside; it never leaves both live).

## Decisions

- **Config (Claude's call).** `[logs]` gains `activity_dir = "logs/activity"` and `sync = "logs/sync.jsonl"`. The existing `activity` key stays and now names the legacy file the migration reads. Missing keys fall back to these defaults, so vaults whose `vault.toml` didn't take the copier merge still work.
- **API (Claude's call).** Path resolvers stay in `vault_config.py`. Writers go in a new `_lib/activity.py`. `activity_log()` keeps its name but returns the month file, so existing call sites and test monkeypatches keep working.
- **Classification (Claude's call).** Ingest and sync scripts call `log_sync`. Analysis skills and `add_tasks.py` (which changes an external system) call `append_activity`. `data-sync` writes one summary line for the run; each sub-job also writes its own.
- **Entry format:**

  ```
  ## 2026-11-16T12:00

  **Action:** <text>
  **Sources:** a, b
  **Outputs:**
  - [[link]]
  **Notes:** <text>
  ```

  Empty fields are left out. A list value renders as bullets. Each entry is written with a single `write()` call (O_APPEND), so concurrent launchd jobs and sessions don't interleave.
- **Sync line (Claude's call).** `{"ts": "<ISO 8601, local, with offset, seconds>", "job": "<slug>", "ok": true|false|null, "duration_s": float|null, "stats": {}, "outputs": [], "message": str|null}`, written with `ensure_ascii=False`. Legacy lines add `"legacy": true, "raw": "<original entry body>"` and use `ok: null`.
- **Migration (Claude's call).** A dry run is the default and prints counts. `--apply` writes the new files and renames the old one to `activity.md.pre-split-YYYYMMDD`. It refuses if the targets already hold entries. Within a date, entries keep their file order (stable sort by date only), because time-less headings like "(later)" depend on it. Date-only headings stay `## YYYY-MM-DD`, and any suffix moves into a `**Time:**` line. Headings without a parseable date go to `logs/activity/unsorted.md`. Before renaming anything, the run checks that entries in equals entries out.
- **Rollout.** Upstream PR → Šimon merges and tags → `copier update` PR in pki-simon → migration dry run on the host → Šimon OKs → `--apply`, between launchd runs.
