"""OKF v0.2 bundle helpers for the output/ directory.

See _lib/OKF.md for the format spec. Every analysis skill that emits a
markdown document into output/ should write it via write_concept() so the
bundle stays OKF-conformant (frontmatter + non-empty `type`) and the root
log.md changelog stays current.
"""

import errno
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_config import assert_writable, output_dir  # noqa: E402

RESERVED_SLUGS = {"index", "log"}
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

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


_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_UNESCAPES = {"n": "\n", "r": "\r", "t": "\t"}
# Line breaks to str.splitlines() (and so to read_frontmatter) besides \n and \r.
_LINE_BREAKS = {"\x85", " ", " "}
_BARE_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
# Plain scalars YAML resolves to null/bool rather than a string key.
_YAML_SPECIAL = {"null", "true", "false", "yes", "no", "on", "off", "y", "n"}
MAX_FLOW_DEPTH = 32


def _yaml_printable(cp: int) -> bool:
    """YAML 1.2 c-printable (minus what _ESCAPES handles)."""
    return (
        0x20 <= cp <= 0x7E
        or 0xA0 <= cp <= 0xD7FF
        or 0xE000 <= cp <= 0xFFFD
        or 0x10000 <= cp <= 0x10FFFF
    )


def _escape_char(ch: str) -> str:
    if ch in _ESCAPES:
        return _ESCAPES[ch]
    if not _yaml_printable(ord(ch)) or ch in _LINE_BREAKS:
        # Controls, C1, NEL, U+2028/9, U+FFFE/F and lone surrogates (which
        # would also fail UTF-8 encoding) all fit in a \uXXXX escape.
        return f"\\u{ord(ch):04x}"
    return ch


def _scalar(value: Any) -> str:
    """Render a value as a double-quoted YAML scalar on one physical line:
    quotes, backslashes, control characters and Unicode line breaks escaped."""
    return '"' + "".join(_escape_char(ch) for ch in str(value)) + '"'


def _key(key: Any) -> str:
    """Render a mapping key bare when it is a plain identifier YAML reads as a
    string, else as a quoted scalar (so a key can't inject structure)."""
    k = str(key)
    if _BARE_KEY.fullmatch(k) and k.lower() not in _YAML_SPECIAL:
        return k
    return _scalar(k)


def _render_value(value: Any, depth: int = 0) -> str:
    """Render a value in YAML flow style: mappings as {k: v}, sequences as
    [a, b], recursively; every leaf is a double-quoted scalar. Raises
    ValueError past MAX_FLOW_DEPTH nested collections (this also stops cycles)."""
    if isinstance(value, (dict, list, tuple)) and depth >= MAX_FLOW_DEPTH:
        raise ValueError(f"frontmatter nests deeper than {MAX_FLOW_DEPTH} collections")
    if isinstance(value, dict):
        return "{" + ", ".join(
            f"{_key(k)}: {_render_value(v, depth + 1)}" for k, v in value.items()
        ) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_render_value(v, depth + 1) for v in value) + "]"
    return _scalar(value)


def render_frontmatter(fields: dict[str, Any]) -> str:
    """Render frontmatter fields as a YAML block. None values are skipped.
    Returns the block including the --- fences and a trailing blank line."""
    lines = ["---"]
    for key, value in fields.items():
        if value is None:
            continue
        lines.append(f"{_key(key)}: {_render_value(value)}")
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
    _check_slug(slug)
    if not generated_by or not generated_by.strip():
        raise ValueError("generated_by must be a non-empty actor string")
    if stale_days is not None and stale_days < 0:
        raise ValueError(f"stale_days must not be negative (got {stale_days})")
    computed = _COMPUTED_FIELDS.intersection(extra)
    if computed:
        raise TypeError(f"write_concept computes {sorted(computed)}; do not pass them")
    now = datetime.now().astimezone()
    ts = timestamp or now.strftime("%Y-%m-%d")
    if not _DATE.fullmatch(ts[:10]):
        raise ValueError(f"timestamp must start with YYYY-MM-DD (got {ts!r})")
    horizon = stale_days if stale_days is not None else DEFAULT_STALE_DAYS.get(type, FALLBACK_STALE_DAYS)
    feeds = list(dict.fromkeys(source_feeds or []))
    target_dir = assert_writable(Path(out_dir) if out_dir is not None else output_dir(subdir))
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{slug}.md"
    assert_writable(path)  # the file itself may be a symlink planted into the notes vault
    log_root = output_dir("root")
    assert_writable(log_root / "log.md")  # notes-vault guard first: its error is more specific
    # Then refuse planted symlinks anywhere else, before writing anything, so a
    # refused changelog can't leave a half-written concept behind.
    _refuse_symlink(path)
    _refuse_symlink(log_root / "log.md")
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
    _write_no_follow(path, render_frontmatter(fields) + body)
    _append_log(log_root, ts[:10], "Update" if existed else "Creation", title)
    return path


