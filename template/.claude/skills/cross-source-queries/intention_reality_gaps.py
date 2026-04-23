#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "anthropic>=0.40.0",
#     "openai>=1.0.0",
#     "pydantic-ai>=0.1.0",
# ]
# ///
"""
Intention ↔ Reality Gap Analysis

Semantically assesses progress on yearly goals by feeding the goals file,
all weekly reflections, and supplementary signals (task/email/notes counts)
to an LLM. Falls back to regex keyword heuristics when no API key is set.

Usage:
    uv run intention_reality_gaps.py
"""

import os
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Literal, Optional

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

try:
    from pydantic import BaseModel, Field
    from pydantic_ai import Agent
    from pydantic_ai.providers.anthropic import AnthropicProvider
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.models.openai import OpenAIChatModel
    _HAS_PYDANTIC_AI = True
except ImportError:
    _HAS_PYDANTIC_AI = False


# ── Structured goal parsing ─────────────────────────────────────────


@dataclass
class SubGoal:
    text: str
    done: bool = False


@dataclass
class Goal:
    text: str
    section: str
    sub_goals: List[SubGoal] = field(default_factory=list)
    has_checkbox: bool = False
    is_checked: bool = False
    is_postponed: bool = False


_BULLET_RE = re.compile(r"^(?P<indent>[ \t]*)[-*]\s+(?P<body>.*)$")
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*$")
_CHECKBOX_RE = re.compile(r"^\[(?P<mark>[ xX])\]\s+(?P<rest>.*)$")


def _strip_checkbox(body: str) -> tuple[str, bool, bool]:
    m = _CHECKBOX_RE.match(body)
    if m:
        return m.group("rest"), True, m.group("mark").lower() == "x"
    return body, False, False


def _strip_strikethrough(body: str) -> tuple[str, bool]:
    if body.startswith("~~") and body.endswith("~~") and len(body) > 4:
        return body[2:-2], True
    if "~~" in body:
        return body.replace("~~", ""), True
    return body, False


def parse_goals_structured(path: Path) -> List[Goal]:
    """Parse a markdown goals file into a hierarchy-aware list of Goals.

    Rules:
    - Headings (`##`, `###`) build a section path joined by ' > '.
    - The first bullet after a heading defines that section's top-level indent.
    - Bullets at that indent are top-level goals; deeper bullets are sub-goals.
    - Checkbox state and ~~strikethrough~~ are detected on the bullet body.
    """
    lines = path.read_text(errors="ignore").splitlines()
    section_stack: List[tuple[int, str]] = []
    goals: List[Goal] = []
    top_indent: Optional[int] = None

    for raw in lines:
        if not raw.strip():
            continue

        if not raw.lstrip().startswith(("-", "*")):
            h = _HEADING_RE.match(raw)
            if h:
                level = len(h.group("hashes"))
                title = h.group("title").strip()
                while section_stack and section_stack[-1][0] >= level:
                    section_stack.pop()
                section_stack.append((level, title))
                top_indent = None
                continue

        m = _BULLET_RE.match(raw)
        if not m:
            continue

        indent = len(m.group("indent").replace("\t", "    "))
        body = m.group("body").strip()
        body, has_checkbox, is_checked = _strip_checkbox(body)
        body, is_postponed = _strip_strikethrough(body)
        body = body.strip()
        if not body:
            continue

        section = " > ".join(t for _, t in section_stack)

        if top_indent is None or indent <= top_indent:
            top_indent = indent
            goals.append(
                Goal(
                    text=body,
                    section=section,
                    has_checkbox=has_checkbox,
                    is_checked=is_checked,
                    is_postponed=is_postponed,
                )
            )
        else:
            if goals:
                goals[-1].sub_goals.append(SubGoal(text=body, done=is_checked))

    return goals


# ── Heuristic keyword fallback ──────────────────────────────────────

STOP_WORDS = {
    "a", "an", "the", "to", "of", "and", "or", "in", "on", "at", "for",
    "want", "need", "should", "will", "can", "get", "make", "do",
    "more", "better", "learn", "improve", "continue", "start", "finish",
}


