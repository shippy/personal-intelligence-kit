#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Data Sync Orchestrator

Runs all sync workers for enabled sources in parallel. Reads vault.toml to
determine which workers to instantiate.

Usage:
    uv run sync_all.py
    uv run sync_all.py --yes
    uv run sync_all.py --skip email --skip browser
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILLS_DIR / "_lib"))
from vault_config import (  # noqa: E402
    load_config,
    source_enabled,
    email_path,
    activity_log,
    owner_name,
)


@dataclass
class SyncResult:
    name: str
    success: bool
    duration: float
    message: str


def run_subprocess(cmd: list[str], cwd: Path | None = None) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            check=False,
            timeout=1800,
        )
        if result.returncode == 0:
            return True, result.stdout.strip() or "OK"
        return False, (result.stderr or result.stdout).strip()[:500]
    except FileNotFoundError:
        return False, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return False, "timeout (30min)"
    except Exception as e:
        return False, f"error: {e}"


def sync_email() -> SyncResult:
    start = time.time()
    epath = email_path()
    if not epath:
        return SyncResult("email", False, 0, "no email path configured")

    # Run mbsync
    ok, msg = run_subprocess(["mbsync", "-a"])
    if not ok:
        return SyncResult("email", False, time.time() - start, f"mbsync: {msg}")

    # Run notmuch new
    ok, msg = run_subprocess(["notmuch", "new"])
    if not ok:
        return SyncResult("email", False, time.time() - start, f"notmuch: {msg}")

    return SyncResult("email", True, time.time() - start, "mbsync + notmuch complete")


def sync_browser() -> SyncResult:
    start = time.time()
    script = SKILLS_DIR / "browser-ingest" / "ingest.py"
    if not script.exists():
        return SyncResult("browser", False, 0, "browser-ingest skill not installed")
    ok, msg = run_subprocess(["uv", "run", str(script)], cwd=script.parent)
    return SyncResult("browser", ok, time.time() - start, msg)


def sync_journal() -> SyncResult:
    start = time.time()
    script = SKILLS_DIR / "journal-ingest" / "ingest.py"
    if not script.exists():
        return SyncResult("journal", False, 0, "journal-ingest skill not installed")
    ok, msg = run_subprocess(["uv", "run", str(script)], cwd=script.parent)
    return SyncResult("journal", ok, time.time() - start, msg)


def sync_tasks() -> SyncResult:
    start = time.time()
    script = SKILLS_DIR / "tasks-import" / "import_tasks.py"
    if not script.exists():
        return SyncResult("tasks", False, 0, "tasks-import skill not installed")
    ok, msg = run_subprocess(["uv", "run", str(script)], cwd=script.parent)
    return SyncResult("tasks", ok, time.time() - start, msg)


WORKER_MAP = {
    "email": sync_email,
    "browser": sync_browser,
    "journal": sync_journal,
    "tasks": sync_tasks,
}


def notify(title: str, message: str, success: bool):
    try:
        subprocess.run([
            "terminal-notifier",
            "-title", "Claude",
            "-subtitle", title,
            "-message", message,
            "-sound", "default" if success else "Basso",
            "-group", "claude-data-sync",
        ], check=False)
    except FileNotFoundError:
        pass  # Linux or no terminal-notifier


def main():
    parser = argparse.ArgumentParser(description="Data sync orchestrator")
    parser.add_argument("--yes", "-y", action="store_true", help="skip prompts")
    parser.add_argument("--skip", action="append", default=[], help="skip a source")
    args = parser.parse_args()

    load_config()

    # Determine which workers to run
    to_run = []
    for source_name, worker in WORKER_MAP.items():
        if source_name in args.skip:
            continue
        if source_enabled(source_name):
            to_run.append((source_name, worker))

    if not to_run:
        print("No enabled sources to sync.")
        return

    print(f"Syncing {len(to_run)} source(s) for {owner_name()}: {', '.join(n for n, _ in to_run)}")

    start = time.time()
    results: list[SyncResult] = []

    with ThreadPoolExecutor(max_workers=len(to_run)) as ex:
        futures = {ex.submit(worker): name for name, worker in to_run}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
            except Exception as e:
                result = SyncResult(name, False, 0, str(e))
            results.append(result)
            icon = "✓" if result.success else "✗"
            print(f"  {icon} {result.name} ({result.duration:.1f}s) — {result.message}")

    total = time.time() - start
    all_ok = all(r.success for r in results)
    summary = ", ".join(f"{r.name}:{'ok' if r.success else 'fail'}" for r in results)

    print(f"\nTotal: {total:.1f}s — {summary}")
    notify("Data Sync Complete" if all_ok else "Data Sync: errors", summary, all_ok)

    # Log
    log = activity_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(f"\n## {datetime.now().strftime('%Y-%m-%dT%H:%M')}\n\n")
        f.write("**Action:** Data sync\n")
        f.write(f"**Sources:** {', '.join(r.name for r in results)}\n")
        f.write(f"**Duration:** {total:.1f}s\n")
        f.write("**Results:**\n")
        for r in results:
            icon = "✓" if r.success else "✗"
            f.write(f"- {icon} {r.name} ({r.duration:.1f}s): {r.message}\n")
        f.write("\n---\n")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
