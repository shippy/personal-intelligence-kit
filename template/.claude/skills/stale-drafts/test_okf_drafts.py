import sys
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import okf
import stale_drafts as mod


def test_empty_report_is_okf_conformant(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path if name == "root" else tmp_path / name)
    reporter = mod.StaleDraftsOrchestrator.__new__(mod.StaleDraftsOrchestrator)
    reporter.review_date = "2026-06-20"
    path = reporter._generate_empty_report()
    fm = okf.read_frontmatter(path)
    assert fm["type"] == "alert"
    assert fm["generated"]["by"] == "process:stale-drafts"
    assert (tmp_path / "log.md").exists()


def test_reports_record_llm_model(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path if name == "root" else tmp_path / name)
    reporter = mod.StaleDraftsOrchestrator.__new__(mod.StaleDraftsOrchestrator)
    reporter.review_date = "2026-06-20"
    reporter.analyzer = SimpleNamespace(stats={"total": 0, "non_empty": 0, "empty": 0}, posted=[])
    reporter.contexts = []
    reporter.result = SimpleNamespace(summary="s")
    reporter.enriched_result = None
    reporter.suggester = SimpleNamespace(model_name="m")
    reporter.data_sources_used = ["notes-drafts", "llm"]
    for path in reporter._generate_reports([], []):
        fm = okf.read_frontmatter(path)
        assert fm["generated"]["by"] == "stale-drafts/m"
        assert fm["sources"][0] == {"resource": "source:notes-drafts"}
