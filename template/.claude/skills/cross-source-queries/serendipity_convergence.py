#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Serendipity & Convergence Detection

Detects topics and people appearing across multiple disconnected data sources.
Surfaces emerging interests, intensifying relationships, and unexpected convergences.

Usage:
    uv run serendipity_convergence.py
"""

import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import (  # noqa: E402
    load_config,
    notes_vault_path,
    journal_db_path,
    browser_db_path,
    source_enabled,
    output_dir,
    activity_log,
)
import okf  # noqa: E402


# ── Topic extraction ─────────────────────────────────────────────────

def _extract_topics_regex(text: str, max_topics: int = 10) -> Dict[str, int]:
    """
    Extract topics from text using regex (capitalized words + long words).

    EXTENSION POINT: Use an LLM for semantic topic extraction.
    E.g., call Claude Haiku with: "Extract 3-10 main topics from this text.
    Return JSON array of lowercase 2-4 word phrases."
    """
    words = re.findall(r"\b[A-Z][a-z]+\b|\b\w{5,}\b", text)
    counter = Counter(w.lower() for w in words)
    # Remove very common words
    for w in ["which", "would", "there", "their", "about", "other", "could", "these", "being"]:
        counter.pop(w, None)
    return dict(counter.most_common(max_topics))


# ── Per-source extraction ────────────────────────────────────────────

class SerendipityConvergenceAnalyzer:
    def __init__(self):
        load_config()
        self._nvault = notes_vault_path()
        self._jdb = journal_db_path()
        self._bdb = browser_db_path()

    def _journal_topics(self, days: int) -> Dict[str, int]:
        if not self._jdb or not self._jdb.exists():
            return {}
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            conn = sqlite3.connect(self._jdb)
            rows = conn.execute("SELECT body FROM entries WHERE date >= ?", (start,)).fetchall()
            conn.close()
            text = "\n\n".join(r[0] for r in rows)
            return _extract_topics_regex(text) if text else {}
        except Exception:
            return {}

    def _notes_topics(self, days: int) -> Dict[str, int]:
        if not self._nvault or not self._nvault.exists():
            return {}
        cutoff = (datetime.now() - timedelta(days=days)).timestamp()
        topics = Counter()
        for md in self._nvault.rglob("*.md"):
            if any(p.startswith(".") for p in md.relative_to(self._nvault).parts):
                continue
            try:
                if md.stat().st_mtime < cutoff:
                    continue
                name_words = re.findall(r"\b[A-Z][a-z]+\b|\b\w{5,}\b", md.stem)
                for w in name_words:
                    topics[w.lower()] += 1
                headings = re.findall(r"^#+\s+(.+)$", md.read_text(errors="ignore"), re.MULTILINE)
                for h in headings:
                    for w in re.findall(r"\b[A-Z][a-z]+\b|\b\w{5,}\b", h):
                        topics[w.lower()] += 1
            except Exception:
                continue
        return dict(topics)

    def _browser_topics(self, days: int) -> Dict[str, int]:
        if not self._bdb or not self._bdb.exists():
            return {}
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            conn = sqlite3.connect(self._bdb)
            rows = conn.execute(
                "SELECT title FROM visits WHERE last_visit_time >= ? AND title IS NOT NULL", (start,)
            ).fetchall()
            conn.close()
            topics = Counter()
            for (title,) in rows:
                for w in re.findall(r"\b[A-Z][a-z]+\b|\b\w{5,}\b", title):
                    if w.lower() not in {"reply", "gmail", "inbox", "google"}:
                        topics[w.lower()] += 1
            return dict(topics)
        except Exception:
            return {}

    def _email_topics(self, days: int) -> Dict[str, int]:
        if not source_enabled("email"):
            return {}
        try:
            r = subprocess.run(
                ["notmuch", "search", "--format=json", "--limit=100", f"date:{days}d.."],
                capture_output=True, text=True, check=False, timeout=30,
            )
            if r.returncode != 0:
                return {}
            import json
            results = json.loads(r.stdout)
            topics = Counter()
            for msg in results:
                subject = msg.get("subject", "")
                for w in re.findall(r"\b[A-Z][a-z]+\b|\b\w{5,}\b", subject):
                    if w.lower() not in {"reply", "gmail", "inbox"}:
                        topics[w.lower()] += 1
            return dict(topics)
        except Exception:
            return {}

    # ── Topic convergence ────────────────────────────────────────────

    def detect_topic_convergence(self, days_back: int = 7, min_sources: int = 2) -> List[Dict]:
        topics_by_source = {}
        j = self._journal_topics(days_back)
        if j:
            topics_by_source["journal"] = j
        n = self._notes_topics(days_back)
        if n:
            topics_by_source["notes"] = n
        b = self._browser_topics(days_back)
        if b:
            topics_by_source["browser"] = b
        e = self._email_topics(days_back)
        if e:
            topics_by_source["email"] = e

        topic_sources = defaultdict(list)
        for source, topics in topics_by_source.items():
            for topic, freq in topics.items():
                topic_sources[topic].append({"source": source, "frequency": freq})

        convergences = [
            {"topic": t, "source_count": len(srcs), "sources": srcs,
             "strength": sum(s["frequency"] for s in srcs)}
            for t, srcs in topic_sources.items() if len(srcs) >= min_sources
        ]
        convergences.sort(key=lambda x: (x["source_count"], x["strength"]), reverse=True)
        return convergences

    # ── Person convergence ───────────────────────────────────────────

    def _journal_people(self, days: int) -> Dict[str, int]:
        if not self._jdb or not self._jdb.exists():
            return {}
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            conn = sqlite3.connect(self._jdb)
            rows = conn.execute(
                "SELECT m.name, COUNT(*) FROM mentions m "
                "JOIN entries e ON m.entry_id = e.id "
                "WHERE e.date >= ? GROUP BY m.name ORDER BY 2 DESC",
                (start,),
            ).fetchall()
            conn.close()
            return dict(rows)
        except Exception:
            return {}

    def _notes_people(self, days: int) -> Dict[str, int]:
        if not self._nvault or not self._nvault.exists():
            return {}
        cutoff = (datetime.now() - timedelta(days=days)).timestamp()
        people = Counter()
        # Check for a People/ folder
        for parent in ["People", "people", "Contacts", "contacts"]:
            pdir = self._nvault / parent
            if not pdir.is_dir():
                for sub in self._nvault.iterdir():
                    if sub.is_dir():
                        candidate = sub / parent
                        if candidate.is_dir():
                            pdir = candidate
                            break
            if pdir.is_dir():
                for md in pdir.glob("*.md"):
                    try:
                        if md.stat().st_mtime >= cutoff:
                            people[md.stem] += 2  # Weight for explicit person file
                    except Exception:
                        continue
        # Extract names from recent notes
        for md in self._nvault.rglob("*.md"):
            if any(p.startswith(".") for p in md.relative_to(self._nvault).parts):
                continue
            try:
                if md.stat().st_mtime < cutoff:
                    continue
                names = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b", md.read_text(errors="ignore")[:2000])
                stop = {"The", "This", "That", "Note", "Link"}
                for n in names:
                    if n not in stop and len(n) > 3:
                        people[n] += 1
            except Exception:
                continue
        return dict(people)

    def _email_people(self, days: int) -> Dict[str, int]:
        if not source_enabled("email"):
            return {}
        try:
            import json
            r = subprocess.run(
                ["notmuch", "search", "--format=json", "--limit=100", f"date:{days}d.."],
                capture_output=True, text=True, check=False, timeout=30,
            )
            if r.returncode != 0:
                return {}
            results = json.loads(r.stdout)
            names = Counter()
            for msg in results:
                authors = msg.get("authors", "")
                for name in re.findall(r"([A-Z][a-z]+ [A-Z][a-z]+)", authors):
                    names[name] += 1
            return dict(names)
        except Exception:
            return {}

    def detect_person_convergence(self, days_back: int = 7) -> List[Dict]:
        people_by_source = {}
        j = self._journal_people(days_back)
        if j:
            people_by_source["journal"] = j
        n = self._notes_people(days_back)
        if n:
            people_by_source["notes"] = n
        e = self._email_people(days_back)
        if e:
            people_by_source["email"] = e

        person_sources = defaultdict(list)
        for source, people in people_by_source.items():
            for person, freq in people.items():
                person_sources[person].append({"source": source, "frequency": freq})

        convergences = [
            {"person": p, "source_count": len(srcs), "sources": srcs,
             "strength": sum(s["frequency"] for s in srcs)}
            for p, srcs in person_sources.items() if len(srcs) >= 2
        ]
        convergences.sort(key=lambda x: (x["source_count"], x["strength"]), reverse=True)
        return convergences


# ── Report ───────────────────────────────────────────────────────────

def generate_report() -> Optional[Path]:
    analyzer = SerendipityConvergenceAnalyzer()
    date = datetime.now().strftime("%Y-%m-%d")
    sources_used = []

    topic_conv = analyzer.detect_topic_convergence(days_back=30, min_sources=2)
    person_conv = analyzer.detect_person_convergence(days_back=30)

    lines = [
        f"# Convergence Report — {date}\n",
    ]

    if topic_conv:
        lines.append(f"## Topic Convergence ({len(topic_conv)} topics across 2+ sources)\n")
        lines.append("| Topic | Sources | Strength |")
        lines.append("|-------|---------|----------|")
        for c in topic_conv[:20]:
            src_list = ", ".join(f"{s['source']}({s['frequency']})" for s in c["sources"])
            lines.append(f"| **{c['topic']}** | {src_list} | {c['strength']} |")
        lines.append("")

    if person_conv:
        lines.append(f"## Person Convergence ({len(person_conv)} people across 2+ sources)\n")
        lines.append("| Person | Sources | Strength |")
        lines.append("|--------|---------|----------|")
        for c in person_conv[:20]:
            src_list = ", ".join(f"{s['source']}({s['frequency']})" for s in c["sources"])
            lines.append(f"| **{c['person']}** | {src_list} | {c['strength']} |")
        lines.append("")

    if not topic_conv and not person_conv:
        lines.append("No convergences detected in the last 30 days.\n")

    out = okf.write_concept(
        "reports", f"convergence-{date}", type="report",
        title=f"Convergence Report — {date}",
        description=f"{len(topic_conv)} topic, {len(person_conv)} person convergences",
        body="\n".join(lines), timestamp=date, status="final", tags=["convergence"],
    )
    print(f"Wrote {out}")

    log = activity_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(f"\n## {datetime.now().strftime('%Y-%m-%dT%H:%M')}\n\n")
        f.write(f"**Action:** Convergence analysis\n")
        f.write(f"**Topics:** {len(topic_conv)}, **People:** {len(person_conv)}\n\n---\n")
    return out


if __name__ == "__main__":
    generate_report()
