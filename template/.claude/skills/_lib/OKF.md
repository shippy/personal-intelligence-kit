# OKF Conformance (output/ bundle)

The `output/` directory is an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
v0.2 bundle: a directory of markdown files, each with YAML frontmatter. All
analysis skills emit concepts via `_lib/okf.py::write_concept`, which
guarantees conformance — do not hand-roll frontmatter in a writer.

v0.2 keeps v0.1's conformance rules (parseable frontmatter, non-empty `type`,
reserved-file structure). This bundle adopts the subset of v0.2 that fits
machine-generated personal reports: provenance (`generated`), structured
`sources`, and a freshness horizon (`stale_after`).

## Concept frontmatter

| Field | Status | Notes |
|-------|--------|-------|
| `type` | **required** | Non-empty (e.g. `alert`, `report`, `reflection`). |
| `title` | recommended | Human-readable title. |
| `description` | recommended | One-line summary. |
| `resource` | recommended | Canonical URI (usually omitted for generated reports). |
| `tags` | recommended | List of strings. |
| `timestamp` | v0.1 compat | `YYYY-MM-DD`; the date of `generated.at` (or the caller's report date). |
| `generated` | always emitted | `{by: <actor>, at: <ISO 8601 datetime with offset>}`. `write_concept(generated_by=…)` is required. |
| `sources` | when feeds given | `[{resource: "source:<feed>"}, …]` from `write_concept(source_feeds=[…])`; duplicates dropped, omitted when empty. |
| `stale_after` | always emitted | ISO 8601 instant = `generated.at` + horizon (see below). |
| `verified` | supported, not emitted | `write_concept(verified=[…])` renders it as given. Writers leave it out: nothing human-reviews these reports, so they sit in the honest "unverified" trust tier. |
| ~~`status`~~ | dropped | v0.1 writers emitted `status: "final"`, which is not in v0.2's enum (`draft\|stable\|deprecated`). Absent means `stable`. |
| other keys | custom | Passed through `**extra`; consumers must keep unknown keys. `write_concept` raises `TypeError` for `status`, `sources`, `generated` or `stale_after` in `**extra`, since it computes those itself. |

Nested values are written in YAML flow style (`generated: {by: "…", at: "…"}`)
with every leaf double-quoted. Control characters and Unicode line breaks are
escaped so each field stays on one physical line, and mapping keys that aren't
plain identifiers (or that YAML would read as null/bool) are quoted.
`okf.read_frontmatter` parses that subset without PyYAML (nesting is capped at
`MAX_FLOW_DEPTH`); keep both dependency-free.

### Staleness horizons

`stale_after` uses `DEFAULT_STALE_DAYS` by `type`, overridable per call with
`write_concept(stale_days=N)`:

| `type` | Days |
|--------|------|
| `alert` | 7 |
| `report` | 14 |
| `reflection` | 30 |
| anything else | 14 (`FALLBACK_STALE_DAYS`) |

### Actor convention (`generated.by`)

Build it with `okf.actor(skill, model=None)`:

- Deterministic skills → `process:<skill>` (e.g. `process:session-analyzer`).
- LLM-authored documents → `<skill>/<model>` when the LLM path actually ran
  (e.g. `weekly-reflection/gpt-5.5`), else `process:<skill>`. Pass the model
  that produced the output, including any fallback model.

### Not adopted from v0.2

These don't fit generated personal reports and are deliberately skipped:
`verified` emission, the `Attested Computation` type with its `# Computation`
heading and `executor`/`attester`, per-claim footnote attribution, `sources`
credibility signals (`author`/`usage_count`/`last_modified`), `usage_window`,
and the `references/` convention.

## Output destinations

`write_concept` resolves the named category through `vault_config.output_dir`,
so overrides such as `[output].reflections` are preserved. Callers can supply
`out_dir` for an explicit destination. The changelog remains at
`output_dir("root") / "log.md"`, even when a category lives elsewhere.

Write safety (enforced by `write_concept`):

- A slug must be one filename component (no `/`, `\`, NUL, `.` or `..`) and
  not `index`/`log` in any letter case.
- The concept file and `log.md` are never written through a symlink, wherever
  it points. Both are checked before anything is written, and opened with
  `O_NOFOLLOW`. A symlinked *category directory* is still allowed.
- `timestamp` must start with `YYYY-MM-DD`. Line breaks and control characters
  in a title are collapsed to spaces in its changelog entry.

## Links

Use `okf.link(target, text)` for cross-links between concepts. It produces
bundle-relative absolute links (`[text](/alerts/foo.md)`) that stay valid when
documents move. Broken links are tolerated by the spec.

## Reserved files

- **`output/index.md`** — optional bundle root manifest. The only index file
  permitted frontmatter, and only to carry `okf_version: "0.2"` (plus optional
  `title`/`description`). Authored manually if you want one — `output/` is
  runtime-created local data, so nothing is shipped here by the template.
- **`output/<subdir>/index.md`** — optional, manual, **no frontmatter**. Body is
  a curated grouped listing. See "Authoring index.md" below.
- **`output/log.md`** — single root changelog, auto-maintained by
  `write_concept`. Newest date first; `* **Creation**: <title>` on first write of
  a slug, `* **Update**: <title>` thereafter. Do not edit by hand.

> Note: `output/log.md` is the OKF bundle changelog. It is **separate** from the
> operational `logs/activity/` and `logs/sync.jsonl`, which record skill-run activity and are not part
> of the bundle.

## Conformance checklist

1. Every non-reserved `.md` has parseable YAML frontmatter. ✓ (`write_concept`)
2. Each frontmatter has a non-empty `type`. ✓ (required argument)
3. Reserved files (`index.md`, `log.md`) follow their structures. ✓
4. (Optional) Root `output/index.md` may declare `okf_version` — OKF says
   bundles *may* declare it. Add manually if desired; not shipped by default.

## Authoring index.md (manual)

Add an `index.md` to a subdirectory only when it has grown large enough that a
curated listing helps a reader find things. These are editorial — do **not**
auto-generate them. Format (no frontmatter, except the root index which carries
only `okf_version`):

```markdown
# Group Name
* [Title](/subdir/file.md) - one-line description
```
