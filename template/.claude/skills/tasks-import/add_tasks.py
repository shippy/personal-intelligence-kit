#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "azure-identity>=1.15.0"]
# ///
"""
Add tasks to Microsoft To Do (create only).

Creates new tasks in an existing list. It cannot update, complete or delete a
task, and never creates, renames or deletes a list. Preview is the default;
nothing is written without --yes.

Usage:
    uv run add_tasks.py --list Social "Text Viktor" "Suggest a coffee time"
    uv run add_tasks.py --list Social --file tasks.txt        # one title per line
    uv run add_tasks.py --list Social --file tasks.txt --yes  # actually create them
    uv run add_tasks.py --test-auth                           # one-time interactive sign-in
    uv run add_tasks.py --lists                               # print list names (read-only)
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_tasks as todo  # noqa: E402  (also puts _lib on sys.path)
from vault_config import activity_log, load_config, safe_write  # noqa: E402

WRITE_SCOPE = "Tasks.ReadWrite"
WRITE_CACHE = "pik-todo-write"
WRITE_RECORD = "ms_auth_record_write.json"
MAX_TITLE = 255
MAX_BATCH = 50
AUTH_HINT = "run `uv run add_tasks.py --test-auth` first"
OPEN_FILTER = "$filter=" + quote("status ne 'completed'", safe="'")


class UsageError(Exception):
    pass


class NotSignedIn(Exception):
    pass


def write_credential(interactive: bool = False):
    return todo._get_ms_credential(
        scope=WRITE_SCOPE, cache_name=WRITE_CACHE, record_name=WRITE_RECORD,
        interactive=interactive,
    )


def fetch_lists(credential) -> list[dict]:
    return todo._ms_graph_collection(credential, "/me/todo/lists", scope=WRITE_SCOPE)


def connect():
    """Non-interactive sign-in and list fetch. Never prompts."""
    try:
        credential = write_credential()
        return credential, fetch_lists(credential)
    except todo.MsAuthRequired:
        raise NotSignedIn(f"No write sign-in found: {AUTH_HINT}.") from None
    except Exception as exc:
        if type(exc).__name__ == "AuthenticationRequiredError":  # azure.identity, session expired
            raise NotSignedIn(f"The write sign-in needs renewing: {AUTH_HINT}.") from None
        raise


# Bidi marks, embeddings, overrides and isolates: they reorder how text is displayed.
_BIDI_CONTROLS = set("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def _unsafe_char(c: str) -> bool:
    """C0/C1 controls and DEL (category Cc), or a bidi control."""
    return unicodedata.category(c) == "Cc" or c in _BIDI_CONTROLS


def display(text: str) -> str:
    """Text safe to print or log: unsafe characters become U+FFFD."""
    return "".join("\ufffd" if _unsafe_char(c) else c for c in text)


def list_names(lists: list[dict]) -> list[str]:
    """Display-safe list names (Graph data is untrusted)."""
    return [display(tl.get("displayName", "")) for tl in lists]


def match_list(lists: list[dict], name: str) -> dict:
    """The one list whose display name equals `name`, ignoring case."""
    wanted = name.strip().casefold()
    hits = [tl for tl in lists if tl.get("displayName", "").strip().casefold() == wanted]
    if len(hits) == 1:
        return hits[0]
    problem = "No list matches" if not hits else "More than one list matches"
    names = "\n".join(f"  - {n}" for n in list_names(lists))
    raise UsageError(f"{problem} {name!r}. Available lists:\n{names}")


def _key(title: str) -> str:
    return title.strip().casefold()


def validate_titles(titles: list[str]) -> list[str]:
    cleaned = [t.strip() for t in titles]
    if not cleaned:
        raise UsageError("No task titles given")
    if len(cleaned) > MAX_BATCH:
        raise UsageError(f"Too many tasks: {len(cleaned)} (max {MAX_BATCH} per run)")
    for t in cleaned:
        if not t:
            raise UsageError("Task titles must not be empty")
        if len(t) > MAX_TITLE:
            raise UsageError(f"Title longer than {MAX_TITLE} characters: {t[:40]!r}...")
        if any(_unsafe_char(c) for c in t):
            raise UsageError(f"Title contains a control or bidi character: {display(t[:40])!r}")
    return cleaned


def open_task_titles(credential, list_id: str) -> set[str]:
    endpoint = f"/me/todo/lists/{quote(list_id, safe='')}/tasks?{OPEN_FILTER}"
    tasks = todo._ms_graph_collection(credential, endpoint, scope=WRITE_SCOPE)
    return {_key(t.get("title", "")) for t in tasks}


def plan(titles: list[str], existing: set[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Split titles into (to create, [(skipped title, reason)])."""
    seen = set()
    create, skipped = [], []
    for t in titles:
        k = _key(t)
        if k in existing:
            skipped.append((t, "already an open task in this list"))
        elif k in seen:
            skipped.append((t, "duplicate within this batch"))
        else:
            seen.add(k)
            create.append(t)
    return create, skipped


