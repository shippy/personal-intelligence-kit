"""
LLM Suggester for Stale Drafts

Uses Claude Sonnet via pydantic-ai for two-pass draft assessment:
  Pass 1: Content-only finishability assessment
  Pass 2: Re-ranking with cross-source context (momentum signals)
"""

import os
import sys
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.models.anthropic import AnthropicModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))

from draft_analyzer import Draft, PostedDraft
from context_enricher import DraftContext


# ── Structured output schemas ──────────────────────────────────────

class DraftAssessment(BaseModel):
    draft_file: str = Field(description="Filename of the draft")
    finishability: Literal["high", "medium", "low"] = Field(
        description="high=one sitting, medium=needs development, low=seed only"
    )
    idea_summary: str = Field(description="One sentence: core idea of this draft")
    whats_missing: str = Field(description="Specific, concrete: what it takes to finish")
    recommendation: Literal["finish", "drop", "merge"] = Field(
        description="finish=worth completing, drop=abandon, merge=combine with another"
    )
    merge_target: Optional[str] = Field(default=None, description="If merge: which draft to combine with")
    similar_posted: Optional[str] = Field(default=None, description="If already posted: name the file")


class DraftQueueResult(BaseModel):
    summary: str = Field(description="Brief observation about the collection (1-2 sentences)")
    assessments: list[DraftAssessment]


class EnrichedAssessment(BaseModel):
    draft_file: str
    original_finishability: Literal["high", "medium", "low"]
    adjusted_finishability: Literal["high", "medium", "low"]
    momentum_signal: Literal["strong", "some", "none"]
    ranking_rationale: str
    recommendation: Literal["finish", "drop", "merge"]
    idea_summary: str
    whats_missing: str
    merge_target: Optional[str] = None
    similar_posted: Optional[str] = None


class EnrichedQueueResult(BaseModel):
    summary: str
    assessments: list[EnrichedAssessment]


# ── Suggester ──────────────────────────────────────────────────────