def _check_slug(slug: str) -> None:
    """A slug must be one safe filename part: no separators, NUL, `.`/`..`,
    and not a reserved name in any case (macOS volumes are case-insensitive)."""
    if (
        not slug
        or slug in {".", ".."}
        or any(ch in slug for ch in "/\\\x00")
    ):
        raise ValueError(f"slug {slug!r} must be a single filename component")
    if slug.lower() in RESERVED_SLUGS:
        raise ValueError(f"slug {slug!r} is reserved (index/log)")


def _refuse_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing to write through symlink {path}")


def _write_no_follow(path: Path, text: str) -> None:
    """Write text, refusing to follow a symlink at the final path component
    (O_NOFOLLOW closes the race left by an earlier is_symlink() check)."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise ValueError(f"Refusing to write through symlink {path}") from e
        raise
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def _one_line(text: str) -> str:
    """Collapse line breaks and control characters to spaces so text can't add
    lines to a line-structured file such as log.md."""
    joined = " ".join(str(text).splitlines())
    return "".join(" " if ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F else ch for ch in joined)


def _append_log(root: Path, date: str, kind: str, title: str) -> None:
    root = assert_writable(root)
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "log.md"
    assert_writable(log_path)
    _refuse_symlink(log_path)
    if not _DATE.fullmatch(date):
        raise ValueError(f"changelog date must be YYYY-MM-DD (got {date!r})")
    entry = f"* **{kind}**: {_one_line(title)}"
    existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    heading = f"## {date}"
    lines = existing.splitlines()
    if heading in lines:
        out: list[str] = []
        for line in lines:
            out.append(line)
            if line == heading:
                out.append(entry)
        _write_no_follow(log_path, "\n".join(out) + "\n")
    else:
        _write_no_follow(log_path, f"{heading}\n{entry}\n\n" + existing)


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
        key, raw = (_split_flow(line, sep=":", maxsplit=1) + [""])[:2]
        out[_unquote(key)] = _parse_value(raw)
    return out


def _parse_value(raw: str, depth: int = 0) -> Any:
    """Parse the flow subset emitted by _render_value (recursively).
    Raises ValueError past MAX_FLOW_DEPTH nested collections."""
    raw = raw.strip()
    is_seq = raw.startswith("[") and raw.endswith("]")
    is_map = raw.startswith("{") and raw.endswith("}")
    if (is_seq or is_map) and depth >= MAX_FLOW_DEPTH:
        raise ValueError(f"frontmatter nests deeper than {MAX_FLOW_DEPTH} collections")
    if is_seq:
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_parse_value(item, depth + 1) for item in _split_flow(inner)]
    if is_map:
        inner = raw[1:-1].strip()
        if not inner:
            return {}
        out: dict[str, Any] = {}
        for entry in _split_flow(inner):
            key, value = (_split_flow(entry, sep=":", maxsplit=1) + [""])[:2]
            out[_unquote(key)] = _parse_value(value, depth + 1)
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
    body, out, i = s[1:-1], [], 0
    while i < len(body):
        ch = body[i]
        if ch != "\\" or i + 1 == len(body):
            out.append(ch)
            i += 1
            continue
        nxt = body[i + 1]
        hex4 = body[i + 2:i + 6]
        if nxt == "u" and len(hex4) == 4 and all(c in "0123456789abcdefABCDEF" for c in hex4):
            out.append(chr(int(hex4, 16)))
            i += 6
        else:
            # Known escapes decode; an unknown one keeps its backslash so it
            # can't collapse into a different key or value.
            out.append(_UNESCAPES.get(nxt, nxt if nxt in '"\\' else "\\" + nxt))
            i += 2
    return "".join(out)