def provenance(note: str | None, today: date | None = None) -> str:
    text = f"Added by Claude (pki vault), {(today or date.today()).isoformat()}"
    return f"{text}\n{note}" if note else text


def create_tasks(credential, list_id: str, titles: list[str], body: str):
    """POST each title in order; stop at the first failure.

    Returns (created, failure) where failure is None or (title, error).
    """
    endpoint = f"/me/todo/lists/{quote(list_id, safe='')}/tasks"
    created = []
    for t in titles:
        payload = {"title": t, "body": {"contentType": "text", "content": body}}
        try:
            todo._ms_graph_post(credential, endpoint, payload, scope=WRITE_SCOPE)
        except Exception as exc:  # report and stop; never retry a POST blindly
            return created, (t, exc)
        created.append(t)
    return created, None


_MD_SPECIAL = set("\\`*_[]()#!|~{}+-.:>")


def md_text(text: str) -> str:
    """Render untrusted text inert in Markdown: entity-escape HTML, backslash-escape Markdown."""
    text = display(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "".join(f"\\{c}" if c in _MD_SPECIAL else c for c in text)


def log_activity(list_name: str, created: list[str], failed: str | None) -> None:
    lines = [
        f"\n## {datetime.now().strftime('%Y-%m-%dT%H:%M')}\n\n",
        f"**Action:** Tasks added (Microsoft To Do, list: {md_text(list_name)})\n",
        f"**Created:** {len(created)}\n",
        *(f"- {md_text(t)}\n" for t in created),
    ]
    if failed is not None:
        lines.append(f"**Stopped at failure:** {md_text(failed)}\n")
    lines.append("\n---\n")
    safe_write(activity_log(), "".join(lines), append=True)


def read_titles(args) -> list[str]:
    titles = list(args.titles)
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
        titles += [line for line in text.splitlines() if line.strip()]
    return titles


def sign_in() -> int:
    try:
        credential = write_credential(interactive=True)
        lists = fetch_lists(credential)
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        print(f"To start over, delete {todo._ms_auth_record_path(WRITE_RECORD)} and rerun --test-auth.")
        return 1
    print(f"Write access works — {len(lists)} task lists:")
    for name in list_names(lists):
        print(f"  - {name}")
    return 0


def run(args) -> int:
    titles = validate_titles(read_titles(args))
    credential, lists = connect()
    target = match_list(lists, args.list)
    list_name = target.get("displayName", "")
    to_create, skipped = plan(titles, open_task_titles(credential, target["id"]))

    mode = "Creating" if args.yes else "Preview (nothing written; add --yes to create)"
    print(f"{mode}: list {display(list_name)!r}")
    for t in to_create:
        print(f"  + {t}")
    for t, reason in skipped:
        print(f"  - skip {t!r}: {reason}")
    if not args.yes:
        return 0
    if not to_create:
        print("Nothing to create.")
        return 0

    created, failure = create_tasks(credential, target["id"], to_create, provenance(args.note))
    log_activity(list_name, created, failure[0] if failure else None)
    print(f"Created {len(created)} of {len(to_create)}.")
    if failure is None:
        return 0
    title, exc = failure
    print(f"Failed on {title!r}: {exc}")
    print("  It may still have been created if the request timed out; check To Do before rerunning.")
    not_created = to_create[len(created):]
    print("Not created:")
    for t in not_created:
        print(f"  - {t}")
    return 1


def lists_only() -> int:
    _, lists = connect()
    for name in list_names(lists):
        print(name)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create tasks in Microsoft To Do (create only).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", help="Target list display name (case-insensitive)")
    mode.add_argument("--lists", action="store_true", help="Print list names and exit")
    mode.add_argument("--test-auth", action="store_true", help="One-time interactive sign-in")
    parser.add_argument("titles", nargs="*", help="Task titles")
    parser.add_argument("--file", help="File with one title per line (blank lines ignored)")
    parser.add_argument("--note", help="Extra line for each task's body")
    parser.add_argument("--yes", action="store_true", help="Actually create the tasks")
    args = parser.parse_args(argv)

    load_config()

    if args.test_auth:
        return sign_in()
    try:
        return lists_only() if args.lists else run(args)
    except NotSignedIn as exc:
        print(exc)
        return 1
    except UsageError as exc:
        print(exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
