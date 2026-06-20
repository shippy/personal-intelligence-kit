# OKF Conformance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the kit's generated `output/` directory a conformant OKF v0.1 bundle by funnelling every analysis skill's markdown through one shared helper.

**Architecture:** A new `_lib/okf.py` renders OKF frontmatter, writes concept files, maintains a single root `output/log.md` changelog, and provides link/parse utilities. The six existing output writers drop their hand-rolled frontmatter and call `okf.write_concept(...)`. `index.md` files stay manually authored (guidance only).

**Tech Stack:** Python 3.11+, `uv` for running, `pytest` for tests, `pyyaml` (tests/consumers only — the write path is dependency-free).

## Global Constraints

- Run everything with `uv` (never bare `python`/`pip`). Tests: `uv run --no-project --with pytest --with pyyaml pytest <file> -v`.
- Writer-wiring tests additionally need that skill's own dependencies. Run them with the dependency set declared in the skill's inline `# /// script` metadata or its `pyproject.toml`, plus `pytest` and `pyyaml`. (weekly-reflection and cross-source-queries import `pydantic_ai`; copy their `--with`/project deps.)
- The OKF bundle root is `output_dir("root")` (resolves to `output/`). Subdirs: `alerts`, `reports`, `reflections`, `briefings`, `drafts-for-review`.
- Reserved slugs are `index` and `log`; `write_concept` must reject them.
- All string frontmatter values are double-quoted YAML scalars.
- **Do not touch the operational activity log** (`logs/activity.md`, via `activity_log()`). The OKF `output/log.md` is separate and is written only by `okf.write_concept`. Leave every existing `activity_log()` / `[[wikilink]]` block exactly as-is.
- The write path (`write_concept`, `render_frontmatter`, `link`) must not import `yaml`. Only `read_frontmatter` (a consumer/test utility) may.
- Commit after every task with a passing test.

---

## File Structure

- **Create** `template/.claude/skills/_lib/okf.py` — the helper (the only new logic).
- **Create** `template/.claude/skills/_lib/test_okf.py` — helper unit tests.
- **Create** `template/.claude/skills/_lib/OKF.md` — format spec, conformance checklist, `index.md` authoring guidance.
- **Create** `template/output/index.md` — starter root manifest carrying `okf_version`.
- **Modify** the six writers + add a `test_okf_wiring.py` beside each:
  - `weekly-reflection/output_formatter.py`
  - `session-analyzer/analyze.py`
  - `cross-source-queries/commitment_accountability.py`
  - `cross-source-queries/serendipity_convergence.py`
  - `cross-source-queries/intention_reality_gaps.py`
  - `stale-drafts/stale_drafts.py`
- **Modify** `template/.claude/skills/_lib/SCHEMAS.md` — pointer to `OKF.md`.

All paths below are relative to `template/.claude/skills/` unless prefixed otherwise.

---

## Task 1: Frontmatter rendering

**Files:**
- Create: `_lib/okf.py`
- Test: `_lib/test_okf.py`

**Interfaces:**
- Produces: `render_frontmatter(fields: dict) -> str` — returns a YAML block fenced by `---`, ending with a blank line. `None` values skipped. Strings/lists double-quoted.

- [ ] **Step 1: Write the failing test**

```python
# _lib/test_okf.py
import sys
from pathlib import Path

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-project --with pytest --with pyyaml pytest _lib/test_okf.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'okf'`.

- [ ] **Step 3: Write minimal implementation**

```python
# _lib/okf.py
"""OKF v0.1 bundle helpers for the output/ directory.

See _lib/OKF.md for the format spec. Every analysis skill that emits a
markdown document into output/ should write it via write_concept() so the
bundle stays OKF-conformant (frontmatter + non-empty `type`) and the root
log.md changelog stays current.
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_config import output_dir  # noqa: E402

RESERVED_SLUGS = {"index", "log"}


def _scalar(value: Any) -> str:
    """Render a value as a double-quoted, escaped YAML scalar."""
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _render_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_scalar(v) for v in value) + "]"
    return _scalar(value)


def render_frontmatter(fields: dict[str, Any]) -> str:
    """Render frontmatter fields as a YAML block. None values are skipped.
    Returns the block including the --- fences and a trailing blank line."""
    lines = ["---"]
    for key, value in fields.items():
        if value is None:
            continue
        lines.append(f"{key}: {_render_value(value)}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-project --with pytest --with pyyaml pytest _lib/test_okf.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add template/.claude/skills/_lib/okf.py template/.claude/skills/_lib/test_okf.py
git commit -m "Add OKF frontmatter renderer"
```

