import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import okf
import serendipity_convergence as mod


def test_convergence_report_is_okf_conformant(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)
    monkeypatch.setattr(mod, "activity_log", lambda: tmp_path / "activity.md")

    class FakeAnalyzer:
        def detect_topic_convergence(self, days_back, min_sources):
            return []

        def detect_person_convergence(self, days_back):
            return []

    monkeypatch.setattr(mod, "SerendipityConvergenceAnalyzer", lambda: FakeAnalyzer())
    path = mod.generate_report()
    assert okf.read_frontmatter(path)["type"] == "report"
    assert (tmp_path / "log.md").exists()