def extract_keywords(text: str, max_kw: int = 5) -> List[str]:
    """Regex keyword extractor used only by the heuristic fallback path."""
    words = re.findall(r"\b\w+\b", text.lower())
    kw = [w for w in words if w not in STOP_WORDS and len(w) > 3]
    seen: set[str] = set()
    result: List[str] = []
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

    def find_yearly_goals(self) -> Optional[Dict]:
        if not self._nvault or not self._nvault.exists():
            return None
        year = datetime.now().year
        for y in [year, year - 1]:
            for pattern in [f"{y} Goals.md", f"{y} goals.md", f"Goals {y}.md", f"{y}-Goals.md"]:
                for f in self._nvault.rglob(pattern):
                    if f.is_file():
                        return {
                            "file_path": str(f),
                            "year": y,
                            "goals": parse_goals_structured(f),
                        }
        return None

    def check_goal_evidence(self, goal: str) -> Dict:
        """Heuristic per-goal signal: notes files, email count, task count.

        Used both as the heuristic fallback assessment and as a supplementary
        signal passed to the LLM alongside reflections.
        """
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

    def find_journal_intentions(self, days_back: int = 30) -> List[Dict]:
        if not self._jdb or not self._jdb.exists():
            return []
        start = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        intentions: List[Dict] = []
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


# ── LLM assessment ──────────────────────────────────────────────────


if _HAS_PYDANTIC_AI:

    class GoalAssessment(BaseModel):
        goal: str = Field(description="The goal text as written in the goals file")
        section: str = Field(description="Section path, e.g. 'Career > DebateFlow'")
        status: Literal["active", "stale", "neglected", "completed", "postponed"] = Field(
            description=(
                "active = evidence in last ~4 weeks; stale = historical evidence only; "
                "neglected = no meaningful evidence; completed = [x] in goals file; "
                "postponed = ~~struck through~~ in goals file"
            )
        )
        confidence: Literal["high", "medium", "low"] = Field(
            description="Confidence in the status assessment given the available evidence"
        )
        evidence_summary: str = Field(
            description="1-2 sentences summarizing evidence (or lack thereof) across sources"
        )
        sub_goal_progress: str = Field(
            description="e.g. '2/4 sub-goals done' or 'no sub-goals'"
        )
        last_seen_in_reflection: Optional[str] = Field(
            default=None,
            description="ISO date (YYYY-MM-DD) of the most recent weekly reflection mentioning this goal, or null",
        )
        recommendation: Optional[str] = Field(
            default=None,
            description="For stale/neglected goals only: concrete suggestion for what to do next",
        )

    class IntentionRealityReport(BaseModel):
        summary: str = Field(description="2-3 sentence executive summary of overall goal health")
        assessments: List[GoalAssessment] = Field(description="One entry per top-level goal")


