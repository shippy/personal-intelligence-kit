#!/usr/bin/env python3
"""
Draft Queue Skill

Surface the most finishable drafts and rank them for writing sessions.
Two-pass LLM assessment with cross-source context enrichment.

Usage:
    cd .claude/skills/stale-drafts
    uv run stale_drafts.py
"""

import math
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import load_config, vault_root, output_dir, activity_log  # noqa: E402

from draft_analyzer import DraftAnalyzer
from llm_suggester import LLMSuggester, DraftQueueResult, EnrichedQueueResult
from context_enricher import enrich_drafts, DraftContext


class StaleDraftsOrchestrator:
    def __init__(self):
        load_config()
        self.review_date = datetime.now().strftime("%Y-%m-%d")
        self.data_sources_used: list[str] = []

        # ANTHROPIC_API_KEY should be in env via: uv run --env-file .env
        # Also accepts CLAUDE_API_KEY as alias (avoids conflict with Claude Code's own env)
        self.llm_available = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY"))
        if not self.llm_available:
            print("Warning: ANTHROPIC_API_KEY (or CLAUDE_API_KEY) not found — LLM assessment will be skipped.")

        self.analyzer = DraftAnalyzer()
        self.suggester = None
        self.result: DraftQueueResult | None = None
        self.enriched_result: EnrichedQueueResult | None = None
        self.contexts: list[DraftContext] = []

    def run(self):
        print("=" * 60)
        print("DRAFT QUEUE")
        print(f"Date: {self.review_date}")
        print("=" * 60)

        # Phase 1: Scan
        print("\n=== Phase 1: Scanning Drafts ===\n")
        self.analyzer.scan()
        self.analyzer.load_posted()
        self.data_sources_used.append("notes-drafts")
        stats = self.analyzer.stats
        print(f"Found {stats['total']} drafts: {stats['non_empty']} with content, {stats['empty']} empty")

        non_empty = self.analyzer.get_non_empty_drafts()
        if not non_empty:
            print("\nNo drafts with content found.")
            self._generate_empty_report()
            return

        # Phase 2: LLM assessment
        if self.llm_available:
            print("\n=== Phase 2: Assessing Finishability ===\n")
            self.suggester = LLMSuggester()
            self.result = self.suggester.assess(non_empty, self.analyzer.posted)
            self.data_sources_used.append("llm")

        # Phase 2.5: Cross-source context
        print("\n=== Phase 2.5: Gathering Active Context ===\n")
        self.contexts = enrich_drafts(non_empty)
        self.data_sources_used.append("cross-source")

        # Phase 2.6: Re-rank with context
        if self.llm_available and self.result and self.suggester:
            print("\n=== Phase 2.6: Re-ranking with Context ===\n")
            try:
                self.enriched_result = self.suggester.rerank(self.result, self.contexts, non_empty)
            except Exception as e:
                print(f"  ⚠ Pass 2 re-ranking failed ({e.__class__.__name__}), using Pass 1 results.")
                self.enriched_result = None

        # Phase 3: Rank
        finish_queue, drop_list = self._rank_drafts(non_empty)

        # Phase 4: Reports
        print("\n=== Phase 3: Generating Reports ===\n")
        terse_path, dense_path = self._generate_reports(finish_queue, drop_list)

        # Phase 5: Notify
        self._send_notification(len(finish_queue), terse_path)

        # Phase 6: Log
        self._log_activity(len(finish_queue), len(drop_list))

        print(f"\nDone! Terse: {terse_path}")

    def _rank_drafts(self, non_empty):
        finish_queue, drop_list = [], []
        fw_map = {"high": 3, "medium": 2, "low": 1}
        mw_map = {"strong": 1.5, "some": 1.0, "none": 0.7}

        for draft in non_empty:
            enriched, assessment = None, None
            if self.enriched_result:
                for a in self.enriched_result.assessments:
                    if a.draft_file == draft.filename:
                        enriched = a
                        break
            if not enriched and self.result:
                for a in self.result.assessments:
                    if a.draft_file == draft.filename:
                        assessment = a
                        break
            item = enriched or assessment
            if item and item.recommendation in ("drop", "merge"):
                drop_list.append((draft, item, 0))
                continue
            if enriched:
                fw = fw_map.get(enriched.adjusted_finishability, 1)
                mw = mw_map.get(enriched.momentum_signal, 1.0)
            elif assessment:
                fw = fw_map.get(assessment.finishability, 1)
                mw = 1.0
            else:
                fw, mw = 1, 1.0
            freshness = 0.5 + 0.5 * math.exp(-draft.days_stale / 180)
            score = fw * freshness * mw
            finish_queue.append((draft, enriched or assessment, score))

        finish_queue.sort(key=lambda x: x[2], reverse=True)
        return finish_queue, drop_list

    def _generate_reports(self, finish_queue, drop_list):
        stats = self.analyzer.stats
        empty_count = stats["empty"]
        ctx_by_file = {c.draft_filename: c for c in self.contexts}

        # ── Terse ──
        terse = f"""---
created: {self.review_date}
type: alert
status: final
sources: [{', '.join(self.data_sources_used)}]
---

# Draft Queue — {self.review_date}

**Stats:** {stats['total']} scanned | {len(finish_queue)} finishable | {len(drop_list) + empty_count} to drop

"""
        if self.result:
            terse += f"*{self.result.summary}*\n\n"

        terse += "## Finish Queue (pick one → run draft-coach)\n\n"
        for i, (draft, item, score) in enumerate(finish_queue, 1):
            if item and hasattr(item, "adjusted_finishability"):
                fin = item.adjusted_finishability
                emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(fin, "⚪")
                mom = {"strong": " 🔥", "some": " 💨", "none": ""}.get(item.momentum_signal, "")
                adj = f"↑ from {item.original_finishability}, " if item.original_finishability != fin else ""
                terse += f"{i}. {emoji} **{draft.filename}** ({adj}{fin}, {draft.days_stale}d){mom}\n"
                terse += f"   {item.idea_summary} {item.whats_missing}\n"
                ctx = ctx_by_file.get(draft.filename)
                if ctx and ctx.total_matches > 0:
                    parts = []
                    if ctx.matching_tabs:
                        ws_names = set(t.workspace for t in ctx.matching_tabs)
                        parts.append(f"{len(ctx.matching_tabs)} tabs in {', '.join(ws_names)}")
                    if ctx.matching_notes:
                        parts.append(f"{len(ctx.matching_notes)} notes")
                    if ctx.matching_vault_files:
                        parts.append(f"{len(ctx.matching_vault_files)} vault files")
                    terse += f"   Active: {', '.join(parts)}\n"
                terse += "\n"
            elif item and hasattr(item, "finishability"):
                emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(item.finishability, "⚪")
                terse += f"{i}. {emoji} **{draft.filename}** ({item.finishability}, {draft.days_stale}d)\n"
                terse += f"   {item.idea_summary} {item.whats_missing}\n\n"
            else:
                terse += f"{i}. ⚪ **{draft.filename}** ({draft.days_stale}d, {draft.word_count}w)\n"
                terse += f"   {draft.preview[:100]}\n\n"

        if drop_list or empty_count:
            terse += "## Consider Dropping\n\n"
            for draft, item, _ in drop_list:
                reason = item.whats_missing if item else "no assessment"
                terse += f"- **{draft.filename}** ({draft.days_stale}d) — {reason}\n"
            for draft in self.analyzer.get_empty_drafts():
                terse += f"- **{draft.filename}** — no content\n"

        # ── Dense ──
        dense = f"""---
created: {self.review_date}
type: report
status: final
sources: [{', '.join(self.data_sources_used)}]
---

# Draft Queue Analysis — {self.review_date}

| Category | Count |
|----------|-------|
| Total | {stats['total']} |
| With content | {stats['non_empty']} |
| Empty | {stats['empty']} |
| Finishable | {len(finish_queue)} |
| To drop | {len(drop_list)} |
| Posted | {len(self.analyzer.posted)} |

"""
        for i, (draft, item, score) in enumerate(finish_queue, 1):
            dense += f"### {i}. {draft.filename}\n\n"
            dense += f"| Field | Value |\n|-------|-------|\n"
            dense += f"| Score | {score:.2f} |\n| Age | {draft.days_stale}d |\n| Words | {draft.word_count} |\n"
            if item and hasattr(item, "adjusted_finishability"):
                dense += f"| Finishability (adj) | {item.adjusted_finishability} |\n"
                dense += f"| Momentum | {item.momentum_signal} |\n\n"
                dense += f"**Idea:** {item.idea_summary}\n\n**Missing:** {item.whats_missing}\n\n"
                dense += f"**Rationale:** {item.ranking_rationale}\n\n"
            elif item and hasattr(item, "finishability"):
                dense += f"| Finishability | {item.finishability} |\n\n"
                dense += f"**Idea:** {item.idea_summary}\n\n**Missing:** {item.whats_missing}\n\n"
            dense += f"```\n{draft.preview}\n```\n\n---\n\n"

        # Save
        alerts = output_dir("alerts")
        reports = output_dir("reports")
        alerts.mkdir(parents=True, exist_ok=True)
        reports.mkdir(parents=True, exist_ok=True)
        terse_path = alerts / f"stale-drafts-{self.review_date}.md"
        dense_path = reports / f"stale-drafts-analysis-{self.review_date}.md"
        terse_path.write_text(terse)
        dense_path.write_text(dense)
        print(f"Saved: {terse_path}")
        print(f"Saved: {dense_path}")
        return terse_path, dense_path

    def _generate_empty_report(self):
        alerts = output_dir("alerts")
        alerts.mkdir(parents=True, exist_ok=True)
        path = alerts / f"stale-drafts-{self.review_date}.md"
        path.write_text(f"---\ncreated: {self.review_date}\ntype: alert\n---\n\n# Draft Queue — {self.review_date}\n\nNo drafts with content found.\n")
        print(f"Saved: {path}")

    def _send_notification(self, count, path):
        try:
            subprocess.run([
                "terminal-notifier", "-title", "Claude", "-subtitle", "Draft Queue",
                "-message", f"{count} drafts ready to finish",
                "-sound", "default", "-group", "claude-stale-drafts",
                "-open", f"file://{path}",
            ], check=False)
        except FileNotFoundError:
            pass

    def _log_activity(self, finish_count, drop_count):
        log = activity_log()
        log.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M")
        with open(log, "a") as f:
            f.write(f"\n## {ts}\n\n**Action:** Draft queue review\n"
                    f"**Sources:** {', '.join(self.data_sources_used)}\n"
                    f"**Finishable:** {finish_count}, **Drop:** {drop_count}\n"
                    f"**LLM:** {'Yes' if self.llm_available else 'No'}\n\n---\n")


def main():
    StaleDraftsOrchestrator().run()


if __name__ == "__main__":
    main()
