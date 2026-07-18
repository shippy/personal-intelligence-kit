"""
LLM Synthesizer for Weekly Reflection

Uses OpenAI (GPT-5.5 with fallback to GPT-5.4) via pydantic-ai to generate
qualitative insights from weekly text data. Produces structured output:
themes, tensions, commitments, notable moments, and reflection questions.
"""

import os
import sys
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.openai import OpenAIChatModel

PRIMARY_MODEL = "gpt-5.5"
FALLBACK_MODEL = "gpt-5.4"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import owner_name  # noqa: E402

from text_extractor import WeeklyText


# ── Structured output schemas ──────────────────────────────────────

class Theme(BaseModel):
    title: str = Field(description="Short title for the theme (3-7 words)")
    description: str = Field(description="Narrative description with specific evidence from the data")
    sources: List[str] = Field(description="Which data sources support this (journal, tasks, emails, notes, browser)")


class Tension(BaseModel):
    title: str = Field(description="Short title (3-7 words)")
    description: str = Field(description="Narrative description of the conflict or gap")
    evidence: str = Field(description="Specific evidence from the data")


class Commitment(BaseModel):
    person: str = Field(description="Person's name")
    context: str = Field(description="What was the interaction or commitment about")
    source: str = Field(description="Where this came from (email, journal, etc)")
    status: Optional[str] = Field(default=None, description="fulfilled, pending, or overdue")


class NotableMoment(BaseModel):
    title: str = Field(description="Short title (3-7 words)")
    description: str = Field(description="What happened and why it matters")
    date: Optional[str] = Field(default=None, description="Date if known")


class ReflectionQuestion(BaseModel):
    question: str = Field(description="The question to ponder")
    context: str = Field(description="Why this question matters based on the data")


class WeeklyReflection(BaseModel):
    opening_observation: str = Field(
        description="Opening narrative observation about the week (2-3 sentences, conversational)"
    )
    themes: List[Theme] = Field(description="2-4 main themes of the week")
    tensions: List[Tension] = Field(
        description="0-3 tensions or conflicts. Return an empty list when the week's data shows no genuine tension — do not manufacture filler."
    )
    commitments: List[Commitment] = Field(description="Relationship interactions and commitments")
    notable_moments: List[NotableMoment] = Field(description="2-3 notable moments")
    reflection_questions: List[ReflectionQuestion] = Field(description="3-5 questions for reflection")


# ── Redaction ──────────────────────────────────────────────────────

# Add your own sensitive keywords here if you use --redact mode.
# These terms will be filtered from the LLM input.
REDACT_KEYWORDS: list[str] = [
    # Example: "therapy", "job search", "relationship",
]


# ── Synthesizer ────────────────────────────────────────────────────

