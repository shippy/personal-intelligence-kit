import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import migrate_activity_log as mig
import vault_config as vc

FIXTURE = """# Activity Log
stray preamble line

## 2026-03-01T10:00:00

**Action:** Morning planning
**Sources:** journal

---

## 2026-03-01T (later)

**Action:** Follow-up review

---

## 2026-02-16T15:10

**Action:** Older session placed late

---

## garbage

**Action:** Unknown date entry

---

## 2026-03-02T05:00

**Action:** Data sync
**Results:**
- ✓ journal (1.0s): ok

---

## 2026-03-03T09:00

**Action:** Přehled týdne — žluťoučký kůň

---

## 2026-03-04T08:00+01:00

**Action:** Last entry without separator
"""


@pytest.fixture
def vault(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    notes = tmp_path / "Notes"
    notes.mkdir()
    root.mkdir()
    (root / "vault.toml").write_text(f'[notes]\napp = "obsidian"\nvault_path = "{notes}"\n')
    (root / "logs").mkdir()
    (root / "logs" / "activity.md").write_text(FIXTURE, encoding="utf-8")
    monkeypatch.setenv(vc.ENV_VAR, str(root))
    vc.clear_cache()
    yield root.resolve()
    vc.clear_cache()


def _snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, p.read_bytes())
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_parse_counts():
    preamble, entries = mig.parse_entries(FIXTURE)
    assert len(entries) == 7
    assert "stray preamble line" in preamble
    later = entries[1]
    assert later.date == date(2026, 3, 1) and later.time is None and later.suffix == "(later)"
    assert entries[0].time == "10:00"
    assert entries[3].date is None
    assert entries[4].action == "Data sync"
    assert not entries[-1].body.endswith("---")
    assert "\n---" not in entries[0].body


def test_plan_split_routes():
    split = mig.plan_split(*mig.parse_entries(FIXTURE))
    assert len(split.sync_lines) == 1
    line = split.sync_lines[0]
    assert line["job"] == "data-sync" and line["legacy"] is True and line["ok"] is None
    assert line["ts"] == "2026-03-02T05:00"
    assert "✓ journal" in line["raw"]
    assert len(split.unsorted) == 1 and "garbage" in split.unsorted[0]
    assert list(split.months) == ["2026-02", "2026-03"]
    assert sum(len(v) for v in split.months.values()) == 5


def test_order_within_date_preserved():
    split = mig.plan_split(*mig.parse_entries(FIXTURE))
    march = "".join(split.months["2026-03"])
    assert march.index("## 2026-03-01T10:00\n") < march.index("## 2026-03-01\n")
    later = split.months["2026-03"][1]
    assert later.startswith("\n## 2026-03-01\n\n**Time:** (later)\n")
    # February entry sorts before March ones despite appearing later in the file.
    assert "Older session placed late" in split.months["2026-02"][0]
    assert split.fixed_headings >= 3


def test_job_prefixes():
    assert mig.JOB_ACTIONS["To-Do sync"] == "tasks-import"
    assert mig.job_slug("Journal ingest (rosebud)") == "journal-ingest"
    assert mig.job_slug("Weekly review") is None


def test_dry_run_writes_nothing(vault, capsys):
    before = _snapshot(vault)
    assert mig.main([]) == 0
    assert _snapshot(vault) == before
    out = capsys.readouterr().out
    assert "2026-03: 4" in out and "jobs: 1" in out and "unsorted: 1" in out


def test_apply_roundtrip(vault):
    assert mig.main(["--apply"]) == 0
    logs = vault / "logs"
    assert not (logs / "activity.md").exists()
    assert len(list(logs.glob("activity.md.pre-split-*"))) == 1
    month_text = "".join((logs / "activity" / f"{m}.md").read_text(encoding="utf-8") for m in ("2026-02", "2026-03"))
    unsorted = (logs / "activity" / "unsorted.md").read_text(encoding="utf-8")
    sync_lines = [json.loads(line) for line in (logs / "sync.jsonl").read_text(encoding="utf-8").splitlines()]
    everything = month_text + unsorted + "".join(s["raw"] for s in sync_lines)
    _, entries = mig.parse_entries(FIXTURE)
    for e in entries:
        assert everything.count(e.body) == 1, e.body
    assert "Přehled týdne — žluťoučký kůň" in month_text
    assert "stray preamble line" in (logs / "activity" / "2026-02.md").read_text(encoding="utf-8")
    assert (logs / "activity" / "2026-03.md").read_text(encoding="utf-8").startswith("# Activity — 2026-03\n")
    assert month_text.count("\n## ") + unsorted.count("\n## ") + len(sync_lines) == 7


def test_apply_twice_refuses(vault):
    assert mig.main(["--apply"]) == 0
    before = _snapshot(vault)
    assert mig.main(["--apply"]) == 2
    assert _snapshot(vault) == before


def test_apply_refuses_when_targets_hold_entries(vault):
    sync = vault / "logs" / "sync.jsonl"
    sync.write_text('{"job": "x"}\n')
    before = _snapshot(vault)
    assert mig.main(["--apply"]) == 2
    assert _snapshot(vault) == before


def test_apply_refuses_targets_in_notes(vault, tmp_path):
    toml = vault / "vault.toml"
    toml.write_text(toml.read_text() + f'\n[logs]\nactivity_dir = "{tmp_path}/Notes/activity"\n')
    vc.clear_cache()
    assert mig.main(["--apply"]) == 2
    assert list((tmp_path / "Notes").iterdir()) == []
    assert (vault / "logs" / "activity.md").exists()
