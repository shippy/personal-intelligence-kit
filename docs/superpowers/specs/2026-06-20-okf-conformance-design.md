# OKF Conformance for `output/` — Design

**Date:** 2026-06-20
**Status:** Implemented

> **Implementation note:** the root `output/index.md` manifest is *not* shipped.
> `output/` is gitignored as local user data and created at runtime by the
> skills, and `okf_version` is OKF-optional (the spec says bundles *may* declare
> it). `_lib/OKF.md` documents the format for users who want to add one manually.
**Spec referenced:** Open Knowledge Format (OKF) v0.1 — https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md

## Summary

Make the kit's generated `output/` directory a conformant **OKF v0.1 bundle**: a
directory of markdown files where every concept carries YAML frontmatter with a
non-empty `type`, cross-links use bundle-relative absolute paths, and reserved
files (`index.md`, `log.md`) follow OKF conventions.

The work is centralised in one new shared helper (`_lib/okf.py`) that every
output writer calls. `index.md` files are authored manually (editorial curation);
`log.md` is auto-maintained by the helper.

## Scope

**In scope:** the markdown the analysis skills emit into `output/`.

**Out of scope:** the `data/*.db` SQLite layer. OKF is a knowledge *distribution*
format, not a working datastore; the SQLite stores defined in `_lib/SCHEMAS.md`
remain the engine and are not rendered as OKF. The notes vault, email (notmuch),
and other ingest sources are likewise untouched.

## Why this is small

`output/` already satisfies OKF's two hard requirements today: every writer emits
YAML frontmatter with a non-empty `type` (`type: alert` / `report` / `reflection`).
"Faithful adoption" adds the recommended fields, OKF-style links, the reserved
files, and a version marker — concentrated in one helper plus mechanical wiring in
six existing writers.

## Architecture

```
_lib/okf.py          ← new: the only new logic
_lib/test_okf.py     ← new: unit tests
_lib/OKF.md          ← new: the format spec + conformance checklist + index.md guidance
_lib/SCHEMAS.md      ← edit: add a pointer to OKF.md

output/              ← bundle root (not in template; gitignored / _skip_if_exists)
  index.md           ← manual root manifest, carries okf_version
  log.md             ← auto-appended changelog (single, root-level)
  alerts/            ← concept group
  reports/
  reflections/
  briefings/
  drafts-for-review/
```

### Component: `_lib/okf.py`

Single responsibility: render an OKF-conformant concept document, maintain the
changelog, and format bundle links. Pure and importable; no skill-specific logic.

Public surface:

```python
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
    timestamp: str | None = None,   # ISO 8601; defaults to now
    **extra,                        # pass-through frontmatter (e.g. status, sources)
) -> Path:
    """Write output/<subdir>/<slug>.md with OKF frontmatter, then append a
    log.md entry. Raises ValueError if slug is reserved ('index' or 'log')."""

def link(target: str, text: str) -> str:
    """Return a bundle-relative absolute link: [text](/<target>).
    e.g. link('alerts/foo.md', 'Foo') -> '[Foo](/alerts/foo.md)'."""
```

Paths derive from the existing `vault_config.output_dir(...)`, so the helper
inherits current path handling. Missing subdirs are created with
`mkdir(parents=True, exist_ok=True)`.

### Wiring: the six output writers

Each swaps its hand-rolled `f.write("---\n...")` blocks and `[[wikilink]]`
strings for `okf.write_concept(...)` / `okf.link(...)`:

- `weekly-reflection/output_formatter.py`
- `session-analyzer/analyze.py`
- `cross-source-queries/commitment_accountability.py`
- `cross-source-queries/intention_reality_gaps.py`
- `cross-source-queries/serendipity_convergence.py`
- `stale-drafts/stale_drafts.py`

This is mechanical: the prose/markdown body each skill already builds is passed as
`body`; the frontmatter fields it already computes (`type`, date, `status`,
`sources`) map to the corresponding parameters.

## OKF format the kit adopts

### Concept frontmatter

| Field | OKF status | Notes |
|-------|-----------|-------|
| `type` | **required** | Non-empty. Already used (`alert`/`report`/`reflection`/…). |
| `title` | recommended | Added. |
| `description` | recommended | One-liner; added. |
| `timestamp` | recommended | ISO 8601. The existing `created` value folds into this (avoid two date fields). |
| `tags` | recommended | List; added where a skill has meaningful tags. |
| `resource` | recommended | Canonical URI; usually omitted for generated reports. |
| `status`, `sources`, … | custom | Preserved via `**extra`. OKF mandates consumers keep unknown keys. |

### Links

`okf.link()` emits bundle-relative absolute links — `[text](/alerts/foo.md)` —
which OKF prefers because they are stable across document moves. This replaces the
current `[[output/alerts/...]]` Obsidian wikilinks. Broken links are tolerated by
the spec, so links to docs that may not exist on a given run are acceptable.

### Reserved files

- **`output/index.md`** — bundle root manifest. The one index permitted
  frontmatter, solely to carry `okf_version: "0.1"` (plus optional `title` /
  `description`). **Manually authored.**
- **`output/<subdir>/index.md`** — optional, **manual, no frontmatter**; body is
  grouped `* [Title](/path) - description` lists. Authored only when a directory
  is large enough that progressive-disclosure curation helps. `OKF.md` documents
  *when* and *how*; the helper does not generate these.
- **`output/log.md`** — single root changelog, newest-first `## YYYY-MM-DD`
  headings, **auto-appended** by `write_concept`:
  `* **Creation**: <title>` on first write of a slug, `* **Update**: <title>`
  when the slug already existed.

### Version declaration

The root `output/index.md` declares `okf_version: "0.1"` in its frontmatter (the
only OKF-permitted location).

### Conformance checklist (also in `OKF.md`)

1. Every non-reserved `.md` has parseable YAML frontmatter. ✓
2. Each such frontmatter has a non-empty `type`. ✓
3. Reserved files follow their structures when present. ✓
4. Root `index.md` declares `okf_version`. ✓

## Testing

`_lib/test_okf.py`, pytest via inline-script metadata (matching
`journal-ingest/test_ingest.py`), run against a `tmp_path` bundle root:

- frontmatter renders with `type` present + recommended fields; extras pass
  through; `created` → `timestamp` fold works;
- `link()` produces `[text](/subdir/file.md)`;
- `write_concept` creates the file and appends a correct `log.md` line —
  Creation on first write, Update when the slug already exists;
- reserved slug (`index` / `log`) raises `ValueError`;
- conformance smoke check: parse every `.md` the helper wrote, assert frontmatter
  parses and `type` is non-empty.

## Error handling

- **Reserved-slug collision** → raise `ValueError`; a concept must never clobber
  `index.md` / `log.md`.
- **Concurrent `log.md` appends** — skills run on independent launchd schedules,
  so two could append at once. Open in append mode and write each entry in a
  single `write()` call (atomic enough at this volume). Documented as best-effort,
  not transactional; no locking (overkill for a personal vault).
- **Bundle root resolution** — paths come from `vault_config.output_dir(...)`;
  subdirs created with `mkdir(parents=True, exist_ok=True)`.
- **Idempotent reruns** — re-writing the same slug overwrites the doc and logs an
  Update (no duplicate file), matching how skills already overwrite dated outputs.

## Out of scope / non-goals

- Rendering `data/*.db` as OKF concepts (misuses the format; large, no benefit).
- Auto-generating `index.md` (editorial curation, not mechanical — kept manual).
- Per-subdir `log.md` (single root log chosen for simplicity).
- Link-integrity enforcement (OKF tolerates broken links).
