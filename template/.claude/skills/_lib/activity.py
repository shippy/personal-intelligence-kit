# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Activity and job-run logging.

- ``append_activity`` appends a session/analysis entry to ``logs/activity/YYYY-MM.md``.
- ``log_sync`` appends one JSON line per scheduled job run to ``logs/sync.jsonl``.

Every write goes through ``vault_config.safe_write`` (refuses the notes vault) and
is a single ``write()`` in append mode, so concurrent writers don't interleave.

CLI for host sessions:
    uv run .claude/skills/_lib/activity.py --action "…" --sources a,b \\
        --output "[[…]]" --notes "…" [--field KEY=VALUE ...]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_config import activity_log, safe_write, sync_log  # noqa: E402

FieldValue = str | Sequence[str]
_HEADING_LINE = re.compile(r"^(#+ )", re.MULTILINE)
_BAD_KEY = re.compile(r"[\r\n*:]")


def _text(value: str) -> str:
    """Escape Markdown headings inside a value so an entry always has exactly one heading."""
    return _HEADING_LINE.sub(r"\\\1", str(value))


def format_entry(action: str, fields: Mapping[str, FieldValue] | None = None, *, when: datetime) -> str:
    """Render one activity entry: heading, **Action:**, then each non-empty field, then ``---``."""
    if not str(action).strip():
        raise ValueError("action must not be empty")
    lines = ["", f"## {when:%Y-%m-%dT%H:%M}", "", f"**Action:** {_text(action)}"]
    for key, value in (fields or {}).items():
        if not key or _BAD_KEY.search(key) or key != key.strip():
            raise ValueError(f"invalid field name: {key!r}")
        if value is None or value == "" or (not isinstance(value, str) and not list(value)):
            continue
        if isinstance(value, str):
            lines.append(f"**{key}:** {_text(value)}")
        else:
            lines.append(f"**{key}:**")
            lines.extend(f"- {_text(item)}" for item in value)
    lines += ["", "---", ""]
    return "\n".join(lines)


def append_activity(action: str, fields: Mapping[str, FieldValue] | None = None, *,
                    when: datetime | None = None) -> Path:
    """Append an entry to the month file chosen from `when` (default now). Returns the path."""
    when = when or datetime.now()
    path = activity_log(when)
    text = format_entry(action, fields, when=when)
    if not path.exists() or path.stat().st_size == 0:
        text = f"# Activity — {when:%Y-%m}\n" + text
    return safe_write(path, text, append=True)


def log_sync(job: str, *, ok: bool | None, duration_s: float | None = None,
             stats: Mapping[str, Any] | None = None, outputs: Sequence[str] = (),
             message: str | None = None, when: datetime | None = None) -> Path:
    """Append one JSON line describing a job run to sync.jsonl. Returns the path."""
    when = (when or datetime.now()).astimezone()
    record = {
        "ts": when.isoformat(timespec="seconds"),
        "job": job,
        "ok": ok,
        "duration_s": None if duration_s is None else round(float(duration_s), 3),
        "stats": dict(stats or {}),
        "outputs": [str(o) for o in outputs],
        "message": message,
    }
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    return safe_write(sync_log(), line, append=True)


def _field(arg: str) -> tuple[str, str]:
    key, sep, value = arg.partition("=")
    key = key.strip()
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got {arg!r}")
    return key, value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append an entry to the monthly activity log.")
    parser.add_argument("--action", required=True, help="What was done")
    parser.add_argument("--sources", help="Comma-separated sources used")
    parser.add_argument("--output", action="append", default=[], help="An output (repeatable)")
    parser.add_argument("--notes", help="Free-text notes")
    parser.add_argument("--field", action="append", default=[], type=_field, metavar="KEY=VALUE",
                        help="Extra field (repeatable)")
    args = parser.parse_args(argv)

    fields: dict[str, FieldValue] = {}
    if args.sources:
        fields["Sources"] = ", ".join(s.strip() for s in args.sources.split(",") if s.strip())
    if args.output:
        fields["Outputs"] = args.output
    for key, value in args.field:
        fields[key] = value
    if args.notes:
        fields["Notes"] = args.notes
    try:
        path = append_activity(args.action, fields)
    except ValueError as e:
        parser.error(str(e))
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
