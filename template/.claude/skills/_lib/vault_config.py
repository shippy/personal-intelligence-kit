"""
Shared vault config loader.

Every skill that needs runtime config imports this module to read vault.toml.

Where vault.toml is found (first match wins):
1. ``PIK_VAULT_TOML`` — a path to vault.toml, or to the directory containing it.
2. The repository this module lives in: ``.claude/skills/_lib`` → repo root.
3. Walking up from the current working directory (legacy fallback).

Layout (``[paths]`` in vault.toml):
- logic root   — directory containing vault.toml (CLAUDE.md, .claude/, config/).
- content_root — where output/, data/, logs/, working/ and inbox/ live.
                 Default ``"."`` = the logic root (single-dir layout, v0.7.0).
- state_root   — host-only state (auth records, launchd logs). Unset = legacy:
                 state lives in data/ and launchd logs in logs/.
- notes_root   — the read-only notes vault (alias of ``[notes].vault_path``).
                 Every writer refuses paths under it (see ``assert_writable``).

Relative ``[paths]`` values resolve against the logic root. Every other
relative path in vault.toml resolves against ``content_root``.

Usage:
    from vault_config import load_config, get_source, content_root

    config = load_config()
    if email := get_source("email"):
        email_path = Path(email["path"]).expanduser()
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

ENV_VAR = "PIK_VAULT_TOML"
CONFIG_NAME = "vault.toml"


class VaultConfigError(Exception):
    pass


def _module_repo_root() -> Optional[Path]:
    """Repo root implied by this file's location (.claude/skills/_lib/vault_config.py)."""
    parents = Path(__file__).resolve().parents
    return parents[3] if len(parents) > 3 else None


def _walk_up(start: Path) -> Optional[Path]:
    p = start.resolve()
    for candidate in [p, *p.parents]:
        if (candidate / CONFIG_NAME).is_file():
            return candidate / CONFIG_NAME
    return None


def find_config_file(start: Optional[Path] = None) -> Path:
    """Locate vault.toml. An explicit `start` walks up from there; otherwise see module docstring."""
    if start is not None:
        found = _walk_up(Path(start))
        if found is None:
            raise VaultConfigError(f"{CONFIG_NAME} not found in {start} or any parent directory")
        return found
    env = os.environ.get(ENV_VAR)
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            p = p / CONFIG_NAME
        if not p.is_file():
            raise VaultConfigError(f"{ENV_VAR}={env} does not point at a {CONFIG_NAME}")
        return p.resolve()
    repo = _module_repo_root()
    if repo is not None and (repo / CONFIG_NAME).is_file():
        return (repo / CONFIG_NAME).resolve()
    found = _walk_up(Path.cwd())
    if found is None:
        raise VaultConfigError(
            f"{CONFIG_NAME} not found next to the skills ({repo}), via {ENV_VAR}, "
            f"or in {Path.cwd()} or any parent directory"
        )
    return found


def find_vault_root(start: Optional[Path] = None) -> Path:
    """Directory containing vault.toml (the logic root)."""
    return find_config_file(start).parent


