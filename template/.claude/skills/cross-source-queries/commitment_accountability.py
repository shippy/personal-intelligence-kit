#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Commitment ↔ Accountability Analysis

Scans sent email for commitment phrases, checks for follow-through, and
optionally cross-references with notes vault.

Usage:
    uv run commitment_accountability.py
"""

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import (  # noqa: E402
    load_config,
    source_enabled,
    notes_vault_path,
    output_dir,
    activity_log,
    owner_name,
)
import okf  # noqa: E402


COMMITMENT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"i'?ll\s+(?:send|share|write|draft|prepare|review|get back|follow up)",
        r"i\s+will\s+(?:send|share|write|draft|prepare|review|get back|follow up)",
        r"let me\s+(?:send|share|write|draft|prepare)",
        r"will have\s+(?:this|it|that)\s+(?:by|before)",
        r"by\s+(?:monday|tuesday|wednesday|thursday|friday|tomorrow|end of (?:week|day))",
    ]
]


def _notmuch(args: list[str]) -> str:
    try:
        r = subprocess.run(["notmuch", *args], capture_output=True, text=True, check=False, timeout=30)
        return r.stdout if r.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


class CommitmentAccountabilityAnalyzer:
    def __init__(self):
        load_config()
        self._nvault = notes_vault_path()

    def extract_email_commitments(self, days_back: int = 30) -> List[Dict]:
        """
        Extract commitments from sent emails using regex patterns.

        EXTENSION POINT: Replace _extract_commitment() with an LLM call
        for more nuanced extraction (e.g., Claude Haiku → structured JSON).
        """
        if not source_enabled("email"):
            return []

        # Get sent message IDs
        output = _notmuch(["search", "--output=messages", "--limit=200",
                           f"tag:sent date:{days_back}d.."])
        msg_ids = [line.strip() for line in output.splitlines() if line.strip()]
        commitments = []

        for mid in msg_ids:
            body = _notmuch(["show", "--format=text", "--body=true", mid])
            if not body:
                continue
            hdrs = _notmuch(["show", "--format=text", "--body=false", mid])
            subject, to_addr, date_str = "(no subject)", "", ""
            for line in hdrs.splitlines():
                if line.startswith("Subject:"):
                    subject = line.split(":", 1)[1].strip()
                elif line.startswith("To:"):
                    to_addr = line.split(":", 1)[1].strip()
                elif line.startswith("Date:"):
                    date_str = line.split(":", 1)[1].strip()

            commitment = self._extract_commitment(body)
            if commitment:
                commitment.update({
                    "email_subject": subject,
                    "recipient": to_addr,
                    "email_date": date_str,
                    "message_id": mid,
                    "follow_up": self._check_follow_up(to_addr, date_str),
                })
                commitments.append(commitment)

        return commitments

    def _extract_commitment(self, text: str) -> Optional[Dict]:
        """
        Regex-based commitment extraction.

        EXTENSION POINT: Use an LLM for higher precision. The LLM version
        would parse the email and return structured JSON with commitment_text,
        deadline, and action_required.
        """
        for pattern in COMMITMENT_PATTERNS:
            m = pattern.search(text)
            if m:
                start = max(0, m.start() - 50)
                end = min(len(text), m.end() + 100)
                return {
                    "commitment_text": text[start:end].strip()[:200],
                    "deadline": None,
                    "action_required": "See email for details",
                }
        return None

    def _check_follow_up(self, recipient: str, date_str: str) -> Dict:
        if not recipient or not date_str:
            return {"followed_up": False, "days_since": None}
        # Simple check: any email to same recipient after the commitment
        try:
            q = f'to:"{recipient}" tag:sent date:{date_str[:10]}..'
            output = _notmuch(["search", "--format=json", "--limit=5", q])
            results = json.loads(output) if output.strip() else []
            return {"followed_up": len(results) > 1, "days_since": None}
        except Exception:
            return {"followed_up": False, "days_since": None}

    def analyze(self, days_back: int = 30) -> Dict:
        commitments = self.extract_email_commitments(days_back)
        followed = sum(1 for c in commitments if c.get("follow_up", {}).get("followed_up"))
        return {
            "scan_period_days": days_back,
            "total": len(commitments),
            "followed_up": followed,
            "no_follow_up": len(commitments) - followed,
            "details": commitments,
        }


def generate_report() -> Optional[Path]:
    if not source_enabled("email"):
        print("Email source not enabled. Skipping commitment analysis.")
        return None

    analyzer = CommitmentAccountabilityAnalyzer()
    date = datetime.now().strftime("%Y-%m-%d")
    report = analyzer.analyze(days_back=30)

    lines = [
        f"# Commitment Accountability — {date}\n",
        f"Found **{report['total']}** commitments in sent mail (last 30 days).",
        f"Followed up: {report['followed_up']}, No follow-up: {report['no_follow_up']}\n",
        "_Note: Regex-based extraction. See EXTENSION POINT comments in the code for LLM upgrade path._\n",
        "## Commitments\n",
    ]
    for c in report["details"]:
        followed = "✓ followed up" if c.get("follow_up", {}).get("followed_up") else "⚠ no follow-up"
        lines.append(f"### {c['email_subject']}")
        lines.append(f"- **To:** {c['recipient']}")
        lines.append(f"- **Date:** {c['email_date']}")
        lines.append(f"- **Status:** {followed}")
        lines.append(f"- **Excerpt:** _{c['commitment_text']}_\n")

    out = okf.write_concept(
        "alerts", f"commitment-accountability-{date}", type="alert",
        title=f"Commitment Accountability — {date}",
        description=f"{report['total']} commitments tracked in sent mail",
        body="\n".join(lines), timestamp=date, status="final",
        sources=["email"], tags=["commitment"],
    )
    print(f"Wrote {out}")

    log = activity_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(f"\n## {datetime.now().strftime('%Y-%m-%dT%H:%M')}\n\n")
        f.write(f"**Action:** Commitment accountability scan\n")
        f.write(f"**Commitments:** {report['total']}\n\n---\n")
    return out


if __name__ == "__main__":
    generate_report()
