# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""One-off migration: split logs/activity.md into monthly files and sync.jsonl.

    uv run .claude/skills/_lib/migrate_activity_log.py           # dry run: print counts
    uv run .claude/skills/_lib/migrate_activity_log.py --apply   # write, then rename the old file

Job-run entries (ingests, data sync, tasks import) become legacy lines in
``logs/sync.jsonl`` with their original text in ``raw``. Every other entry goes to
``logs/activity/YYYY-MM.md``, sorted by date only (entries on the same date keep
their file order). Entries whose heading has no parseable date go to
``logs/activity/unsorted.md``. ``--apply`` refuses if any target file already exists,
checks that entries in equals entries out, and renames the old file to
``activity.md.pre-split-YYYYMMDD``; it never deletes it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_config import (  # noqa: E402
    VaultConfigError,
    activity_dir,
    assert_writable,
    legacy_activity_log,
    sync_log,
)

HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2})(?::\d{2})?(?:[+-]\d{2}:?\d{2})?)?\s*(.*)$")
ACTION = re.compile(r"^\*\*Action:\*\*\s*(.*)$", re.MULTILINE)

JOB_ACTIONS: dict[str, str] = {
    "Journal ingest": "journal-ingest",
    "Git commit stats ingest": "git-stats",
    "Browser history ingest": "browser-ingest",
    "Data sync": "data-sync",
    "Tasks import": "tasks-import",
    "To-Do sync": "tasks-import",
}


@dataclass
class Entry:
    heading: str
    date: date | None
    time: str | None
    suffix: str | None
    body: str
    action: str | None
    index: int


@dataclass
class Split:
    months: dict[str, list[str]]
    sync_lines: list[dict]
    unsorted: list[str]
    fixed_headings: int
    preamble: str = ""


def _parse_heading(heading: str) -> tuple[date | None, str | None, str | None]:
    m = HEADING.match(heading)
    if not m:
        return None, None, None
    try:
        d = date.fromisoformat(m.group(1))
    except ValueError:
        return None, None, None
    suffix = m.group(3).strip()
    if suffix == "T" or suffix.startswith("T "):  # "T (later)", bare "T"
        suffix = suffix[1:].strip()
    return d, m.group(2), suffix or None


def _strip_body(text: str) -> str:
    body = text.strip("\n")
    if body == "---":
        return ""
    if body.endswith("\n---"):
        body = body[: -len("\n---")].rstrip("\n")
    return body


