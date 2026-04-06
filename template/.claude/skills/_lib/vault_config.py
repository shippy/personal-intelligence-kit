"""
Shared vault config loader.

Every skill that needs runtime config imports this module to read vault.toml.
Walks up from CWD (or a given path) to find vault.toml and parse it.

Usage:
    from vault_config import load_config, get_source, vault_root

    config = load_config()
    if email := get_source("email"):
        email_path = Path(email["path"]).expanduser()
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional


class VaultConfigError(Exception):
    pass


def find_vault_root(start: Optional[Path] = None) -> Path:
    """Walk upward from `start` (default: CWD) to find the directory containing vault.toml."""
    p = (start or Path.cwd()).resolve()
    for candidate in [p, *p.parents]:
        if (candidate / "vault.toml").exists():
            return candidate
    raise VaultConfigError(
        f"vault.toml not found in {start or Path.cwd()} or any parent directory"
    )


@lru_cache(maxsize=1)
def load_config(start: Optional[Path] = None) -> dict[str, Any]:
    """Load and parse vault.toml. Result is cached for the process lifetime."""
    root = find_vault_root(start)
    with open(root / "vault.toml", "rb") as f:
        cfg = tomllib.load(f)
    cfg["_vault_root"] = str(root)
    return cfg


def vault_root() -> Path:
    """Absolute path to the vault root (directory containing vault.toml)."""
    return Path(load_config()["_vault_root"])


def expand(path_str: str) -> Path:
    """Expand ~ and resolve a path. Relative paths resolve against vault_root."""
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        p = vault_root() / p
    return p.resolve()


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
    path = notes.get("vault_path")
    return expand(path) if path else None


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
    return vault_root() / "data" / "tasks.db"


def tasks_source_dir() -> Optional[Path]:
    """Return the raw tasks data directory from vault.toml (for providers that dump files)."""
    s = get_source("tasks")
    return expand(s["db_path"]) if s and "db_path" in s else None


def task_provider() -> Optional[str]:
    s = get_source("tasks")
    return s.get("provider") if s else None


# ---------- Normalized data contracts ----------
# Ingest skills write to these fixed locations inside vault data/.
# Analysis skills read from these locations.
# See .claude/skills/_lib/SCHEMAS.md for the full schema specification.

def journal_db_path() -> Optional[Path]:
    """Normalized path to the journal SQLite database (from journal-ingest)."""
    if not source_enabled("journal"):
        return None
    return vault_root() / "data" / "journal.db"


def browser_db_path() -> Optional[Path]:
    """Normalized path to the browser history SQLite database (from browser-ingest)."""
    if not source_enabled("browser"):
        return None
    return vault_root() / "data" / "browser-history.db"


# ---------- Output ----------

def output_dir(name: str = "root") -> Path:
    """Resolve an output path by name (e.g., 'reports', 'alerts', 'root')."""
    output_cfg = load_config().get("output", {})
    rel = output_cfg.get(name, f"output/{name}")
    return expand(rel)


def logs_dir() -> Path:
    return expand(load_config().get("logs", {}).get("root", "logs"))


def activity_log() -> Path:
    return expand(load_config().get("logs", {}).get("activity", "logs/activity.md"))
