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
