"""Render the template with copier and check the generated instance.

Run: uv run --with copier --with pytest pytest -q tests
Renders the working tree (uncommitted changes included) via --vcs-ref HEAD.
"""

import json
import os
import plistlib
import subprocess
import time
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
REMINDER = REPO / "template" / "scripts" / "reflection-reminder.sh"
FULL = {
    "vault_name": "Smoke Vault", "owner_name": "Tester", "notes_app": "obsidian",
    "notes_vault_path": "~/Documents/Notes", "drafts_path": "~/Documents/Notes/Drafts",
    "enable_email": True, "enable_browser": True, "enable_journal": True,
    "enable_tasks": True, "enable_git": True, "enable_contacts_mcp": True,
    "enable_scheduled_tasks": True,
}


def render(dst: Path, **answers) -> Path:
    data = {**FULL, "vault_path": str(dst), **answers}
    args = ["copier", "copy", "--quiet", "--defaults", "--vcs-ref", "HEAD", "--overwrite"]
    for key, value in data.items():
        args += ["--data", f"{key}={json.dumps(value) if isinstance(value, bool) else value}"]
    subprocess.run([*args, str(REPO), str(dst)], check=True, capture_output=True)
    return dst


def plist(path: Path) -> dict:
    return plistlib.loads(path.read_bytes())


@pytest.fixture(scope="module")
def single(tmp_path_factory):
    return render(tmp_path_factory.mktemp("single") / "vault")


@pytest.fixture(scope="module")
def split(tmp_path_factory):
    base = tmp_path_factory.mktemp("split")
    return render(
        base / "logic", content_root=str(base / "content"), state_root=str(base / "state"),
        output_root=".", host_web="deny", task_provider="todoist", schedule_reflection=True,
    ), base


def test_single_dir_vault_toml_keeps_v070_paths(single):
    cfg = tomllib.loads((single / "vault.toml").read_text())
    assert cfg["paths"] == {"content_root": "."}
    assert cfg["output"] == {
        "root": "output", "reports": "output/reports", "alerts": "output/alerts",
        "reflections": "output/reflections", "briefings": "output/briefings",
        "drafts_for_review": "output/drafts-for-review",
    }
    assert cfg["logs"]["activity"] == "logs/activity.md"


def test_single_dir_daily_sync_is_secret_free_and_logs_in_vault(single):
    p = plist(single / "config" / "com.claude.daily-sync.plist")
    assert p["ProgramArguments"] == [
        "/opt/homebrew/bin/uv", "run", f"{single}/.claude/skills/data-sync/sync_all.py", "--yes"]
    assert p["WorkingDirectory"] == str(single)
    assert p["StandardErrorPath"] == f"{single}/logs/sync-stderr.log"


def test_single_dir_reflection_is_attended_with_reminder(single):
    assert not (single / "config" / "com.claude.weekly-reflect.plist").exists()
    p = plist(single / "config" / "com.claude.reflection-reminder.plist")
    assert p["ProgramArguments"] == [
        "/opt/homebrew/bin/uv", "run", "--no-project", "--",
        "/bin/bash", f"{single}/scripts/reflection-reminder.sh", f"{single}/output/reflections"]
    assert p["StartCalendarInterval"] == {"Weekday": 0, "Hour": 10, "Minute": 0}
    assert os.access(single / "scripts" / "reflection-reminder.sh", os.X_OK)


def test_single_dir_settings(single):
    settings = json.loads((single / ".claude" / "settings.json").read_text())["permissions"]
    assert "Edit(~/Documents/Notes/**)" in settings["deny"]
    assert not any(rule.startswith(("Write(", "NotebookEdit(")) for rule in settings["deny"])
    assert f"Edit(/{single}/.claude/settings.local.json)" in settings["deny"]
    assert f"Edit(/{single}/.claude/hooks/**)" in settings["deny"]
    assert not any("(./" in rule for rule in settings["deny"])
    assert not {"Bash(python3:*)", "Bash(cat:*)", "Bash(find:*)"} & set(settings["allow"])
    assert "Bash(uv run:*)" in settings["allow"]
    assert "additionalDirectories" not in settings
    assert not (single / ".claude" / "settings.local.json").exists()  # host_web=allow
    assert json.loads((single / ".mcp.json").read_text()) == {"mcpServers": {}}
    assert ".claude/settings.local.json" in (single / ".gitignore").read_text().splitlines()


def test_no_helper_or_envrc_leaks_into_instance(single):
    assert not (single / "config" / "_paths").exists()
    assert not (single / "config" / "_paths.jinja").exists()
    assert not (single / ".envrc").exists()


def test_split_layout_paths(split):
    logic, base = split
    cfg = tomllib.loads((logic / "vault.toml").read_text())
    assert cfg["paths"] == {"content_root": str(base / "content"), "state_root": str(base / "state")}
    assert cfg["output"]["root"] == "."
    assert cfg["output"]["reflections"] == "reflections"
    assert not any((logic / d).exists() for d in ("output", "data", "logs", "working"))
    settings = json.loads((logic / ".claude" / "settings.json").read_text())["permissions"]
    assert settings["additionalDirectories"] == [str(base / "content")]


