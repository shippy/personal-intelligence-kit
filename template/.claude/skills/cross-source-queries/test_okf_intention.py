import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import activity
import okf
import intention_reality_gaps as mod


def test_intention_report_is_okf_conformant(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path if name == "root" else tmp_path / name)

    class FakeAnalyzer:
        def find_yearly_goals(self):
            return None

    monkeypatch.setattr(mod, "IntentionRealityAnalyzer", lambda: FakeAnalyzer())
    path = mod.generate_report()
    fm = okf.read_frontmatter(path)
    assert fm["type"] == "report"
    assert fm["generated"]["by"] == "process:intention-reality-gaps"
    assert (tmp_path / "log.md").exists()


def test_intention_llm_path_records_model(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path if name == "root" else tmp_path / name)
    monkeypatch.setattr(activity, "activity_log", lambda when=None: tmp_path / "activity.md")

    class FakeAnalyzer:
        def find_yearly_goals(self):
            return {"goals": []}

        def find_journal_intentions(self, days_back):
            return []

    class FakeAssessor:
        model_name = "fake-model"

        @staticmethod
        def available():
            return True

        def assess(self, goals, analyzer):
            return object()

    monkeypatch.setattr(mod, "IntentionRealityAnalyzer", lambda: FakeAnalyzer())
    monkeypatch.setattr(mod, "LLMAssessor", FakeAssessor)
    monkeypatch.setattr(mod, "_render_llm_report", lambda goals_data, report, date: "# r\n")
    monkeypatch.setattr(mod, "_print_terse_summary", lambda report: None)
    monkeypatch.setattr(mod, "content_link", lambda p: Path(p).stem)
    fm = okf.read_frontmatter(mod.generate_report())
    assert fm["generated"]["by"] == "intention-reality-gaps/fake-model"
    assert {"resource": "source:notes"} in fm["sources"]
