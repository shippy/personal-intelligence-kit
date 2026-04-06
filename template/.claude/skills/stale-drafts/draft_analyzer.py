"""
Draft Analyzer

Scans the drafts folder, classifies by content presence, parses frontmatter,
and cross-references with Posted/ directory.
"""

import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import drafts_path  # noqa: E402


@dataclass
class Draft:
    path: Path
    filename: str
    modified_date: datetime
    created_date: datetime
    days_stale: int  # Days since CREATED
    days_since_modified: int
    content: str
    word_count: int
    created: Optional[str] = None
    status: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    is_linkedin: bool = False
    preview: str = ""
    created_from_frontmatter: bool = False

    @property
    def is_empty(self) -> bool:
        return self.word_count == 0

    @property
    def recently_touched(self) -> bool:
        return self.days_since_modified < 7


@dataclass
class PostedDraft:
    path: Path
    filename: str
    content: str
    preview: str


class DraftAnalyzer:
    def __init__(self, drafts_dir: Optional[Path] = None):
        self.drafts_dir = drafts_dir or drafts_path()
        if not self.drafts_dir:
            raise ValueError("No drafts directory configured (set notes.drafts_path in vault.toml)")
        self.posted_dir = self.drafts_dir / "Posted"
        self.drafts: list[Draft] = []
        self.posted: list[PostedDraft] = []
        self.stats = {"total": 0, "empty": 0, "non_empty": 0}

    def scan(self) -> list[Draft]:
        if not self.drafts_dir.exists():
            print(f"Drafts directory not found: {self.drafts_dir}")
            return []
        md_files = [f for f in self.drafts_dir.glob("*.md") if f.is_file() and f.name != "Drafts.md"]
        now = datetime.now()
        drafts = []
        for md in md_files:
            mtime = datetime.fromtimestamp(md.stat().st_mtime)
            days_since_modified = (now - mtime).days
            content = md.read_text(encoding="utf-8", errors="ignore")
            created_str, status, tags = self._parse_frontmatter(content)
            created_date = mtime
            created_from_frontmatter = False
            if created_str:
                parsed = self._parse_date(created_str)
                if parsed:
                    created_date = parsed
                    created_from_frontmatter = True
            days_stale = (now - created_date).days
            body = self._strip_frontmatter(content)
            word_count = len(body.split())
            preview = body[:150].replace("\n", " ").strip()
            if len(body) > 150:
                preview += "..."
            drafts.append(Draft(
                path=md, filename=md.name, modified_date=mtime, created_date=created_date,
                days_stale=days_stale, days_since_modified=days_since_modified,
                content=content, word_count=word_count, created=created_str,
                status=status, tags=tags, is_linkedin=md.name.startswith("LI -"),
                preview=preview, created_from_frontmatter=created_from_frontmatter,
            ))
        drafts.sort(key=lambda d: d.days_stale, reverse=True)
        self.drafts = drafts
        self.stats = {
            "total": len(drafts),
            "empty": sum(1 for d in drafts if d.is_empty),
            "non_empty": sum(1 for d in drafts if not d.is_empty),
        }
        return drafts

    def load_posted(self) -> list[PostedDraft]:
        if not self.posted_dir.exists():
            return []
        posted = []
        for md in self.posted_dir.glob("*.md"):
            content = md.read_text(encoding="utf-8", errors="ignore")
            body = self._strip_frontmatter(content)
            preview = body[:150].replace("\n", " ").strip()
            if len(body) > 150:
                preview += "..."
            posted.append(PostedDraft(path=md, filename=md.name, content=content, preview=preview))
        self.posted = posted
        return posted

    def get_empty_drafts(self) -> list[Draft]:
        return [d for d in self.drafts if d.is_empty]

    def get_non_empty_drafts(self) -> list[Draft]:
        return [d for d in self.drafts if not d.is_empty]

    def _parse_frontmatter(self, content: str):
        created, status, tags = None, None, []
        if not content.startswith("---"):
            return created, status, tags
        parts = content.split("---", 2)
        if len(parts) < 3:
            return created, status, tags
        fm = parts[1]
        m = re.search(r"created:\s*(.+)", fm)
        if m:
            created = m.group(1).strip()
        m = re.search(r"status:\s*(.+)", fm)
        if m:
            status = m.group(1).strip()
        m = re.search(r"tags:\s*\n((?:\s+-\s*.+\n)+)", fm)
        if m:
            tags = [t.strip().lstrip('- "\'').rstrip('"\'') for t in m.group(1).strip().split("\n")]
        else:
            m = re.search(r"tags:\s*\[(.+)\]", fm)
            if m:
                tags = [t.strip().strip("\"'") for t in m.group(1).split(",")]
        return created, status, tags

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d"]:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except ValueError:
                continue
        return None

    def _strip_frontmatter(self, content: str) -> str:
        if not content.startswith("---"):
            return content.strip()
        parts = content.split("---", 2)
        return parts[2].strip() if len(parts) >= 3 else content.strip()
