import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import okf
import analyze


def test_session_docs_are_okf_conformant(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)
    paths = analyze.write_session_docs(
        active=[], dormant=[], cluster_stats={}, date="2026-06-20", dormant_days=14
    )
    assert len(paths) == 2
    types = {okf.read_frontmatter(p)["type"] for p in paths}
    assert types == {"alert", "report"}
    assert (tmp_path / "log.md").exists()
