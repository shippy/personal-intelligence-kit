import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import okf


def test_render_frontmatter_quotes_strings_and_skips_none():
    block = okf.render_frontmatter(
        {"type": "alert", "title": "Health: 2026", "resource": None, "tags": ["a", "b"]}
    )
    assert block == (
        '---\n'
        'type: "alert"\n'
        'title: "Health: 2026"\n'
        'tags: ["a", "b"]\n'
        '---\n'
        '\n'
    )


def test_render_frontmatter_escapes_quotes():
    block = okf.render_frontmatter({"type": 'a"b'})
    assert 'type: "a\\"b"' in block