class LLMAssessor:
    """Semantically assess goals against weekly reflections + supplementary signals."""

    def __init__(self):
        self.model = self._get_model() if _HAS_PYDANTIC_AI else None

    @staticmethod
    def available() -> bool:
        if not _HAS_PYDANTIC_AI:
            return False
        return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY") or os.getenv("OPENAI_API_KEY"))

    def _get_model(self):
        api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
        if api_key:
            return AnthropicModel(
                model_name="claude-sonnet-4-6",
                provider=AnthropicProvider(api_key=api_key),
            )
        if api_key := os.getenv("OPENAI_API_KEY"):
            return OpenAIChatModel(
                model_name="gpt-5.4",
                provider=OpenAIProvider(api_key=api_key),
            )
        return None

    def _format_goals(self, goals: List[Goal]) -> str:
        out: List[str] = []
        cur_section: Optional[str] = None
        for g in goals:
            if g.section != cur_section:
                cur_section = g.section
                out.append(f"\n### {cur_section or '(unsectioned)'}")
            if g.is_postponed:
                state = "~~postponed~~"
            elif g.is_checked:
                state = "[x]"
            elif g.has_checkbox:
                state = "[ ]"
            else:
                state = "(aspirational)"
            sub_note = ""
            if g.sub_goals:
                done = sum(1 for s in g.sub_goals if s.done)
                sub_note = f"  — {done}/{len(g.sub_goals)} sub-goals done"
            out.append(f"- {state} {g.text}{sub_note}")
            for s in g.sub_goals:
                mark = "[x]" if s.done else "[ ]"
                out.append(f"    - {mark} {s.text}")
        return "\n".join(out)

    def _load_reflections(self) -> str:
        ref_dir = output_dir("reflections")
        if not ref_dir.exists():
            return ""
        files = sorted(ref_dir.glob("*.md"))
        if not files:
            return ""
        chunks: List[str] = []
        for f in files:
            try:
                content = f.read_text(errors="ignore")
            except Exception:
                continue
            chunks.append(f"\n## Reflection file: {f.stem}\n\n{content}")
        return "\n".join(chunks)

    def _supplementary_signals(self, goals: List[Goal], analyzer: IntentionRealityAnalyzer) -> str:
        lines = ["_Heuristic keyword match counts — noisy, use only as a tiebreaker._\n"]
        for g in goals:
            if g.is_checked or g.is_postponed:
                continue
            ev = analyzer.check_goal_evidence(g.text)
            lines.append(
                f"- **{g.text[:80]}** ({g.section}): "
                f"{ev['task_count']} tasks ({ev['active_task_count']} active), "
                f"{ev['email_count']} emails, {len(ev['notes_files'])} notes files"
            )
        return "\n".join(lines)

    def assess(self, goals: List[Goal], analyzer: IntentionRealityAnalyzer) -> Optional["IntentionRealityReport"]:
        if not self.model:
            return None

        goals_fmt = self._format_goals(goals)
        reflections = self._load_reflections()
        signals = self._supplementary_signals(goals, analyzer)

        name = owner_name()
        today = datetime.now().strftime("%Y-%m-%d")

        context = f"""You are assessing {name}'s progress on their yearly goals as of {today}.

# Inputs

1. **Goals structure** — every top-level goal with section, checkbox state, and sub-goals.
2. **Weekly reflections** — the primary evidence of what {name} was actually doing each week. Filenames start with the ISO date.
3. **Supplementary signals** — keyword-match counts for tasks/emails/notes. These are NOISY: common words match unrelated goals. Use only as a tiebreaker when reflections are silent.

# Rules

- For every top-level goal, produce one `GoalAssessment`.
- `status` must be one of: `active`, `stale`, `neglected`, `completed`, `postponed`.
    - `completed` iff the goal line is `[x]` in the goals structure.
    - `postponed` iff the goal line is `~~struck-through~~`.
    - Otherwise judge from reflections: `active` if mentioned in the last ~4 weeks of reflections, `stale` if mentioned historically but not recently, `neglected` if no meaningful mention anywhere.
- Prefer reflections over supplementary signals. Signals alone do NOT justify `active`.
- `evidence_summary`: 1-2 sentences. Quote or paraphrase the most recent reflection evidence when possible.
- `last_seen_in_reflection`: the ISO date embedded in the reflection filename (e.g. `2026-04-19-weekly-reflection` → `2026-04-19`), or null if never mentioned.
- `recommendation`: only for `stale`/`neglected` goals; one concrete next step.
- `sub_goal_progress`: e.g. "2/4 done" or "no sub-goals".
- Keep the `goal` and `section` fields verbatim from the goals structure.

# Goals

{goals_fmt}

# Weekly Reflections

{reflections or "_No weekly reflections available — lean on supplementary signals but flag low confidence._"}

# Supplementary Signals

{signals}
"""

        agent = Agent(
            model=self.model,
            output_type=IntentionRealityReport,
            system_prompt=(
                f"You are a thoughtful analyst helping {name} honestly assess progress on their yearly goals. "
                "Be specific, cite reflection evidence where possible, and don't inflate neglected goals into active ones."
            ),
        )

        print(f"  → Assessing {len(goals)} goals via LLM...")
        result = agent.run_sync(context)
        return result.output


