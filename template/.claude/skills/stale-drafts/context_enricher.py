"""
Context Enricher for Stale Drafts

Gathers cross-source signals (browser tabs/history, notes vault, vault workspace)
to determine which drafts have "active momentum".
"""

import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import (  # noqa: E402
    notes_vault_path,
    drafts_path,
    vault_root,
    browser_db_path,
    browser_sessions_path,
    browser_type,
)

from draft_analyzer import Draft

# ── Stopwords ──────────────────────────────────────────────────────

STOPWORDS: set[str] = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "her", "was", "one", "our", "out", "has", "his", "how", "its", "may",
    "new", "now", "old", "see", "way", "who", "did", "get", "got", "him",
    "let", "say", "she", "too", "use", "will", "with", "been", "have",
    "from", "this", "that", "they", "what", "when", "make", "like", "time",
    "very", "your", "just", "know", "take", "come", "could", "than", "look",
    "only", "into", "year", "some", "them", "more", "also", "about", "would",
    "there", "their", "which", "other", "were", "then", "each", "these",
    "after", "many", "being", "those", "much", "well", "back", "should",
    "where", "every", "still", "think", "here", "most", "need", "want",
    "does", "going", "great", "because", "through", "while", "before",
    "between", "over", "such", "really", "things", "something", "even",
    "thing", "first", "same", "right", "work", "part", "long", "never",
    "down", "good",
    # Draft-specific noise
    "draft", "post", "linkedin", "note", "article", "blog", "idea",
    "people", "person",
}

_TOKEN_RE = re.compile(r"[a-z\u00e1\u010d\u010f\u00e9\u011b\u00ed\u0148\u00f3\u0159\u0161\u0165\u00fa\u016f\u00fd\u017e]{3,}", re.IGNORECASE)
_LI_PREFIX_RE = re.compile(r"^LI\s*[-\u2013\u2014]\s*", re.IGNORECASE)


# ── Data classes ───────────────────────────────────────────────────

@dataclass
class TabMatch:
    title: str
    url: str
    workspace: str


@dataclass
class NoteMatch:
    path: str
    preview: str
    days_since_modified: int


@dataclass
class DraftContext:
    draft_filename: str
    keywords: list[str] = field(default_factory=list)
    matching_tabs: list[TabMatch] = field(default_factory=list)
    matching_notes: list[NoteMatch] = field(default_factory=list)
    matching_vault_files: list[NoteMatch] = field(default_factory=list)

    @property
    def total_matches(self) -> int:
        return len(self.matching_tabs) + len(self.matching_notes) + len(self.matching_vault_files)

    @property
    def active_context_score(self) -> float:
        count_score = min(self.total_matches / 5.0, 1.0)
        recency_bonus = 0.0
        if self.matching_tabs:
            recency_bonus = 0.3
        else:
            all_notes = self.matching_notes + self.matching_vault_files
            if any(n.days_since_modified < 7 for n in all_notes):
                recency_bonus = 0.3
            elif any(n.days_since_modified < 30 for n in all_notes):
                recency_bonus = 0.1
        return min(count_score + recency_bonus, 1.0)


# ── Keyword extraction ─────────────────────────────────────────────

def extract_keywords(draft: Draft, max_keywords: int = 10) -> list[str]:
    freq: dict[str, int] = {}
    title = _LI_PREFIX_RE.sub("", draft.filename)
    if title.endswith(".md"):
        title = title[:-3]
    for tok in _TOKEN_RE.findall(title):
        word = tok.lower()
        if word not in STOPWORDS:
            freq[word] = freq.get(word, 0) + 3
    for tok in _TOKEN_RE.findall(draft.content):
        word = tok.lower()
        if word not in STOPWORDS:
            freq[word] = freq.get(word, 0) + 1
    return sorted(freq, key=lambda w: (-freq[w], w))[:max_keywords]


# ── Browser tab search ─────────────────────────────────────────────

_TAB_FRAGMENT_RE = re.compile(r'\{[^{}]*?"urlForThumbnail":"(https?://[^"]+)"[^{}]*?\}')
_WORKSPACE_ID_RE = re.compile(r'"workspaceId":([0-9.e+]+)')


def _load_all_browser_tabs() -> list[tuple[str, str, str]]:
    """Load tabs from browser sessions (Vivaldi) or fall back to browser-history.db."""
    sessions = browser_sessions_path()
    btype = browser_type()

    # Vivaldi sessions (native workspace support)
    if sessions and sessions.is_dir() and btype == "vivaldi":
        return _load_vivaldi_tabs(sessions)

    # Fallback: query browser-history.db
    db = browser_db_path()
    if db and db.exists():
        return _load_tabs_from_db(db)

    return []


