from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import re
import subprocess

from .cli import git, overlap


def task_directory(state_root, name):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise ValueError("Task name must be lowercase kebab-case")
    return state_root / "tasks" / name


@contextmanager
def task_lock(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Task is already open; exit its Pi session before changing repositories or resuming") from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def resolve_repository(value, references, vault, para, state_root):
    matches = [path for path in references if path.name == value]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous repository name: {value}; use an absolute path")
    repository = matches[0] if matches else Path(value).expanduser().resolve(strict=True)
    repository = repository.resolve(strict=True)
    if any(overlap(repository, protected) for protected in (vault, para, state_root)):
        raise ValueError(f"Repository overlaps protected vault/state: {repository}")
    if Path(git(repository, "rev-parse", "--show-toplevel")).resolve() != repository:
        raise ValueError(f"Use a repository root: {repository}")
    return repository


def save_manifest(directory, manifest):
    temporary = directory / "manifest.pending"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n")
    temporary.replace(directory / "manifest.json")


def load_manifest(directory, vault, para, state_root):
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("version") != 1 or manifest.get("name") != directory.name or not isinstance(manifest.get("repositories"), list):
        raise ValueError("Invalid task manifest")
    roots = []
    repositories = set()
    for entry in manifest["repositories"]:
        repository = resolve_repository(entry["repository"], [], vault, para, state_root)
        expected = repository / ".pi/worktrees" / directory.name
        checkout = Path(entry["worktree"])
        if checkout != expected or checkout.resolve(strict=True) != expected:
            raise ValueError(f"Unexpected or symlinked task worktree: {checkout}")
        if Path(git(checkout, "rev-parse", "--show-toplevel")).resolve() != checkout:
            raise ValueError(f"Task checkout is no longer a worktree: {checkout}")
        if git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir") != git(repository, "rev-parse", "--path-format=absolute", "--git-common-dir"):
            raise ValueError("Task worktree belongs to a different repository")
        if repository in repositories or any(overlap(checkout, root) for root in roots):
            raise ValueError("Duplicate or overlapping task worktrees")
        repositories.add(repository)
        roots.append(checkout)
    if not roots:
        raise ValueError("Task has no completed worktrees; recreate or inspect its manifest before proceeding")
    return manifest


def add_repositories(directory, manifest, repositories):
    existing = {entry["repository"] for entry in manifest["repositories"]}
    plans = []
    for repository in dict.fromkeys(repositories):
        if str(repository) in existing:
            raise ValueError(f"Repository already belongs to this task: {repository}")
        destination = repository / ".pi/worktrees" / manifest["name"]
        if destination.exists() or destination.is_symlink() or any(parent.is_symlink() for parent in (repository / ".pi", repository / ".pi/worktrees")):
            raise ValueError(f"Worktree path exists or is symlinked: {destination}")
        branch = f"pi/{manifest['name']}"
        if git(repository, "branch", "--list", branch):
            raise ValueError(f"Branch already exists: {repository}: {branch}")
        base = git(repository, "rev-parse", "--verify", "HEAD^{commit}")
        plans.append((repository, destination, branch, base))
    for repository, destination, branch, base in plans:
        exclude = Path(git(repository, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude"))
        exclude.parent.mkdir(parents=True, exist_ok=True)
        if not exclude.exists() or "/.pi/worktrees/" not in exclude.read_text().splitlines():
            with exclude.open("a") as stream:
                stream.write("\n/.pi/worktrees/\n")
        subprocess.run(["git", "-C", str(repository), "worktree", "add", "-b", branch, str(destination), base], check=True)
        manifest["repositories"].append({"repository": str(repository), "worktree": str(destination), "branch": branch, "base": base})
        save_manifest(directory, manifest)
        print(f"Task worktree: {destination}", flush=True)