# ── Reporting ────────────────────────────────────────────────────────

_STATUS_ICON = {
    "active": "🟢",
    "stale": "🟡",
    "neglected": "🔴",
    "completed": "✅",
    "postponed": "⏸️",
}

_STATUS_LABEL = {
    "active": "Active",
    "stale": "Stale",
    "neglected": "Neglected",
    "completed": "Completed",
    "postponed": "Postponed",
}

_STATUS_ORDER = ["neglected", "stale", "active", "completed", "postponed"]


def _print_terse_summary(report: "IntentionRealityReport") -> None:
    buckets: Dict[str, List[str]] = defaultdict(list)
    for a in report.assessments:
        buckets[a.status].append(a.goal)
    for status in _STATUS_ORDER:
        items = buckets.get(status, [])
        if not items:
            continue
        preview = ", ".join(items[:4])
        more = f", +{len(items) - 4} more" if len(items) > 4 else ""
        print(f"{_STATUS_ICON[status]} {_STATUS_LABEL[status]} ({len(items)}): {preview}{more}")


def _render_llm_report(goals_data: Dict, report: "IntentionRealityReport", date: str) -> str:
    lines = [
        "---",
        f"created: {date}",
        "type: report",
        "status: final",
        "sources: [notes, reflections, llm]",
        "---",
        "",
        f"# Intention ↔ Reality Gap Analysis — {date}",
        "",
        f"Goals file: `{goals_data['file_path']}`",
        "",
        "## Summary",
        "",
        report.summary,
        "",
    ]

    buckets: Dict[str, List["GoalAssessment"]] = defaultdict(list)
    for a in report.assessments:
        buckets[a.status].append(a)
    for status in _STATUS_ORDER:
        items = buckets.get(status, [])
        if not items:
            continue
        preview = ", ".join(a.goal for a in items[:6])
        more = f", +{len(items) - 6} more" if len(items) > 6 else ""
        lines.append(f"- {_STATUS_ICON[status]} **{_STATUS_LABEL[status]} ({len(items)}):** {preview}{more}")
    lines.append("")

    by_section: Dict[str, List["GoalAssessment"]] = defaultdict(list)
    for a in report.assessments:
        by_section[a.section or "(unsectioned)"].append(a)

    lines.append("## Assessments by Section")
    lines.append("")
    for section, items in by_section.items():
        lines.append(f"### {section}")
        lines.append("")
        for a in items:
            icon = _STATUS_ICON.get(a.status, "•")
            label = _STATUS_LABEL.get(a.status, a.status)
            lines.append(f"#### {icon} {a.goal}")
            lines.append("")
            lines.append(f"- **Status:** {label} (confidence: {a.confidence})")
            lines.append(f"- **Evidence:** {a.evidence_summary}")
            lines.append(f"- **Sub-goals:** {a.sub_goal_progress}")
            if a.last_seen_in_reflection:
                lines.append(f"- **Last seen in reflection:** {a.last_seen_in_reflection}")
            if a.recommendation:
                lines.append(f"- **Recommendation:** {a.recommendation}")
            lines.append("")

    return "\n".join(lines)


