import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import okf
import intention_reality_gaps as mod


def test_intention_report_is_okf_conformant(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)

    class FakeAnalyzer:
        def find_yearly_goals(self):
            return None

    monkeypatch.setattr(mod, "IntentionRealityAnalyzer", lambda: FakeAnalyzer())
    path = mod.generate_report()
    assert okf.read_frontmatter(path)["type"] == "report"
    assert (tmp_path / "log.md").exists()
