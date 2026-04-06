"""
Text Extractor for Weekly Reflection

Queries databases and extracts text from all enabled sources:
- Journal entries (journal.db — see SCHEMAS.md)
- Tasks (tasks.db)
- Email archive (notmuch CLI)
- Notes vault (filesystem)
- Browser history (browser-history.db)

Each extractor checks vault.toml before accessing a source and returns
empty results if the source is disabled or unavailable.
"""

import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import (
    load_config,
    source_enabled,
    notes_vault_path,
    journal_db_path,
    tasks_db_path,
    browser_db_path,
    owner_name,
)


@dataclass
class JournalEntry:
    date: str
    speaker: str
    body: str
    entry_order: int = 0


@dataclass
class Task:
    title: str
    body: Optional[str]
    status: str
    created: Optional[str]
    completed: Optional[str]
    due: Optional[str]
    list_name: str


@dataclass
class Email:
    subject: str
    sender: str
    recipient: str
    date: str
    snippet: str
    thread_id: str


@dataclass
class VaultNote:
    filename: str
    path: str
    section: str  # Top-level folder name
    modified: str
    preview: str


@dataclass
class WeeklyText:
    journal_entries: List[JournalEntry]
    tasks: Dict[str, List[Task]]  # created, completed, overdue
    emails: Dict[str, List[Email]]  # sent, received
    notes: List[VaultNote]
    browser_titles: Dict[str, List[str]]  # domain -> page titles


