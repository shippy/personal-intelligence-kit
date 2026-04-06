#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx"]
# ///
"""
Tasks Import

Dispatches to the configured provider and writes normalized tasks to
data/tasks.db. Provider is read from vault.toml → sources.tasks.provider.

Usage:
    uv run import_tasks.py
    uv run import_tasks.py --full
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from vault_config import (  # noqa: E402
    load_config,
    source_enabled,
    task_provider,
    vault_root,
    activity_log,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT,
    status TEXT CHECK(status IN ('open', 'done', 'cancelled')),
    list_name TEXT,
    created_at TIMESTAMP,
    completed_at TIMESTAMP,
    due_date TIMESTAMP,
    provider TEXT NOT NULL,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_completed ON tasks(completed_at);
"""


def open_db() -> sqlite3.Connection:
    db_path = vault_root() / "data" / "tasks.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def upsert(conn: sqlite3.Connection, task: dict):
    conn.execute(
        """
        INSERT INTO tasks (id, title, body, status, list_name,
                           created_at, completed_at, due_date, provider, raw_json)
        VALUES (:id, :title, :body, :status, :list_name,
                :created_at, :completed_at, :due_date, :provider, :raw_json)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title, body=excluded.body, status=excluded.status,
            list_name=excluded.list_name, completed_at=excluded.completed_at,
            due_date=excluded.due_date, raw_json=excluded.raw_json
        """,
        task,
    )


# ---------- Todoist ----------

def import_todoist(full: bool) -> int:
    import httpx
    token = os.environ.get("TODOIST_API_TOKEN")
    if not token:
        print("TODOIST_API_TOKEN not set in .env")
        return 0

    conn = open_db()
    imported = 0
    with httpx.Client(
        base_url="https://api.todoist.com/rest/v2",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    ) as client:
        # Active tasks
        resp = client.get("/tasks")
        resp.raise_for_status()
        for t in resp.json():
            upsert(conn, {
                "id": f"todoist:{t['id']}",
                "title": t.get("content", ""),
                "body": t.get("description", ""),
                "status": "open",
                "list_name": str(t.get("project_id", "")),
                "created_at": t.get("created_at"),
                "completed_at": None,
                "due_date": (t.get("due") or {}).get("date"),
                "provider": "todoist",
                "raw_json": json.dumps(t),
            })
            imported += 1
        # Completed (last 30 days) — separate endpoint
        try:
            resp = client.get(
                "https://api.todoist.com/sync/v9/completed/get_all",
                params={"limit": 200},
            )
            if resp.status_code == 200:
                for t in resp.json().get("items", []):
                    upsert(conn, {
                        "id": f"todoist:{t['task_id']}",
                        "title": t.get("content", ""),
                        "body": "",
                        "status": "done",
                        "list_name": str(t.get("project_id", "")),
                        "created_at": None,
                        "completed_at": t.get("completed_at"),
                        "due_date": None,
                        "provider": "todoist",
                        "raw_json": json.dumps(t),
                    })
                    imported += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    return imported


# ---------- Microsoft To-Do ----------

def import_microsoft_todo(full: bool) -> int:
    """Stub: requires az CLI auth or app registration.

    For a production setup, install azure-identity + msgraph-core and implement:
        credential = AzureCliCredential()
        token = credential.get_token("https://graph.microsoft.com/.default")
        # fetch /me/todo/lists, then /me/todo/lists/{id}/tasks
    """
    print("Microsoft To-Do import not implemented in this template.")
    print("See SKILL.md for setup instructions.")
    print("The easiest path: `az login`, then extend this script to call the Graph API.")
    return 0


# ---------- Things 3 ----------

def import_things(full: bool) -> int:
    import glob
    candidates = glob.glob(
        str(Path.home() / "Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac"
            "/ThingsData-*/Things Database.thingsdatabase/main.sqlite")
    )
    if not candidates:
        print("Things 3 database not found. Is Things 3 installed?")
        return 0

    src = candidates[0]
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    src_conn.row_factory = sqlite3.Row

    conn = open_db()
    imported = 0
    # Things stores tasks in TMTask; status=0 open, 3 done, 2 cancelled
    for row in src_conn.execute(
        "SELECT uuid, title, notes, status, creationDate, stopDate, dueDate "
        "FROM TMTask WHERE trashed=0"
    ):
        status_map = {0: "open", 2: "cancelled", 3: "done"}
        upsert(conn, {
            "id": f"things:{row['uuid']}",
            "title": row["title"] or "",
            "body": row["notes"] or "",
            "status": status_map.get(row["status"], "open"),
            "list_name": None,
            "created_at": row["creationDate"],
            "completed_at": row["stopDate"],
            "due_date": row["dueDate"],
            "provider": "things",
            "raw_json": "",
        })
        imported += 1
    conn.commit()
    conn.close()
    src_conn.close()
    return imported


PROVIDERS = {
    "todoist": import_todoist,
    "microsoft-todo": import_microsoft_todo,
    "things": import_things,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()

    load_config()
    if not source_enabled("tasks"):
        print("Tasks source not enabled.")
        return 1

    provider = task_provider()
    if provider not in PROVIDERS:
        print(f"Unknown or unsupported task provider: {provider}")
        return 1

    print(f"Importing from {provider}...")
    count = PROVIDERS[provider](args.full)
    print(f"Imported/updated {count} tasks")

    log = activity_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(f"\n## {datetime.now().strftime('%Y-%m-%dT%H:%M')}\n\n")
        f.write(f"**Action:** Tasks import ({provider})\n")
        f.write(f"**Imported/updated:** {count}\n")
        f.write(f"**Output:** `data/tasks.db`\n\n---\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