class LLMSuggester:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY (or CLAUDE_API_KEY) not found.")
        self.provider = AnthropicProvider(api_key=self.api_key)
        model = AnthropicModel(model_name="claude-sonnet-4-6", provider=self.provider)
        self.agent = Agent(model=model, output_type=DraftQueueResult, system_prompt=self._pass1_prompt())

    @staticmethod
    def _normalize_filenames(assessments, valid_filenames: set[str]):
        lookup = {}
        for f in valid_filenames:
            lookup[f.lower()] = f
            lookup[f.lower().removesuffix(".md")] = f
            stripped = f.lower().removeprefix("li - ").removesuffix(".md")
            if stripped not in lookup:
                lookup[stripped] = f
        for a in assessments:
            if a.draft_file in valid_filenames:
                continue
            key = a.draft_file.lower().removesuffix(".md")
            if key in lookup:
                a.draft_file = lookup[key]
            else:
                key2 = key.removeprefix("[linkedin] ")
                if key2 in lookup:
                    a.draft_file = lookup[key2]

    def _pass1_prompt(self) -> str:
        return """You are helping someone prioritize their draft content.

For each draft, evaluate:

1. **finishability** (high/medium/low):
   - high: Clear angle, substance, one focused sitting. Maybe needs stronger opening or one more example.
   - medium: Real idea but needs development. Missing argument, evidence, or structural work.
   - low: Seed only, far from postable.

2. **idea_summary**: One sentence core insight. If there isn't one, say so.

3. **whats_missing**: Specific. Not "needs more work" but "needs a concrete example" or "the real point is buried in paragraph 3."

4. **recommendation** (finish/drop/merge):
   - finish: Real idea worth completing.
   - drop: Dead weight — empty concept or topic already covered.
   - merge: Overlaps with another draft.

Guidelines:
- Substance over length.
- Personal stories and evergreen insights stay valuable regardless of age.
- Time-sensitive takes lose value quickly.
- If topic is already posted, flag it.
- Don't be afraid to recommend drop.
- You MUST assess EVERY draft in the input."""

    def _prepare_context(self, drafts: list[Draft], posted: list[PostedDraft]) -> str:
        ctx = "# Drafts to Assess\n\n"
        for d in drafts:
            prefix = "[LinkedIn] " if d.is_linkedin else ""
            ctx += f"## {prefix}{d.filename}\n"
            ctx += f"- Created: {d.days_stale} days ago\n- Word count: {d.word_count}\n"
            if d.tags:
                ctx += f"- Tags: {', '.join(d.tags)}\n"
            ctx += f"\n**Content:**\n```\n{d.content[:2000]}\n```\n\n"
        if posted:
            ctx += "\n# Already Posted (cross-reference)\n\n"
            for p in posted[:20]:
                ctx += f"- **{p.filename}**: {p.preview}\n"
        return ctx

    def assess(self, drafts: list[Draft], posted: list[PostedDraft], max_retries: int = 2) -> DraftQueueResult:
        if not drafts:
            return DraftQueueResult(summary="No drafts to assess.", assessments=[])
        print(f"\nAssessing {len(drafts)} drafts...")
        valid = {d.filename for d in drafts}
        context = self._prepare_context(drafts, posted)
        result = self.agent.run_sync(context)
        all_a = list(result.output.assessments)
        self._normalize_filenames(all_a, valid)
        summary = result.output.summary
        for attempt in range(max_retries):
            assessed = {a.draft_file for a in all_a}
            missing = [d for d in drafts if d.filename not in assessed]
            if not missing:
                break
            print(f"  {len(missing)} missing — retry {attempt + 1}...")
            retry = self.agent.run_sync(self._prepare_context(missing, posted))
            new = list(retry.output.assessments)
            self._normalize_filenames(new, valid)
            for a in new:
                if a.draft_file not in assessed:
                    all_a.append(a)
                    assessed.add(a.draft_file)
        print(f"Assessed {len(all_a)}/{len(drafts)} drafts.")
        return DraftQueueResult(summary=summary, assessments=all_a)

    def rerank(self, pass1: DraftQueueResult, contexts: list[DraftContext], drafts: list[Draft], max_retries: int = 2) -> EnrichedQueueResult:
        if not pass1.assessments:
            return EnrichedQueueResult(summary="No drafts to re-rank.", assessments=[])
        print(f"\nRe-ranking {len(pass1.assessments)} drafts with context...")
        model = AnthropicModel(model_name="claude-sonnet-4-6", provider=self.provider)
        pass2_agent = Agent(model=model, output_type=EnrichedQueueResult, system_prompt=self._pass2_prompt(), retries=3)
        valid = {a.draft_file for a in pass1.assessments}
        context = self._pass2_context(pass1, contexts, drafts)
        result = pass2_agent.run_sync(context)
        all_a = list(result.output.assessments)
        self._normalize_filenames(all_a, valid)
        summary = result.output.summary
        for attempt in range(max_retries):
            enriched = {a.draft_file for a in all_a}
            missing_f = valid - enriched
            if not missing_f:
                break
            print(f"  {len(missing_f)} missing from re-rank — retry {attempt + 1}...")
            partial = DraftQueueResult(summary="", assessments=[a for a in pass1.assessments if a.draft_file in missing_f])
            retry = pass2_agent.run_sync(self._pass2_context(partial, contexts, drafts))
            for a in retry.output.assessments:
                if a.draft_file not in enriched:
                    all_a.append(a)
                    enriched.add(a.draft_file)
        print(f"Re-ranked {len(all_a)}/{len(valid)} drafts.")
        return EnrichedQueueResult(summary=summary, assessments=all_a)

    def _pass2_prompt(self) -> str:
        return """You are re-ranking stale drafts based on ACTIVE CONTEXT from the author's other work.

You have Pass 1 assessments (content-only). Now enrich with cross-source signals: browser tabs, notes, and workspace files.

## Momentum Signals
- **strong**: Multiple matching sources. PROMOTE — author is in the headspace.
- **some**: 1-2 matches. Slight boost.
- **none**: No context. DEMOTE unless content alone justifies high.

## Rules
- You CAN change finishability up or down based on context.
- Carry forward idea_summary and whats_missing. Refine if context reveals new info.
- Be concrete about what signals mean.
- You MUST assess EVERY draft from Pass 1.
- Order assessments by priority: highest-value first.

## CRITICAL OUTPUT FORMAT
You MUST return BOTH a summary AND an assessments array. The assessments array must contain one EnrichedAssessment for EVERY draft listed below. Do NOT return a summary alone."""

    def _pass2_context(self, pass1: DraftQueueResult, contexts: list[DraftContext], drafts: list[Draft]) -> str:
        ctx_by_file = {c.draft_filename: c for c in contexts}
        draft_by_file = {d.filename: d for d in drafts}
        lines = ["# Pass 2: Re-ranking with Context\n"]
        for a in pass1.assessments:
            lines.append(f"## {a.draft_file}")
            lines.append(f"- Pass 1 Finishability: {a.finishability}")
            lines.append(f"- Recommendation: {a.recommendation}")
            lines.append(f"- Idea: {a.idea_summary}")
            lines.append(f"- Missing: {a.whats_missing}")
            d = draft_by_file.get(a.draft_file)
            if d:
                lines.append(f"- Age: {d.days_stale} days, Words: {d.word_count}")
            ctx = ctx_by_file.get(a.draft_file)
            if ctx and ctx.total_matches > 0:
                lines.append(f"\n**Active Context ({ctx.total_matches} matches):**")
                for tab in ctx.matching_tabs[:5]:
                    lines.append(f"  - Tab [{tab.workspace}]: {tab.title}")
                for note in ctx.matching_notes[:5]:
                    lines.append(f"  - Note: {note.path} ({note.days_since_modified}d ago)")
                for vf in ctx.matching_vault_files[:5]:
                    lines.append(f"  - Vault: {vf.path} ({vf.days_since_modified}d ago)")
            else:
                lines.append("\n**Active Context:** None found")
            lines.append("")
        return "\n".join(lines)
