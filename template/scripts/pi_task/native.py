import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request

from .cli import ASSETS, discover, git, overlap, session_state
from .model import local_model
from .tasks import add_repositories, load_manifest, resolve_repository, task_directory, task_lock
from .handoff import transition_args, validate_approval


EXTENSION = Path.home() / ".local/share/pi-task/vendor/node_modules/pi-seatbelt-sandbox"
SAFE_ENV = {"HOME", "PATH", "TERM", "COLORTERM", "LANG", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME", "SHELL", "TMPDIR", "TERM_PROGRAM"}


def reference_repositories(config, vault, para):
    configured = config.get("pi_task", {}).get("reference_repositories", [])
    if not isinstance(configured, list) or any(not isinstance(path, str) or not path.strip() for path in configured):
        raise ValueError("pi_task.reference_repositories must be a list of repository paths")
    references = []
    for value in configured:
        path = Path(value).expanduser()
        path = (path if path.is_absolute() else vault / path).resolve(strict=True)
        if any(overlap(path, source) for source in (vault, para)):
            raise ValueError(f"Reference repository overlaps a vault: {path}")
        try:
            root = Path(git(path, "rev-parse", "--show-toplevel")).resolve()
        except subprocess.CalledProcessError as error:
            raise ValueError(f"Reference is not a Git repository: {path}") from error
        if path != root:
            raise ValueError(f"Reference must name a repository or worktree root: {path}")
        if path not in references:
            references.append(path)
    return references


def content_dirs(config, vault):
    """Content directories from vault.toml, mirroring _lib/vault_config.py (stdlib only).

    [paths].content_root resolves against the vault (logic) dir; output/working/data/logs
    roots resolve against the content root. Defaults reproduce the single-dir layout.
    """
    def resolve(base, raw, leaf=True):
        path = Path(raw).expanduser()
        path = path if path.is_absolute() else base / path
        if leaf or path.name in ("", ".", ".."):
            return path.resolve()  # same as vault_config: symlinks first
        # Writable roots: resolve the parent but keep the last component, so native_policy
        # still sees (and refuses) a symlinked output/working/logs dir.
        return path.parent.resolve() / path.name
    paths = config.get("paths", {})
    root = resolve(vault, paths.get("content_root") or ".")
    dirs = {"root": root, **{name: resolve(root, config.get(name, {}).get("root", name), leaf=name == "data") for name in ("output", "working", "data", "logs")}}
    data = config.get("data", {})
    dirs["databases"] = [resolve(root, value) for key, value in data.items() if key != "root" and isinstance(value, str)]
    # Host-only files the sandbox must not touch: configured state (auth records) and the activity log.
    dirs["state"] = resolve(vault, paths["state_root"]) if paths.get("state_root") else None
    dirs["activity"] = resolve(root, config.get("logs", {}).get("activity", "logs/activity.md"))
    # Notes vault, as the skills' write guard sees it: [paths].notes_root against the logic dir,
    # else [notes].vault_path against the content root.
    if paths.get("notes_root"):
        dirs["notes"] = resolve(vault, paths["notes_root"])
    else:
        raw = config.get("notes", {}).get("vault_path")
        dirs["notes"] = resolve(root, raw) if raw else None
    output = dirs["output"]
    if output in (root, vault) or any(output in dirs[name].parents or output == dirs[name] for name in ("working", "data", "logs")) or output in vault.parents:
        # Flattened output (output.root = "."): never grant the whole content/logic root,
        # only the configured deliverable dirs and the OKF changelog.
        named = [resolve(root, value, leaf=False) for key, value in config.get("output", {}).items() if key != "root" and isinstance(value, str)]
        dirs["deliverables"] = [path for path in dict.fromkeys(named) if path not in (root, vault)]
        dirs["changelog"] = output / "log.md"
    else:
        dirs["deliverables"] = [output]
        dirs["changelog"] = None
    return dirs


def prepare_writable(writable, files=()):
    """Create writable roots before the sandbox starts. File entries (the OKF changelog) only get their parent."""
    files = {str(path) for path in files}
    for path in writable:
        target = Path(path)
        (target.parent if str(path) in files else target).mkdir(parents=True, exist_ok=True)


def native_policy(profile, checkout, vault, para, state_root, temporary, references=(), task_roots=(), dirs=None):
    dirs = dirs or content_dirs({}, vault)
    project_roots = list(task_roots) if task_roots else [checkout]
    notes_roots = [para] if para is not None else []
    readable = [*project_roots, *notes_roots, dirs["output"], dirs["working"], dirs["data"], ASSETS, *references]
    readable += [Path(path) for path in ("/bin", "/sbin", "/usr", "/System", "/Library", "/opt/homebrew", "/etc", "/private/etc", "/dev/null", "/dev/urandom")]
    changelog = [dirs["changelog"]] if dirs.get("changelog") else []
    writable = [*dirs["deliverables"], *changelog, dirs["working"], dirs["logs"]] if profile == "pkm" else project_roots
    host_state = [dirs["state"]] if dirs.get("state") else []
    for path in writable:
        if path.is_symlink() or any(overlap(path.resolve(), root) for root in (para, state_root, *host_state)):
            raise ValueError(f"Unsafe writable root: {path}")
    forbidden = [*notes_roots, state_root, *host_state, dirs["data"], *dirs.get("databases", []), ASSETS, EXTENSION, Path.home() / ".pi", Path.home() / ".config/herdr", Path.home() / ".local/bin/pi-task"]
    forbidden += [root / name for root in project_roots for name in (".git", ".pi", ".agents", ".claude")]
    forbidden += [path for path in references if not (profile == "project" and any(path == root or path in root.parents for root in project_roots))]
    for root in project_roots:
        try:
            forbidden.append(Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")))
        except subprocess.CalledProcessError:
            pass
    secrets = [Path.home() / name for name in (".ssh", ".aws", ".gnupg", ".config/gcloud", ".netrc", ".git-credentials", ".pi/agent/auth.json")]
    secrets += [root / name for root in (*project_roots, vault, *references) for name in (".env", ".envrc", ".env.local")]
    secrets += host_state
    if profile == "pkm" and dirs.get("activity"):
        forbidden.append(dirs["activity"])  # host-only log; sandboxes must not forge entries
    if profile == "project":
        forbidden += [vault] + ([dirs["root"]] if dirs["root"] != vault else [])
    return {
        "enabled": True, "failClosed": True,
        "readable": [str(path) for path in readable] + [str(temporary)],
        "writable": [str(path) for path in writable] + [str(temporary)],
        "denyRead": [str(path) for path in secrets],
        "denyWrite": [str(path) for path in forbidden + secrets],
        "environment": {"mode": "filtered", "deny": ["PI_CODING_AGENT_DIR", "PI_SEATBELT_PROFILE", "PI_SEATBELT_PROFILE_SCOPE"]},
        "network": {"mode": "localhost"},
    }


def parser():
    arguments = argparse.ArgumentParser(description="Native Seatbelt Pi session for the current vault or project")
    arguments.add_argument("prompt", nargs="?")
    arguments.add_argument("--cwd", type=Path, default=Path.cwd())
    arguments.add_argument("--vault", type=Path, default=Path(os.environ.get("PI_TASK_VAULT", ASSETS.parents[1])))
    arguments.add_argument("--new", metavar="SLUG")
    arguments.add_argument("--task", metavar="SLUG", help="Open an existing multi-repository task")
    arguments.add_argument("--repo", action="append", default=[], help="Repository path or reference name to include in --new (repeatable)")
    arguments.add_argument("--add-repo", action="append", default=[], help="Add a worktree to --task and resume (repeatable)")
    arguments.add_argument("--handoff", type=Path)
    arguments.add_argument("--prepare-only", action="store_true")
    arguments.add_argument("--resume", action="store_true")
    arguments.add_argument("--describe", action="store_true")
    arguments.add_argument("--check", action="store_true", help="Run native sandbox tests on disposable fixtures")
    arguments.add_argument("--check-model", action="store_true")
    return arguments


def run(args, stack):
    if sys.platform != "darwin":
        raise ValueError("This launcher requires macOS Seatbelt")
    if not (EXTENSION / "index.ts").exists():
        raise ValueError(f"Seatbelt 0.1.4 is not installed at {EXTENSION}")
    if json.loads((EXTENSION / "package.json").read_text())["version"] != "0.1.4":
        raise ValueError("Unexpected Seatbelt version; review before upgrading")
    if args.check:
        return subprocess.run(["node", str(ASSETS / "check-seatbelt.mjs"), str(EXTENSION)], check=True)
    vault = args.vault.expanduser().resolve(strict=True)
    config = tomllib.loads((vault / "vault.toml").read_text())
    notes = config.get("notes", {})
    dirs = content_dirs(config, vault)
    para = None
    if notes.get("app", "none") != "none":
        if dirs["notes"] is None:
            raise ValueError("notes.vault_path (or paths.notes_root) is required when a notes source is enabled")
        para = dirs["notes"].resolve(strict=True)
    elif config.get("paths", {}).get("notes_root"):
        para = dirs["notes"]  # explicit notes root: protect it even without a notes app
    references = reference_repositories(config, vault, para)
    if args.new and args.task or args.repo and not args.new or args.add_repo and not args.task:
        raise ValueError("Use --repo with --new, or --add-repo with --task; --new and --task are exclusive")
    profile, checkout = ("project", vault) if args.task or (args.new and args.repo) else discover(args.cwd.expanduser(), vault, para)
    state_root = Path.home() / ".local/state/pi-task"
    if any(overlap(state_root, root) for root in (checkout, vault, para)):
        raise ValueError("Private launcher state overlaps a workspace")
    task_roots = []
    manifest = None
    if args.new or args.task:
        state = task_directory(state_root, args.new or args.task)
        stack.enter_context(task_lock(state))
    if args.new:
        if profile == "pkm" or args.resume or args.describe or args.check_model:
            raise ValueError("--new requires a project launch, not PKM/resume/diagnostics")
        if (state / "manifest.json").exists():
            raise ValueError("Task already exists; use --task NAME --add-repo REPO")
        if not args.handoff:
            raise ValueError("--new requires --handoff FILE")
        brief = args.handoff.expanduser().resolve(strict=True).read_text()
        repositories = [resolve_repository(value, references, vault, para, state_root) for value in (args.repo or [str(checkout)])]
        manifest = {"version": 1, "name": args.new, "repositories": []}
        (state / "handoff.md").write_text(brief)
        add_repositories(state, manifest, repositories)
    elif args.task:
        if args.prepare_only and not args.add_repo:
            raise ValueError("--prepare-only with --task requires --add-repo")
        manifest = load_manifest(state, vault, para, state_root)
        if args.add_repo:
            if args.describe or args.check_model:
                raise ValueError("--add-repo cannot be combined with diagnostics")
            repositories = [resolve_repository(value, references, vault, para, state_root) for value in args.add_repo]
            add_repositories(state, manifest, repositories)
            if args.handoff:
                (state / "handoff.md").write_text(args.handoff.expanduser().resolve(strict=True).read_text())
            args.resume = bool(list((state / "sessions").glob("*.jsonl")))
    elif args.prepare_only:
        raise ValueError("--prepare-only requires --new or --task --add-repo")
    if manifest:
        task_roots = [Path(entry["worktree"]) for entry in manifest["repositories"]]
        checkout = task_roots[0]
        references = list(dict.fromkeys([*references, *(Path(entry["repository"]) for entry in manifest["repositories"])]))
        profile = "project"
    if args.prepare_only:
        return
    if not manifest:
        state = session_state(state_root, checkout)
    handoff = args.handoff.expanduser().resolve(strict=True) if args.handoff else state / "handoff.md"
    print(f"PI-TASK | native Seatbelt | {profile.upper()} | {checkout}", flush=True)
    print(f"PARA: {'READ-ONLY' if para is not None else 'disabled'} | Exa: enabled | Fal: not configured\nShell network: localhost | trusted Exa extension: external HTTPS", flush=True)
    for reference in references:
        print(f"Reference: {reference} (read-only outside active workspace)", flush=True)
    for root in task_roots:
        print(f"Task write root: {root}", flush=True)
    if args.describe:
        print(json.dumps(native_policy(profile, checkout, vault, para, state_root, Path(tempfile.gettempdir()) / "pi-task-session", references, task_roots, dirs), indent=2))
        return
    provider, model_id, _, models = local_model(Path.home())
    original = json.loads((Path.home() / ".pi/agent/models.json").read_text())["providers"][provider]["baseUrl"]
    models["providers"][provider]["baseUrl"] = original
    try:
        with urllib.request.urlopen(original.rstrip("/") + "/models", timeout=5) as response:
            served = json.load(response)
        if model_id not in [model["id"] for model in served.get("data", [])]:
            raise ValueError("Configured local model is not served")
    except OSError as error:
        raise ValueError(f"Start your local model server and retry: {error}") from error
    if args.check_model:
        print(f"PASS: {provider}/{model_id} available; no prompt submitted")
        return
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    sessions = state / "sessions"
    sessions.mkdir(exist_ok=True, mode=0o700)
    if args.resume and not list(sessions.glob("*.jsonl")):
        raise ValueError("No saved conversation here; launch without --resume")
    pi = shutil.which("pi")
    if not pi:
        raise ValueError("pi is not on PATH")
    with tempfile.TemporaryDirectory(prefix="runtime-", dir=state_root) as runtime_name, tempfile.TemporaryDirectory(prefix="pi-task-shell-") as scratch_name:
        runtime = Path(runtime_name)
        scratch = Path(scratch_name)
        (runtime / "extensions").mkdir()
        policy = native_policy(profile, checkout, vault, para, state_root, scratch, references, task_roots, dirs)
        prepare_writable(policy["writable"], [dirs["changelog"]] if dirs["changelog"] else [])
        (runtime / "extensions/seatbelt.json").write_text(json.dumps(policy))
        (runtime / "models.json").write_text(json.dumps(models))
        (runtime / "settings.json").write_text(json.dumps({"defaultProvider": provider, "defaultModel": model_id, "defaultProjectTrust": "never"}))
        context = (ASSETS / "native-context.md").read_text()
        eligible = list(dict.fromkeys([*references, *([checkout] if profile == "project" and not manifest else [])]))
        handoff_config = {
            "requestPath": str(scratch / "project-handoff.json"),
            "approvalPath": str(runtime / "handoff-approved.json"),
            "repositories": [str(path) for path in eligible],
            "currentTask": manifest["name"] if manifest else None,
            "currentRepositories": [entry["repository"] for entry in manifest["repositories"]] if manifest else [],
        }
        (runtime / "handoff-config.json").write_text(json.dumps(handoff_config))
        context += f"\nProfile: {profile}\nWorkspace: {checkout}\nHuman vault: {para or '(disabled)'}\nAI vault: {vault}\nWritable roots: {', '.join(policy['writable'])}\n"
        context += f"\nProject handoff request file: {handoff_config['requestPath']}\nEligible handoff repositories: {', '.join(handoff_config['repositories'])}\nCurrent named task: {handoff_config['currentTask'] or '(none)'}\n"
        context += "Reference repositories (read-only except the active workspace):\n" + "\n".join(str(path) for path in references) + "\n"
        if manifest:
            context += "\n## Multi-repository task\n" + json.dumps(manifest, indent=2)
            context += "\nRead each worktree's AGENTS.md/CLAUDE.md before editing there. Use absolute paths or shell cd for secondary worktrees. Original repositories remain reference-only; only listed task worktrees are writable.\n"
        if handoff.exists():
            context += "\n## Task handoff\n" + handoff.read_text()
        command = [pi, "--no-approve", "--no-extensions", "-e", str(EXTENSION / "index.ts"), "-e", str(ASSETS / "exa.mjs"), "-e", str(ASSETS / "native-guard.mjs"), "-e", str(ASSETS / "project-handoff.mjs"), "--no-skills", "--skill", str(ASSETS / "skills/project-handoff/SKILL.md"), "--no-prompt-templates", "--session-dir", str(sessions), "--append-system-prompt", context]
        if args.resume:
            command += ["--continue"]
        prompt = args.prompt
        if prompt is None and args.new:
            prompt = "Carry out the task in the attached handoff using the project conventions and relevant vault evidence."
        if prompt:
            command.append(prompt)
        environment = {key: value for key, value in os.environ.items() if key in SAFE_ENV}
        environment.update({"PI_CODING_AGENT_DIR": str(runtime), "TMPDIR": str(scratch), "PI_TASK_HANDOFF_CONFIG": str(runtime / "handoff-config.json")})
        subprocess.run(command, cwd=checkout, env=environment, check=True)
        if (runtime / "handoff-approved.json").exists():
            request = validate_approval(json.loads((runtime / "handoff-approved.json").read_text()), handoff_config["repositories"], handoff_config["currentTask"])
            return request


def main():
    try:
        args = parser().parse_args()
        with ExitStack() as transitions:
            while True:
                with ExitStack() as stack:
                    request = run(args, stack)
                if not isinstance(request, dict):
                    break
                temporary = Path(transitions.enter_context(tempfile.TemporaryDirectory(prefix="pi-handoff-", dir=Path.home() / ".local/state/pi-task")))
                brief_path = temporary / "handoff.md"
                brief_path.write_text(request["brief"])
                args = transition_args(args, request, brief_path)
    except (ValueError, OSError, KeyError, StopIteration, subprocess.CalledProcessError) as error:
        print(f"pi-task: {error}", file=sys.stderr)
        sys.exit(1)