def _render_heuristic_report(goals_data: Dict, analyzer: IntentionRealityAnalyzer, date: str) -> str:
    """Fallback rendering when no LLM is available — keyword-match based."""
    goals: List[Goal] = goals_data["goals"]
    active: List[tuple[Goal, Dict, Optional[int]]] = []
    no_evidence: List[tuple[Goal, Dict]] = []
    completed: List[Goal] = []
    postponed: List[Goal] = []

    for g in goals:
        if g.is_checked:
            completed.append(g)
            continue
        if g.is_postponed:
            postponed.append(g)
            continue
        ev = analyzer.check_goal_evidence(g.text)
        if ev["has_evidence"]:
            last = ev.get("notes_last_modified")
            days = (datetime.now() - last).days if last else None
            active.append((g, ev, days))
        else:
            no_evidence.append((g, ev))

    lines = [
        "---",
        f"created: {date}",
        "type: report",
        "status: final",
        "sources: [notes]",
        "---",
        "",
        f"# Intention ↔ Reality Gap Analysis — {date}",
        "",
        "_LLM unavailable — using keyword-match heuristics. Results are noisy._",
        "",
        f"## Yearly Goals ({goals_data['year']})",
        "",
        f"File: `{goals_data['file_path']}`",
        "",
        (
            f"Total: {len(goals)}, Active: {len(active)}, Neglected: {len(no_evidence)}, "
            f"Completed: {len(completed)}, Postponed: {len(postponed)}"
        ),
        "",
    ]

    if active:
        active.sort(key=lambda x: (x[2] if x[2] is not None else 9999))
        lines.append("### Goals with Evidence")
        lines.append("")
        for g, ev, days in active:
            tag = f"last activity {days}d ago" if days is not None else "activity detected"
            lines.append(f"✅ **{g.text}** — {tag}")
            lines.append(
                f"   Keywords: {', '.join(ev['keywords'])}, "
                f"{len(ev['notes_files'])} notes, {ev['email_count']} emails, "
                f"{ev['active_task_count']} active tasks"
            )
            lines.append("")
    if no_evidence:
        lines.append("### Goals WITHOUT Evidence")
        lines.append("")
        for g, ev in no_evidence:
            lines.append(f"❌ **{g.text}**")
            lines.append(f"   Keywords searched: {', '.join(ev['keywords'])}")
            lines.append("")
    if completed:
        lines.append("### Completed")
        lines.append("")
        for g in completed:
            lines.append(f"- ✓ {g.text}")
        lines.append("")
    if postponed:
        lines.append("### Postponed")
        lines.append("")
        for g in postponed:
            lines.append(f"- ⏸ {g.text}")
        lines.append("")

    return "\n".join(lines)


def generate_report() -> Optional[Path]:
    analyzer = IntentionRealityAnalyzer()
    date = datetime.now().strftime("%Y-%m-%d")
    sources: List[str] = []

    goals_data = analyzer.find_yearly_goals()
    if not goals_data:
        md = (
            f"---\ncreated: {date}\ntype: report\nstatus: final\n---\n\n"
            f"# Intention ↔ Reality Gap Analysis — {date}\n\n"
            f"⚠ No yearly goals file found in notes vault\n"
        )
        out = output_dir("reports") / f"intention-reality-{date}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md)
        print(f"Wrote {out}")
        return out

    sources.append("notes")
    goals: List[Goal] = goals_data["goals"]

    report = None
    if LLMAssessor.available():
        try:
            report = LLMAssessor().assess(goals, analyzer)
            if report:
                sources.extend(["reflections", "llm"])
        except Exception as e:
            print(f"  ⚠ LLM assessment failed, falling back to heuristics: {e}")
            report = None

    if report is not None:
        md = _render_llm_report(goals_data, report, date)
        print()
        _print_terse_summary(report)
    else:
        md = _render_heuristic_report(goals_data, analyzer, date)

    intentions = analyzer.find_journal_intentions(days_back=30)
    if intentions:
        sources.append("journal")
        md += "\n## Recent Intentions from Journal\n\n"
        md += f"Found **{len(intentions)}** in last 30 days:\n\n"
        for i in intentions[:10]:
            md += f"- **[{i['date']}]** {i['text']}\n"
        if len(intentions) > 10:
            md += f"\n_...and {len(intentions) - 10} more_\n"

    out = output_dir("reports") / f"intention-reality-{date}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(f"Wrote {out}")

    log = activity_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(f"\n## {datetime.now().strftime('%Y-%m-%dT%H:%M')}\n\n")
        f.write("**Action:** Intention-reality gap analysis\n")
        f.write(f"**Sources:** {', '.join(sources)}\n")
        f.write(f"**Output:** [[output/reports/intention-reality-{date}]]\n\n---\n")
    return out


if __name__ == "__main__":
    generate_report()
