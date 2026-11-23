#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "azure-identity>=1.15.0"]
# ///
"""
Add tasks to Microsoft To Do (create only).

Creates new tasks in an existing list, optionally with steps (checklist items).
It cannot update, complete or delete a task, and never creates, renames or
deletes a list. Preview is the default; nothing is written without --yes.

In a --file, each unindented line is a task and each indented line under it is
one of that task's steps:

    Pack for the talk
        Laptop charger
        HDMI adapter
    Text Viktor

Usage:
    uv run add_tasks.py --list Social "Text Viktor" "Suggest a coffee time"
    uv run add_tasks.py --list Social --file tasks.txt        # tasks, indented steps
    uv run add_tasks.py --list Social --file tasks.txt --yes  # actually create them
    uv run add_tasks.py --test-auth                           # one-time interactive sign-in
    uv run add_tasks.py --lists                               # print list names (read-only)
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_tasks as todo  # noqa: E402  (also puts _lib on sys.path)
from activity import append_activity  # noqa: E402
from vault_config import load_config  # noqa: E402

WRITE_SCOPE = "Tasks.ReadWrite"
WRITE_CACHE = "pik-todo-write"
WRITE_RECORD = "ms_auth_record_write.json"
MAX_TITLE = 255
MAX_BATCH = 50
MAX_STEPS = 20          # per task
MAX_STEPS_TOTAL = 100   # per run
AUTH_HINT = "run `uv run add_tasks.py --test-auth` first"
OPEN_FILTER = "$filter=" + quote("status ne 'completed'", safe="'")


class UsageError(Exception):
    pass


class Task(NamedTuple):
    title: str
    steps: tuple[str, ...] = ()


class Failure(NamedTuple):
    """Where a run stopped. `step` is None when the task itself wasn't created."""
    task: Task
    error: Exception
    step: str | None = None
    steps_added: int = 0
    step_sent: bool = True   # False: stopped before sending the step (no usable task id)


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
_LINE_SEPARATORS = {"\u2028", "\u2029"}  # categories Zl/Zp: they break lines like a newline


def _unsafe_char(c: str) -> bool:
    """C0/C1 controls and DEL (category Cc), a bidi control, or a Unicode line separator."""
    return unicodedata.category(c) == "Cc" or c in _BIDI_CONTROLS or c in _LINE_SEPARATORS


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


def _check_text(text: str, label: str) -> str:
    """Stripped `text`, or UsageError if it is empty, too long or unsafe.

    Only spaces and tabs around the text are allowed; any other control character
    is refused wherever it is, so stripping can't hide one.
    """
    if any(_unsafe_char(c) for c in text.strip(" \t")):
        raise UsageError(f"{label} contains a control or bidi character: {display(text.strip()[:40])!r}")
    t = text.strip()
    if not t:
        raise UsageError(f"{label} must not be empty")
    if len(t) > MAX_TITLE:
        raise UsageError(f"{label} longer than {MAX_TITLE} characters: {display(t[:40])!r}...")
    return t


def validate_titles(titles: list[str]) -> list[str]:
    if not titles:
        raise UsageError("No task titles given")
    if len(titles) > MAX_BATCH:
        raise UsageError(f"Too many tasks: {len(titles)} (max {MAX_BATCH} per run)")
    return [_check_text(t, "Title") for t in titles]


def validate_tasks(tasks: list[Task]) -> list[Task]:
    titles = validate_titles([t.title for t in tasks])
    total = sum(len(t.steps) for t in tasks)
    if total > MAX_STEPS_TOTAL:
        raise UsageError(f"Too many steps: {total} (max {MAX_STEPS_TOTAL} per run)")
    cleaned = []
    for title, task in zip(titles, tasks):
        if len(task.steps) > MAX_STEPS:
            raise UsageError(f"Too many steps under {title!r}: {len(task.steps)} (max {MAX_STEPS})")
        cleaned.append(Task(title, tuple(_check_text(s, "Step") for s in task.steps)))
    return cleaned


def open_task_titles(credential, list_id: str) -> set[str]:
    endpoint = f"/me/todo/lists/{quote(list_id, safe='')}/tasks?{OPEN_FILTER}"
    tasks = todo._ms_graph_collection(credential, endpoint, scope=WRITE_SCOPE)
    return {_key(t.get("title", "")) for t in tasks}


def plan(tasks: list[Task], existing: set[str]) -> tuple[list[Task], list[tuple[Task, str]]]:
    """Split tasks into (to create, [(skipped task, reason)]). A skipped task's steps go with it."""
    seen = set()
    create, skipped = [], []
    for t in tasks:
        k = _key(t.title)
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