def _load_vivaldi_tabs(sessions_dir: Path) -> list[tuple[str, str, str]]:
    vivaldi_dir = sessions_dir.parent
    prefs_path = vivaldi_dir / "Preferences"
    workspace_names: dict[str, str] = {}
    try:
        prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        for ws in prefs.get("vivaldi", {}).get("workspaces", {}).get("list", []):
            ws_id = str(ws.get("id", ""))
            if ws_id:
                workspace_names[ws_id] = ws.get("name", "Unknown")
    except Exception:
        pass

    tab_files = sorted(sessions_dir.glob("Tabs_*"), key=os.path.getmtime, reverse=True)
    if not tab_files:
        return []
    try:
        raw = tab_files[0].read_bytes().decode("latin-1")
    except Exception:
        return []

    tabs, seen = [], set()
    for frag in _TAB_FRAGMENT_RE.finditer(raw):
        url = frag.group(1)
        if url in seen:
            continue
        seen.add(url)
        workspace = "Unknown"
        ws_m = _WORKSPACE_ID_RE.search(frag.group(0))
        if ws_m:
            try:
                ws_id = str(int(float(ws_m.group(1))))
            except (ValueError, OverflowError):
                ws_id = ws_m.group(1)
            workspace = workspace_names.get(ws_id, "Unknown")
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.strip("/").split("/") if p][:3]
        title = parsed.netloc
        if path_parts:
            title += " / " + " / ".join(path_parts)
        tabs.append((workspace, title, url))
    return tabs


def _load_tabs_from_db(db: Path) -> list[tuple[str, str, str]]:
    try:
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT domain, title, url FROM visits ORDER BY last_visit_time DESC LIMIT 500"
        ).fetchall()
        conn.close()
        return [(r[0] or "", r[1] or "", r[2]) for r in rows]
    except Exception:
        return []


def _match_tabs(all_tabs: list[tuple[str, str, str]], keywords: list[str]) -> list[TabMatch]:
    matches = []
    for ws_name, title, url in all_tabs:
        tab_text = (title + " " + url).lower()
        if any(kw.lower() in tab_text for kw in keywords):
            matches.append(TabMatch(title=title, url=url, workspace=ws_name))
    return matches


# ── Notes vault search ─────────────────────────────────────────────

def search_notes_vault(keywords: list[str], max_age_days: int = 30) -> list[NoteMatch]:
    nvault = notes_vault_path()
    if not nvault or not nvault.is_dir():
        return []
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    cutoff_ts = now.timestamp() - (max_age_days * 86400)
    dpath = drafts_path()
    matches = []
    for md in nvault.rglob("*.md"):
        if any(p.startswith(".") for p in md.relative_to(nvault).parts):
            continue
        if dpath and md.resolve().is_relative_to(dpath.resolve()):
            continue
        try:
            mtime = md.stat().st_mtime
            if mtime < cutoff_ts:
                continue
            days = int((now.timestamp() - mtime) / 86400)
            stem = md.stem.lower()
            head = md.read_text(encoding="utf-8", errors="ignore")[:200].lower()
            searchable = stem + " " + head
            if any(kw.lower() in searchable for kw in keywords):
                preview = md.read_text(encoding="utf-8", errors="ignore")[:100].replace("\n", " ").strip()
                rel = str(md.relative_to(nvault))
                matches.append(NoteMatch(path=rel, preview=preview, days_since_modified=days))
        except Exception:
            continue
    return matches


# ── Vault workspace search ─────────────────────────────────────────

def search_vault_workspace(keywords: list[str], max_age_days: int = 30) -> list[NoteMatch]:
    vroot = vault_root()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    cutoff_ts = now.timestamp() - (max_age_days * 86400)
    matches = []
    for subdir in ["working", "output"]:
        d = vroot / subdir
        if not d.is_dir():
            continue
        for md in d.rglob("*.md"):
            try:
                mtime = md.stat().st_mtime
                if mtime < cutoff_ts:
                    continue
                days = int((now.timestamp() - mtime) / 86400)
                stem = md.stem.lower()
                head = md.read_text(encoding="utf-8", errors="ignore")[:200].lower()
                if any(kw.lower() in (stem + " " + head) for kw in keywords):
                    preview = md.read_text(encoding="utf-8", errors="ignore")[:100].replace("\n", " ").strip()
                    rel = str(md.relative_to(vroot))
                    matches.append(NoteMatch(path=rel, preview=preview, days_since_modified=days))
            except Exception:
                continue
    return matches


# ── Orchestration ──────────────────────────────────────────────────

def enrich_drafts(drafts: list[Draft]) -> list[DraftContext]:
    print(f"Enriching {len(drafts)} drafts with cross-source context...")
    all_tabs = _load_all_browser_tabs()
    if all_tabs:
        print(f"  Loaded {len(all_tabs)} browser tabs/visits")
    results, with_ctx = [], 0
    for draft in drafts:
        kw = extract_keywords(draft)
        if not kw:
            results.append(DraftContext(draft_filename=draft.filename))
            continue
        ctx = DraftContext(
            draft_filename=draft.filename,
            keywords=kw,
            matching_tabs=_match_tabs(all_tabs, kw),
            matching_notes=search_notes_vault(kw),
            matching_vault_files=search_vault_workspace(kw),
        )
        results.append(ctx)
        if ctx.total_matches > 0:
            with_ctx += 1
    print(f"  {with_ctx}/{len(drafts)} drafts have active context")
    return results
