"""OKF v0.2 bundle helpers for the output/ directory.

See _lib/OKF.md for the format spec. Every analysis skill that emits a
markdown document into output/ should write it via write_concept() so the
bundle stays OKF-conformant (frontmatter + non-empty `type`) and the root
log.md changelog stays current.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_config import assert_writable, output_dir  # noqa: E402

RESERVED_SLUGS = {"index", "log"}

# Days after generation at which a concept should be treated as stale (OKF v0.2
# `stale_after`). Types not listed fall back to FALLBACK_STALE_DAYS.
DEFAULT_STALE_DAYS = {"alert": 7, "report": 14, "reflection": 30}
FALLBACK_STALE_DAYS = 14

# Fields write_concept computes itself; passing them through **extra would emit
# a stale or conflicting shape (v0.1 `status`/flat `sources`).
_COMPUTED_FIELDS = {"status", "sources", "generated", "stale_after"}


def actor(skill: str, model: str | None = None) -> str:
    """Return the `generated.by` actor: `<skill>/<model>` when an LLM produced
    the document, else `process:<skill>`."""
    return f"{skill}/{model}" if model else f"process:{skill}"


def _scalar(value: Any) -> str:
    """Render a value as a double-quoted, escaped YAML scalar."""
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _render_value(value: Any) -> str:
    """Render a value in YAML flow style: mappings as {k: v}, sequences as
    [a, b], recursively; every leaf is a double-quoted scalar."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {_render_value(v)}" for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_render_value(v) for v in value) + "]"
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


def write_concept(
    subdir: str,
    slug: str,
    *,
    type: str,
    title: str,
    body: str,
    generated_by: str,
    description: str | None = None,
    tags: list[str] | None = None,
    resource: str | None = None,
    timestamp: str | None = None,
    source_feeds: list[str] | None = None,
    stale_days: int | None = None,
    verified: list[dict[str, Any]] | None = None,
    out_dir: Path | None = None,
    **extra: Any,
) -> Path:
    """Write an OKF concept document and return its path.

    `generated_by` is the actor (see actor()); `source_feeds` are coarse feed
    names rendered as `sources: [{resource: "source:<feed>"}]`; `stale_days`
    overrides DEFAULT_STALE_DAYS for this type; `verified` is rendered as given.
    """
    if slug in RESERVED_SLUGS:
        raise ValueError(f"slug {slug!r} is reserved (index/log)")
    if not generated_by or not generated_by.strip():
        raise ValueError("generated_by must be a non-empty actor string")
    if stale_days is not None and stale_days < 0:
        raise ValueError(f"stale_days must not be negative (got {stale_days})")
    computed = _COMPUTED_FIELDS.intersection(extra)
    if computed:
        raise TypeError(f"write_concept computes {sorted(computed)}; do not pass them")
    now = datetime.now().astimezone()
    ts = timestamp or now.strftime("%Y-%m-%d")
    horizon = stale_days if stale_days is not None else DEFAULT_STALE_DAYS.get(type, FALLBACK_STALE_DAYS)
    feeds = list(dict.fromkeys(source_feeds or []))
    target_dir = assert_writable(Path(out_dir) if out_dir is not None else output_dir(subdir))
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{slug}.md"
    assert_writable(path)  # the file itself may be a symlink planted into the notes vault
    existed = path.exists()
    fields: dict[str, Any] = {
        "type": type,
        "title": title,
        "description": description,
        "resource": resource,
        "tags": tags,
        "timestamp": ts,
        "generated": {"by": generated_by, "at": now.isoformat(timespec="seconds")},
        "sources": [{"resource": f"source:{feed}"} for feed in feeds] or None,
        "stale_after": (now + timedelta(days=horizon)).isoformat(timespec="seconds"),
        "verified": verified,
    }
    fields.update(extra)
    path.write_text(render_frontmatter(fields) + body)
    _append_log(output_dir("root"), ts[:10], "Update" if existed else "Creation", title)
    return path


def _append_log(root: Path, date: str, kind: str, title: str) -> None:
    root = assert_writable(root)
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "log.md"
    assert_writable(log_path)
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


def link(target: str, text: str) -> str:
    """Return a bundle-relative absolute markdown link."""
    if not target.startswith("/"):
        target = "/" + target
    return f"[{text}]({target})"


def read_frontmatter(path: Any) -> dict[str, Any]:
    """Parse a concept file's YAML frontmatter into a dict ({} if none).

    Handles the subset emitted by render_frontmatter (double-quoted scalars and
    nested flow mappings/sequences of them) plus simple unquoted scalars, so no
    external YAML dependency is required.
    """
    text = Path(path).read_text()
    if not text.startswith("---\n"):
        return {}
    block, sep, _ = text[4:].partition("\n---")
    if not sep:
        return {}
    out: dict[str, Any] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        out[key.strip()] = _parse_value(raw.strip())
    return out


def _parse_value(raw: str) -> Any:
    """Parse the flow subset emitted by _render_value (recursively)."""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_parse_value(item) for item in _split_flow(inner)]
    if raw.startswith("{") and raw.endswith("}"):
        inner = raw[1:-1].strip()
        if not inner:
            return {}
        out: dict[str, Any] = {}
        for entry in _split_flow(inner):
            key, value = (_split_flow(entry, sep=":", maxsplit=1) + [""])[:2]
            out[_unquote(key)] = _parse_value(value)
        return out
    return _unquote(raw)


def _split_flow(inner: str, sep: str = ",", maxsplit: int = -1) -> list[str]:
    """Split flow content on `sep` where it is neither inside a quoted scalar
    nor inside a nested [..] or {..} collection."""
    items: list[str] = []
    buf: list[str] = []
    depth = 0
    in_quote = esc = False
    for ch in inner:
        if esc:
            buf.append(ch)
            esc = False
        elif ch == "\\":
            buf.append(ch)
            esc = True
        elif ch == '"':
            in_quote = not in_quote
            buf.append(ch)
        elif in_quote:
            buf.append(ch)
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth -= 1
            buf.append(ch)
        elif ch == sep and depth == 0 and (maxsplit < 0 or len(items) < maxsplit):
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    items.append("".join(buf))
    return items


def _unquote(s: str) -> str:
    s = s.strip()
    if not (len(s) >= 2 and s[0] == '"' and s[-1] == '"'):
        return s
    out: list[str] = []
    esc = False
    for ch in s[1:-1]:
        if esc:
            out.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        else:
            out.append(ch)
    return "".join(out)