class ReflectionSynthesizer:
    """Generate qualitative reflections using OpenAI via pydantic-ai."""

    def __init__(self, api_key: Optional[str] = None, redact: bool = False):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.redact = redact
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not found. Set it in .env or pass to constructor.")

        self.model_name = PRIMARY_MODEL
        self.agent = self._build_agent(PRIMARY_MODEL)

    def _build_agent(self, model_name: str) -> Agent:
        provider = OpenAIProvider(api_key=self.api_key)
        model = OpenAIChatModel(model_name=model_name, provider=provider)
        return Agent(
            model=model,
            output_type=WeeklyReflection,
            system_prompt=self._system_prompt(),
        )

    def _system_prompt(self) -> str:
        name = owner_name()
        prompt = f"""You are a thoughtful analyst helping {name} reflect on their week.

{name} is the user this reflection is for. Their own name and email address may appear
in the data (email From/To lines, thread participants, git authorship). Never list
{name} as a contact, commitment, or relationship — a message from {name} is their own
sent mail, not correspondence from a third party.

Your role is to:
1. Identify **themes** — what they were actually focused on (not just what they planned)
2. Detect **tensions** — gaps between intention and action, conflicting priorities
3. Surface **commitments** — relationship work, promises made
4. Highlight **notable moments** — insights, decisions, turning points
5. Generate **reflection questions** — thought-provoking questions based on patterns

Data source weighting:
- **Journal entries and tasks** are the strongest signals — they reflect deliberate intention and action.
- **Browser tabs** are LOW signal. Tabs accumulate over weeks/months. Don't treat tab counts as evidence of attention.
- **Email is a partial window only.** Most real communication happens elsewhere (Slack, messaging). Don't overweight email.
- **Notes vault** notes are strong signals when modified recently — they represent processed thought.
- **Git commits** show concrete output. Commit subjects reveal what was actually built. Repos with many commits reveal where energy went.

Guidelines:
- Be conversational and narrative, not statistical
- Use specific evidence from the data (quote journal entries, task titles, email subjects)
- Focus on what the data reveals about priorities, focus, and patterns
- Detect cross-source patterns (same topic in journal + tasks + notes = strong signal)
- Be honest about intention-reality gaps without judgment, but be conservative about what counts as a gap:
  - Task due dates are aspirational planning hints, not commitments. A task being "overdue" by a few days is normal — it doesn't mean failure.
  - Only flag a tension when the gap is corroborated by another source: a journal complaint, a stated intention that went unmet, an unanswered email, a note that names the conflict directly. A bare overdue task is not enough.
  - When the week mostly went well, it is correct to report zero tensions. Do not invent one to fill the slot.
- Make observations that help the person think, not just summarize

Tone: Thoughtful, direct, curious. Like a smart friend reviewing the week with you."""

        if self.redact:
            prompt += """

IMPORTANT: This reflection will be shared. Exclude themes, tensions, or questions about
sensitive personal topics. Focus only on professional execution, teaching, community building,
and technical work."""
        return prompt

    def _is_redacted(self, text: str) -> bool:
        lower = text.lower()
        return any(kw in lower for kw in REDACT_KEYWORDS)

    def _prepare_context(self, text: WeeklyText) -> str:
        ctx = "# Weekly Data\n\n"

        if text.journal_entries:
            ctx += "\n## Journal Entries\n\n"
            for e in text.journal_entries[:20]:
                if self.redact and self._is_redacted(e.body):
                    continue
                ctx += f"**{e.date}** ({e.speaker}):\n{e.body}\n\n"

        if any(text.tasks.values()):
            ctx += "\n## Tasks\n\n"
            task_headers = {
                "created": "Created this week:",
                "completed": "Completed this week:",
                "overdue": "Stale open tasks (due >14 days ago, any age — these are aspirational due dates, not commitments):",
            }
            for label, tasks in text.tasks.items():
                if not tasks:
                    continue
                ctx += f"**{task_headers.get(label, label.title() + ':')}**\n"
                for t in tasks[:15]:
                    if self.redact and self._is_redacted(t.title + (t.body or "")):
                        continue
                    detail = t.list_name or ""
                    if t.created:
                        detail += f", created {t.created[:10]}" if detail else f"created {t.created[:10]}"
                    ctx += f"- {t.title} ({detail})\n" if detail else f"- {t.title}\n"
                ctx += "\n"

        if any(text.emails.values()):
            ctx += "\n## Emails (partial window — most communication happens elsewhere)\n\n"
            for label, emails in text.emails.items():
                if not emails:
                    continue
                ctx += f"**{label.title()}:**\n"
                for e in emails[:10]:
                    if self.redact and self._is_redacted(e.subject + e.recipient + e.sender):
                        continue
                    who = e.recipient if label == "sent" else e.sender
                    ctx += f"- {label.title()} {who}: {e.subject}\n"
                ctx += "\n"

        if text.notes:
            ctx += "\n## Notes Vault (Modified)\n\n"
            for n in text.notes[:10]:
                if self.redact and self._is_redacted(n.filename + n.preview):
                    continue
                ctx += f"**{n.filename}** ({n.section}, {n.modified}):\n{n.preview[:200]}...\n\n"

        if text.browser_titles:
            ctx += "\n## Browser Activity (background context only)\n\n"
            # Show top domains by number of pages
            sorted_domains = sorted(text.browser_titles.items(), key=lambda x: len(x[1]), reverse=True)
            for domain, titles in sorted_domains[:5]:
                ctx += f"**{domain}** (sample titles):\n"
                for t in titles[:8]:
                    ctx += f"- {t}\n"
                ctx += "\n"

        if text.git_commits:
            ctx += "\n## Git Commits (concrete output)\n\n"
            # Group by repo for readability
            by_repo: dict[str, list] = {}
            for c in text.git_commits:
                by_repo.setdefault(c.repo, []).append(c)
            for repo, commits in sorted(by_repo.items(), key=lambda x: -len(x[1])):
                total_add = sum(c.insertions for c in commits)
                total_del = sum(c.deletions for c in commits)
                ctx += f"**{repo}** ({len(commits)} commits, +{total_add}/-{total_del}):\n"
                for c in commits[:15]:
                    ctx += f"- {c.subject}\n"
                if len(commits) > 15:
                    ctx += f"- ... and {len(commits) - 15} more\n"
                ctx += "\n"

        return ctx

    def synthesize(self, text: WeeklyText) -> WeeklyReflection:
        print(f"\nSynthesizing with OpenAI {self.model_name} (pydantic-ai)...")
        context = self._prepare_context(text)
        try:
            result = self.agent.run_sync(context)
        except Exception as e:
            if self.model_name == PRIMARY_MODEL and _is_model_not_found(e):
                print(f"⚠ {PRIMARY_MODEL} unavailable ({e!s}); falling back to {FALLBACK_MODEL}")
                self.model_name = FALLBACK_MODEL
                self.agent = self._build_agent(FALLBACK_MODEL)
                result = self.agent.run_sync(context)
            else:
                raise
        print(f"✓ Synthesis complete (model: {self.model_name})")
        return result.output


def _is_model_not_found(exc: Exception) -> bool:
    """Heuristic: did OpenAI reject the model name?"""
    msg = str(exc).lower()
    return any(s in msg for s in ("model_not_found", "does not exist", "404", "no such model", "invalid model"))
