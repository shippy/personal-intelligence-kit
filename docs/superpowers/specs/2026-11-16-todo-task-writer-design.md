# Microsoft To Do task writer: design

**Date:** 2026-11-16
**Status:** approved, ready to implement
**Skill:** `template/.claude/skills/tasks-import/`

## Why

Host sessions keep ending with lists of things for the owner to follow up on, such as "message Karin" or "suggest a coffee time to Lepka". The vault can read Microsoft To Do (`import_tasks.py`, scope `Tasks.Read`) but can't write to it, so the owner has to retype each list. This feature lets an agent **add** tasks. It doesn't let an agent manage them.

## Scope

In scope: creating new tasks in an existing To Do list.

Out of scope, and must not be possible through this script:
- updating, completing or deleting a task
- creating, renaming or deleting a list
- reminders, due dates and recurrence (a plain due date may be added later, but not now)

## Interface

A new script, `template/.claude/skills/tasks-import/add_tasks.py`. It is a plain file with inline `# /// script` metadata, like `import_tasks.py`. Reuse that script's helpers by importing them, not by copying.

```bash
uv run add_tasks.py --list Social "Text Viktor on WhatsApp" "Suggest a coffee time to Lepka"
uv run add_tasks.py --list Social --file tasks.txt       # one title per line, blank lines ignored
uv run add_tasks.py --list Social --file tasks.txt --yes # actually create them
uv run add_tasks.py --test-auth                          # one-time interactive sign-in
uv run add_tasks.py --lists                              # print list names (read-only)
```

- **Preview is the default.** Without `--yes`, the script prints what it would create (list name, each title, which ones are skipped and why) and exits 0 without writing.
- `--list` matches the list's display name, ignoring case. If no list matches, or more than one does, the script exits non-zero and prints the available names. It never creates a list.
- `--note TEXT` (optional) adds a line to each task's body, for example a wikilink back to the briefing.

## Behaviour

1. **Dedup.** Before creating anything, the script GETs the target list's open tasks (`status ne 'completed'`, paginated with the existing collection helper). It skips any title that matches an open task after trimming whitespace and ignoring case. Duplicates within the batch are skipped too.
2. **Create.** For each remaining title, `POST /me/todo/lists/{id}/tasks` with `{"title": ..., "body": {"contentType": "text", "content": <provenance>}}`.
   - The provenance is `Added by Claude (pki vault), YYYY-MM-DD`, plus the `--note` text if one was given.
3. **Partial failure.** Stop at the first failed POST. Report which tasks were created and which were not. Don't retry a POST, since retrying could create a duplicate. Only a 429 or 503 carrying `Retry-After` may be retried, the way `_ms_graph_get` does it.
4. **Logging.** After a real run (`--yes`), append one entry to `logs/activity.md` in the content root, giving the list and the created titles. Resolve the path through `vault_config.py` and write it with the existing safe-write helper. Never write under the notes vault.
5. **Input limits.** Reject titles that are empty or longer than 255 characters, and reject batches of more than 50 tasks. Titles are sent as data and never interpolated into URLs.

## Auth

- Same public client as the reader (`_MS_CLIENT_ID`, the Microsoft Graph CLI app, tenant `common`). No app registration is needed.
- Scope `Tasks.ReadWrite`.
- **Separate persistence:** use the token cache name `pik-todo-write` and the auth record `ms_auth_record_write.json` in `state_dir()`. The nightly read sync (`import_tasks.py`, launchd) must keep using `pik-todo`, `ms_auth_record.json` and `Tasks.Read`, and must stay byte-for-byte unchanged in behaviour.
- Without a record, `--test-auth` runs the device-code flow. Every other mode must fail fast with a clear message ("run `uv run add_tasks.py --test-auth` first") rather than prompting. Agents run this script non-interactively, and a device-code prompt nobody sees would hang.
- Document the caveat in SKILL.md: consent is per app and per account, so granting `Tasks.ReadWrite` to the Graph CLI app means a refresh token from the read cache could in principle request write access too. Separate caches keep the sync job independent, but they don't create a privilege boundary.

## Refactor allowed

Small, behaviour-preserving changes to `import_tasks.py` are fine where they make reuse possible. For example, parametrise `_get_ms_credential` and the auth-record path by scope, cache name and record name, with defaults equal to today's values, or add a POST helper next to `_ms_graph_get`. Existing tests must pass unchanged.

## Tests

Use pytest in the style of `test_microsoft_todo.py`: monkeypatch the HTTP layer, with no network access and no real credentials. Cover:
- preview writes nothing
- `--yes` POSTs the expected payloads
- dedup against open tasks and within the batch
- list matching: exact, case-insensitive, missing, ambiguous
- stopping at the first failed POST, with a correct report
- refusing to prompt when there is no write auth record (outside `--test-auth`)
- the read-only `import_tasks.py` still requesting only `Tasks.Read` with the `pik-todo` cache
- input limits

Run the tests in `sbxa` (Docker sandbox), not on the host. The host is only for the real sign-in, which the owner does.

## Docs

- `SKILL.md.jinja`: add a "Writing tasks" section covering the interface, preview-by-default, the one-time `--test-auth` step (with a note that a new uv Python may trigger a macOS Keychain dialog: choose Always Allow), and the consent caveat.
- Update the skill description so it no longer says "not for task management" without qualification. It should read as "sync for analysis, plus create-only task capture".
- `template/CLAUDE.md` (the rendered vault CLAUDE.md), if it lists skills: one line saying tasks-import can now create tasks on request, always previewed first.

## Done when

- `add_tasks.py` and its tests exist, and the full template test suite passes in sbxa.
- `import_tasks.py` behaviour is unchanged.
- Docs are updated.
- Changes stay uncommitted on branch `todo-task-writer` for the security review. Don't commit, push or tag.