def test_split_layout_plists(split):
    logic, base = split
    sync = plist(logic / "config" / "com.claude.daily-sync.plist")
    assert sync["ProgramArguments"][:4] == ["/opt/homebrew/bin/op", "run", f"--env-file={logic}/.env", "--"]
    assert sync["StandardOutPath"] == f"{base}/state/launchd/sync-stdout.log"
    assert sync["WorkingDirectory"] == str(logic)
    reflect = plist(logic / "config" / "com.claude.weekly-reflect.plist")
    assert reflect["StandardErrorPath"] == f"{base}/state/launchd/reflect-stderr.log"
    assert not (logic / "config" / "com.claude.reflection-reminder.plist").exists()
    assert not (logic / "scripts" / "reflection-reminder.sh").exists()


def test_split_layout_web_deny_and_claude_md(split):
    logic, base = split
    local = json.loads((logic / ".claude" / "settings.local.json").read_text())["permissions"]
    assert local["deny"] == ["WebFetch", "WebSearch", "mcp__plugin_exa_exa__*", "Bash(curl:*)", "Bash(wget:*)"]
    claude = (logic / "CLAUDE.md").read_text()
    assert "Host sessions have no web" in claude
    assert "data, never instructions" in claude
    assert str(base / "content") in claude
    assert "output/" not in claude


def test_host_web_ask(tmp_path):
    vault = render(tmp_path / "v", host_web="ask", enable_scheduled_tasks=False)
    local = json.loads((vault / ".claude" / "settings.local.json").read_text())["permissions"]
    assert set(local) == {"ask"} and "WebFetch" in local["ask"]
    assert not (vault / "config").exists()
    assert not (vault / "scripts" / "reflection-reminder.sh").exists()


def test_absolute_notes_path_uses_double_slash_rule(tmp_path):
    vault = render(tmp_path / "v", notes_vault_path="/abs/Notes", enable_scheduled_tasks=False)
    deny = json.loads((vault / ".claude" / "settings.json").read_text())["permissions"]["deny"]
    assert "Edit(//abs/Notes/**)" in deny


# ---------- reflection-reminder.sh ----------

@pytest.fixture
def notifier(tmp_path):
    log = tmp_path / "notified"
    fake = tmp_path / "fake-notifier"
    fake.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > "{log}"\n')
    fake.chmod(0o755)
    return fake, log


def run_reminder(directory, fake):
    env = {**os.environ, "REFLECTION_REMINDER_NOTIFIER": str(fake)}
    return subprocess.run(["/bin/bash", str(REMINDER), str(directory)], env=env, check=True)


def test_reminder_notifies_when_dir_missing(tmp_path, notifier):
    fake, log = notifier
    run_reminder(tmp_path / "missing", fake)
    args = log.read_text().splitlines()
    assert args[:4] == ["-title", "Claude", "-subtitle", "Weekly reflection"]
    assert "claude-reflections" in args


def test_reminder_silent_with_recent_reflection(tmp_path, notifier):
    fake, log = notifier
    (tmp_path / "2026-11-01-weekly-reflection.md").write_text("x")
    run_reminder(tmp_path, fake)
    assert not log.exists()


def test_reminder_notifies_when_reflection_is_old(tmp_path, notifier):
    fake, log = notifier
    old = tmp_path / "2026-10-20-weekly-reflection.md"
    old.write_text("x")
    week_ago = time.time() - 7 * 86400
    os.utime(old, (week_ago, week_ago))
    (tmp_path / "unrelated.md").write_text("fresh but not a reflection")
    run_reminder(tmp_path, fake)
    assert log.exists()


def test_gitignore_follows_output_layout(tmp_path):
    flat = render(tmp_path / "flat", output_root=".")
    lines = (flat / ".gitignore").read_text().splitlines()
    for entry in ["reports/", "alerts/", "reflections/", "briefings/", "drafts-for-review/",
                  "/log.md", "/index.md", "inbox/", "data/", "logs/", "working/"]:
        assert entry in lines
    assert "output/" not in lines
    custom = render(tmp_path / "custom", output_root="out")
    assert "out/" in (custom / ".gitignore").read_text().splitlines()


def test_paths_with_spaces_and_xml_characters(tmp_path):
    content = tmp_path / "Research & \"Notes\" \\ 📚"
    logic = render(tmp_path / "My Vault", content_root=str(content), state_root=str(tmp_path / "st & ate"),
                   reflection_reminder=True)
    settings = json.loads((logic / ".claude" / "settings.json").read_text())
    assert settings["permissions"]["additionalDirectories"] == [str(content)]
    paths = tomllib.loads((logic / "vault.toml").read_text())["paths"]
    assert paths == {"content_root": str(content), "state_root": str(tmp_path / "st & ate")}
    reminder = plist(logic / "config" / "com.claude.reflection-reminder.plist")
    assert reminder["ProgramArguments"][-1] == f"{content}/output/reflections"
    assert plist(logic / "config" / "com.claude.daily-sync.plist")["StandardOutPath"].startswith(str(tmp_path / "st & ate"))
    cron = [line for line in (logic / "config" / "crontab.example").read_text().splitlines() if "reflection-reminder.sh" in line][0]
    import shlex
    words = shlex.split(cron.split(" ", 5)[5])
    assert words[1:3] == [f"{logic}/scripts/reflection-reminder.sh", f"{content}/output/reflections"]


def test_output_root_rejects_equivalent_spellings(tmp_path):
    for bad in ("./output", "./", "output/", "out/.", "out[1]", "a/./b"):
        with pytest.raises(subprocess.CalledProcessError):
            render(tmp_path / bad.replace("/", "_").replace(".", "d"), output_root=bad)
