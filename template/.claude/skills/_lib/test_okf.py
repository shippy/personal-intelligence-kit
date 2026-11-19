import sys
from datetime import datetime, timedelta
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
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path if name == "root" else tmp_path / name)
    path = okf.write_concept(
        "alerts", "demo",
        type="alert", title="Demo", body="# Demo\n\nhi\n",
        timestamp="2026-06-20", generated_by="process:test",
    )
    assert path == tmp_path / "alerts" / "demo.md"
    text = path.read_text()
    assert text.startswith('---\ntype: "alert"\n')
    assert "status:" not in text
    assert text.endswith("# Demo\n\nhi\n")


def test_write_concept_rejects_reserved_slug(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path if name == "root" else tmp_path / name)
    with pytest.raises(ValueError):
        okf.write_concept("alerts", "index", generated_by="process:test", type="alert", title="x", body="")


def test_log_records_creation_then_update(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path if name == "root" else tmp_path / name)
    okf.write_concept("alerts", "demo", generated_by="process:test", type="alert", title="First",
                      body="x", timestamp="2026-06-20")
    okf.write_concept("alerts", "demo", generated_by="process:test", type="alert", title="First again",
                      body="y", timestamp="2026-06-20")
    log = (tmp_path / "log.md").read_text()
    assert "## 2026-06-20" in log
    assert "* **Creation**: First" in log
    assert "* **Update**: First again" in log


def test_log_newest_date_first(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path if name == "root" else tmp_path / name)
    okf.write_concept("alerts", "a", generated_by="process:test", type="alert", title="A", body="x",
                      timestamp="2026-06-19")
    okf.write_concept("alerts", "b", generated_by="process:test", type="alert", title="B", body="x",
                      timestamp="2026-06-20")
    log = (tmp_path / "log.md").read_text()
    assert log.index("## 2026-06-20") < log.index("## 2026-06-19")


def test_link_makes_bundle_relative_absolute():
    assert okf.link("alerts/foo.md", "Foo") == "[Foo](/alerts/foo.md)"
    assert okf.link("/reports/bar.md", "Bar") == "[Bar](/reports/bar.md)"


def test_read_frontmatter_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path if name == "root" else tmp_path / name)
    path = okf.write_concept("reports", "r", generated_by="process:test", type="report", title="T",
                             body="# T\n", tags=["x"], timestamp="2026-06-20")
    fm = okf.read_frontmatter(path)
    assert fm["type"] == "report"
    assert fm["title"] == "T"
    assert fm["tags"] == ["x"]


def test_read_frontmatter_unescapes_and_splits(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path if name == "root" else tmp_path / name)
    path = okf.write_concept("reports", "r", generated_by="process:test", type="report",
                             title='Has "quotes" and, comma', body="x",
                             tags=["a, b", 'c"d'], timestamp="2026-06-20")
    fm = okf.read_frontmatter(path)
    assert fm["title"] == 'Has "quotes" and, comma'
    assert fm["tags"] == ["a, b", 'c"d']


