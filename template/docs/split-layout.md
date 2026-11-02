# Split layout

Since v0.8.0 a vault can keep its **logic** and its **content** in separate directories:

| Part | What | Git | Config |
|---|---|---|---|
| Logic | `CLAUDE.md`, `vault.toml`, `.claude/`, `config/`, `scripts/` | yes (this repo) | `vault_path` |
| Content | `output/`, `data/`, `logs/`, `working/`, `inbox/` (e.g. an Obsidian vault) | no | `[paths].content_root` |
| State | auth records, launchd logs; host-only, never mounted into sandboxes | no | `[paths].state_root` |
| Notes | your notes vault, read-only | n/a | `[notes].vault_path` (alias `[paths].notes_root`) |

Sessions start in the logic dir. The content dir is added to Claude Code through
`permissions.additionalDirectories` and is never a working directory, so a sandbox can get a
clone of the logic without any personal data, plus only the content folders you mount.

`content_root = "."` (the default) keeps the single-dir layout of earlier versions: nothing moves.

## How paths resolve

- Skills find `vault.toml` next to themselves (`.claude/skills/_lib` → repo root), so the current
  working directory doesn't matter. `PIK_VAULT_TOML=/path/to/vault.toml` overrides this.
- Relative `[paths]` values resolve against the logic dir. Every other relative path in
  `vault.toml` (`[output]`, `[logs]`, `[data]`, `[inbox]`, sources) resolves against `content_root`.
- `output_root = "."` puts `reports/`, `alerts/`, `reflections/` … directly at the content root.
- Without `state_root`, state stays in `data/` and launchd logs in `logs/` (single-dir behaviour).
- Skill writers refuse any path inside the notes vault (`safe_write()`, `okf.write_concept()`,
  `activity_log()`).

## New instance

```bash
copier copy --vcs-ref v0.8.0 gh:<owner>/personal-intelligence-kit ~/Documents/my-vault-logic \
  --data content_root=~/Documents/My-Vault --data state_root=~/.local/state/my-vault
mkdir -p ~/Documents/My-Vault/{data,logs,working,inbox/research} ~/.local/state/my-vault/launchd
```

## Migrating a single-dir instance

The existing instance keeps working throughout. Nothing below deletes anything until the last step.

1. **Update in place first** (optional but recommended): `copier update --vcs-ref v0.8.0` in the
   old instance with the defaults (`content_root="."`). Paths stay the same, so check that skills still run.
2. **Generate the logic dir** with the same answers plus the new layout:
   ```bash
   copier copy --vcs-ref v0.8.0 <template> ~/Documents/<new-logic-dir> \
     --data-file <old-instance>/.copier-answers.yml \
     --data vault_path=~/Documents/<new-logic-dir> \
     --data content_root=<old-instance> \
     --data state_root=~/.local/state/<name>
   ```
   Pointing `content_root` at the old instance means its `output/`, `data/`, `logs/` and
   `working/` stay exactly where they are, and Obsidian links keep working.
3. **Secrets:** copy the `op://` reference lines from the old `.env` into the new one by hand.
   Don't add a dotenv `.envrc`. Commands that need a secret run as `op run --env-file=.env -- …`.
4. **Port local changes** (custom skills, CLAUDE.md sections) into the new logic repo.
5. **State:** `mkdir -p ~/.local/state/<name>/launchd`. `tasks-import` copies
   `data/ms_auth_record.json` into `state_root` on its first run. Remove the old file once
   that run has worked.
6. **Host-only settings:** contacts MCP via `claude mcp add --scope local …`. Review
   `.claude/settings.local.json` (generated when `host_web` is `ask` or `deny`).
7. **launchd:** `launchctl bootout` the old `com.claude.*` jobs, then install the new plists from
   `config/` (see `config/README.md`). Logs now go to `state_root/launchd/`.
8. **Verify** from the new logic dir: run each ingest and analysis skill once and check that outputs
   land in the content dir and that `git status` in the logic repo stays clean.
9. **Decommission** the old logic files (`.claude/`, `CLAUDE.md`, `vault.toml`, `.git`, `.env`)
   in the content dir only after the new setup has run for a while. Keep a backup.

## Optional: flatten `output/`

To get rid of the `output/` level, move its children to the content root, rewrite links
(`[[output/…` → `[[…`), and set `[output]` in `vault.toml` (or answer `output_root="."` when generating). Check for name
collisions with `data/`, `logs/`, `working/` and `inbox/` first, and update the launchd reminder's
reflections path (re-render with `copier update`).
