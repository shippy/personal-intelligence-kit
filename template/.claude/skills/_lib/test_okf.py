import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import okf


def test_render_frontmatter_quotes_strings_and_skips_none():
    block = okf.render_frontmatter(
        {"type": "alert", "title": "Health: 2026", "resource": None, "tags": ["a", "b"]}
    )
    assert block == (
        '---\n'
        'type: "alert"\n'
        'title: "Health: 2026"\n'
        'tags: ["a", "b"]\n'
        '---\n'
        '\n'
    )


def test_render_frontmatter_escapes_quotes():
    block = okf.render_frontmatter({"type": 'a"b'})
    assert 'type: "a\\"b"' in block


def test_write_concept_writes_frontmatter_and_body(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)
    path = okf.write_concept(
        "alerts", "demo",
        type="alert", title="Demo", body="# Demo\n\nhi\n",
        timestamp="2026-06-20", status="final",
    )
    assert path == tmp_path / "alerts" / "demo.md"
    text = path.read_text()
    assert text.startswith('---\ntype: "alert"\n')
    assert 'status: "final"' in text
    assert text.endswith("# Demo\n\nhi\n")


def test_write_concept_rejects_reserved_slug(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)
    with pytest.raises(ValueError):
        okf.write_concept("alerts", "index", type="alert", title="x", body="")


def test_log_records_creation_then_update(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)
    okf.write_concept("alerts", "demo", type="alert", title="First",
                      body="x", timestamp="2026-06-20")
    okf.write_concept("alerts", "demo", type="alert", title="First again",
                      body="y", timestamp="2026-06-20")
    log = (tmp_path / "log.md").read_text()
    assert "## 2026-06-20" in log
    assert "* **Creation**: First" in log
    assert "* **Update**: First again" in log


def test_log_newest_date_first(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)
    okf.write_concept("alerts", "a", type="alert", title="A", body="x",
                      timestamp="2026-06-19")
    okf.write_concept("alerts", "b", type="alert", title="B", body="x",
                      timestamp="2026-06-20")
    log = (tmp_path / "log.md").read_text()
    assert log.index("## 2026-06-20") < log.index("## 2026-06-19")


def test_link_makes_bundle_relative_absolute():
    assert okf.link("alerts/foo.md", "Foo") == "[Foo](/alerts/foo.md)"
    assert okf.link("/reports/bar.md", "Bar") == "[Bar](/reports/bar.md)"


def test_read_frontmatter_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)
    path = okf.write_concept("reports", "r", type="report", title="T",
                             body="# T\n", tags=["x"], timestamp="2026-06-20")
    fm = okf.read_frontmatter(path)
    assert fm["type"] == "report"
    assert fm["title"] == "T"
    assert fm["tags"] == ["x"]


def test_read_frontmatter_unescapes_and_splits(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)
    path = okf.write_concept("reports", "r", type="report",
                             title='Has "quotes" and, comma', body="x",
                             tags=["a, b", 'c"d'], timestamp="2026-06-20")
    fm = okf.read_frontmatter(path)
    assert fm["title"] == 'Has "quotes" and, comma'
    assert fm["tags"] == ["a, b", 'c"d']
