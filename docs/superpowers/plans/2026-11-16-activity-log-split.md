# Activity Log Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the vault's single `logs/activity.md` into monthly session logs plus a JSONL job log, with one formatter for both and a lossless migration of the old file.

**Architecture:** Path resolvers stay in `_lib/vault_config.py`. A new `_lib/activity.py` owns entry formatting and appending (`append_activity`, `log_sync`, plus a CLI for host sessions). Every skill writer switches to one of those two functions. A separate `_lib/migrate_activity_log.py` does the one-off split.

**Tech Stack:** Python 3.11+ stdlib only (`json`, `datetime`, `re`, `argparse`), `uv`, `pytest`, copier.

**Spec:** `docs/superpowers/specs/2026-11-16-activity-log-split-design.md`

## Global Constraints

- Run everything with `uv`, never bare `python`/`pip`. Skill tests: `uv --no-config run --locked --project template/.claude/skills/weekly-reflection --with pytest --with rapidfuzz pytest -q template/.claude/skills`. Template tests: `uv run --no-project --with copier --with pytest pytest -q tests`.
- No new third-party dependencies in the write path.
- Every write goes through `assert_writable` / `safe_write` from `vault_config.py`.
- Each log entry is written with a single `write()` call in append mode.
- Defaults: `activity_dir = "logs/activity"`, `sync = "logs/sync.jsonl"`, month file name `YYYY-MM.md`, heading format `## %Y-%m-%dT%H:%M`.
- JSON lines use `ensure_ascii=False`. `ts` is local ISO 8601 with offset and seconds (`datetime.now().astimezone().isoformat(timespec="seconds")`).
- Branch `activity-log-split`. Open the PR as the bot with `gh pr create --reviewer shippy`. Don't tag a version; Šimon tags after merging.
- Another branch (`todo-task-writer`) has uncommitted edits to `tasks-import/import_tasks.py` in Šimon's main checkout. Keep the change to that file minimal (only the logging block) so his rebase stays easy.

## Review Focus

1. **Month boundary:** an entry with `when=2026-11-30T23:59` lands in `2026-11.md` even if written after midnight. The file is chosen from `when`, never from a second `now()` call. Test in Task 2.
2. **Old config:** a `vault.toml` whose `[logs]` lacks `activity_dir`/`sync` still resolves to the defaults. Test in Task 1.
3. **Unicode:** Czech diacritics and ✓/✗ round-trip through both the markdown and the JSONL writers and through the migration. Tests in Tasks 2 and 5.
4. **Messy legacy input:** a preamble before the first heading, a heading with no parseable date, `## 2026-03-01T (later)`, `## 2026-03-01T10:00:00`, and an entry with no trailing `---` all survive the migration. The entry count in equals the count out. Test in Task 5.
5. **Re-running the migration:** after `--apply`, a second `--apply` refuses with a non-zero exit and changes nothing. Test in Task 5.

---

### Task 1: Config keys and path resolvers

**Files:**
- Modify: `template/vault.toml.jinja` (`[logs]` block)
- Modify: `template/.claude/skills/_lib/vault_config.py:395-401`
- Modify: `template/.claude/skills/_lib/__init__.py` (exports)
- Test: `template/.claude/skills/_lib/test_vault_config.py`, `tests/test_template.py:63`

**Interfaces:**
- Produces: `activity_dir() -> Path`; `activity_log(when: datetime | None = None) -> Path` (returns `activity_dir() / f"{when:%Y-%m}.md"`, with `when` defaulting to now); `legacy_activity_log() -> Path` (the `activity` key, default `logs/activity.md`); `sync_log() -> Path`. All four go through `assert_writable` and are exported from `_lib/__init__.py`.

- [ ] **Step 1: Write failing tests** in `test_vault_config.py`, reusing that file's existing fixtures:
  - `test_activity_log_is_monthly`: `vc.activity_log(datetime(2026, 11, 30, 23, 59)) == root / "logs" / "activity" / "2026-11.md"`
  - `test_logs_defaults_without_new_keys`: config whose `[logs]` has only `activity = "logs/activity.md"` → `vc.sync_log() == root / "logs" / "sync.jsonl"` and `vc.activity_dir() == root / "logs" / "activity"`
  - `test_legacy_activity_log`: `vc.legacy_activity_log() == root / "logs" / "activity.md"`
  - Update the existing `activity_log()` assertions at lines 135 and 178 to the monthly path.
  - Keep `test_activity_log_inside_notes_is_refused`, and add the same check for `activity_dir` and `sync` pointed inside notes.