def create_tasks(credential, list_id: str, tasks: list[Task], body: str):
    """POST each task, then each of its steps, in order; stop at the first failure.

    Returns (created, failure): `created` holds the tasks created with all their
    steps, `failure` is None or a Failure. A task whose step failed was created,
    with `steps_added` of its steps, and is in the Failure, not in `created`.
    """
    endpoint = f"/me/todo/lists/{quote(list_id, safe='')}/tasks"
    created = []
    for t in tasks:
        payload = {"title": t.title, "body": {"contentType": "text", "content": body}}
        try:
            response = todo._ms_graph_post(credential, endpoint, payload, scope=WRITE_SCOPE)
        except Exception as exc:  # report and stop; never retry a POST blindly
            return created, Failure(t, exc)
        if t.steps:
            task_id = response.get("id") if isinstance(response, dict) else None
            if not isinstance(task_id, str) or task_id in ("", ".", ".."):
                return created, Failure(t, RuntimeError("Graph returned no usable task id"),
                                        t.steps[0], step_sent=False)
            steps_endpoint = f"{endpoint}/{quote(task_id, safe='')}/checklistItems"
            for i, step in enumerate(t.steps):
                try:
                    todo._ms_graph_post(credential, steps_endpoint, {"displayName": step},
                                        scope=WRITE_SCOPE)
                except Exception as exc:
                    return created, Failure(t, exc, step, steps_added=i)
        created.append(t)
    return created, None


_MD_SPECIAL = set("\\`*_[]()#!|~{}+-.:>")


def md_text(text: str) -> str:
    """Render untrusted text inert in Markdown: entity-escape HTML, backslash-escape Markdown."""
    text = display(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "".join(f"\\{c}" if c in _MD_SPECIAL else c for c in text)


def _with_steps(task: Task) -> str:
    n = len(task.steps)
    return md_text(task.title) + (f" ({n} step{'s' if n != 1 else ''})" if n else "")


def _failure_text(failure: Failure) -> str:
    if failure.step is None:
        return md_text(failure.task.title)
    return (f"{md_text(failure.task.title)} (task created with {failure.steps_added} of "
            f"{len(failure.task.steps)} steps; failed on step {md_text(failure.step)})")


def log_activity(list_name: str, created: list[Task], failure: Failure | None) -> None:
    """Append one entry to the monthly activity log. Untrusted text is escaped first."""
    fields = {
        "List": md_text(list_name),
        "Created": [_with_steps(t) for t in created],
        "Stopped at failure": _failure_text(failure) if failure is not None else None,
    }
    append_activity(f"Tasks added to Microsoft To Do ({len(created)})", fields)


def read_tasks(args) -> list[Task]:
    """Positional titles, then the file's tasks; an indented file line is a step of the task above."""
    tasks = [Task(t) for t in args.titles]
    if args.file:
        from_file: list[tuple[str, list[str]]] = []
        text = Path(args.file).read_text(encoding="utf-8")
        # Split on LF (and CRLF) only: str.splitlines() would also split on VT, FF,
        # NEL, U+2028 and other controls, which validation must see and refuse.
        for n, line in enumerate(text.split("\n"), 1):
            line = line.removesuffix("\r")
            if not line.strip(" \t"):  # blank; a line of other controls becomes a task and is refused
                continue
            if line[0] in " \t":
                if not from_file:
                    raise UsageError(f"Line {n} of {args.file} is indented (a step) but no task comes before it")
                from_file[-1][1].append(line)
            else:
                from_file.append((line, []))
        tasks += [Task(title, tuple(steps)) for title, steps in from_file]
    return tasks


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


def report_failure(failure: Failure, not_created: list[Task]) -> None:
    task = failure.task
    if failure.step is None:
        print(f"Failed on {task.title!r}: {failure.error}")
        print("  It may still have been created if the request timed out; check To Do before rerunning.")
    else:
        print(f"{task.title!r} was created, but only {failure.steps_added} of {len(task.steps)} "
              f"steps were added. Failed on step {failure.step!r}: {failure.error}")
        if failure.step_sent:
            print("  That step may still have been added if the request timed out.")
        print("  A rerun skips this task because it is now open: add the missing steps in To Do,")
        print("  or delete the task there and rerun.")
        print("Steps not added:")
        for s in task.steps[failure.steps_added:]:
            print(f"  - {s}")
    if not_created:
        print("Not created:")
        for t in not_created:
            print(f"  - {t.title}")


def run(args) -> int:
    tasks = validate_tasks(read_tasks(args))
    credential, lists = connect()
    target = match_list(lists, args.list)
    list_name = target.get("displayName", "")
    to_create, skipped = plan(tasks, open_task_titles(credential, target["id"]))

    mode = "Creating" if args.yes else "Preview (nothing written; add --yes to create)"
    print(f"{mode}: list {display(list_name)!r}")
    for t in to_create:
        print(f"  + {t.title}")
        for s in t.steps:
            print(f"      [ ] {s}")
    for t, reason in skipped:
        n = len(t.steps)
        steps = f" (and its {n} step{'s' if n != 1 else ''})" if n else ""
        print(f"  - skip {t.title!r}{steps}: {reason}")
    if not args.yes:
        return 0
    if not to_create:
        print("Nothing to create.")
        return 0

    created, failure = create_tasks(credential, target["id"], to_create, provenance(args.note))
    try:
        log_activity(list_name, created, failure)
    except Exception as exc:  # the To Do outcome below matters more than the log
        print(f"Warning: the activity log entry was not written: {exc}")
    # A task that failed on a step was created, so it counts, and "not created" starts after it.
    partial = 1 if failure is not None and failure.step is not None else 0
    print(f"Created {len(created) + partial} of {len(to_create)}.")
    if failure is None:
        return 0
    report_failure(failure, to_create[len(created) + partial:])
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
    parser.add_argument("--file", help="File with one task per line; indented lines are steps of "
                                       "the task above (blank lines ignored)")
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
