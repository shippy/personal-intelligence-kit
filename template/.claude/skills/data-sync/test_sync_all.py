"""data-sync logs one summary line per run to sync.jsonl."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import vault_config as vc  # noqa: E402

spec = importlib.util.spec_from_file_location("sync_all", Path(__file__).with_name("sync_all.py"))
sync_all = importlib.util.module_from_spec(spec)
sys.modules["sync_all"] = sync_all
spec.loader.exec_module(sync_all)


def test_run_writes_summary_line(tmp_path, monkeypatch):
    (tmp_path / "vault.toml").write_text("[vault]\nowner = \"T\"\n")
    monkeypatch.setenv(vc.ENV_VAR, str(tmp_path))
    vc.clear_cache()
    monkeypatch.setattr(sync_all, "WORKER_MAP", {
        "journal": lambda: sync_all.SyncResult("journal", True, 1.5, "✓ 3 nových"),
        "email": lambda: sync_all.SyncResult("email", False, 0.5, "boom"),
    })
    monkeypatch.setattr(sync_all, "source_enabled", lambda s: True)
    monkeypatch.setattr(sync_all, "notify", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["sync_all.py", "--yes"])
    try:
        with pytest.raises(SystemExit) as exit_:
            sync_all.main()
    finally:
        vc.clear_cache()
    assert exit_.value.code == 1
    obj = json.loads((tmp_path / "logs" / "sync.jsonl").read_text().splitlines()[-1])
    assert obj["job"] == "data-sync"
    assert obj["ok"] is False
    assert obj["stats"]["sources"]["journal"] == {"ok": True, "duration_s": 1.5, "message": "✓ 3 nových"}
    assert obj["stats"]["sources"]["email"]["ok"] is False
    assert not (tmp_path / "logs" / "activity").exists()
