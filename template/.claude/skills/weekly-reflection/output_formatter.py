"""
Output Formatter for Weekly Reflection

Generates markdown from WeeklyReflection structured output.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import output_dir, owner_name  # noqa: E402

from llm_synthesizer import WeeklyReflection


class ReflectionFormatter:
    def __init__(self):
        self.review_date = datetime.now().strftime("%Y-%m-%d")

    def format(self, reflection: WeeklyReflection) -> str:
        md = self._frontmatter()
        md += self._opening(reflection)
        md += self._themes(reflection)
        md += self._tensions(reflection)
        md += self._commitments(reflection)
        md += self._notable_moments(reflection)
        md += self._reflection_questions(reflection)
        md += self._footer()
        return md

    def _frontmatter(self) -> str:
        return f"""---
created: {self.review_date}
type: reflection
status: final
---

"""

    def _opening(self, reflection: WeeklyReflection) -> str:
        return f"""# Weekly Reflection — {self.review_date}

> "{reflection.opening_observation}"

---

"""

    def _themes(self, reflection: WeeklyReflection) -> str:
        md = "## Themes of the Week\n\n"
        for theme in reflection.themes:
            md += f"### {theme.title}\n\n"
            md += f"{theme.description}\n\n"
            md += f"*Sources: {', '.join(theme.sources)}*\n\n"
        return md

    def _tensions(self, reflection: WeeklyReflection) -> str:
        if not reflection.tensions:
            return ""
        md = "## Tensions & Conflicts\n\n"
        for t in reflection.tensions:
            md += f"### {t.title}\n\n{t.description}\n\n**Evidence:** {t.evidence}\n\n"
        return md

    def _commitments(self, reflection: WeeklyReflection) -> str:
        if not reflection.commitments:
            return ""
        md = "## Commitments & Relationships\n\n"
        for c in reflection.commitments:
            status_mark = ""
            if c.status:
                if "fulfilled" in c.status.lower():
                    status_mark = " ✓"
                elif "overdue" in c.status.lower():
                    status_mark = " ⚠"
            md += f"- **{c.person}**{status_mark}: {c.context} *({c.source})*\n"
        md += "\n"
        return md

    def _notable_moments(self, reflection: WeeklyReflection) -> str:
        if not reflection.notable_moments:
            return ""
        md = "## Notable Moments\n\n"
        for m in reflection.notable_moments:
            date_str = f" ({m.date})" if m.date else ""
            md += f"### {m.title}{date_str}\n\n{m.description}\n\n"
        return md

    def _reflection_questions(self, reflection: WeeklyReflection) -> str:
        name = owner_name()
        md = f"## Reflections for {name}\n\n"
        for i, q in enumerate(reflection.reflection_questions, 1):
            md += f"{i}. **{q.question}**\n   \n   {q.context}\n\n"
        return md

    def _footer(self) -> str:
        return """---

*This reflection is generated from actual text from your week.*
"""

    def save(self, content: str, out_dir: Path | None = None) -> Path:
        out = out_dir or output_dir("reflections")
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{self.review_date}-weekly-reflection.md"
        path.write_text(content)
        return path
