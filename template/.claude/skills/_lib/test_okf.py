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
