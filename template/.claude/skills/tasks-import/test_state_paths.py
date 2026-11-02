"""ms_auth_record.json lives in state_root, with a one-time copy from data/."""

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import vault_config as vc  # noqa: E402

spec = importlib.util.spec_from_file_location("todo_state", Path(__file__).with_name("import_tasks.py"))
todo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(todo)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    def make(extra=""):
        logic = tmp_path / "logic"
        logic.mkdir(exist_ok=True)
        (logic / "vault.toml").write_text(f'[sources.tasks]\nenabled = true\n{extra}')
        monkeypatch.setenv(vc.ENV_VAR, str(logic))
        vc.clear_cache()
        return logic
    yield make
    vc.clear_cache()


def test_legacy_layout_keeps_record_in_data(vault):
    logic = vault()
    assert todo._ms_auth_record_path() == logic.resolve() / "data" / "ms_auth_record.json"
    assert todo.tasks_db_path() == logic.resolve() / "data" / "tasks.db"


def test_state_root_record_is_copied_once_from_legacy_location(vault, tmp_path):
    content, state = tmp_path / "content", tmp_path / "state"
    vault(f'[paths]\ncontent_root = "{content}"\nstate_root = "{state}"\n')
    legacy = content / "data" / "ms_auth_record.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"old": true}')

    assert todo._ms_auth_record_path() == state.resolve() / "ms_auth_record.json"
    assert todo._load_ms_auth_record_text() == '{"old": true}'
    assert (state / "ms_auth_record.json").read_text() == '{"old": true}'
    assert legacy.exists()  # left for the user to remove

    (state / "ms_auth_record.json").write_text('{"new": true}')
    assert todo._load_ms_auth_record_text() == '{"new": true}'


def test_missing_record_returns_none(vault, tmp_path):
    vault(f'[paths]\nstate_root = "{tmp_path / "state"}"\n')
    assert todo._load_ms_auth_record_text() is None
