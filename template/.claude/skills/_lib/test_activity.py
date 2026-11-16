import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import activity


@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "activity_log", lambda when=None: tmp_path / "activity" / f"{(when or datetime.now()):%Y-%m}.md")
    monkeypatch.setattr(activity, "sync_log", lambda: tmp_path / "sync.jsonl")
    return tmp_path


def test_format_entry_exact():
    text = activity.format_entry(
        "Weekly review",
        {"Sources": "journal, email", "Outputs": ["[[a]]", "[[b]]"], "Notes": ""},
        when=datetime(2026, 11, 16, 12, 0),
    )
    assert text == (
        "\n## 2026-11-16T12:00\n\n**Action:** Weekly review\n**Sources:** journal, email\n"
        "**Outputs:**\n- [[a]]\n- [[b]]\n\n---\n"
    )


def test_format_entry_escapes_heading_lines_in_values():
    text = activity.format_entry("A\n## 2020-01-01T00:00", {"Notes": "x\n## fake"}, when=datetime(2026, 1, 1))
    assert text.count("\n## ") == 1


def test_format_entry_rejects_bad_field_names():
    with pytest.raises(ValueError):
        activity.format_entry("A", {"Bad\nKey": "v"}, when=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        activity.format_entry("", when=datetime(2026, 1, 1))


def test_append_creates_month_header_once(logs):
    activity.append_activity("one", when=datetime(2026, 11, 1, 9, 0))
    path = activity.append_activity("two", when=datetime(2026, 11, 2, 9, 0))
    text = path.read_text()
    assert text.startswith("# Activity — 2026-11\n")
    assert text.count("# Activity — 2026-11") == 1
    assert text.count("\n## ") == 2


def test_month_boundary_uses_when(logs):
    path = activity.append_activity("x", when=datetime(2026, 11, 30, 23, 59))
    assert path == logs / "activity" / "2026-11.md"
    assert "## 2026-11-30T23:59" in path.read_text()


def test_log_sync_line(logs):
    path = activity.log_sync("journal-ingest", ok=True, stats={"new": 3})
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["job"] == "journal-ingest"
    assert obj["ok"] is True
    assert obj["stats"] == {"new": 3}
    assert list(obj) == ["ts", "job", "ok", "duration_s", "stats", "outputs", "message"]
    assert datetime.fromisoformat(obj["ts"]).tzinfo is not None


def test_unicode_roundtrip(logs):
    path = activity.log_sync("data-sync", ok=False, message="✗ deník – chyba")
    raw = path.read_text(encoding="utf-8")
    assert "✗ deník – chyba" in raw and "\\u" not in raw
    md = activity.append_activity("✓ Týdenní přehled", {"Notes": "✗ deník – chyba"})
    assert "✓ Týdenní přehled" in md.read_text(encoding="utf-8")
    assert "✗ deník – chyba" in md.read_text(encoding="utf-8")


def test_cli_writes_entry(logs, capsys):
    assert activity.main(["--action", "A", "--sources", "x,y", "--output", "[[o]]", "--notes", "n",
                          "--field", "LLM=Yes"]) == 0
    path = Path(capsys.readouterr().out.strip())
    text = path.read_text()
    assert "**Sources:** x, y" in text
    assert "- [[o]]" in text
    assert text.index("**Outputs:**") < text.index("**LLM:** Yes") < text.index("**Notes:** n")


def test_cli_rejects_field_without_equals(logs):
    with pytest.raises(SystemExit):
        activity.main(["--action", "A", "--field", "novalue"])
    assert not (logs / "activity").exists()
