# pi-task: native macOS pilot

Run `pi-task` from the AI vault or a project/worktree. No Docker, login, model
selection, trust dialog or canned task. A plain launch opens an idle Pi session.
It uses the currently configured local HTTP model from ~/.pi/agent, reconstructing
only the selected model definition with a dummy key. It does not import host
credentials, arbitrary model headers, packages or secret-lookup commands.

```sh
cd ~/Documents/my-vault && pi-task
cd ~/Documents/presentations && pi-task "Explore a talk about my second-brain practice"
pi-task --resume
pi-task --describe
pi-task --check
pi-task --check-model
```

Launches inside a subdirectory use the enclosing Git checkout, or the AI vault
root for PKM. Launching from PARA or a folder without a project is rejected.
--vault PATH (or PI_TASK_VAULT) selects another AI vault containing vault.toml.
The default vault is the one containing this installed script. Notes are optional:
with `[notes] app = "none"`, vault and project sessions omit the human-notes
reference and its policy roots.

Shared reference repositories are configured in that vault's vault.toml:

```toml
[pi_task]
reference_repositories = [
    "~/Documents/presentations",
    "~/Documents/resume",
    "~/Documents/website",
]
```

This is a local opt-in list, not access to all of ~/Documents. Paths must be
existing Git repository/worktree roots; relative paths resolve from the vault.
Missing/invalid entries stop launch with an error. An omitted list grants no
extra reference access. Both PKM and project sessions can read these repositories;
only the active checkout is writable in project mode. Root .env, .env.local and
.envrc remain read-denied in references too. Other secret files are not exhaustively
scanned. Restart pi-task after changing the list; running sessions keep their policy.

## Handoff

### From an interactive Pi conversation

1. Start `pi-task` in the AI vault and develop the idea interactively.
2. Ask: "Prepare a project handoff to presentations and website."
   The automatically discoverable project-handoff skill drafts a structured brief
   and repository request. `/skill:project-handoff` explicitly loads it if needed.
3. Type `/project-handoff` yourself. Review the resolved new worktree paths and
   brief preview, then confirm once. Cancel to remain in the source session.
4. Pi exits gracefully and the host launcher creates the worktrees and starts the
   receiving task in the same terminal. No copied shell command or nested TUI.

For an existing named task, ask the skill to add a repo to that task; the same
command approves the addition and resumes its saved conversation. Eligible repos
are the configured references and the current project. Arbitrary unlisted paths
require a deliberate host-side configuration/CLI action, not a model proposal.

The command is not an agent tool and refuses headless use. It snapshots the JSON
before approval, so changing the proposal during confirmation cannot change the
approved roots or brief. The approved snapshot lives in a tool-write-denied runtime
directory; only the host launcher consumes it after Pi exits and task locks close.
No approval file means no host operation. The handoff draft is temporary until
approved. New tasks receive the brief, not the entire source transcript; the source
conversation remains resumable separately. If creation fails (for example a branch
collision), inspect the error and resume the source session; no rollback deletes
existing work. The source session may have already closed at that point.

Restart existing pi-task sessions to load this skill and command. The bare pi
command is unchanged. The interactive command does not authorize publishing,
credential access, or writes to original checkouts/PARA.

### Multi-repository task

From any folder, using names from the reference list (or absolute repo paths):

```sh
pi-task --new pkm-talk --repo presentations --repo website --repo resume --handoff /path/to/brief.md
pi-task --task pkm-talk --resume
```

The first repository is the session's home. Every listed repository gets a new
branch/worktree, with all task worktrees writable and original checkouts reference-only.
There is one private manifest, brief and session directory under
~/.local/state/pi-task/tasks/pkm-talk. The manifest records repository, worktree,
branch and base commit. Instructions list all worktrees and require reading each
project's local conventions before editing. Publication remains host-side.

To expand the task later, exit its Pi session, then run:

```sh
pi-task --task pkm-talk --add-repo resume
```