- [ ] **Step 2: Run, expect FAIL** (`AttributeError` / wrong path).
- [ ] **Step 3: Implement** the four resolvers. Add `activity_dir = "logs/activity"` and `sync = "logs/sync.jsonl"` to `vault.toml.jinja`, and add assertions for both keys in `tests/test_template.py`.
- [ ] **Step 4: Run both test commands, expect PASS.**
- [ ] **Step 5: Commit** `feat(logs): monthly activity and sync log paths`.

### Task 2: Writers and CLI

**Files:**
- Create: `template/.claude/skills/_lib/activity.py`
- Modify: `template/.claude/skills/_lib/__init__.py`
- Test: `template/.claude/skills/_lib/test_activity.py`

**Interfaces:**
- Consumes: `activity_log`, `sync_log`, `safe_write` (Task 1).
- Produces:
  - `format_entry(action: str, fields: Mapping[str, str | Sequence[str]] | None = None, *, when: datetime) -> str`. Heading `## {when:%Y-%m-%dT%H:%M}`, then `**Action:** …`, then each non-empty field in the given order. A str renders as `**K:** v`; a list renders as `**K:**` followed by `- item` lines. Ends with `\n---\n`.
  - `append_activity(action: str, fields: Mapping[str, str | Sequence[str]] | None = None, *, when: datetime | None = None) -> Path`. When creating a new month file, it first writes `# Activity — YYYY-MM\n`. One `safe_write(..., append=True)` per entry.
  - `log_sync(job: str, *, ok: bool | None, duration_s: float | None = None, stats: Mapping[str, Any] | None = None, outputs: Sequence[str] = (), message: str | None = None, when: datetime | None = None) -> Path`. Writes one JSON line with keys in the order `ts, job, ok, duration_s, stats, outputs, message`.
  - CLI: `uv run .claude/skills/_lib/activity.py --action TEXT [--sources a,b] [--output X ...] [--notes TEXT] [--field KEY=VALUE ...]` calls `append_activity` with fields in the order Sources, Outputs, extra fields, Notes, and prints the path written.

- [ ] **Step 1: Write failing tests** (monkeypatch `activity_log`/`sync_log` to `tmp_path`, as `test_okf_commitment.py` does):
  - `test_format_entry_exact`: `format_entry("Weekly review", {"Sources": "journal, email", "Outputs": ["[[a]]", "[[b]]"], "Notes": ""}, when=datetime(2026,11,16,12,0)) == "\n## 2026-11-16T12:00\n\n**Action:** Weekly review\n**Sources:** journal, email\n**Outputs:**\n- [[a]]\n- [[b]]\n\n---\n"`. The empty Notes is omitted.
  - `test_append_creates_month_header_once`: two appends in November → the file starts with `# Activity — 2026-11\n` exactly once and holds two `## ` headings.
  - `test_month_boundary_uses_when`: `append_activity("x", when=datetime(2026,11,30,23,59))` writes to `2026-11.md` (see Review Focus 1).
  - `test_log_sync_line`: `json.loads` of the single line → `job == "journal-ingest"`, `ok is True`, `stats == {"new": 3}`, `list(obj) == ["ts","job","ok","duration_s","stats","outputs","message"]`, and `ts` parses with `datetime.fromisoformat` and has `tzinfo`.
  - `test_unicode_roundtrip`: `log_sync("data-sync", ok=False, message="✗ deník – chyba")`; the raw file text contains `✗ deník – chyba` (not `\u` escapes). The same text through `append_activity` reads back verbatim.
  - `test_cli_writes_entry`: run `main(["--action","A","--sources","x,y","--output","[[o]]","--notes","n"])` and check the file contains `**Sources:** x, y` and `- [[o]]`.
- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError`).
- [ ] **Step 3: Implement** `activity.py` with inline script metadata (`# /// script`, `requires-python = ">=3.11"`, no dependencies) and `main(argv: list[str] | None = None) -> int`. Export `append_activity` and `log_sync` from `__init__`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(logs): append_activity, log_sync and CLI`.

### Task 3: Job writers → `log_sync`