def parse_entries(text: str) -> tuple[str, list[Entry]]:
    """Split the legacy log into (preamble, entries) on lines starting with ``## ``."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("## ")]
    preamble = "\n".join(lines[: starts[0]] if starts else lines)
    entries = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        heading = lines[start].rstrip()
        body = _strip_body("\n".join(lines[start + 1 : end]))
        d, t, suffix = _parse_heading(heading)
        m = ACTION.search(body)
        entries.append(Entry(heading, d, t, suffix, body, m.group(1).strip() if m else None, n))
    return preamble, entries


def job_slug(action: str | None) -> str | None:
    if not action:
        return None
    for prefix, slug in JOB_ACTIONS.items():
        if action.startswith(prefix):
            return slug
    return None


def _render(heading: str, body: str, time_note: str | None = None) -> str:
    time_line = f"**Time:** {time_note}\n" if time_note else ""
    return f"\n{heading}\n\n{time_line}{body}\n\n---\n"


def plan_split(preamble: str, entries: list[Entry]) -> Split:
    months: dict[str, list[str]] = {}
    sync_lines: list[dict] = []
    unsorted: list[str] = []
    fixed = 0
    sessions = []
    for e in entries:
        if e.date is None:
            unsorted.append(_render(e.heading, e.body))
        elif slug := job_slug(e.action):
            sync_lines.append({
                "ts": f"{e.date.isoformat()}T{e.time or '00:00'}", "job": slug, "ok": None,
                "duration_s": None, "stats": {}, "outputs": [], "message": None,
                "legacy": True, "raw": e.body, "heading": e.heading,
            })
        else:
            sessions.append(e)
    for e in sorted(sessions, key=lambda e: e.date):  # stable: same date keeps file order
        heading = f"## {e.date.isoformat()}T{e.time}" if e.time else f"## {e.date.isoformat()}"
        fixed += heading != e.heading
        months.setdefault(f"{e.date:%Y-%m}", []).append(_render(heading, e.body, e.suffix))
    kept = preamble.strip("\n").splitlines()
    if kept and kept[0].startswith("# "):
        kept = kept[1:]
    return Split(dict(sorted(months.items())), sync_lines, unsorted, fixed, "\n".join(kept).strip("\n"))


def _render_files(split: Split, adir: Path, sync: Path) -> dict[Path, str]:
    files: dict[Path, str] = {}
    pre = f"\n{split.preamble}\n" if split.preamble else ""
    for n, (month, items) in enumerate(split.months.items()):
        files[adir / f"{month}.md"] = f"# Activity — {month}\n" + (pre if n == 0 else "") + "".join(items)
    if split.unsorted or (pre and not split.months):
        files[adir / "unsorted.md"] = "# Activity — unsorted\n" + ("" if split.months else pre) + "".join(split.unsorted)
    if split.sync_lines:
        files[sync] = "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in split.sync_lines)
    return files


def _count_out(files: dict[Path, str], sync: Path) -> int:
    total = 0
    for path, text in files.items():
        total += len(text.splitlines()) if path == sync else len(parse_entries(text)[1])
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Split logs/activity.md into monthly files and sync.jsonl.")
    parser.add_argument("--apply", action="store_true", help="write the new files and rename the old one")
    args = parser.parse_args(argv)

    try:
        legacy, adir, sync = legacy_activity_log(), activity_dir(), sync_log()
    except VaultConfigError as e:
        print(f"Refusing: {e}", file=sys.stderr)
        return 2
    if not legacy.is_file():
        print(f"Nothing to migrate: {legacy} does not exist.", file=sys.stderr)
        return 2

    preamble, entries = parse_entries(legacy.read_text(encoding="utf-8"))
    split = plan_split(preamble, entries)
    files = _render_files(split, adir, sync)
    renamed = legacy.with_name(f"{legacy.name}.pre-split-{datetime.now():%Y%m%d}")

    for month, items in split.months.items():
        print(f"{month}: {len(items)}")
    print(f"jobs: {len(split.sync_lines)}")
    print(f"unsorted: {len(split.unsorted)}")
    print(f"fixed headings: {split.fixed_headings}")
    print(f"entries in: {len(entries)}")

    if _count_out(files, sync) != len(entries):
        print("Refusing: entry count out does not match entry count in.", file=sys.stderr)
        return 1
    try:
        targets = [assert_writable(p) for p in [*files, renamed]]
    except VaultConfigError as e:
        print(f"Refusing: {e}", file=sys.stderr)
        return 2
    blocking = [p for p in targets if p.exists()]
    for p in blocking:
        print(f"target already exists: {p}", file=sys.stderr)

    if not args.apply:
        print("Dry run: nothing written. Re-run with --apply.")
        return 0
    if blocking:
        print("Refusing: move the existing targets aside first.", file=sys.stderr)
        return 2

    # 1. Stage every output under a unique temp name next to its target and verify it.
    # 2. Publish with os.link, which fails instead of overwriting an existing target.
    # 3. Move the legacy file aside the same way (link to the backup name, then unlink).
    # On any failure only this run's staged temps are removed. Published targets are never
    # deleted or modified (a host session may already be appending to them); they are listed
    # for the person to reconcile by hand.
    staged: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for path, text in files.items():
            target = assert_writable(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
            staged[target] = assert_writable(tmp)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
        written = {p: staged[assert_writable(p)].read_text(encoding="utf-8") for p in files}
        if _count_out(written, sync) != len(entries) or written != files:
            raise RuntimeError("staged files don't hold every entry")
        for target, tmp in staged.items():
            os.link(tmp, target)  # FileExistsError if the target appeared meanwhile
            published.append(target)
        os.link(legacy, assert_writable(renamed))
        published.append(renamed)
        legacy.unlink()
    except Exception as e:
        print(f"Error: {e}. {legacy.name} left in place.", file=sys.stderr)
        if published:
            print("These files were already published and were left untouched; reconcile them by hand"
                  f" against {legacy} (move them aside before re-running --apply):", file=sys.stderr)
            for target in published:
                print(f"  {target}", file=sys.stderr)
        return 1
    finally:
        for tmp in staged.values():
            tmp.unlink(missing_ok=True)
    print(f"Wrote {len(files)} file(s); renamed {legacy.name} → {renamed.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
