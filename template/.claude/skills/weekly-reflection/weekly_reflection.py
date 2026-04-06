#!/usr/bin/env python3
"""
Qualitative Weekly Reflection

Generates thoughtful narrative reflections from weekly data using Claude
via pydantic-ai. Complements statistical reviews with qualitative insights.

Usage:
    cd .claude/skills/weekly-reflection
    uv run weekly_reflection.py
    uv run weekly_reflection.py --redact
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import (  # noqa: E402
    load_config,
    vault_root,
    output_dir,
    activity_log,
    owner_name,
)

from text_extractor import TextExtractor, WeeklyText
from llm_synthesizer import ReflectionSynthesizer
from output_formatter import ReflectionFormatter


class WeeklyReflectionOrchestrator:
    def __init__(self, redact: bool = False):
        load_config()
        self.review_date = datetime.now().strftime("%Y-%m-%d")
        self.redact = redact

        # ANTHROPIC_API_KEY should be in env via: uv run --env-file .env
        # Also accepts CLAUDE_API_KEY as alias (avoids conflict with Claude Code's own env)
        api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY (or CLAUDE_API_KEY) not found in .env file. "
                "Set it to enable LLM synthesis."
            )

        self.stats_review_path = self._find_latest_stats_review()
        self.text_extractor = TextExtractor(days_back=7)
        self.synthesizer = ReflectionSynthesizer(api_key=api_key, redact=redact)

    def _find_latest_stats_review(self) -> Path | None:
        """Find most recent statistical weekly review (optional)."""
        reviews_dir = output_dir("root") / "reviews"
        if not reviews_dir.exists():
            return None
        reviews = sorted(reviews_dir.glob("*-weekly-review.md"), reverse=True)
        if reviews:
            print(f"✓ Found statistical review: {reviews[0].name}")
            return reviews[0]
        return None

    def _load_stats(self) -> dict:
        if not self.stats_review_path:
            return {}
        try:
            content = self.stats_review_path.read_text()
            stats = {}
            lines = content.split("\n")
            in_stats = False
            for line in lines:
                if "Stats" in line and line.startswith("#"):
                    in_stats = True
                    continue
                if in_stats:
                    if line.startswith("#") or line.startswith("→"):
                        break
                    if line.startswith("-") and ":" in line:
                        key, value = line.lstrip("- ").split(":", 1)
                        stats[key.strip()] = value.strip()
            return stats
        except Exception:
            return {}

    def run(self) -> Path:
        print("=" * 60)
        print("QUALITATIVE WEEKLY REFLECTION")
        print("=" * 60)

        # Phase 1: Extract text
        print("\n=== Phase 1: Text Extraction ===")
        weekly_text = self.text_extractor.extract_all()

        # Phase 2: Load statistics (optional)
        print("\n=== Phase 2: Loading Statistics ===")
        stats = self._load_stats()
        print(f"✓ Loaded {len(stats)} statistics" if stats else "No statistical review found (proceeding without)")

        # Phase 3: Synthesize with LLM
        print("\n=== Phase 3: LLM Synthesis ===")
        reflection = self.synthesizer.synthesize(weekly_text, stats)
        print(f"✓ {len(reflection.themes)} themes, {len(reflection.tensions)} tensions, "
              f"{len(reflection.reflection_questions)} questions")

        # Phase 4: Format and save
        print("\n=== Phase 4: Output ===")
        formatter = ReflectionFormatter(
            str(self.stats_review_path) if self.stats_review_path else None
        )
        markdown = formatter.format(reflection, stats)
        out = output_dir("reflections")
        output_path = formatter.save(markdown, out)
        print(f"✓ Saved: {output_path}")

        # Phase 5: Notify
        self._notify(output_path, len(reflection.reflection_questions))

        # Phase 6: Log
        self._log(weekly_text, reflection)

        print("\n" + "=" * 60)
        print("REFLECTION COMPLETE")
        print("=" * 60)
        return output_path

    def _notify(self, path: Path, question_count: int):
        try:
            subprocess.run([
                "terminal-notifier",
                "-title", "Claude",
                "-subtitle", "Weekly Reflection Ready",
                "-message", f"{question_count} questions for reflection",
                "-sound", "default",
                "-group", "claude-weekly-reflection",
                "-open", f"file://{path}",
            ], check=False)
            print("✓ Notification sent")
        except FileNotFoundError:
            pass

    def _log(self, text: WeeklyText, reflection):
        log = activity_log()
        log.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M")

        sources_used = []
        if text.journal_entries:
            sources_used.append("journal")
        if any(text.tasks.values()):
            sources_used.append("tasks")
        if any(text.emails.values()):
            sources_used.append("email")
        if text.notes:
            sources_used.append("notes")
        if text.browser_titles:
            sources_used.append("browser")

        entry = f"""## {ts}

**Action:** Qualitative weekly reflection
**Sources used:** {', '.join(sources_used)}
**Outputs:**
- [[output/reflections/{self.review_date}-weekly-reflection]]
**Stats:**
- Journal entries: {len(text.journal_entries)}
- Tasks: {len(text.tasks['created'])} created, {len(text.tasks['completed'])} completed
- Emails: {len(text.emails['sent'])} sent, {len(text.emails['received'])} received
- Notes modified: {len(text.notes)}
- Themes generated: {len(reflection.themes)}
- Reflection questions: {len(reflection.reflection_questions)}

---

"""
        with open(log, "a") as f:
            f.write(entry)
        print(f"✓ Logged to {log}")


def main():
    parser = argparse.ArgumentParser(description="Qualitative Weekly Reflection")
    parser.add_argument("--redact", action="store_true",
                        help="Exclude sensitive topics (configure REDACT_KEYWORDS in llm_synthesizer.py)")
    args = parser.parse_args()
    orchestrator = WeeklyReflectionOrchestrator(redact=args.redact)
    orchestrator.run()


if __name__ == "__main__":
    main()
