# Intention ↔ Reality Gap Analysis — LLM Upgrade

**Date:** 2026-04-23
**Status:** Draft
**Scope:** Replace regex heuristics in `intention_reality_gaps.py` with LLM-powered semantic assessment

---

## Problem

The current intention-reality gap script uses keyword extraction + grep to find "evidence" for each goal. This produces false positives (matching common words like "hand", "over", "about") and misses semantic connections. The journal intention extraction is similarly noisy — any sentence containing "need to" or "want to" gets captured, yielding 431 "intentions" in 30 days.

Result: the report says 24/24 goals have evidence, which is useless.

## Design

### Phase A: Accurate neglect detection

Replace the per-goal keyword loop with a single LLM call that receives:
1. The full goals file, parsed structurally
2. All weekly reflections from `output/reflections/`
3. Lightweight supplementary signals (task counts, email counts) from existing sources

The LLM semantically judges evidence per goal, producing structured output.

### Phase B (future): Progress dashboard

Layer on sub-goal progress tracking, completion percentages per section, and trend detection across reflections over time. Not in scope for this change.

---

## Goal file parser

The goals file (`2026 Goals.md`) has a richer structure than the current parser handles:

- **Section headers** (`## Personal`, `### DebateFlow`) provide category context
- **Top-level goals** — first-indentation bullets within a section
- **Sub-goals** — indented bullets under a top-level goal
- **Checkbox state** — `[ ]` (open), `[x]` (done), no checkbox (aspirational)
- **Struck-through** — `~~text~~` means postponed/dropped
- **Nested sections** — `### DebateFlow` under `## Career` → section = `Career > DebateFlow`

The parser produces a structured list:

```python
@dataclass
class SubGoal:
    text: str
    done: bool  # [x] vs [ ]

@dataclass  
class Goal:
    text: str
    section: str           # e.g. "Personal", "Career > DebateFlow"
    sub_goals: list[SubGoal]
    has_checkbox: bool      # False for aspirational bullets without [ ]
    is_checked: bool        # True for [x] items
    is_postponed: bool      # True for ~~struck-through~~ items
```

**What counts as a top-level goal:** Any bullet at the first indentation level within a section. Sub-goals are progress indicators, not separate assessment targets.

Goals that are already `[x]` checked or `~~postponed~~` are included in the output but skip LLM assessment (status = "completed" or "postponed" automatically).

---

## LLM assessment

### Context assembly

The LLM receives a single prompt containing:

1. **Goals structure** — the parsed goal hierarchy, formatted as readable text with section headers, sub-goal completion counts, and checkbox states
2. **Weekly reflections** — all files from `output/reflections/`, concatenated with date headers. ~2300 lines / ~30-40k tokens total. These are the primary evidence source.
3. **Supplementary signals** — for each top-level goal, the existing heuristic search results (task count, email count, notes file count) are included as a brief addendum. These help catch activity not covered by reflections.

### Structured output

```python
class GoalAssessment(BaseModel):
    goal: str = Field(description="The goal text as written")
    section: str = Field(description="Section path, e.g. 'Career > DebateFlow'")
    status: Literal["active", "stale", "neglected"] = Field(
        description="active = evidence in last 4 weeks; stale = evidence exists but nothing recent; neglected = no meaningful evidence"
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="How confident the assessment is based on available evidence"
    )
    evidence_summary: str = Field(
        description="1-2 sentences summarizing what evidence exists across sources"
    )
    sub_goal_progress: str = Field(
        description="e.g. '2/4 sub-goals done' or 'no sub-goals'"
    )
    last_seen_in_reflection: Optional[str] = Field(
        description="ISO date of the most recent weekly reflection mentioning this goal, or null"
    )
    recommendation: Optional[str] = Field(
        description="For stale/neglected goals only: concrete suggestion for what to do"
    )

class IntentionRealityReport(BaseModel):
    summary: str = Field(description="2-3 sentence executive summary of overall goal health")
    assessments: list[GoalAssessment]
```

### Model fallback chain

```python
def _get_model():
    if api_key := os.getenv("ANTHROPIC_API_KEY"):
        return AnthropicModel("claude-sonnet-4-6", provider=AnthropicProvider(api_key=api_key))
    if api_key := os.getenv("OPENAI_API_KEY"):
        return OpenAIModel("gpt-5.4", provider=OpenAIProvider(api_key=api_key))
    return None  # fall back to heuristic mode
```

Dependencies: `pydantic-ai`, `anthropic`, `openai`.

When no API key is available, the script falls back to the existing heuristic approach (with the improved goal parser, but regex keyword matching for evidence).

---

## Report output

Follows the vault's terse + dense pattern.

### Terse summary (stdout + top of report file)

```
🔴 Neglected (3): diet, budget overview, cold outreach
🟡 Stale (4): DebateFlow initial run, Grantomat, knowledge graphs, ...  
🟢 Active (12): proposal, evals.cz, London move, ...
✅ Completed (3): AISI application, Bouncer, ...
⏸️ Postponed (2): Data Talk show, ...
```

### Dense report

Written to `output/reports/intention-reality-YYYY-MM-DD.md` with:
- Frontmatter (created, type, status, sources)
- Executive summary from the LLM
- Per-section breakdown with each goal's full assessment
- Sources consulted and any data gaps noted

---

## File changes

All changes are in `.claude/skills/cross-source-queries/intention_reality_gaps.py`:

1. **Replace inline script deps** — add `pydantic-ai`, `anthropic`, `openai` to the `# /// script` metadata
2. **New: `parse_goals_structured()`** — replaces `_parse_goals_file()` with hierarchy-aware parser
3. **New: `LLMAssessor` class** — handles context assembly, model selection, structured output
4. **Modified: `generate_report()`** — uses LLM assessor when available, falls back to heuristic
5. **Keep: existing heuristic methods** — `extract_keywords()`, `check_goal_evidence()` stay as fallback

No new files. No changes to vault_config or other skills.

---

## What's NOT in scope

- Phase B progress dashboard / trend tracking
- Changes to the journal intention extraction (that's a separate fix)
- Changes to commitment_accountability.py or serendipity_convergence.py
- LLM extension points in those other scripts
