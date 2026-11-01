import argparse
import re
from pathlib import Path


def validate_approval(request, eligible, current_task):
    if not isinstance(request, dict) or set(request) != {"action", "task", "repositories", "brief"}:
        raise ValueError("Invalid handoff approval")
    action, task = request["action"], request["task"]
    if action not in ("create", "add") or not isinstance(task, str) or len(task) > 64 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", task):
        raise ValueError("Invalid approved task/action")
    if action == "add" and task != current_task:
        raise ValueError("Approval does not target the current task")
    repositories = request["repositories"]
    if not isinstance(repositories, list) or not 1 <= len(repositories) <= 8 or any(not isinstance(path, str) or path not in eligible for path in repositories) or len(set(repositories)) != len(repositories):
        raise ValueError("Approved repositories are outside the eligible set or duplicated")
    if not isinstance(request["brief"], str) or not request["brief"].strip() or len(request["brief"]) > 64000:
        raise ValueError("Invalid approved brief")
    return request


def transition_args(previous, request, brief_path):
    return argparse.Namespace(
        vault=previous.vault, cwd=Path(request["repositories"][0]),
        new=request["task"] if request["action"] == "create" else None,
        task=request["task"] if request["action"] == "add" else None,
        repo=request["repositories"] if request["action"] == "create" else [],
        add_repo=request["repositories"] if request["action"] == "add" else [],
        handoff=brief_path, prompt=None, prepare_only=False, resume=False,
        describe=False, check=False, check_model=False,
    )
