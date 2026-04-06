---
name: email-ingest
description: Sync email via mbsync (IMAP) and index via notmuch. Run before email-analyzer queries or as part of data-sync. First-time setup requires ~/.mbsyncrc and notmuch database init — see SETUP.md.
---

# Email Ingest

Pulls mail from IMAP servers via `mbsync` and indexes it with `notmuch`.

## Invocation

```bash
cd .claude/skills/email-ingest
uv run ingest.py
```

Or called automatically by `data-sync`.

## What It Does

1. Runs `mbsync -a` to sync all configured IMAP accounts
2. Runs `notmuch new` to index new messages into the notmuch database
3. Reports counts of new/updated messages
4. Logs activity to `logs/activity.md`

## Prerequisites

- `mbsync` (from `isync`) installed: `brew install isync`
- `notmuch` installed: `brew install notmuch`
- `~/.mbsyncrc` configured (see SETUP.md)
- notmuch database initialized (`notmuch setup` once)
- IMAP credentials in macOS Keychain or gpg-encrypted file

## First-Time Setup

See [SETUP.md](SETUP.md) for complete walkthrough:
1. Install mbsync + notmuch
2. Store IMAP credentials securely
3. Configure `~/.mbsyncrc` with `MaxMessages 1000` (limit initial sync)
4. Run `notmuch setup` (interactive)
5. Run `notmuch new` to index

## Troubleshooting

- **mbsync authentication fails** → check keychain password / app password
- **notmuch new finds 0 messages** → verify Maildir path matches in both `.mbsyncrc` and `~/.notmuch-config`
- **Initial sync takes forever** → set `MaxMessages 1000` in `.mbsyncrc` to limit history

## Philosophy

Email sync is a one-shot operation: pull, index, done. No streaming, no webhooks. Run it on a schedule (daily is plenty) via `data-sync`.