**Files (logging block only):** `data-sync/sync_all.py:191-205`, `journal-ingest/ingest.py:~387`, `browser-ingest/ingest.py:~161`, `git-stats/ingest.py:~327`, `tasks-import/import_tasks.py` (its `activity_log()` block). Tests: `browser-ingest/test_browser_ingest.py:42`, plus each skill's existing tests.

**Interfaces:** Consumes `log_sync` (Task 2). Job slugs are fixed: `data-sync`, `journal-ingest`, `browser-ingest`, `git-stats`, `tasks-import`. `stats` carries the numbers each script already writes today (e.g. `{"new": n, "updated": m}`; for data-sync `{"sources": {name: {"ok": bool, "duration_s": float, "message": str}}}`). `outputs` carries the DB path each script writes today.

- [ ] **Step 1: Update tests first:** `test_browser_ingest.py:42` asserts `(content / "logs" / "sync.jsonl").exists()` and that its last line has `job == "browser-ingest"`. Add the same one-line assertion to any existing test of the other four scripts that runs the logging path.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Replace each hand-written markdown block** with one `log_sync(...)` call. data-sync passes `ok=all_ok` and `duration_s=total`.
- [ ] **Step 4: Run the full skill test command, expect PASS.**
- [ ] **Step 5: Commit** `refactor(logs): job runs write sync.jsonl`.

### Task 4: Session and analysis writers → `append_activity`

**Files (logging block only):** `stale-drafts/stale_drafts.py:256-264`, `cross-source-queries/{commitment_accountability.py:~180, intention_reality_gaps.py:~697, serendipity_convergence.py:~318}`, `session-analyzer/analyze.py:~166`, `weekly-reflection/weekly_reflection.py:~104`, `tasks-import/add_tasks.py:153-163`. Tests: `tasks-import/test_add_tasks.py:76`, `cross-source-queries/test_okf_*.py`.

**Interfaces:** Consumes `append_activity` (Task 2). Each call keeps today's Action text and today's extra fields as `fields` entries (e.g. stale-drafts `{"Sources": ..., "Finishable": str(n), "Drop": str(m), "LLM": "Yes"/"No"}`; add_tasks `{"Created": [titles], "Stopped at failure": failed}`).

- [ ] **Step 1: Update tests first:**
  - `test_add_tasks.py:76`: read the month file (`vault/"logs"/"activity"/f"{now:%Y-%m}.md"`) and assert the `**Created:**` bullets.
  - `test_okf_*.py`: they currently monkeypatch `mod.activity_log`. Once those modules call `append_activity`, that patch no longer redirects the write, so they would write to the configured vault. Retarget them at the name `append_activity` resolves, i.e. `activity.activity_log`, and assert that `tmp_path` received the entry.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Replace each block** with one `append_activity(...)` call.
- [ ] **Step 4: Run the full skill test command, expect PASS.** Then `grep -rn 'open(.*activity\|activity_log().open' template/.claude/skills` should return nothing outside `_lib/`.
- [ ] **Step 5: Commit** `refactor(logs): session writers use append_activity`.

### Task 5: Migration script

**Files:**
- Create: `template/.claude/skills/_lib/migrate_activity_log.py`
- Test: `template/.claude/skills/_lib/test_migrate_activity_log.py`

