#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "azure-identity>=1.15.0"]
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
    assert_writable,
    load_config,
    source_enabled,
    task_provider,
    tasks_db_path,
    data_dir,
    state_dir,
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
    db_path = assert_writable(tasks_db_path())
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


# ---------- Microsoft To-Do (Graph API) ----------

# Public client ID for Microsoft Graph CLI tools — no app registration needed
_MS_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
_MS_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_MS_SCOPE = "Tasks.Read"


_MS_AUTH_RECORD = "ms_auth_record.json"


def _ms_auth_record_path() -> Path:
    """Path to the persisted Azure authentication record (host-only state)."""
    return assert_writable(state_dir() / _MS_AUTH_RECORD)  # the file may be a symlink


def _load_ms_auth_record_text() -> str | None:
    """Read the auth record, migrating it once from data/ (pre-v0.8.0 location).

    The old file is left in place; remove it by hand once the new one works.
    """
    record_path = _ms_auth_record_path()
    if record_path.exists():
        return record_path.read_text()
    legacy = data_dir() / _MS_AUTH_RECORD
    if legacy != record_path and legacy.exists():
        text = legacy.read_text()
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(text)
        print(f"Copied auth record {legacy} → {record_path}")
        return text
    return None


def _get_ms_credential():
    """Build a DeviceCodeCredential with persistent token cache.

    First run triggers interactive device-code flow (browser sign-in).
    Subsequent runs re-use the cached token silently.
    """
    from azure.identity import (
        AuthenticationRecord,
        DeviceCodeCredential,
        TokenCachePersistenceOptions,
    )

    cache_options = TokenCachePersistenceOptions(name="pik-todo")

    auth_record = None
    record_path = _ms_auth_record_path()
    record_text = _load_ms_auth_record_text()
    if record_text is not None:
        try:
            auth_record = AuthenticationRecord.deserialize(record_text)
        except Exception as exc:
            print(f"Warning: could not load auth record: {exc}")

    def _prompt(uri, code, expires_in):
        print(f"\n  Visit {uri} and enter code: {code}  (expires in {expires_in}s)\n")

    credential = DeviceCodeCredential(
        client_id=_MS_CLIENT_ID,
        tenant_id="common",
        prompt_callback=_prompt,
        cache_persistence_options=cache_options,
        authentication_record=auth_record,
    )

    if auth_record is None:
        print("First-time authentication — follow the prompt above...")
        new_record = credential.authenticate(scopes=[_MS_SCOPE])
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(new_record.serialize())
        print(f"Auth record saved to {record_path}")

    return credential


def _ms_graph_get(credential, endpoint: str, *, retries: int = 3) -> dict:
    """Make an authenticated GET to the Graph API with retry on 429/503."""
    if endpoint.startswith(_MS_GRAPH_BASE + "/"):
        url = endpoint
    elif endpoint.startswith("/"):
        url = f"{_MS_GRAPH_BASE}{endpoint}"
    else:
        raise ValueError("Graph pagination URL must use the configured Graph API base")

    import httpx
    import time

    token = credential.get_token(_MS_SCOPE)
    headers = {"Authorization": f"Bearer {token.token}"}

    delay = 1.0
    for attempt in range(retries):
        resp = httpx.get(url, headers=headers, timeout=30.0)
        if resp.status_code == 429 or resp.status_code == 503:
            if attempt < retries - 1:
                wait = delay
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait = max(wait, int(retry_after))
                print(f"  Rate limited ({resp.status_code}), retrying in {wait}s...")
                time.sleep(wait)
                delay *= 2
                # Refresh token in case it expired during wait
                token = credential.get_token(_MS_SCOPE)
                headers = {"Authorization": f"Bearer {token.token}"}
                continue
            resp.raise_for_status()
        resp.raise_for_status()
        return resp.json()

    raise RuntimeError("Graph API request failed after retries")


def _ms_graph_collection(credential, endpoint: str) -> list[dict]:
    """Fetch every page, rejecting incomplete responses before reconciliation."""
    items = []
    visited = set()
    while endpoint:
        if endpoint in visited:
            raise ValueError("Graph pagination repeated a page")
        visited.add(endpoint)
        page = _ms_graph_get(credential, endpoint)
        if not isinstance(page.get("value"), list):
            raise ValueError("Graph collection response is missing its value list")
        items.extend(page["value"])
        endpoint = page.get("@odata.nextLink")
        if endpoint is not None and (not isinstance(endpoint, str) or not endpoint):
            raise ValueError("Graph collection response has an invalid nextLink")
    return items


def import_microsoft_todo(full: bool) -> int:
    """Import tasks from Microsoft To-Do via the Graph API.

    Uses device-code flow authentication (no app registration required).
    Run `uv run import_tasks.py --test-auth` to verify auth before first sync.
    """
    import time

    credential = _get_ms_credential()

    # Fetch all task lists
    task_lists = _ms_graph_collection(credential, "/me/todo/lists")
    print(f"  Found {len(task_lists)} task lists")

    conn = open_db()
    imported = 0
    seen_ids: set[str] = set()
    complete = True

    try:
        with conn:
            for tl in task_lists:
                list_id = tl["id"]
                list_name = tl.get("displayName", "")
                time.sleep(0.1)  # Respect rate limits

                try:
                    tasks = _ms_graph_collection(credential, f"/me/todo/lists/{list_id}/tasks")
                except Exception as exc:
                    print(f"  Warning: failed to fetch list '{list_name}': {exc}")
                    complete = False
                    continue

                for t in tasks:
                    task_id = f"ms-todo:{t['id']}"
                    seen_ids.add(task_id)
                    status = "done" if t.get("status") == "completed" else "open"
                    upsert(conn, {
                        "id": task_id,
                        "title": t.get("title", ""),
                        "body": (t.get("body") or {}).get("content", ""),
                        "status": status,
                        "list_name": list_name,
                        "created_at": t.get("createdDateTime"),
                        "completed_at": (t.get("completedDateTime") or {}).get("dateTime"),
                        "due_date": (t.get("dueDateTime") or {}).get("dateTime"),
                        "provider": "microsoft-todo",
                        "raw_json": json.dumps(t),
                    })
                    imported += 1

            if complete:
                # Only a complete account snapshot proves absence. Per-row updates
                # avoid SQLite's bind-variable limit for large task collections.
                missing = [
                    (task_id,) for (task_id,) in conn.execute(
                        "SELECT id FROM tasks WHERE provider = 'microsoft-todo' AND status = 'open'"
                    ) if task_id not in seen_ids
                ]
                conn.executemany("UPDATE tasks SET status = 'cancelled' WHERE id = ?", missing)
            else:
                print("  Skipping deletion reconciliation: task snapshot is incomplete")
    finally:
        conn.close()
    return imported


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


def test_ms_auth() -> int:
    """Test Microsoft Graph authentication and list available task lists."""
    try:
        credential = _get_ms_credential()
        resp = _ms_graph_get(credential, "/me/todo/lists")
        lists = resp.get("value", [])
        print(f"Authentication successful — {len(lists)} task lists:")
        for tl in lists:
            print(f"  - {tl.get('displayName', '(unnamed)')}")
        return 0
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        print("\nTroubleshooting:")
        print("  1. Run: az login")
        print("  2. Or delete auth record and re-authenticate:")
        print(f"     rm {_ms_auth_record_path()}")
        return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--test-auth", action="store_true",
                        help="Test Microsoft Graph authentication")
    args = parser.parse_args()

    load_config()

    if args.test_auth:
        return test_ms_auth()

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
