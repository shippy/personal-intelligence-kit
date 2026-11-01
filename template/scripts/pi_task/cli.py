import hashlib
import json
from pathlib import Path
import re
import subprocess


ASSETS = Path(__file__).resolve().parent


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE).strip()


def overlap(first, second):
    return first == second or first in second.parents or second in first.parents


def discover(cwd, vault, para):
    cwd = cwd.resolve(strict=True)
    if cwd == para or para in cwd.parents:
        raise ValueError("PARA is a reference source. Launch pi-task from the AI vault or an actioning project.")
    if cwd == vault or vault in cwd.parents:
        return "pkm", vault
    try:
        checkout = Path(git(cwd, "rev-parse", "--show-toplevel")).resolve()
    except subprocess.CalledProcessError as error:
        raise ValueError("Launch inside the AI vault or a Git project/worktree") from error
    if overlap(checkout, para) or overlap(checkout, vault):
        raise ValueError("Project root overlaps a vault; refusing a broad writable mount")
    return "project", checkout


def session_state(root, checkout):
    identity = hashlib.sha256(str(checkout).encode()).hexdigest()[:16]
    return root / f"{checkout.name}-{identity}"


def prepare(checkout, slug, state_root, handoff):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("Worktree name must be lowercase kebab-case")
    if not handoff:
        raise ValueError("--new requires --handoff FILE; prepare the brief before launching")
    brief = handoff.expanduser().resolve(strict=True).read_text()
    destination = checkout / ".pi/worktrees" / slug
    if destination.exists() or (checkout / ".pi").is_symlink() or (checkout / ".pi/worktrees").is_symlink():
        raise ValueError("Worktree already exists or its parent is a symlink")
    state = session_state(state_root, destination)
    if state.exists():
        raise ValueError("Task state already exists; nothing overwritten")
    base = git(checkout, "rev-parse", "--verify", "HEAD^{commit}")
    exclude = Path(git(checkout, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude"))
    exclude.parent.mkdir(parents=True, exist_ok=True)
    previous = exclude.read_text() if exclude.exists() else ""
    if "/.pi/worktrees/" not in previous.splitlines():
        with exclude.open("a") as stream:
            stream.write("\n/.pi/worktrees/\n")
    subprocess.run(["git", "-C", str(checkout), "worktree", "add", "-b", f"pi/{slug}", str(destination), base], check=True)
    state.mkdir(parents=True, mode=0o700)
    (state / "handoff.md").write_text(brief)
    (state / "origin.json").write_text(json.dumps({"repository": str(checkout), "base": base, "worktree": str(destination)}, indent=2))
    print(f"Prepared {destination}; uncommitted source files were not copied.")
    return destination
