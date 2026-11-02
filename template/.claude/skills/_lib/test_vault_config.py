import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import okf
import vault_config as vc

# vault.toml exactly as v0.7.0 renders it (all sources on, Obsidian notes).
V070_TOML = """\
[vault]
name = "Test Vault"
owner = "Tester"

[notes]
app = "obsidian"
vault_path = "{notes}"
drafts_path = "{notes}/Drafts"

[sources]

[sources.email]
enabled = true
path = "~/data/email"
provider = "notmuch"

[sources.browser]
enabled = true
type = "vivaldi"

[sources.journal]
enabled = true
type = "rosebud"
path = "~/data/journal"

[sources.tasks]
enabled = true
provider = "microsoft-todo"
db_path = "~/data/tasks"

[sources.git]
enabled = true

[output]
root = "output"
reports = "output/reports"
alerts = "output/alerts"
reflections = "output/reflections"
briefings = "output/briefings"
drafts_for_review = "output/drafts-for-review"

[logs]
root = "logs"
activity = "logs/activity.md"
errors = "logs/errors.md"
"""


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.delenv(vc.ENV_VAR, raising=False)
    vc.clear_cache()
    yield
    vc.clear_cache()


def _write(root: Path, text: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "vault.toml").write_text(text)
    return root / "vault.toml"


def test_env_override_wins_over_cwd(tmp_path, monkeypatch):
    a = _write(tmp_path / "a", '[vault]\nname = "A"\n')
    _write(tmp_path / "b", '[vault]\nname = "B"\n')
    monkeypatch.chdir(tmp_path / "b")
    monkeypatch.setenv(vc.ENV_VAR, str(a))
    assert vc.vault_name() == "A"
    assert vc.logic_root() == (tmp_path / "a").resolve()


def test_env_override_accepts_directory(tmp_path, monkeypatch):
    _write(tmp_path / "a", '[vault]\nname = "A"\n')
    monkeypatch.setenv(vc.ENV_VAR, str(tmp_path / "a"))
    assert vc.vault_name() == "A"


def test_env_override_missing_file_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv(vc.ENV_VAR, str(tmp_path / "nope.toml"))
    with pytest.raises(vc.VaultConfigError):
        vc.load_config()