---

## Task 2: write_concept — file write + reserved-slug guard

**Files:**
- Modify: `_lib/okf.py`
- Test: `_lib/test_okf.py`

**Interfaces:**
- Produces: `write_concept(subdir, slug, *, type, title, body, description=None, tags=None, resource=None, timestamp=None, **extra) -> Path`. Writes `output/<subdir>/<slug>.md` = frontmatter + body. Defaults `timestamp` to today (`YYYY-MM-DD`). Raises `ValueError` for reserved slugs. Resolves the bundle root via `okf.output_dir("root")` (patchable in tests). (log.md is added in Task 3.)

- [ ] **Step 1: Write the failing test**

```python
# append to _lib/test_okf.py
import pytest


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-project --with pytest --with pyyaml pytest _lib/test_okf.py -v`
Expected: FAIL — `AttributeError: module 'okf' has no attribute 'write_concept'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to _lib/okf.py
def write_concept(
    subdir: str,
    slug: str,
    *,
    type: str,
    title: str,
    body: str,
    description: str | None = None,
    tags: list[str] | None = None,
    resource: str | None = None,
    timestamp: str | None = None,
    **extra: Any,
) -> Path:
    """Write an OKF concept document and return its path."""
    if slug in RESERVED_SLUGS:
        raise ValueError(f"slug {slug!r} is reserved (index/log)")
    ts = timestamp or datetime.now().strftime("%Y-%m-%d")
    target_dir = output_dir("root") / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{slug}.md"
    fields: dict[str, Any] = {
        "type": type,
        "title": title,
        "description": description,
        "resource": resource,
        "tags": tags,
        "timestamp": ts,
    }
    fields.update(extra)
    path.write_text(render_frontmatter(fields) + body)
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-project --with pytest --with pyyaml pytest _lib/test_okf.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add template/.claude/skills/_lib/okf.py template/.claude/skills/_lib/test_okf.py
git commit -m "Add okf.write_concept with reserved-slug guard"
```

---

## Task 3: log.md changelog

**Files:**
- Modify: `_lib/okf.py`
- Test: `_lib/test_okf.py`