def test_custom_category_directory_and_root_changelog(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    custom = tmp_path / "custom" / "reflections"
    monkeypatch.setattr(okf, "output_dir", lambda name="root": root if name == "root" else custom)
    path = okf.write_concept("reflections", "week", generated_by="process:test", type="reflection", title="Week", body="Notes")
    assert path == custom / "week.md"
    assert path.read_text().endswith("Notes")
    assert (root / "log.md").exists()
    assert not (root / "reflections").exists()


def test_render_nested_flow_shapes():
    block = okf.render_frontmatter({
        "generated": {"by": "process:x", "at": "2026-11-19T10:00:00+00:00"},
        "sources": [{"resource": "source:email"}, {"resource": "source:notes"}],
    })
    assert 'generated: {by: "process:x", at: "2026-11-19T10:00:00+00:00"}\n' in block
    assert 'sources: [{resource: "source:email"}, {resource: "source:notes"}]\n' in block


def test_read_frontmatter_nested(tmp_path):
    p = tmp_path / "c.md"
    p.write_text(okf.render_frontmatter({
        "type": "report",
        "generated": {"by": "a/b", "at": "t"},
        "sources": [{"resource": "source:x"}],
        "empty_map": {},
        "empty_list": [],
    }) + "body")
    fm = okf.read_frontmatter(p)
    assert fm["generated"] == {"by": "a/b", "at": "t"}
    assert fm["sources"][0]["resource"] == "source:x"
    assert fm["empty_map"] == {}
    assert fm["empty_list"] == []


def test_nested_roundtrip_with_special_chars(tmp_path):
    value = {"by": 'we, "q": {x} [y]', "list": ["a, b", {"k": "v: w"}]}
    p = tmp_path / "c.md"
    p.write_text(okf.render_frontmatter({"type": "t", "m": value}))
    assert okf.read_frontmatter(p)["m"] == value


def _fm(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path if name == "root" else tmp_path / name)
    kw.setdefault("type", "report")
    return okf.read_frontmatter(okf.write_concept("reports", "r", title="T", body="x", **kw))


def _horizon(fm):
    at = datetime.fromisoformat(fm["generated"]["at"])
    return datetime.fromisoformat(fm["stale_after"]) - at


def test_write_concept_emits_generated_and_no_status(tmp_path, monkeypatch):
    fm = _fm(tmp_path, monkeypatch, generated_by="process:demo", timestamp="2026-06-20")
    assert fm["generated"]["by"] == "process:demo"
    assert datetime.fromisoformat(fm["generated"]["at"]).tzinfo is not None
    assert fm["timestamp"] == "2026-06-20"
    assert "status" not in fm
    assert "sources" not in fm
    assert "verified" not in fm


@pytest.mark.parametrize("type_, days", [("alert", 7), ("report", 14), ("reflection", 30)])
def test_stale_after_default_per_type(tmp_path, monkeypatch, type_, days):
    fm = _fm(tmp_path, monkeypatch, type=type_, generated_by="process:x")
    assert _horizon(fm) == timedelta(days=days)


def test_stale_after_fallback_for_unknown_type(tmp_path, monkeypatch):
    fm = _fm(tmp_path, monkeypatch, type="t", generated_by="process:x")
    assert _horizon(fm) == timedelta(days=14)


def test_stale_days_overrides_default(tmp_path, monkeypatch):
    fm = _fm(tmp_path, monkeypatch, generated_by="process:x", stale_days=3)
    assert _horizon(fm) == timedelta(days=3)


def test_negative_stale_days_rejected(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        _fm(tmp_path, monkeypatch, generated_by="process:x", stale_days=-1)


def test_source_feeds_render_structured(tmp_path, monkeypatch):
    fm = _fm(tmp_path, monkeypatch, generated_by="process:x", source_feeds=["email", "notes"])
    assert fm["sources"] == [{"resource": "source:email"}, {"resource": "source:notes"}]


def test_source_feeds_dedupe_and_empty(tmp_path, monkeypatch):
    fm = _fm(tmp_path, monkeypatch, generated_by="process:x", source_feeds=["a", "b", "a"])
    assert fm["sources"] == [{"resource": "source:a"}, {"resource": "source:b"}]
    fm = _fm(tmp_path, monkeypatch, generated_by="process:x", source_feeds=[])
    assert "sources" not in fm


def test_verified_rendered_when_given(tmp_path, monkeypatch):
    verified = [{"by": "person:alex", "at": "2026-11-19"}]
    fm = _fm(tmp_path, monkeypatch, generated_by="process:x", verified=verified)
    assert fm["verified"] == verified


@pytest.mark.parametrize("actor", ["", "   "])
def test_write_concept_requires_actor(tmp_path, monkeypatch, actor):
    with pytest.raises(ValueError):
        _fm(tmp_path, monkeypatch, generated_by=actor)


@pytest.mark.parametrize("legacy", [{"status": "final"}, {"sources": ["email"]}])
def test_write_concept_rejects_legacy_fields(tmp_path, monkeypatch, legacy):
    with pytest.raises(TypeError):
        _fm(tmp_path, monkeypatch, generated_by="process:x", **legacy)


def test_actor_convention():
    assert okf.actor("session-analyzer") == "process:session-analyzer"
    assert okf.actor("weekly-reflection", "gpt-5.5") == "weekly-reflection/gpt-5.5"
    assert okf.actor("x", None) == "process:x"


def test_unsafe_mapping_keys_are_quoted(tmp_path):
    value = [{"by": "person:alice", "at, by": "person:mallory", "null": "x", "a: b": "y"}]
    p = tmp_path / "c.md"
    p.write_text(okf.render_frontmatter({"type": "t", "verified": value}))
    text = p.read_text()
    assert '{by: "person:alice", "at, by": "person:mallory", "null": "x", "a: b": "y"}' in text
    assert okf.read_frontmatter(p)["verified"] == value


def test_control_characters_are_escaped_and_roundtrip(tmp_path):
    nasty = "a\nadmin: yes\tb\r c \x85\x0b\x00\x7f \\n end"
    p = tmp_path / "c.md"
    p.write_text(okf.render_frontmatter({"type": "t", "title": nasty, "m": {"by": nasty}}))
    block = p.read_text().split("---")[1]
    assert block.strip().count("\n") == 2  # one physical line per key
    fm = okf.read_frontmatter(p)
    assert fm["title"] == nasty
    assert fm["m"] == {"by": nasty}
    assert "admin" not in fm


def test_parse_depth_is_limited(tmp_path):
    p = tmp_path / "c.md"
    p.write_text(f'---\ntype: "t"\nm: {"[" * 10}"x"{"]" * 10}\n---\n')
    assert okf.read_frontmatter(p)["m"] == [[[[[[[[[["x"]]]]]]]]]]
    p.write_text(f'---\ntype: "t"\nm: {"[" * 2000}{"]" * 2000}\n---\n')
    with pytest.raises(ValueError):
        okf.read_frontmatter(p)
