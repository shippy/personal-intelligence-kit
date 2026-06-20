# OKF Conformance (output/ bundle)

The `output/` directory is an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
v0.1 bundle: a directory of markdown files, each with YAML frontmatter. All
analysis skills emit concepts via `_lib/okf.py::write_concept`, which
guarantees conformance — do not hand-roll frontmatter in a writer.

## Concept frontmatter

| Field | Status | Notes |
|-------|--------|-------|
| `type` | **required** | Non-empty (e.g. `alert`, `report`, `reflection`). |
| `title` | recommended | Human-readable title. |
| `description` | recommended | One-line summary. |
| `timestamp` | recommended | ISO 8601 (`YYYY-MM-DD`). |
| `tags` | recommended | List of strings. |
| `resource` | recommended | Canonical URI (usually omitted for generated reports). |
| `status`, `sources`, … | custom | Preserved as-is via `**extra`; consumers must keep unknown keys. |

## Links

Use `okf.link(target, text)` for cross-links between concepts. It produces
bundle-relative absolute links (`[text](/alerts/foo.md)`) that stay valid when
documents move. Broken links are tolerated by the spec.

## Reserved files

- **`output/index.md`** — bundle root manifest. The only index file permitted
  frontmatter, and only to carry `okf_version: "0.1"` (plus optional
  `title`/`description`). Authored manually.
- **`output/<subdir>/index.md`** — optional, manual, **no frontmatter**. Body is
  a curated grouped listing. See "Authoring index.md" below.
- **`output/log.md`** — single root changelog, auto-maintained by
  `write_concept`. Newest date first; `* **Creation**: <title>` on first write of
  a slug, `* **Update**: <title>` thereafter. Do not edit by hand.

> Note: `output/log.md` is the OKF bundle changelog. It is **separate** from the
> operational `logs/activity.md`, which records skill-run activity and is not part
> of the bundle.

## Conformance checklist

1. Every non-reserved `.md` has parseable YAML frontmatter. ✓ (`write_concept`)
2. Each frontmatter has a non-empty `type`. ✓ (required argument)
3. Reserved files (`index.md`, `log.md`) follow their structures. ✓
4. Root `output/index.md` declares `okf_version`. ✓

## Authoring index.md (manual)

Add an `index.md` to a subdirectory only when it has grown large enough that a
curated listing helps a reader find things. These are editorial — do **not**
auto-generate them. Format (no frontmatter, except the root index which carries
only `okf_version`):

```markdown
# Group Name
* [Title](/subdir/file.md) - one-line description
```
