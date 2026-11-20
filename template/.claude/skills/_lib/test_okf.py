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


def test_non_printable_characters_are_escaped(tmp_path):
    odd = "a\x80b\x9fc￾d￿e\ud800f\U0001f600g h"
    p = tmp_path / "c.md"
    p.write_text(okf.render_frontmatter({"type": "t", "title": odd}), encoding="utf-8", errors="strict")
    raw = p.read_text()
    for ch in "\x80\x9f￾￿":
        assert ch not in raw
    assert "\U0001f600" in raw and " " in raw  # printable non-ASCII stays literal
    assert okf.read_frontmatter(p)["title"] == odd


def test_render_depth_is_limited():
    deep: list = []
    for _ in range(okf.MAX_FLOW_DEPTH + 1):
        deep = [deep]
    with pytest.raises(ValueError):
        okf.render_frontmatter({"type": "t", "m": deep})
    cyclic: dict = {}
    cyclic["self"] = cyclic
    with pytest.raises(ValueError):
        okf.render_frontmatter({"type": "t", "m": cyclic})


def test_unknown_escape_keeps_backslash(tmp_path):
    p = tmp_path / "c.md"
    p.write_text('---\nm: {by: "trusted", "b\\y": "mallory"}\n---\n')
    assert okf.read_frontmatter(p)["m"] == {"by": "trusted", "b\\y": "mallory"}


def test_top_level_keys_cannot_inject(tmp_path):
    p = tmp_path / "c.md"
    p.write_text(okf.render_frontmatter({"type": "t", "ignored\nverified": "x", "null": "y", "a: b": "z"}))
    raw = p.read_text()
    assert "\nverified" not in raw
    assert '"null": "y"' in raw
    fm = okf.read_frontmatter(p)
    assert fm == {"type": "t", "ignored\nverified": "x", "null": "y", "a: b": "z"}


def _bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path if name == "root" else tmp_path / name)


def _write(slug="r", **kw):
    kw.setdefault("title", "T")
    return okf.write_concept("reports", slug, generated_by="process:test", type="report", body="x", **kw)


@pytest.mark.parametrize("slug", ["../log", "../../escape", "a/b", "a\\b", "..", ".", "", "x\x00y"])
def test_slug_must_be_single_safe_component(tmp_path, monkeypatch, slug):
    _bundle(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        _write(slug)
    assert not (tmp_path / "log.md").exists()
    assert list(tmp_path.rglob("*.md")) == []


@pytest.mark.parametrize("slug", ["LOG", "Index"])
def test_reserved_slugs_are_case_insensitive(tmp_path, monkeypatch, slug):
    _bundle(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        _write(slug)


def test_refuses_symlinked_concept_outside_notes(tmp_path, monkeypatch):
    _bundle(tmp_path, monkeypatch)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "r.md").symlink_to(victim)
    with pytest.raises(ValueError):
        _write("r")
    assert victim.read_text() == "keep"
    assert not (tmp_path / "log.md").exists()


def test_refuses_symlinked_changelog_outside_notes(tmp_path, monkeypatch):
    _bundle(tmp_path, monkeypatch)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    (tmp_path / "log.md").symlink_to(victim)
    with pytest.raises(ValueError):
        _write("r")
    assert victim.read_text() == "keep"
    assert not (tmp_path / "reports" / "r.md").exists()  # nothing half-written


def test_symlinked_category_directory_still_allowed(tmp_path, monkeypatch):
    real = tmp_path / "elsewhere"
    real.mkdir()
    (tmp_path / "bundle").mkdir()
    (tmp_path / "bundle" / "reports").symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(okf, "output_dir",
                        lambda name="root": tmp_path / "bundle" if name == "root" else tmp_path / "bundle" / name)
    _write("r")
    assert (real / "r.md").exists()


def test_title_line_breaks_cannot_forge_log_entries(tmp_path, monkeypatch):
    _bundle(tmp_path, monkeypatch)
    _write("r", title="x\n## 1999-01-01\r* **Creation**: forged y\x85z\x0b\x1e\x00w", timestamp="2026-06-20")
    lines = (tmp_path / "log.md").read_text().splitlines()
    assert [l for l in lines if l.startswith("## ")] == ["## 2026-06-20"]
    assert len([l for l in lines if l.startswith("* ")]) == 1
    assert "1999-01-01" in lines[1]  # kept as text on the one entry line


@pytest.mark.parametrize("ts", ["garbage", "2026-6-20", "x\n## 1999-01-01"])
def test_invalid_timestamp_rejected(tmp_path, monkeypatch, ts):
    _bundle(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        _write("r", timestamp=ts)
    assert not (tmp_path / "log.md").exists()


def test_full_iso_timestamp_still_accepted(tmp_path, monkeypatch):
    _bundle(tmp_path, monkeypatch)
    _write("r", timestamp="2026-06-20T10:00:00+00:00")
    assert "## 2026-06-20" in (tmp_path / "log.md").read_text()