class TextExtractor:
    """Extract text from all data sources for weekly reflection."""

    def __init__(self, days_back: int = 7):
        self.days_back = days_back
        self.start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        self.end_date = datetime.now().strftime("%Y-%m-%d")

    def extract_all(self) -> WeeklyText:
        print(f"\n=== Extracting Text ({self.start_date} to {self.end_date}) ===\n")
        return WeeklyText(
            journal_entries=self.extract_journal(),
            tasks=self.extract_tasks(),
            emails=self.extract_emails(),
            notes=self.extract_notes(),
            browser_titles=self.extract_browser(),
        )

    def extract_journal(self) -> List[JournalEntry]:
        """Extract journal entries from data/journal.db."""
        print("Extracting journal entries...")
        db = journal_db_path()
        if not db or not db.exists():
            print("  ⚠ Journal database not found")
            return []

        conn = sqlite3.connect(db)
        cursor = conn.execute(
            "SELECT date, COALESCE(speaker, 'author'), body "
            "FROM entries WHERE date >= ? ORDER BY date DESC, id ASC",
            (self.start_date,),
        )
        entries = [
            JournalEntry(date=row[0], speaker=row[1], body=row[2], entry_order=i)
            for i, row in enumerate(cursor.fetchall())
        ]
        conn.close()
        print(f"  ✓ Extracted {len(entries)} journal entries")
        return entries

    def extract_tasks(self) -> Dict[str, List[Task]]:
        """Extract tasks from data/tasks.db."""
        print("Extracting tasks...")
        db = tasks_db_path()
        if not db or not db.exists():
            print("  ⚠ Tasks database not found")
            return {"created": [], "completed": [], "overdue": []}

        conn = sqlite3.connect(db)

        def _query(sql: str, params: tuple = ()) -> List[Task]:
            rows = conn.execute(sql, params).fetchall()
            return [
                Task(title=r[0], body=r[1], status=r[2], created=r[3],
                     completed=r[4], due=r[5], list_name=r[6] or "")
                for r in rows
            ]

        cols = "title, body, status, created_at, completed_at, due_date, list_name"
        created = _query(
            f"SELECT {cols} FROM tasks WHERE DATE(created_at) >= ? ORDER BY created_at DESC",
            (self.start_date,),
        )
        completed = _query(
            f"SELECT {cols} FROM tasks WHERE DATE(completed_at) >= ? ORDER BY completed_at DESC",
            (self.start_date,),
        )
        overdue = _query(
            f"SELECT {cols} FROM tasks WHERE status = 'open' "
            "AND due_date IS NOT NULL AND DATE(due_date) < DATE('now') ORDER BY due_date ASC",
        )
        conn.close()

        print(f"  ✓ {len(created)} created, {len(completed)} completed, {len(overdue)} overdue")
        return {"created": created, "completed": completed, "overdue": overdue}

    def extract_emails(self) -> Dict[str, List[Email]]:
        """Extract emails from notmuch."""
        print("Extracting emails...")
        if not source_enabled("email"):
            return {"sent": [], "received": []}

        try:
            result = subprocess.run(
                ["notmuch", "search", "--format=json", "--output=messages",
                 f"date:{self.days_back}days.."],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                print(f"  ⚠ notmuch search failed: {result.stderr}")
                return {"sent": [], "received": []}

            message_ids = json.loads(result.stdout)
            sent, received = [], []
            name_lower = owner_name().lower()

            for msg_id in message_ids[:50]:
                r = subprocess.run(
                    ["notmuch", "show", "--format=json", f"id:{msg_id}"],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode != 0:
                    continue
                data = json.loads(r.stdout)
                for thread in data:
                    for message in thread:
                        if isinstance(message, list):
                            for msg in message:
                                if isinstance(msg, dict) and "headers" in msg:
                                    headers = msg["headers"]
                                    body = msg.get("body", [{}])
                                    snippet = ""
                                    if body and isinstance(body, list) and body[0]:
                                        snippet = str(body[0].get("content", ""))[:300]
                                    email = Email(
                                        subject=headers.get("Subject", "(no subject)"),
                                        sender=headers.get("From", ""),
                                        recipient=headers.get("To", ""),
                                        date=headers.get("Date", ""),
                                        snippet=snippet,
                                        thread_id=msg_id,
                                    )
                                    if name_lower in headers.get("From", "").lower():
                                        sent.append(email)
                                    else:
                                        received.append(email)

            print(f"  ✓ {len(sent)} sent, {len(received)} received")
            return {"sent": sent, "received": received}
        except subprocess.TimeoutExpired:
            print("  ⚠ Email extraction timed out")
            return {"sent": [], "received": []}
        except Exception as e:
            print(f"  ⚠ Email extraction failed: {e}")
            return {"sent": [], "received": []}

    def extract_notes(self) -> List[VaultNote]:
        """Extract recently modified notes from the notes vault."""
        print("Extracting notes vault...")
        nvault = notes_vault_path()
        if not nvault or not nvault.exists():
            print("  ⚠ Notes vault not found")
            return []

        notes = []
        cutoff = datetime.now() - timedelta(days=self.days_back)
        cutoff_ts = cutoff.timestamp()

        for md in nvault.rglob("*.md"):
            # Skip hidden directories
            if any(p.startswith(".") for p in md.relative_to(nvault).parts):
                continue
            try:
                if md.stat().st_mtime <= cutoff_ts:
                    continue
                relative = md.relative_to(nvault)
                parts = relative.parts
                section = parts[0] if parts else "root"
                content = md.read_text(errors="ignore")
                preview = content[:500]
                mtime = datetime.fromtimestamp(md.stat().st_mtime)
                notes.append(VaultNote(
                    filename=md.name,
                    path=str(relative),
                    section=section,
                    modified=mtime.strftime("%Y-%m-%d %H:%M"),
                    preview=preview,
                ))
            except Exception:
                continue

        notes.sort(key=lambda n: n.modified, reverse=True)
        print(f"  ✓ {len(notes)} modified notes")
        return notes

    def extract_browser(self) -> Dict[str, List[str]]:
        """Extract browser page titles grouped by domain from browser-history.db."""
        print("Extracting browser activity...")
        db = browser_db_path()
        if not db or not db.exists():
            print("  ⚠ Browser history database not found")
            return {}

        try:
            conn = sqlite3.connect(db)
            cursor = conn.execute(
                "SELECT domain, title FROM visits "
                "WHERE last_visit_time >= ? AND title IS NOT NULL "
                "ORDER BY visit_count DESC",
                (self.start_date,),
            )
            by_domain: Dict[str, List[str]] = {}
            for domain, title in cursor:
                if domain not in by_domain:
                    by_domain[domain] = []
                if len(by_domain[domain]) < 10:  # cap per domain
                    by_domain[domain].append(title)
            conn.close()

            total = sum(len(v) for v in by_domain.values())
            print(f"  ✓ {total} titles across {len(by_domain)} domains")
            return by_domain
        except Exception as e:
            print(f"  ⚠ Browser extraction failed: {e}")
            return {}


if __name__ == "__main__":
    load_config()
    extractor = TextExtractor(days_back=7)
    text = extractor.extract_all()
    print(f"\nJournal: {len(text.journal_entries)}")
    print(f"Tasks created: {len(text.tasks['created'])}")
    print(f"Tasks completed: {len(text.tasks['completed'])}")
    print(f"Emails sent: {len(text.emails['sent'])}")
    print(f"Emails received: {len(text.emails['received'])}")
    print(f"Notes modified: {len(text.notes)}")
    print(f"Browser domains: {len(text.browser_titles)}")
