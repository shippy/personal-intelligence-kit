import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import okf
import stale_drafts as mod


def test_empty_report_is_okf_conformant(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)
    reporter = mod.StaleDraftsOrchestrator.__new__(mod.StaleDraftsOrchestrator)
    reporter.review_date = "2026-06-20"
    path = reporter._generate_empty_report()
    assert okf.read_frontmatter(path)["type"] == "alert"
    assert (tmp_path / "log.md").exists()
