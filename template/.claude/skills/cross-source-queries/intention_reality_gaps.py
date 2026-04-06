#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Intention ↔ Reality Gap Analysis

Compares stated intentions (from journal, notes vault goals) against actual
behavior (notes activity, email, tasks). Surfaces priority drift, aspiration
gaps, and neglected yearly goals.

Usage:
    uv run intention_reality_gaps.py
"""

import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import (  # noqa: E402
    load_config,
    notes_vault_path,
    journal_db_path,
    tasks_db_path,
    source_enabled,
    output_dir,
    activity_log,
    owner_name,
)

# ── Keyword extraction ──────────────────────────────────────────────

STOP_WORDS = {
    "a", "an", "the", "to", "of", "and", "or", "in", "on", "at", "for",
    "want", "need", "should", "will", "can", "get", "make", "do",
    "more", "better", "learn", "improve", "continue", "start", "finish",
}


def extract_keywords(text: str, max_kw: int = 5) -> List[str]:
    """
    Simple keyword extraction from text (regex, no LLM).

    EXTENSION POINT: Replace with an LLM-based extractor for higher quality.
    E.g., call Claude Haiku to return a JSON list of 3-5 search terms.
    """
    words = re.findall(r"\b\w+\b", text.lower())
    kw = [w for w in words if w not in STOP_WORDS and len(w) > 3]
    # Deduplicate, keep order
    seen = set()
    result = []
    for w in kw:
        if w not in seen:
            seen.add(w)
            result.append(w)
    return result[:max_kw]


# ── Analyzer ────────────────────────────────────────────────────────

class IntentionRealityAnalyzer:
    def __init__(self):
        load_config()
        self._nvault = notes_vault_path()
        self._jdb = journal_db_path()
        self._tdb = tasks_db_path()

    # ── Goals ────────────────────────────────────────────────────────

    def find_yearly_goals(self) -> Optional[Dict]:
        if not self._nvault or not self._nvault.exists():
            return None
        year = datetime.now().year
        for y in [year, year - 1]:
            for pattern in [f"{y} Goals.md", f"{y} goals.md", f"Goals {y}.md", f"{y}-Goals.md"]:
                for f in self._nvault.rglob(pattern):
                    if f.is_file():
                        goals = self._parse_goals_file(f)
                        return {"file_path": str(f), "year": y, "goals": goals}
        return None

    def _parse_goals_file(self, path: Path) -> List[str]:
        """
        Parse goals from markdown (regex fallback).

        EXTENSION POINT: Use an LLM to semantically extract goals.
        """
        content = path.read_text(errors="ignore")
        goals = re.findall(r"^- \[ \] (.+)$", content, re.MULTILINE)
        if not goals:
            # Fallback: any bullet-point items under "Goals" heading
            goals = re.findall(r"^[-*] (.+)$", content, re.MULTILINE)
        return goals

    def check_goal_evidence(self, goal: str) -> Dict:
        keywords = extract_keywords(goal)
        evidence = {
            "goal": goal,
            "keywords": keywords,
            "notes_files": [],
            "notes_last_modified": None,
            "email_count": 0,
            "task_count": 0,
            "active_task_count": 0,
            "has_evidence": False,
        }

        # Search notes vault
        if self._nvault and self._nvault.exists():
            file_scores: Dict[str, int] = defaultdict(int)
            for kw in keywords:
                for md in self._nvault.rglob("*.md"):
                    if any(p.startswith(".") for p in md.relative_to(self._nvault).parts):
                        continue
                    try:
                        stem = md.stem.lower()
                        head = md.read_text(errors="ignore")[:500].lower()
                        if kw in stem or kw in head:
                            file_scores[str(md)] += (2 if kw in stem else 1)
                    except Exception:
                        continue
            # Keep files with 2+ keyword hits or filename match
            for fp, score in file_scores.items():
                if score >= 2:
                    evidence["notes_files"].append(fp)
            if evidence["notes_files"]:
                mtimes = []
                for fp in evidence["notes_files"]:
                    try:
                        mtimes.append(Path(fp).stat().st_mtime)
                    except Exception:
                        pass
                if mtimes:
                    evidence["notes_last_modified"] = datetime.fromtimestamp(max(mtimes))

        # Search email
        if source_enabled("email"):
            try:
                if len(keywords) >= 2:
                    q = f'(subject:"{keywords[0]}" OR body:"{keywords[0]}") AND (subject:"{keywords[1]}" OR body:"{keywords[1]}")'
                elif keywords:
                    q = f'subject:"{keywords[0]}" OR body:"{keywords[0]}"'
                else:
                    q = None
                if q:
                    r = subprocess.run(["notmuch", "count", q], capture_output=True, text=True, check=False)
                    evidence["email_count"] = int(r.stdout.strip()) if r.returncode == 0 else 0
            except Exception:
                pass

        # Search tasks
        if self._tdb and self._tdb.exists():
            try:
                conn = sqlite3.connect(self._tdb)
                for kw in keywords:
                    rows = conn.execute(
                        "SELECT id, status FROM tasks WHERE title LIKE ? OR body LIKE ?",
                        (f"%{kw}%", f"%{kw}%"),
                    ).fetchall()
                    evidence["task_count"] += len(rows)
                    evidence["active_task_count"] += sum(1 for _, s in rows if s == "open")
                conn.close()
            except Exception:
                pass

        evidence["has_evidence"] = (
            bool(evidence["notes_files"]) or evidence["email_count"] > 0 or evidence["active_task_count"] > 0
        )
        return evidence

    # ── Journal intentions ───────────────────────────────────────────

    def find_journal_intentions(self, days_back: int = 30) -> List[Dict]:
        """Query journal.db for recent intention sentences."""
        if not self._jdb or not self._jdb.exists():
            return []
        start = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        intentions = []
        try:
            conn = sqlite3.connect(self._jdb)
            rows = conn.execute(
                "SELECT i.text, e.date FROM intentions i "
                "JOIN entries e ON i.entry_id = e.id "
                "WHERE e.date >= ? ORDER BY e.date DESC",
                (start,),
            ).fetchall()
            for text, date in rows:
                intentions.append({"text": text, "date": date})
            conn.close()
        except Exception as e:
            print(f"  ⚠ Journal intentions query failed: {e}")
        return intentions

    # ── Full analysis ────────────────────────────────────────────────

    def analyze_yearly_goals(self) -> Dict:
        report = {
            "goals_file": None, "year": None,
            "goals_checked": 0, "goals_with_evidence": 0, "goals_without_evidence": 0,
            "details": [],
        }
        goals_data = self.find_yearly_goals()
        if not goals_data:
            report["error"] = "No yearly goals file found in notes vault"
            return report

        report["goals_file"] = goals_data["file_path"]
        report["year"] = goals_data["year"]
        report["goals_checked"] = len(goals_data["goals"])

        for goal in goals_data["goals"]:
            evidence = self.check_goal_evidence(goal)
            if evidence["has_evidence"]:
                report["goals_with_evidence"] += 1
            else:
                report["goals_without_evidence"] += 1
            report["details"].append(evidence)
        return report


# ── Report generation ────────────────────────────────────────────────

def generate_report() -> Optional[Path]:
    analyzer = IntentionRealityAnalyzer()
    date = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"---\ncreated: {date}\ntype: report\nstatus: final\n---\n",
        f"# Intention ↔ Reality Gap Analysis — {date}\n",
    ]
    sources = []

    # 1. Yearly goals
    goals = analyzer.analyze_yearly_goals()
    if "error" in goals:
        lines.append(f"## Yearly Goals\n\n⚠ {goals['error']}\n")
    else:
        sources.append("notes")
        lines.append(f"## Yearly Goals ({goals['year']})\n")
        lines.append(f"File: `{goals['goals_file']}`\n")
        lines.append(f"Checked: {goals['goals_checked']}, With evidence: {goals['goals_with_evidence']}, "
                      f"Without: {goals['goals_without_evidence']}\n")

        active = [(d, (datetime.now() - d["notes_last_modified"]).days) for d in goals["details"]
                   if d["has_evidence"] and d.get("notes_last_modified")]
        no_evidence = [d for d in goals["details"] if not d["has_evidence"]]

        if active:
            active.sort(key=lambda x: x[1])
            lines.append("### Goals with Evidence\n")
            for detail, days in active:
                lines.append(f"✅ **{detail['goal']}** — last activity {days}d ago")
                lines.append(f"   Keywords: {', '.join(detail['keywords'])}, "
                              f"{len(detail['notes_files'])} notes, {detail['email_count']} emails, "
                              f"{detail['active_task_count']} active tasks\n")
        if no_evidence:
            lines.append("### Goals WITHOUT Evidence\n")
            for d in no_evidence:
                lines.append(f"❌ **{d['goal']}**")
                lines.append(f"   Keywords searched: {', '.join(d['keywords'])}\n")

    # 2. Journal intentions
    intentions = analyzer.find_journal_intentions(days_back=30)
    if intentions:
        sources.append("journal")
        lines.append(f"\n## Recent Intentions from Journal\n")
        lines.append(f"Found **{len(intentions)}** in last 30 days:\n")
        for i in intentions[:10]:
            lines.append(f"- **[{i['date']}]** {i['text']}")
        if len(intentions) > 10:
            lines.append(f"\n*...and {len(intentions) - 10} more*")

    report = "\n".join(lines)
    out = output_dir("reports") / f"intention-reality-{date}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(f"Wrote {out}")

    log = activity_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(f"\n## {datetime.now().strftime('%Y-%m-%dT%H:%M')}\n\n")
        f.write(f"**Action:** Intention-reality gap analysis\n")
        f.write(f"**Sources:** {', '.join(sources)}\n")
        f.write(f"**Output:** [[output/reports/intention-reality-{date}]]\n\n---\n")
    return out


if __name__ == "__main__":
    generate_report()
