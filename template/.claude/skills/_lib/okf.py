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