This adds a new resume worktree and resumes the task's saved conversation if one
exists. Omit repos already included; repeat --add-repo for multiple additions.
An open task holds a lock: another launcher cannot widen its access or run a
concurrent session. Add --prepare-only to prepare/update without starting Pi.
Use --task NAME --describe to inspect a task without a model call.

Preflight checks all requested branches and destinations before worktree creation.
If Git fails partway through, successfully created worktrees remain recorded;
inspect the task and retry remaining repos with --add-repo. Nothing is deleted or
rolled back automatically. Never pass another task's handoff as authority to add
repositories: --repo/--add-repo are explicit host-side user actions.

### Single repository

From an actioning project's root:

```sh
pi-task --new my-task --handoff /path/to/private-brief.md
```

Creates branch pi/my-task and .pi/worktrees/my-task from committed HEAD, attaches
the brief outside the repo, and starts its task. Add --prepare-only to stop before
launch. Existing worktrees are never overwritten. Uncommitted/ignored source
checkout files are not copied. Worktree paths are added to local Git excludes.
Use --cwd PATH to select another project without changing the shell directory.
--handoff FILE on an existing checkout injects the brief, but does not submit a
task unless a prompt is supplied. Newly named tasks use --task NAME --resume to
continue their shared conversation, even for a single repo. Ordinary pi-task
sessions still use --resume for the current checkout. Existing legacy worktree
sessions are unchanged and are not automatically migrated into named tasks.

## Access and limitations

- PKM: output/, working/, logs/ writable; vault config/data read-only to tools.
- Project: checkout writable; Claude output/working/data are read-only references.
- PARA: readable, explicitly write-denied in all profiles.
- Git metadata, launcher code, runtime policy, and Pi configuration write-denied.
- Common root secrets and host credential directories read-denied; inherited
  environment is allowlisted. This is not a comprehensive secret scanner.
- Shell network: localhost. Exa is a trusted host extension with external HTTPS;
  only public queries/URLs should leave the vault. Fal is not installed.
- Only Seatbelt, Exa, the active-profile guard and project-handoff extension load;
  global/project extension and skill discovery are disabled, with the project-handoff
  skill explicitly included. Vault conventions are appended,
  project AGENTS/CLAUDE instructions still load. Existing vault automation skills
  have not been ported to this native profile yet.

The pinned pi-seatbelt-sandbox 0.1.4 extension OS-sandboxes bash and user !.
Built-in file tools have in-process path guards, not OS-level isolation. Arbitrary
extension/MCP code runs with host privileges; only the reviewed extensions are
enabled. Herdr's host-control integration is not loaded; native process detection
may still identify Pi, but enriched status/session reporting is not configured.

Upstream grants shell read/write access to macOS temporary trees, not just the
launcher scratch folder. File guards are tighter. The tests prove specific
denials, not whole-process containment or freedom from races/bypasses. This is
a scoped pilot, not a replacement for a hardened hostile-code environment.

The native check uses disposable fixtures for overwrite, delete, move, symlink,
and out-of-policy writes, plus built-in write/edit guards; it never writes PARA.
CLI startup and local model readiness are checked separately.

## Install / development

Requires macOS, Python 3.11+, Node with TypeScript stripping for the diagnostic
test, ripgrep, Pi 0.85.1, and the configured local model server running.

```sh
npm install --prefix ~/.local/share/pi-task/vendor --no-save --ignore-scripts --legacy-peer-deps pi-seatbelt-sandbox@0.1.4
ln -s /absolute/path/to/my-vault/scripts/pi-task ~/.local/bin/pi-task
python3 -m unittest discover -s scripts/pi_task -p 'test_*.py' -t scripts
node --test scripts/pi_task/project-handoff.test.mjs
```

Normal `pi` is unchanged and does not load this policy. Reusable launcher sources belong upstream in personal-intelligence-kit/template/scripts.
