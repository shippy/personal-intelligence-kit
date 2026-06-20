import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import okf
import output_formatter
from output_formatter import ReflectionFormatter
from llm_synthesizer import WeeklyReflection


def _min_reflection():
    return WeeklyReflection(
        opening_observation="A quiet, focused week.",
        themes=[], tensions=[], commitments=[],
        notable_moments=[], reflection_questions=[],
    )


def test_reflection_is_okf_conformant(tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "output_dir", lambda name="root": tmp_path)
    monkeypatch.setattr(output_formatter, "owner_name", lambda: "Alex")
    fmt = ReflectionFormatter()
    body = fmt.format(_min_reflection())
    path = fmt.save(body)
    fm = okf.read_frontmatter(path)
    assert fm["type"] == "reflection"
    assert fm["title"]
    assert (tmp_path / "log.md").exists()
    assert not body.startswith("---")  # frontmatter no longer in the body