def test_config_found_from_module_location_regardless_of_cwd(tmp_path, monkeypatch):
    repo = tmp_path / "logic"
    _write(repo, '[vault]\nname = "Logic"\n')
    lib = repo / ".claude" / "skills" / "_lib"
    lib.mkdir(parents=True)
    monkeypatch.setattr(vc, "__file__", str(lib / "vault_config.py"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert vc.vault_name() == "Logic"
    assert vc.logic_root() == repo.resolve()


def test_cwd_walk_up_is_the_last_fallback(tmp_path, monkeypatch):
    _write(tmp_path, '[vault]\nname = "Walk"\n')
    (tmp_path / "x" / "y").mkdir(parents=True)
    monkeypatch.setattr(vc, "__file__", str(tmp_path / "nowhere" / "a" / "b" / "c" / "vault_config.py"))
    monkeypatch.chdir(tmp_path / "x" / "y")
    assert vc.vault_name() == "Walk"


def test_v070_toml_reproduces_single_dir_paths(tmp_path, monkeypatch):
    notes = tmp_path / "Notes"
    root = tmp_path / "vault"
    _write(root, V070_TOML.format(notes=notes))
    monkeypatch.setenv(vc.ENV_VAR, str(root / "vault.toml"))
    r = root.resolve()
    assert vc.vault_root() == r
    assert vc.content_root() == r
    assert not vc.is_split_layout()
    assert vc.journal_db_path() == r / "data" / "journal.db"
    assert vc.browser_db_path() == r / "data" / "browser-history.db"
    assert vc.tasks_db_path() == r / "data" / "tasks.db"
    assert vc.git_commits_db_path() == r / "data" / "git-commits.db"
    assert vc.state_dir() == r / "data"
    assert vc.output_dir() == r / "output"
    assert vc.output_dir("reports") == r / "output" / "reports"
    assert vc.output_dir("unknown") == r / "output" / "unknown"
    assert vc.logs_dir() == r / "logs"
    assert vc.activity_log() == r / "logs" / "activity.md"
    assert vc.working_dir() == r / "working"
    assert vc.notes_vault_path() == notes.resolve()
    assert vc.drafts_path() == (notes / "Drafts").resolve()


def test_split_layout_resolves_content_and_state(tmp_path, monkeypatch):
    logic, content, state = tmp_path / "logic", tmp_path / "content", tmp_path / "state"
    _write(logic, f"""
[paths]
content_root = "{content}"
state_root = "{state}"
notes_root = "{tmp_path / 'Notes'}"

[notes]
app = "obsidian"

[sources.journal]
enabled = true

[sources.tasks]
enabled = true

[data]
journal = "elsewhere/j.db"

[output]
root = "."
reflections = "reflections"
""")
    monkeypatch.setenv(vc.ENV_VAR, str(logic))
    c = content.resolve()
    assert vc.logic_root() == logic.resolve()
    assert vc.content_root() == c
    assert vc.is_split_layout()
    assert vc.state_dir() == state.resolve()
    assert vc.data_dir() == c / "data"
    assert vc.journal_db_path() == c / "elsewhere" / "j.db"
    assert vc.tasks_db_path() == c / "data" / "tasks.db"
    assert vc.output_dir() == c
    assert vc.output_dir("reflections") == c / "reflections"
    assert vc.output_dir("alerts") == c / "alerts"
    assert vc.inbox_dir() == c / "inbox"
    assert vc.activity_log() == c / "logs" / "activity.md"
    assert vc.notes_vault_path() == (tmp_path / "Notes").resolve()


def test_relative_content_root_resolves_against_logic_root(tmp_path, monkeypatch):
    _write(tmp_path / "logic", '[paths]\ncontent_root = "../content"\n')
    monkeypatch.setenv(vc.ENV_VAR, str(tmp_path / "logic"))
    assert vc.content_root() == (tmp_path / "content").resolve()


def _guarded(tmp_path, monkeypatch, extra=""):
    notes = tmp_path / "Notes"
    notes.mkdir()
    _write(tmp_path / "v", f'[notes]\napp = "obsidian"\nvault_path = "{notes}"\n{extra}')
    monkeypatch.setenv(vc.ENV_VAR, str(tmp_path / "v"))
    return notes


def test_guard_rejects_notes_paths_and_symlinks_into_notes(tmp_path, monkeypatch):
    notes = _guarded(tmp_path, monkeypatch)
    with pytest.raises(vc.VaultConfigError):
        vc.assert_writable(notes / "x.md")
    with pytest.raises(vc.VaultConfigError):
        vc.assert_writable(notes)
    link = tmp_path / "v" / "sneaky"
    link.symlink_to(notes)
    with pytest.raises(vc.VaultConfigError):
        vc.safe_write(link / "x.md", "hi")
    assert not (notes / "x.md").exists()


def test_guard_allows_content_paths_and_sibling_prefixes(tmp_path, monkeypatch):
    _guarded(tmp_path, monkeypatch)
    out = vc.safe_write(tmp_path / "v" / "output" / "a.md", "one\n")
    vc.safe_write(out, "two\n", append=True)
    assert out.read_text() == "one\ntwo\n"
    vc.assert_writable(tmp_path / "Notes-sibling" / "x.md")


def test_activity_log_inside_notes_is_refused(tmp_path, monkeypatch):
    notes = _guarded(tmp_path, monkeypatch, f'[logs]\nactivity = "{tmp_path}/Notes/activity.md"\n')
    with pytest.raises(vc.VaultConfigError):
        vc.activity_log()
    assert not (notes / "activity.md").exists()


def test_write_concept_refuses_notes_out_dir(tmp_path, monkeypatch):
    notes = _guarded(tmp_path, monkeypatch)
    with pytest.raises(vc.VaultConfigError):
        okf.write_concept("x", "demo", type="t", title="T", body="b", out_dir=notes / "out")
    assert not (notes / "out").exists()


def test_no_skill_joins_vault_root_with_data():
    skills = Path(__file__).resolve().parents[1]
    offenders = [
        str(p.relative_to(skills))
        for p in skills.rglob("*.py")
        if not p.name.startswith("test_") and 'vault_root() / "data"' in p.read_text()
    ]
    assert offenders == []


def test_content_link_follows_output_root(tmp_path, monkeypatch):
    _write(tmp_path / "v", '[output]\nroot = "output"\n')
    monkeypatch.setenv(vc.ENV_VAR, str(tmp_path / "v"))
    assert vc.content_link(vc.output_dir("reports") / "x-2026.md") == "output/reports/x-2026"
    vc.clear_cache()
    _write(tmp_path / "v", f'[paths]\ncontent_root = "{tmp_path / "c"}"\n\n[output]\nroot = "."\n')
    assert vc.content_link(vc.output_dir("reports") / "x-2026.md") == "reports/x-2026"
    assert vc.content_link("/elsewhere/file.md") == "/elsewhere/file.md"


def test_relative_notes_root_resolves_against_logic(tmp_path, monkeypatch):
    logic = tmp_path / "logic"
    _write(logic, """
[paths]
content_root = "../content"
notes_root = "Notes"
""")
    monkeypatch.setenv(vc.ENV_VAR, str(logic))
    notes = (logic / "Notes").resolve()
    assert vc.notes_root() == notes
    with pytest.raises(vc.VaultConfigError):
        vc.assert_writable(notes / "x.md")
    vc.assert_writable(tmp_path / "content" / "Notes" / "x.md")


def test_relative_notes_vault_path_resolves_against_content(tmp_path, monkeypatch):
    logic = tmp_path / "logic"
    _write(logic, """
[paths]
content_root = "../content"

[notes]
vault_path = "Notes"
""")
    monkeypatch.setenv(vc.ENV_VAR, str(logic))
    assert vc.notes_root() == (tmp_path / "content" / "Notes").resolve()


def test_write_concept_refuses_symlinked_file_and_changelog(tmp_path, monkeypatch):
    notes = _guarded(tmp_path, monkeypatch)
    victim = notes / "victim.md"
    victim.write_text("keep\n")
    out = tmp_path / "v" / "output" / "reports"
    out.mkdir(parents=True)
    (out / "x.md").symlink_to(victim)
    with pytest.raises(vc.VaultConfigError):
        okf.write_concept("reports", "x", type="t", title="T", body="b")
    (out / "x.md").unlink()
    (tmp_path / "v" / "output" / "log.md").symlink_to(victim)
    with pytest.raises(vc.VaultConfigError):
        okf.write_concept("reports", "y", type="t", title="T", body="b")
    assert victim.read_text() == "keep\n"


def test_database_and_state_paths_inside_notes_are_refused(tmp_path, monkeypatch):
    notes = _guarded(tmp_path, monkeypatch, f'[sources.browser]\nenabled = true\n[data]\nbrowser = "{tmp_path}/Notes/b.db"\n[paths]\nstate_root = "{tmp_path}/Notes/state"\n')
    assert vc.browser_db_path() == (notes / "b.db").resolve()  # readers may still open it
    with pytest.raises(vc.VaultConfigError):
        vc.assert_writable(vc.browser_db_path())  # writers guard it
    with pytest.raises(vc.VaultConfigError):
        vc.state_dir()
    assert notes.exists()
