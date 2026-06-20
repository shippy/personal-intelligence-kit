import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import okf
import commitment_accountability as mod


def test_commitment_report_is_okf_conformant(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)
    monkeypatch.setattr(mod, "source_enabled", lambda s: True)
    monkeypatch.setattr(mod, "activity_log", lambda: tmp_path / "activity.md")

    class FakeAnalyzer:
        def analyze(self, days_back):
            return {"total": 0, "followed_up": 0, "no_follow_up": 0, "details": []}

    monkeypatch.setattr(mod, "CommitmentAccountabilityAnalyzer", lambda: FakeAnalyzer())
    path = mod.generate_report()
    assert okf.read_frontmatter(path)["type"] == "alert"
    assert (tmp_path / "log.md").exists()