**Interfaces:**
- Consumes: `legacy_activity_log`, `activity_dir`, `sync_log` (Task 1); `format`-compatible output (Task 2's file header `# Activity — YYYY-MM`).
- Produces:
  - `@dataclass Entry(heading: str, date: date | None, time: str | None, suffix: str | None, body: str, action: str | None, index: int)`
  - `parse_entries(text: str) -> tuple[str, list[Entry]]` returns `(preamble, entries)`. It splits on lines matching `^## `, strips one trailing `\n---\n` separator per entry, and parses the heading with `^## (\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2})(?::\d{2})?(?:[+-]\d{2}:?\d{2})?)?\s*(.*)$`. The third group becomes `suffix` (e.g. `T (later)` → time None, suffix `(later)`).
  - `JOB_ACTIONS: dict[str, str]` maps an Action prefix to a slug: `"Journal ingest" → "journal-ingest"`, `"Git commit stats ingest" → "git-stats"`, `"Browser history ingest" → "browser-ingest"`, `"Data sync" → "data-sync"`, `"Tasks import" → "tasks-import"`, `"To-Do sync" → "tasks-import"`.
  - `plan_split(preamble: str, entries: list[Entry]) -> Split` where `Split(months: dict[str, list[str]], sync_lines: list[dict], unsorted: list[str], fixed_headings: int)`. Job entries become `{"ts": "<date>T<time or 00:00>", "job": slug, "ok": None, "duration_s": None, "stats": {}, "outputs": [], "message": None, "legacy": True, "raw": body}`. Session entries are stable-sorted by `date` only and re-rendered with heading `## YYYY-MM-DDTHH:MM`, or `## YYYY-MM-DD` plus a `**Time:** <suffix>` line when there's no time. Their bodies stay verbatim. A non-empty preamble that isn't just a top-level `# ` title goes first in the earliest month. Entries with `date is None` go to `unsorted`.
  - `main(argv: list[str] | None = None) -> int`. The default is a dry run that prints per-month counts, job count, unsorted count and fixed headings, and returns 0. `--apply` refuses (returns 2, writes nothing) if any target month file, `sync.jsonl` or `unsorted.md` already contains a `## ` heading or a line, or if the legacy file is missing. Otherwise it writes everything, asserts `len(entries) == sum(month entries) + len(sync_lines) + len(unsorted)`, then renames the legacy file to `activity.md.pre-split-YYYYMMDD`.

- [ ] **Step 1: Write failing tests** against a fixture string that contains all of Review Focus 4: a `# Activity Log` title plus one stray preamble line; `## 2026-03-01T10:00:00`; `## 2026-03-01T (later)`; `## 2026-02-16T15:10` placed *after* the March entries; a `## garbage` heading; a `**Action:** Data sync` job entry containing `✓ journal`; a Czech-diacritics session entry; a last entry with no `---`.
  - `test_parse_counts`: 7 entries; the preamble contains the stray line.
  - `test_plan_split_routes`: the job entry goes to `sync_lines`, with `raw` containing `✓ journal`. `garbage` goes to `unsorted`. Month keys are `["2026-02", "2026-03"]`.
  - `test_order_within_date_preserved`: in `2026-03`, the `10:00` entry precedes the `(later)` entry, which renders `## 2026-03-01` + `**Time:** (later)`.
  - `test_apply_roundtrip`: run `main(["--apply"])` on a tmp vault. Every original entry body appears exactly once across the outputs. The diacritics survive. The legacy file is renamed.
  - `test_apply_twice_refuses`: a second `main(["--apply"])` returns 2, and the files' mtimes and contents are unchanged.
  - `test_dry_run_writes_nothing`: `main([])` returns 0, and the tmp vault's file list is unchanged.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** per the interfaces.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(logs): one-off migration of activity.md`.

### Task 6: Docs, full verification, PR

**Files:** `template/CLAUDE.md.jinja` (Vault Geography tree, the "activity log is host-only" line, the Activity Logging section), `template/.claude/skills/{data-sync/SKILL.md.jinja:41, email-ingest/SKILL.md:24, stale-drafts/SKILL.md.jinja:51, tasks-import/SKILL.md.jinja:34+103, cross-source-queries/SKILL.md:88}`, `template/.claude/skills/_lib/OKF.md:46`, `template/.claude/skills/_lib/SCHEMAS.md` (new "sync.jsonl" section with the Task 2 line schema), `README.md:60` + a migration note.

- [ ] **Step 1: Rewrite the CLAUDE.md.jinja Activity Logging section** to: *Log significant actions with `uv run .claude/skills/_lib/activity.py --action "…" --sources a,b --output "[[…]]" --notes "…"`. It appends to `logs/activity/YYYY-MM.md`. Job runs go to `logs/sync.jsonl` automatically. Never edit either file by hand.* Point the other doc mentions at the right one of the two files.
- [ ] **Step 2: README:** add "Upgrading from a single activity.md: run `uv run .claude/skills/_lib/migrate_activity_log.py` (dry run), then `--apply` while no scheduled job is running."
- [ ] **Step 3: Run both test commands.** Expect PASS. `grep -rn "logs/activity.md" template README.md` returns only the legacy-key line in `vault.toml.jinja`, the migration docs and `tests/test_template.py`.
- [ ] **Step 4: Commit** `docs(logs): document split activity and sync logs`.
- [ ] **Step 5: Push and open the PR.** `git push -u origin activity-log-split`, then `gh pr create --reviewer shippy --title "Split activity log into monthly files and sync.jsonl"`. The body lists the spec and the plan, the new config keys, and the rollout steps from the spec.