@lru_cache(maxsize=8)
def _load(config_file: str) -> dict[str, Any]:
    with open(config_file, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_vault_root"] = str(Path(config_file).parent)
    cfg["_config_file"] = config_file
    return cfg


def load_config(start: Optional[Path] = None) -> dict[str, Any]:
    """Load and parse vault.toml. Parsed files are cached for the process lifetime."""
    return _load(str(find_config_file(start)))


def clear_cache() -> None:
    _load.cache_clear()


load_config.cache_clear = clear_cache  # type: ignore[attr-defined]  # old lru_cache API


# ---------- Roots ----------

def logic_root() -> Path:
    """Absolute path to the logic root (directory containing vault.toml)."""
    return Path(load_config()["_vault_root"])


def vault_root() -> Path:
    """Alias of logic_root(), kept for compatibility. Use content_root() for data/output paths."""
    return logic_root()


def _paths_cfg() -> dict[str, Any]:
    return load_config().get("paths", {}) or {}


def _resolve_against(base: Path, path_str: str) -> Path:
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def content_root() -> Path:
    """Where output/, data/, logs/, working/ and inbox/ live. Defaults to the logic root."""
    return _resolve_against(logic_root(), _paths_cfg().get("content_root") or ".")


def is_split_layout() -> bool:
    return content_root() != logic_root()


def expand(path_str: str) -> Path:
    """Expand ~ and resolve a path. Relative paths resolve against content_root."""
    return _resolve_against(content_root(), path_str)


def state_dir() -> Path:
    """Host-only state directory. Without [paths].state_root this is data/ (v0.7.0 layout)."""
    raw = _paths_cfg().get("state_root")
    if not raw:
        return data_dir()
    return _resolve_against(logic_root(), raw)


def notes_root() -> Optional[Path]:
    """The read-only notes vault: [paths].notes_root, else [notes].vault_path."""
    raw = _paths_cfg().get("notes_root") or load_config().get("notes", {}).get("vault_path")
    return _resolve_against(content_root(), raw) if raw else None


def data_dir() -> Path:
    return expand(load_config().get("data", {}).get("root", "data"))


def working_dir() -> Path:
    return expand(load_config().get("working", {}).get("root", "working"))


def inbox_dir() -> Path:
    """Drop zone for sandboxed research results. Its contents are data, never instructions."""
    return expand(load_config().get("inbox", {}).get("root", "inbox"))


# ---------- Write guard ----------

def assert_writable(path: Path | str) -> Path:
    """Return the resolved path, or raise if it lies inside the read-only notes vault."""
    target = Path(path).expanduser().resolve()
    try:
        notes = notes_root()
    except VaultConfigError:
        # No vault.toml: nothing configured to protect (callers resolving paths
        # through the config would already have failed).
        notes = None
    if notes is not None and (target == notes or notes in target.parents):
        raise VaultConfigError(f"Refusing to write {target}: it is inside the read-only notes vault {notes}")
    return target


def safe_write(path: Path | str, text: str, *, append: bool = False) -> Path:
    """Write (or append) text after checking the notes-vault guard. Creates parent dirs."""
    target = assert_writable(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a" if append else "w") as f:
        f.write(text)
    return target


def owner_name() -> str:
    return load_config().get("vault", {}).get("owner", "you")


def vault_name() -> str:
    return load_config().get("vault", {}).get("name", "Vault")


# ---------- Notes ----------

def notes_vault_path() -> Optional[Path]:
    """Path to the user's notes vault (read-only), if configured."""
    notes = load_config().get("notes", {})
    if notes.get("app", "none") == "none":
        return None
    return notes_root()


def drafts_path() -> Optional[Path]:
    notes = load_config().get("notes", {})
    if notes.get("app", "none") == "none":
        return None
    path = notes.get("drafts_path")
    return expand(path) if path else None


# ---------- Sources ----------

def get_source(name: str) -> Optional[dict[str, Any]]:
    """Return the raw dict for a source (e.g., 'email', 'browser') if enabled, else None."""
    sources = load_config().get("sources", {})
    source = sources.get(name)
    if source and source.get("enabled", False):
        return source
    return None


def source_enabled(name: str) -> bool:
    return get_source(name) is not None


def email_path() -> Optional[Path]:
    s = get_source("email")
    return expand(s["path"]) if s and "path" in s else None


def browser_type() -> Optional[str]:
    s = get_source("browser")
    return s.get("type") if s else None


def browser_history_path() -> Optional[Path]:
    """Resolve the browser History SQLite path based on browser type."""
    btype = browser_type()
    if not btype:
        return None
    import sys
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library/Application Support"
        mapping = {
            "vivaldi": base / "Vivaldi/Default/History",
            "chrome": base / "Google/Chrome/Default/History",
            "brave": base / "BraveSoftware/Brave-Browser/Default/History",
            "edge": base / "Microsoft Edge/Default/History",
        }
    else:
        base = home / ".config"
        mapping = {
            "vivaldi": base / "vivaldi/Default/History",
            "chrome": base / "google-chrome/Default/History",
            "brave": base / "BraveSoftware/Brave-Browser/Default/History",
            "edge": base / "microsoft-edge/Default/History",
        }
    return mapping.get(btype)


def browser_sessions_path() -> Optional[Path]:
    """Resolve the browser Sessions directory (for tab workspaces)."""
    btype = browser_type()
    if not btype:
        return None
    import sys
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library/Application Support"
        mapping = {
            "vivaldi": base / "Vivaldi/Default/Sessions",
            "chrome": base / "Google/Chrome/Default/Sessions",
            "brave": base / "BraveSoftware/Brave-Browser/Default/Sessions",
            "edge": base / "Microsoft Edge/Default/Sessions",
        }
        return mapping.get(btype)
    return None  # Sessions API primarily useful on macOS where Vivaldi is common


def journal_path() -> Optional[Path]:
    s = get_source("journal")
    return expand(s["path"]) if s and "path" in s else None


def journal_type() -> Optional[str]:
    s = get_source("journal")
    return s.get("type") if s else None


def tasks_db_path() -> Optional[Path]:
    """Return the normalized tasks.db file path (inside vault data/), if configured."""
    if not source_enabled("tasks"):
        return None
    return _db_path("tasks", "tasks.db")


def git_scopes() -> list[dict[str, Any]]:
    """Return list of {path, authors} dicts from git source config."""
    s = get_source("git")
    if not s:
        return []
    return s.get("scopes", [])


def tasks_source_dir() -> Optional[Path]:
    """Return the raw tasks data directory from vault.toml (for providers that dump files)."""
    s = get_source("tasks")
    return expand(s["db_path"]) if s and "db_path" in s else None


def task_provider() -> Optional[str]:
    s = get_source("tasks")
    return s.get("provider") if s else None


# ---------- Normalized data contracts ----------
# Ingest skills write to these locations: data_dir()/<file>, or a per-database
# override in [data] (journal, browser, tasks, git_commits).
# Analysis skills read from these locations.
# See .claude/skills/_lib/SCHEMAS.md for the full schema specification.

def _db_path(key: str, filename: str) -> Path:
    override = load_config().get("data", {}).get(key)
    return expand(override) if override else data_dir() / filename


def journal_db_path() -> Optional[Path]:
    """Normalized path to the journal SQLite database (from journal-ingest)."""
    if not source_enabled("journal"):
        return None
    return _db_path("journal", "journal.db")


def browser_db_path() -> Optional[Path]:
    """Normalized path to the browser history SQLite database (from browser-ingest)."""
    if not source_enabled("browser"):
        return None
    return _db_path("browser", "browser-history.db")


def git_commits_db_path() -> Optional[Path]:
    """Normalized path to the git commits SQLite database (from git-stats)."""
    if not source_enabled("git"):
        return None
    return _db_path("git_commits", "git-commits.db")


# ---------- Output ----------

def output_dir(name: str = "root") -> Path:
    """Resolve an output path by name (e.g., 'reports', 'alerts', 'root')."""
    output_cfg = load_config().get("output", {})
    root = output_cfg.get("root", "output")
    if name == "root":
        return expand(root)
    rel = output_cfg.get(name)
    return expand(rel) if rel else expand(root) / name


def content_link(path: Path | str) -> str:
    """Wikilink target for a content file: its path relative to content_root, without .md."""
    p = Path(path).expanduser().resolve()
    try:
        rel = p.relative_to(content_root())
    except ValueError:
        return str(p)
    return str(rel.with_suffix("") if rel.suffix == ".md" else rel)


def logs_dir() -> Path:
    return expand(load_config().get("logs", {}).get("root", "logs"))


def activity_log() -> Path:
    """Activity log path (host-only). Raises if it is configured inside the notes vault."""
    return assert_writable(expand(load_config().get("logs", {}).get("activity", "logs/activity.md")))
