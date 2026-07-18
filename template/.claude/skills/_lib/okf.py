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
    existed = path.exists()
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
    _append_log(output_dir("root"), ts[:10], "Update" if existed else "Creation", title)
    return path


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


def link(target: str, text: str) -> str:
    """Return a bundle-relative absolute markdown link."""
    if not target.startswith("/"):
        target = "/" + target
    return f"[{text}]({target})"


def read_frontmatter(path: Any) -> dict[str, Any]:
    """Parse a concept file's YAML frontmatter into a dict ({} if none).

    Handles the subset emitted by render_frontmatter (double-quoted scalars and
    flow lists of quoted scalars) plus simple unquoted scalars, so no external
    YAML dependency is required.
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
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_unquote(item) for item in _split_flow(inner)]
    return _unquote(raw)


def _split_flow(inner: str) -> list[str]:
    """Split a flow sequence on commas that are not inside a quoted scalar."""
    items: list[str] = []
    buf: list[str] = []
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
        elif ch == "," and not in_quote:
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