**Interfaces:**
- Produces: `write_concept` now appends to `output/log.md`. New slug → `* **Creation**: <title>`; existing slug → `* **Update**: <title>`. Entries grouped under newest-first `## YYYY-MM-DD` headings (the timestamp's date), newest entry first within a day.

- [ ] **Step 1: Write the failing test**

```python
# append to _lib/test_okf.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-project --with pytest --with pyyaml pytest _lib/test_okf.py -v`
Expected: FAIL — `log.md` does not exist / `FileNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to _lib/okf.py
def _append_log(root: Path, date: str, kind: str, title: str) -> None:
    log_path = root / "log.md"
    entry = f"* **{kind}**: {title}"
    existing = log_path.read_text() if log_path.exists() else ""
    heading = f"## {date}"
    lines = existing.splitlines()
    if heading in lines:
        out: list[str] = []
        for line in lines:
            out.append(line)
            if line == heading:
                out.append(entry)
        log_path.write_text("\n".join(out) + "\n")
    else:
        log_path.write_text(f"{heading}\n{entry}\n\n" + existing)
```

Then, in `write_concept`, capture existence before writing and append the log entry after:

```python
    path = target_dir / f"{slug}.md"
    existed = path.exists()          # <-- add this line (before path.write_text)
    ...
    path.write_text(render_frontmatter(fields) + body)
    _append_log(output_dir("root"), ts[:10], "Update" if existed else "Creation", title)  # <-- add
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-project --with pytest --with pyyaml pytest _lib/test_okf.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add template/.claude/skills/_lib/okf.py template/.claude/skills/_lib/test_okf.py
git commit -m "Maintain output/log.md changelog in write_concept"
```

---

## Task 4: link() and read_frontmatter() utilities

**Files:**
- Modify: `_lib/okf.py`
- Test: `_lib/test_okf.py`

**Interfaces:**
- Produces: `link(target: str, text: str) -> str` → `[text](/target)` (prefixes `/` if absent). `read_frontmatter(path) -> dict` parses a concept's YAML frontmatter (uses `yaml`); returns `{}` if none.

- [ ] **Step 1: Write the failing test**

```python
# append to _lib/test_okf.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-project --with pytest --with pyyaml pytest _lib/test_okf.py -v`
Expected: FAIL — `module 'okf' has no attribute 'link'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to _lib/okf.py
def link(target: str, text: str) -> str:
    """Return a bundle-relative absolute markdown link."""
    if not target.startswith("/"):
        target = "/" + target
    return f"[{text}]({target})"


def read_frontmatter(path: Any) -> dict[str, Any]:
    """Parse a concept file's YAML frontmatter into a dict ({} if none).
    Consumer/test utility — requires PyYAML."""
    import yaml

    text = Path(path).read_text()
    if not text.startswith("---\n"):
        return {}
    block, sep, _ = text[4:].partition("\n---")
    if not sep:
        return {}
    return yaml.safe_load(block) or {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-project --with pytest --with pyyaml pytest _lib/test_okf.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add template/.claude/skills/_lib/okf.py template/.claude/skills/_lib/test_okf.py
git commit -m "Add okf.link and okf.read_frontmatter utilities"
```

---

## Task 5: Wire weekly-reflection

**Files:**
- Modify: `weekly-reflection/output_formatter.py` (drop `_frontmatter()` from `format()`; rewrite `save()`)
- Test: `weekly-reflection/test_okf_wiring.py`

**Interfaces:**
- Consumes: `okf.write_concept`, `okf.read_frontmatter`.

- [ ] **Step 1: Write the failing test**

```python
# weekly-reflection/test_okf_wiring.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import okf
from output_formatter import ReflectionFormatter
from llm_synthesizer import WeeklyReflection


def _min_reflection():
    return WeeklyReflection(
        opening_observation="A quiet, focused week.",
        themes=[], tensions=[], commitments=[],
        notable_moments=[], reflection_questions=[],
    )


def test_reflection_is_okf_conformant(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)
    fmt = ReflectionFormatter()
    body = fmt.format(_min_reflection())
    path = fmt.save(body)
    fm = okf.read_frontmatter(path)
    assert fm["type"] == "reflection"
    assert fm["title"]
    assert (tmp_path / "log.md").exists()
    assert not body.startswith("---")  # frontmatter no longer in the body
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `weekly-reflection/`): `uv run --with pytest --with pyyaml pytest test_okf_wiring.py -v`
Expected: FAIL — `body` still starts with `---` / `save()` writes a hand-rolled file.

- [ ] **Step 3: Implement**

In `output_formatter.py`, add the import near the top (after the existing `from vault_config import ...`):

```python
import okf
```

Change `format()` so it no longer prepends frontmatter — replace:

```python
        md = self._frontmatter()
```

with:

```python
        md = ""
```

Replace the whole `save()` method:

```python
    def save(self, content: str, out_dir: Path | None = None) -> Path:
        return okf.write_concept(
            "reflections",
            f"{self.review_date}-weekly-reflection",
            type="reflection",
            title=f"Weekly Reflection — {self.review_date}",
            body=content,
            timestamp=self.review_date,
            status="final",
        )
```

(Leave `_frontmatter()` defined but unused, or delete it; deleting is cleaner.)

- [ ] **Step 4: Run test to verify it passes**

Run (from `weekly-reflection/`): `uv run --with pytest --with pyyaml pytest test_okf_wiring.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add template/.claude/skills/weekly-reflection/output_formatter.py template/.claude/skills/weekly-reflection/test_okf_wiring.py
git commit -m "Wire weekly-reflection output through okf.write_concept"
```

---

## Task 6: Wire session-analyzer (two documents)

**Files:**
- Modify: `session-analyzer/analyze.py` (terse alert + dense report write sites, lines ~93–172)
- Test: `session-analyzer/test_okf_wiring.py`

**Interfaces:**
- Consumes: `okf.write_concept`, `okf.read_frontmatter`.

- [ ] **Step 1: Write the failing test**

The two documents are written inside `main()`, which parses args and reads the browser DB. Drive it on the empty path by monkeypatching the analyzer's data source. Inspect `main()` for the cluster-building call; the test below monkeypatches `output_dir` and runs `main` with an empty DB via the `--db` flag pointed at a fresh temp sqlite. If `main()` is not import-friendly, extract the document assembly into a helper `build_session_docs(active, dormant, cluster_stats, date, dormant_days) -> list[tuple]` returning `(subdir, slug, type, title, body, meta)` and test that helper. Use the extraction approach:

```python
# session-analyzer/test_okf_wiring.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import okf
import analyze


def test_session_docs_are_okf_conformant(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)
    paths = analyze.write_session_docs(
        active=[], dormant=[], cluster_stats={}, date="2026-06-20", dormant_days=14
    )
    assert len(paths) == 2
    types = {okf.read_frontmatter(p)["type"] for p in paths}
    assert types == {"alert", "report"}
    assert (tmp_path / "log.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `session-analyzer/`): `uv run --with pytest --with pyyaml pytest test_okf_wiring.py -v`
Expected: FAIL — `module 'analyze' has no attribute 'write_session_docs'`.

- [ ] **Step 3: Implement**

Add `import okf` near the other imports. Extract the terse/dense assembly + writes (lines ~93–175) out of `main()` into a new function, and call it from `main()`:

```python
def write_session_docs(active, dormant, cluster_stats, date, dormant_days):
    terse_lines = [
        f"# Session Health — {date}",
        "",
        "## Summary",
        "",
        f"- 🟢 **{len(active)}** active domain clusters",
        f"- 🔴 **{len(dormant)}** dormant (no activity in {dormant_days} days)",
        "",
    ]
    if dormant[:5]:
        terse_lines.extend(["## Top Dormant Clusters", ""])
        for domain, stats in dormant[:5]:
            days = (datetime.now() - stats["last_visit"]).days if stats["last_visit"] else "?"
            terse_lines.append(f"- 🔴 **{domain}** — {stats['count']} visits, last {days}d ago")
        terse_lines.append("")
    if active[:5]:
        terse_lines.extend(["## Top Active Clusters", ""])
        for domain, stats in active[:5]:
            terse_lines.append(f"- 🟢 **{domain}** — {stats['count']} visits")
    terse_body = "\n".join(terse_lines) + "\n"

    dense_lines = [
        f"# Session Health Report — {date}",
        "",
        "## All Clusters",
        "",
        "| Domain | Visits | Last Seen | Status |",
        "|--------|--------|-----------|--------|",
    ]
    for domain, stats in sorted(cluster_stats.items(), key=lambda x: x[1]["count"], reverse=True)[:50]:
        if stats["last_visit"]:
            days = (datetime.now() - stats["last_visit"]).days
            status = "🔴 dormant" if days > dormant_days else "🟢 active"
            last_seen = stats["last_visit"].strftime("%Y-%m-%d")
        else:
            status, last_seen = "?", "?"
        dense_lines.append(f"| {domain} | {stats['count']} | {last_seen} | {status} |")
    dense_body = "\n".join(dense_lines) + "\n"

    terse_path = okf.write_concept(
        "alerts", f"session-health-{date}", type="alert",
        title=f"Session Health — {date}",
        description=f"{len(active)} active, {len(dormant)} dormant domain clusters",
        body=terse_body, timestamp=date, status="final", sources=["browser"], tags=["session-health"],
    )
    dense_path = okf.write_concept(
        "reports", f"session-health-{date}", type="report",
        title=f"Session Health Report — {date}",
        body=dense_body, timestamp=date, status="final", sources=["browser"], tags=["session-health"],
    )
    return [terse_path, dense_path]
```

In `main()`, replace the inline terse/dense construction + `write_text` block (lines ~93–175) with:

```python
    write_session_docs(active, dormant, cluster_stats, date, args.dormant_days)
```

Leave the `terminal-notifier` call and the `activity_log()` block (lines ~177–194) unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run (from `session-analyzer/`): `uv run --with pytest --with pyyaml pytest test_okf_wiring.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add template/.claude/skills/session-analyzer/analyze.py template/.claude/skills/session-analyzer/test_okf_wiring.py
git commit -m "Wire session-analyzer output through okf.write_concept"
```

---

## Task 7: Wire commitment-accountability

**Files:**
- Modify: `cross-source-queries/commitment_accountability.py` (`generate_report`, lines ~155–174)
- Test: `cross-source-queries/test_okf_commitment.py`

**Interfaces:**
- Consumes: `okf.write_concept`, `okf.read_frontmatter`. Monkeypatches `source_enabled` and `CommitmentAccountabilityAnalyzer`.

- [ ] **Step 1: Write the failing test**

```python
# cross-source-queries/test_okf_commitment.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import okf
import commitment_accountability as mod


def test_commitment_report_is_okf_conformant(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)
    monkeypatch.setattr(mod, "source_enabled", lambda s: True)
    monkeypatch.setattr(mod, "activity_log", lambda: tmp_path / "activity.md")

    class FakeAnalyzer:
        def analyze(self, days_back):
            return {"total": 0, "followed_up": 0, "no_follow_up": 0, "details": []}

    monkeypatch.setattr(mod, "CommitmentAccountabilityAnalyzer", lambda: FakeAnalyzer())
    path = mod.generate_report()
    assert okf.read_frontmatter(path)["type"] == "alert"
    assert (tmp_path / "log.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `cross-source-queries/`): `uv run --with pytest --with pyyaml pytest test_okf_commitment.py -v`
Expected: FAIL — produced file's frontmatter is the hand-rolled block; no `log.md`.

- [ ] **Step 3: Implement**

Add `import okf` to the imports. In `generate_report`, drop the frontmatter line from `lines` and replace the write block. Change line 156 from:

```python
        f"---\ncreated: {date}\ntype: alert\nstatus: final\nsources: [email]\n---\n",
        f"# Commitment Accountability — {date}\n",
```

to:

```python
        f"# Commitment Accountability — {date}\n",
```

Replace lines ~171–174 (the `out = output_dir(...)` / `mkdir` / `write_text` / `print`) with:

```python
    out = okf.write_concept(
        "alerts", f"commitment-accountability-{date}", type="alert",
        title=f"Commitment Accountability — {date}",
        description=f"{report['total']} commitments tracked in sent mail",
        body="\n".join(lines), timestamp=date, status="final",
        sources=["email"], tags=["commitment"],
    )
    print(f"Wrote {out}")
```

Leave the `activity_log()` block (lines ~176–181) unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run (from `cross-source-queries/`): `uv run --with pytest --with pyyaml pytest test_okf_commitment.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add template/.claude/skills/cross-source-queries/commitment_accountability.py template/.claude/skills/cross-source-queries/test_okf_commitment.py
git commit -m "Wire commitment-accountability output through okf.write_concept"
```

---

## Task 8: Wire serendipity-convergence

**Files:**
- Modify: `cross-source-queries/serendipity_convergence.py` (`generate_report`, lines ~284–313)
- Test: `cross-source-queries/test_okf_convergence.py`

**Interfaces:**
- Consumes: `okf.write_concept`, `okf.read_frontmatter`. Monkeypatches the analyzer's `detect_*` methods to return empty lists.

- [ ] **Step 1: Write the failing test**

```python
# cross-source-queries/test_okf_convergence.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import okf
import serendipity_convergence as mod


def test_convergence_report_is_okf_conformant(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)
    monkeypatch.setattr(mod, "activity_log", lambda: tmp_path / "activity.md")

    class FakeAnalyzer:
        def detect_topic_convergence(self, days_back, min_sources):
            return []

        def detect_person_convergence(self, days_back):
            return []

    monkeypatch.setattr(mod, "SerendipityConvergenceAnalyzer", lambda: FakeAnalyzer())
    path = mod.generate_report()
    assert okf.read_frontmatter(path)["type"] == "report"
    assert (tmp_path / "log.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `cross-source-queries/`): `uv run --with pytest --with pyyaml pytest test_okf_convergence.py -v`
Expected: FAIL — hand-rolled frontmatter; no `log.md`.

- [ ] **Step 3: Implement**

Add `import okf`. Drop the frontmatter line. Replace line 285:

```python
        f"---\ncreated: {date}\ntype: report\nstatus: final\n---\n",
        f"# Convergence Report — {date}\n",
```

with:

```python
        f"# Convergence Report — {date}\n",
```

Replace lines ~310–313 (`out = output_dir(...)` / `mkdir` / `write_text` / `print`) with:

```python
    out = okf.write_concept(
        "reports", f"convergence-{date}", type="report",
        title=f"Convergence Report — {date}",
        description=f"{len(topic_conv)} topic, {len(person_conv)} person convergences",
        body="\n".join(lines), timestamp=date, status="final", tags=["convergence"],
    )
    print(f"Wrote {out}")
```

Leave the `activity_log()` block unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run (from `cross-source-queries/`): `uv run --with pytest --with pyyaml pytest test_okf_convergence.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add template/.claude/skills/cross-source-queries/serendipity_convergence.py template/.claude/skills/cross-source-queries/test_okf_convergence.py
git commit -m "Wire serendipity-convergence output through okf.write_concept"
```

---

## Task 9: Wire intention-reality-gaps (three frontmatter sites)

**Files:**
- Modify: `cross-source-queries/intention_reality_gaps.py` — `_render_llm_report` (~line 516), `_render_heuristic_report` (~line 595), and the no-goals path in `generate_report` (~lines 659–668)
- Test: `cross-source-queries/test_okf_intention.py`

**Interfaces:**
- Consumes: `okf.write_concept`, `okf.read_frontmatter`. The render helpers return body **without** frontmatter; `generate_report` writes via `okf.write_concept`.

- [ ] **Step 1: Write the failing test (drives the no-goals path)**

```python
# cross-source-queries/test_okf_intention.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import okf
import intention_reality_gaps as mod


def test_intention_report_is_okf_conformant(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)

    class FakeAnalyzer:
        def find_yearly_goals(self):
            return None

    monkeypatch.setattr(mod, "IntentionRealityAnalyzer", lambda: FakeAnalyzer())
    path = mod.generate_report()
    assert okf.read_frontmatter(path)["type"] == "report"
    assert (tmp_path / "log.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `cross-source-queries/`): `uv run --with pytest --with pyyaml pytest test_okf_intention.py -v`
Expected: FAIL — hand-rolled frontmatter; no `log.md`.

- [ ] **Step 3: Implement**

Add `import okf`. In both `_render_llm_report` (line ~513) and `_render_heuristic_report` (line ~593), each `lines` list begins with exactly six frontmatter entries:

```python
    lines = [
        "---",
        f"created: {date}",
        "type: report",
        "status: final",
        "sources: [notes, reflections, llm]",   # heuristic variant: "sources: [notes]"
        "---",
        ...  # first real body entry (the "# …" heading) follows
    ]
```

Delete those six entries from each list so `lines` starts at the `# …` heading and the returned `md` no longer begins with `---`.

Replace the no-goals path (lines 659–668) with:

```python
    if not goals_data:
        body = (
            f"# Intention ↔ Reality Gap Analysis — {date}\n\n"
            f"⚠ No yearly goals file found in notes vault\n"
        )
        out = okf.write_concept(
            "reports", f"intention-reality-{date}", type="report",
            title=f"Intention ↔ Reality Gap Analysis — {date}",
            body=body, timestamp=date, status="final", tags=["intention-reality"],
        )
        print(f"Wrote {out}")
        return out
```

Replace the main write site (the second `out = output_dir("reports") / f"intention-reality-{date}.md"` near line 700 and its `write_text`) with:

```python
    out = okf.write_concept(
        "reports", f"intention-reality-{date}", type="report",
        title=f"Intention ↔ Reality Gap Analysis — {date}",
        body=md, timestamp=date, status="final",
        sources=sources or None, tags=["intention-reality"],
    )
    print(f"Wrote {out}")
    return out
```

> Verify: after editing the two `_render_*` helpers, `md` no longer begins with `---`. Read lines ~505–520 and ~585–600 to confirm the exact list entries removed.

- [ ] **Step 4: Run test to verify it passes**

Run (from `cross-source-queries/`): `uv run --with pytest --with pyyaml pytest test_okf_intention.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add template/.claude/skills/cross-source-queries/intention_reality_gaps.py template/.claude/skills/cross-source-queries/test_okf_intention.py
git commit -m "Wire intention-reality-gaps output through okf.write_concept"
```

---

## Task 10: Wire stale-drafts (two documents + empty report)

**Files:**
- Modify: `stale-drafts/stale_drafts.py` — main report (lines ~144–241) and `_generate_empty_report` (lines ~243–248)
- Test: `stale-drafts/test_okf_wiring.py`

**Interfaces:**
- Consumes: `okf.write_concept`, `okf.read_frontmatter`. Test drives `_generate_empty_report` (no draft data required).

- [ ] **Step 1: Write the failing test**

```python
# stale-drafts/test_okf_wiring.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import okf
import stale_drafts as mod


def test_empty_report_is_okf_conformant(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)
    reporter = mod.StaleDraftsOrchestrator.__new__(mod.StaleDraftsOrchestrator)
    reporter.review_date = "2026-06-20"
    path = reporter._generate_empty_report()
    assert okf.read_frontmatter(path)["type"] == "alert"
    assert (tmp_path / "log.md").exists()
```

> Note: `_generate_empty_report` currently returns `None` — the implementation below makes it `return path` so the test can assert on it.

- [ ] **Step 2: Run test to verify it fails**

Run (from `stale-drafts/`): `uv run --with pytest --with pyyaml pytest test_okf_wiring.py -v`
Expected: FAIL — `_generate_empty_report` writes hand-rolled frontmatter and returns `None`.

- [ ] **Step 3: Implement**

Add `import okf`. Rewrite `_generate_empty_report` (lines 243–248):

```python
    def _generate_empty_report(self):
        path = okf.write_concept(
            "alerts", f"stale-drafts-{self.review_date}", type="alert",
            title=f"Draft Queue — {self.review_date}",
            body=f"# Draft Queue — {self.review_date}\n\nNo drafts with content found.\n",
            timestamp=self.review_date, status="final", tags=["drafts"],
        )
        print(f"Saved: {path}")
        return path
```

For the main report (the `terse`/`dense` f-strings at lines 144–155 and 197–215, and the save block at lines 230–241): strip the `---\ncreated…\n---\n\n` header from each f-string so `terse`/`dense` start at their `# …` heading, then replace the save block (lines 231–241) with:

```python
        terse_path = okf.write_concept(
            "alerts", f"stale-drafts-{self.review_date}", type="alert",
            title=f"Draft Queue — {self.review_date}",
            description=f"{len(finish_queue)} finishable drafts",
            body=terse, timestamp=self.review_date, status="final",
            sources=list(self.data_sources_used), tags=["drafts"],
        )
        dense_path = okf.write_concept(
            "reports", f"stale-drafts-analysis-{self.review_date}", type="report",
            title=f"Draft Queue Analysis — {self.review_date}",
            body=dense, timestamp=self.review_date, status="final",
            sources=list(self.data_sources_used), tags=["drafts"],
        )
        print(f"Saved: {terse_path}")
        print(f"Saved: {dense_path}")
        return terse_path, dense_path
```

Leave `_log_activity` and `_send_notification` unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run (from `stale-drafts/`): `uv run --with pytest --with pyyaml pytest test_okf_wiring.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add template/.claude/skills/stale-drafts/stale_drafts.py template/.claude/skills/stale-drafts/test_okf_wiring.py
git commit -m "Wire stale-drafts output through okf.write_concept"
```

---

## Task 11: Docs — root manifest, OKF.md, SCHEMAS pointer

**Files:**
- Create: `template/output/index.md`
- Create: `_lib/OKF.md`
- Modify: `_lib/SCHEMAS.md`

**Interfaces:** none (documentation).

- [ ] **Step 1: Create the root manifest**

```markdown
<!-- template/output/index.md -->
---
okf_version: "0.1"
title: "Intelligence Vault — Output Bundle"
description: "Generated alerts, reports, reflections, and briefings as an OKF v0.1 bundle."
---

# Output Bundle

This directory is an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) v0.1 bundle. Each `.md` file is a concept with `type` frontmatter; `log.md` is the chronological changelog.

* [Alerts](/alerts/) - time-sensitive findings
* [Reports](/reports/) - detailed analyses
* [Reflections](/reflections/) - weekly synthesis
* [Briefings](/briefings/) - briefings
* [Drafts for review](/drafts-for-review/) - draft queue
```

- [ ] **Step 2: Create `_lib/OKF.md`**

Write a doc covering: the format the kit targets (bundle root `output/`, concept frontmatter with required `type` + recommended `title`/`description`/`timestamp`/`tags`, pass-through extras); reserved files (`index.md` manual; `output/index.md` is the only index permitted frontmatter, for `okf_version`; `log.md` auto-maintained by `okf.write_concept`); links via `okf.link`; and the conformance checklist:

```markdown
# OKF Conformance (output/ bundle)

`output/` is an OKF v0.1 bundle. All writers emit concepts via
`_lib/okf.py::write_concept`, which guarantees conformance.

## Conformance checklist
1. Every non-reserved `.md` has parseable YAML frontmatter. ✓ (write_concept)
2. Each frontmatter has a non-empty `type`. ✓ (required arg)
3. Reserved files (`index.md`, `log.md`) follow their structures. ✓
4. Root `output/index.md` declares `okf_version`. ✓

## Authoring index.md (manual)
Add an `index.md` to a subdirectory only when it has grown large enough that
a curated listing helps a reader. Format (no frontmatter, except the root
index which carries only `okf_version`):

    # Group Name
    * [Title](/subdir/file.md) - one-line description

Do not auto-generate these — they are editorial.
```

- [ ] **Step 3: Add the pointer to `SCHEMAS.md`**

After the intro paragraph (around line 6, after "All databases live in `data/`..."), add:

```markdown
> **Output format:** analysis skills emit markdown into `output/`, which is an
> OKF v0.1 bundle. See `_lib/OKF.md` and write outputs via `_lib/okf.py`.
```

- [ ] **Step 4: Verify the docs render and commit**

Run: `uv run --no-project --with pyyaml python -c "import yaml,io; print(yaml.safe_load(open('template/output/index.md').read().split('---')[1]))"`
Expected: prints a dict containing `okf_version: '0.1'` (frontmatter parses).

```bash
git add template/output/index.md template/.claude/skills/_lib/OKF.md template/.claude/skills/_lib/SCHEMAS.md
git commit -m "Document OKF conformance and add root bundle manifest"
```

---

## Task 12: Full verification

**Files:** none (verification only).

- [ ] **Step 1: Run the helper suite**

Run: `uv run --no-project --with pytest --with pyyaml pytest template/.claude/skills/_lib/test_okf.py -v`
Expected: 8 passed.

- [ ] **Step 2: Run each writer wiring test**

Run each from its skill directory with that skill's dependency set (see Global Constraints):
- `weekly-reflection/test_okf_wiring.py`
- `session-analyzer/test_okf_wiring.py`
- `cross-source-queries/test_okf_commitment.py`, `test_okf_convergence.py`, `test_okf_intention.py`
- `stale-drafts/test_okf_wiring.py`

Expected: all pass.

- [ ] **Step 3: Confirm no operational-log regressions**

Run: `git grep -n "activity_log" template/.claude/skills` and confirm every `activity_log()` call site is unchanged from `main` (the OKF work must not have altered them).

- [ ] **Step 4: Mark the spec done and commit**

Edit `docs/superpowers/specs/2026-06-20-okf-conformance-design.md` `Status:` line to `Implemented`.

```bash
git add docs/superpowers/specs/2026-06-20-okf-conformance-design.md
git commit -m "Mark OKF conformance spec implemented"
```

---

## Self-Review Notes

- **Spec coverage:** helper (Tasks 1–4) covers frontmatter/type, links, log.md, version util; writers (Tasks 5–10) cover all six output producers; docs (Task 11) cover `index.md` guidance, root `okf_version`, conformance checklist, SCHEMAS pointer. `data/` SQLite and `logs/activity.md` are explicitly out of scope per the spec.
- **Exact names resolved:** Task 8 analyzer is `SerendipityConvergenceAnalyzer`; Task 10 reporter is `StaleDraftsOrchestrator`; Task 9 removes the six leading frontmatter entries from each `_render_*` `lines` list. No guesswork left for the implementer.
- **Type consistency:** every writer calls the single signature `write_concept(subdir, slug, *, type, title, body, ...)`; tests assert via `read_frontmatter(path)["type"]`.
