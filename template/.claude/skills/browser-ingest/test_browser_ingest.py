"""browser-ingest writes to the configured data dir and uses a private temp copy."""

import importlib.util
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import vault_config as vc  # noqa: E402

spec = importlib.util.spec_from_file_location("browser_ingest", Path(__file__).with_name("ingest.py"))
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)


def test_ingest_writes_into_content_root_without_fixed_tmp_file(tmp_path, monkeypatch):
    logic, content = tmp_path / "logic", tmp_path / "content"
    logic.mkdir()
    (logic / "vault.toml").write_text(
        f'[paths]\ncontent_root = "{content}"\n\n[sources.browser]\nenabled = true\ntype = "chrome"\n'
    )
    history = tmp_path / "History"
    with sqlite3.connect(history) as conn:
        conn.execute("CREATE TABLE urls (url TEXT, title TEXT, visit_count INT, last_visit_time INT)")
        conn.execute("INSERT INTO urls VALUES ('https://example.org/a', 'A', 3, 13300000000000000)")
    monkeypatch.setenv(vc.ENV_VAR, str(logic))
    vc.clear_cache()
    monkeypatch.setattr(ingest, "browser_history_path", lambda: history)
    monkeypatch.setattr(sys, "argv", ["ingest.py", "--all"])
    copies = []
    real_copy = ingest.shutil.copy2
    monkeypatch.setattr(ingest.shutil, "copy2", lambda s, d: copies.append(Path(d)) or real_copy(s, d))

    try:
        assert ingest.main() == 0
    finally:
        vc.clear_cache()

    out = content / "data" / "browser-history.db"
    with sqlite3.connect(out) as conn:
        assert conn.execute("SELECT count(*) FROM visits").fetchone()[0] == 1
    assert (content / "logs" / "activity.md").exists()
    assert not (logic / "data").exists()
    assert copies and not copies[0].exists()  # temp copy cleaned up
    assert copies[0].parent.name.startswith("pik_browser_ingest_")
