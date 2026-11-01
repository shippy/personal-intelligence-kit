"""Reconcile deletions only after a complete Microsoft Graph snapshot."""

import importlib.util
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "todo_import", Path(__file__).with_name("import_tasks.py")
)
todo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(todo)


@pytest.fixture
def account(tmp_path, monkeypatch):
    database = tmp_path / "tasks.db"

    def connect():
        conn = sqlite3.connect(database)
        conn.executescript(todo.SCHEMA)
        return conn

    with connect() as conn:
        conn.executemany(
            "INSERT INTO tasks(id, title, status, provider) VALUES (?, ?, ?, ?)",
            [
                ("ms-todo:old", "Existing task", "open", "microsoft-todo"),
                ("ms-todo:done", "Completed task", "done", "microsoft-todo"),
                ("todoist:other", "Other provider", "open", "todoist"),
            ],
        )
    monkeypatch.setattr(todo, "open_db", connect)
    monkeypatch.setattr(todo, "_get_ms_credential", lambda: object())
    monkeypatch.setattr("time.sleep", lambda _: None)
    return database


def responses(monkeypatch, pages):
    calls = []

    def get(credential, endpoint):
        calls.append(endpoint)
        page = pages[endpoint]
        if isinstance(page, Exception):
            raise page
        return page

    monkeypatch.setattr(todo, "_ms_graph_get", get)
    return calls


def statuses(database):
    with sqlite3.connect(database) as conn:
        return dict(conn.execute("SELECT id, status FROM tasks"))


def test_paginates_lists_and_tasks_before_cancelling(account, monkeypatch):
    lists_next = todo._MS_GRAPH_BASE + "/me/todo/lists?$skip=1"
    tasks_next = todo._MS_GRAPH_BASE + "/me/todo/lists/a/tasks?$skip=1"
    calls = responses(monkeypatch, {
        "/me/todo/lists": {"value": [{"id": "a"}], "@odata.nextLink": lists_next},
        lists_next: {"value": [{"id": "b"}]},
        "/me/todo/lists/a/tasks": {"value": [{"id": "new"}], "@odata.nextLink": tasks_next},
        tasks_next: {"value": [{"id": "old"}]},
        "/me/todo/lists/b/tasks": {"value": [{"id": "done", "status": "completed"}]},
    })
    assert todo.import_microsoft_todo(False) == 3
    assert lists_next in calls and tasks_next in calls
    assert statuses(account) == {
        "ms-todo:old": "open", "ms-todo:new": "open",
        "ms-todo:done": "done", "todoist:other": "open",
    }


@pytest.mark.parametrize("failure", [RuntimeError("offline"), {}, {"value": None}])
def test_incomplete_task_page_preserves_existing_tasks(account, monkeypatch, failure):
    responses(monkeypatch, {
        "/me/todo/lists": {"value": [{"id": "a"}, {"id": "b"}]},
        "/me/todo/lists/a/tasks": {"value": [{"id": "new"}]},
        "/me/todo/lists/b/tasks": {"value": [], "@odata.nextLink": "/later"},
        "/later": failure,
    })
    assert todo.import_microsoft_todo(False) == 1
    assert statuses(account)["ms-todo:old"] == "open"
    assert statuses(account)["ms-todo:new"] == "open"


def test_failed_list_page_leaves_database_unchanged(account, monkeypatch):
    before = statuses(account)
    responses(monkeypatch, {
        "/me/todo/lists": {"value": [{"id": "a"}], "@odata.nextLink": "/later"},
        "/later": RuntimeError("offline"),
    })
    with pytest.raises(RuntimeError, match="offline"):
        todo.import_microsoft_todo(False)
    assert statuses(account) == before


@pytest.mark.parametrize("lists", [[], [{"id": "a"}]])
def test_empty_snapshot_cancels_only_open_microsoft_tasks(account, monkeypatch, lists):
    responses(monkeypatch, {
        "/me/todo/lists": {"value": lists},
        "/me/todo/lists/a/tasks": {"value": []},
    })
    assert todo.import_microsoft_todo(False) == 0
    assert statuses(account) == {
        "ms-todo:old": "cancelled", "ms-todo:done": "done", "todoist:other": "open",
    }


def test_returning_task_reopens_and_missing_task_is_cancelled(account, monkeypatch):
    with sqlite3.connect(account) as conn:
        conn.execute("UPDATE tasks SET status = 'cancelled' WHERE id = 'ms-todo:done'")
    responses(monkeypatch, {
        "/me/todo/lists": {"value": [{"id": "a"}]},
        "/me/todo/lists/a/tasks": {"value": [{"id": "done"}]},
    })
    todo.import_microsoft_todo(False)
    assert statuses(account)["ms-todo:done"] == "open"
    assert statuses(account)["ms-todo:old"] == "cancelled"


def test_repeated_page_is_incomplete(account, monkeypatch):
    responses(monkeypatch, {
        "/me/todo/lists": {"value": [{"id": "a"}]},
        "/me/todo/lists/a/tasks": {"value": [], "@odata.nextLink": "/me/todo/lists/a/tasks"},
    })
    todo.import_microsoft_todo(False)
    assert statuses(account)["ms-todo:old"] == "open"


def test_large_snapshot_does_not_need_one_sql_parameter_per_task(account, monkeypatch):
    responses(monkeypatch, {
        "/me/todo/lists": {"value": [{"id": "a"}]},
        "/me/todo/lists/a/tasks": {"value": [{"id": str(i)} for i in range(2000)]},
    })
    assert todo.import_microsoft_todo(False) == 2000
    assert statuses(account)["ms-todo:old"] == "cancelled"


def test_malformed_task_rolls_back_partial_import(account, monkeypatch):
    before = statuses(account)
    responses(monkeypatch, {
        "/me/todo/lists": {"value": [{"id": "a"}]},
        "/me/todo/lists/a/tasks": {"value": [{"id": "new"}, {}]},
    })
    with pytest.raises(KeyError):
        todo.import_microsoft_todo(False)
    assert statuses(account) == before


def test_absolute_next_link_uses_graph_url(monkeypatch):
    url = todo._MS_GRAPH_BASE + "/me/todo/lists?$skip=1"
    calls = []

    def get(target, **kwargs):
        calls.append(target)
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"value": []})

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(get=get))
    credential = SimpleNamespace(get_token=lambda _: SimpleNamespace(token="fixture"))
    assert todo._ms_graph_get(credential, url) == {"value": []}
    assert calls == [url]


def test_pagination_cannot_send_credentials_to_another_host():
    with pytest.raises(ValueError, match="Graph API base"):
        todo._ms_graph_get(None, "https://example.invalid/page")
