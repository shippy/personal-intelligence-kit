"""Create-only Microsoft To Do writer: preview, dedup, list matching, failures, auth."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "_lib"))
import vault_config as vc  # noqa: E402

spec = importlib.util.spec_from_file_location("todo_add", HERE / "add_tasks.py")
add = importlib.util.module_from_spec(spec)
spec.loader.exec_module(add)
todo = add.todo

LISTS = [
    {"id": "soc", "displayName": "Social"},
    {"id": "work", "displayName": "Work"},
    {"id": "w2", "displayName": "work "},
    {"id": "home", "displayName": "Home"},
]


@pytest.fixture
def vault(tmp_path, monkeypatch):
    logic = tmp_path / "logic"
    logic.mkdir()
    (logic / "vault.toml").write_text(
        f'[sources.tasks]\nenabled = true\nprovider = "microsoft-todo"\n'
        f'[paths]\nstate_root = "{tmp_path / "state"}"\n'
    )
    monkeypatch.setenv(vc.ENV_VAR, str(logic))
    vc.clear_cache()
    yield logic
    vc.clear_cache()


@pytest.fixture
def graph(vault, monkeypatch):
    """Fake Graph: lists, open tasks per list, and a record of POSTs."""
    state = SimpleNamespace(open={"soc": ["Existing task"]}, posts=[], gets=[], fail_on=None)

    def get(credential, endpoint, **kwargs):
        assert kwargs == {"scope": "Tasks.ReadWrite"}
        state.gets.append(endpoint)
        if endpoint == "/me/todo/lists":
            return {"value": LISTS}
        list_id = endpoint.split("/")[4].split("?")[0]
        assert endpoint.endswith("?" + add.OPEN_FILTER)
        return {"value": [{"title": t} for t in state.open.get(list_id, [])]}

    def post(credential, endpoint, payload, *, scope):
        assert scope == "Tasks.ReadWrite"
        if payload.get("title", payload.get("displayName")) == state.fail_on:
            raise RuntimeError("HTTP 500")
        state.posts.append((endpoint, payload))
        return {"id": f"t{len(state.posts)}"}

    calls = []

    def credential(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(todo, "_ms_graph_get", get)
    monkeypatch.setattr(todo, "_ms_graph_post", post)
    monkeypatch.setattr(todo, "_get_ms_credential", credential)
    state.credential_calls = calls
    return state


def log_text(vault):
    """Every monthly activity file, concatenated (the helper picks the month)."""
    files = sorted((vault / "logs" / "activity").glob("*.md"))
    assert not (vault / "logs" / "activity.md").exists()
    return "".join(f.read_text() for f in files)


def test_preview_writes_nothing(graph, vault, capsys):
    assert add.main(["--list", "Social", "Text Viktor", "Coffee with Lepka"]) == 0
    assert graph.posts == []
    assert log_text(vault) == ""
    out = capsys.readouterr().out
    assert "Preview" in out and "+ Text Viktor" in out and "+ Coffee with Lepka" in out
    assert graph.credential_calls == [{
        "scope": "Tasks.ReadWrite", "cache_name": "pik-todo-write",
        "record_name": "ms_auth_record_write.json", "interactive": False,
    }]


def test_yes_posts_expected_payloads_and_logs(graph, vault, monkeypatch):
    today = add.date(2026, 11, 16)
    monkeypatch.setattr(add, "date", SimpleNamespace(today=lambda: today))
    assert add.main(["--list", "social", "Text Viktor", "Coffee", "--note", "[[brief]]", "--yes"]) == 0
    body = {"contentType": "text", "content": "Added by Claude (pki vault), 2026-11-16\n[[brief]]"}
    assert graph.posts == [
        ("/me/todo/lists/soc/tasks", {"title": "Text Viktor", "body": body}),
        ("/me/todo/lists/soc/tasks", {"title": "Coffee", "body": body}),
    ]
    log = log_text(vault)
    assert "**Action:** Tasks added to Microsoft To Do (2)\n" in log
    assert "**List:** Social\n" in log and "- Text Viktor\n" in log and "- Coffee\n" in log
    assert "Stopped at failure" not in log
    assert log.count("\n## ") == 1


def test_provenance_without_note():
    assert add.provenance(None, add.date(2026, 1, 2)) == "Added by Claude (pki vault), 2026-01-02"


def test_dedup_against_open_tasks_and_within_batch(graph, capsys):
    titles = ["  existing TASK ", "New one", "new ONE", "Other"]
    assert add.main(["--list", "Social", *titles, "--yes"]) == 0
    assert [p["title"] for _, p in graph.posts] == ["New one", "Other"]
    out = capsys.readouterr().out
    assert "already an open task" in out and "duplicate within this batch" in out


def test_titles_from_file_ignore_blank_lines(graph, tmp_path):
    f = tmp_path / "tasks.txt"
    f.write_text("One\n\n   \nTwo\n")
    assert add.main(["--list", "Social", "--file", str(f), "--yes"]) == 0
    assert [p["title"] for _, p in graph.posts] == ["One", "Two"]


def test_list_ids_are_quoted_in_urls(graph, monkeypatch):
    lists = [{"id": "a/b?c=", "displayName": "Odd"}]
    monkeypatch.setattr(add, "fetch_lists", lambda credential: lists)
    assert add.main(["--list", "Odd", "x", "--yes"]) == 0
    assert graph.posts[0][0] == "/me/todo/lists/a%2Fb%3Fc%3D/tasks"


@pytest.mark.parametrize("name, expected", [("Social", "soc"), ("HOME", "home"), (" home ", "home")])
def test_list_match(name, expected):
    assert add.match_list(LISTS, name)["id"] == expected


@pytest.mark.parametrize("name, problem", [("Nope", "No list matches"), ("WORK", "More than one")])
def test_list_missing_or_ambiguous_exits_nonzero_with_names(graph, capsys, name, problem):
    assert add.main(["--list", name, "x", "--yes"]) != 0
    out = capsys.readouterr().out
    assert problem in out
    assert all(f"- {tl['displayName']}" in out for tl in LISTS)
    assert graph.posts == []


def test_stops_at_first_failed_post(graph, vault, capsys):
    graph.fail_on = "B"
    assert add.main(["--list", "Social", "A", "B", "C", "--yes"]) == 1
    assert [p["title"] for _, p in graph.posts] == ["A"]
    out = capsys.readouterr().out
    assert "Created 1 of 3" in out and "Failed on 'B'" in out
    assert out.split("Not created:")[1].split() == ["-", "B", "-", "C"]
    log = log_text(vault)
    assert "- A\n" in log and "**Stopped at failure:** B\n" in log and "- C" not in log


@pytest.mark.parametrize("titles, message", [
    (["   "], "must not be empty"),
    (["x" * 256], "longer than 255"),
    ([f"t{i}" for i in range(51)], "Too many tasks"),
    (["line\nbreak"], "control or bidi character"),
    (["csi \u009b31m"], "control or bidi character"),
    (["evil \u202etxt.exe"], "control or bidi character"),
    (["isolate \u2066x\u2069"], "control or bidi character"),
])
def test_input_limits(graph, capsys, titles, message):
    assert add.main(["--list", "Social", *titles, "--yes"]) == 2
    assert message in capsys.readouterr().out
    assert graph.posts == [] and graph.gets == []


def test_limits_allow_255_chars_and_50_tasks():
    assert add.validate_titles(["x" * 255])
    assert len(add.validate_titles([f"t{i}" for i in range(50)])) == 50


# ---------- auth ----------

class FakeIdentity:
    """Stand-in for azure.identity that records what the code asked for."""

    def __init__(self):
        self.created, self.authenticated, self.cache_names = [], [], []
        identity = self

        class Record:
            @staticmethod
            def deserialize(text):
                return ("record", text)

        class Options:
            def __init__(self, name):
                identity.cache_names.append(name)

        class Credential:
            def __init__(self, **kwargs):
                identity.created.append(kwargs)

            def authenticate(self, scopes):
                identity.authenticated.append(scopes)
                return SimpleNamespace(serialize=lambda: "{}")

        self.module = SimpleNamespace(
            AuthenticationRecord=Record, DeviceCodeCredential=Credential,
            TokenCachePersistenceOptions=Options,
        )


@pytest.fixture
def identity(vault, monkeypatch):
    fake = FakeIdentity()
    monkeypatch.setitem(sys.modules, "azure", SimpleNamespace(identity=fake.module))
    monkeypatch.setitem(sys.modules, "azure.identity", fake.module)
    return fake


@pytest.mark.parametrize("argv", [["--list", "Social", "x", "--yes"], ["--list", "Social", "x"], ["--lists"]])
def test_refuses_to_prompt_without_write_record(identity, capsys, argv):
    assert add.main(argv) == 1
    assert "run `uv run add_tasks.py --test-auth` first" in capsys.readouterr().out
    assert identity.created == [] and identity.authenticated == []


def test_read_record_does_not_count_as_write_sign_in(identity, vault, tmp_path, capsys):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "ms_auth_record.json").write_text("{}")
    assert add.main(["--lists"]) == 1
    assert identity.created == []


def test_expired_write_session_does_not_prompt(identity, tmp_path, monkeypatch, capsys):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "ms_auth_record_write.json").write_text("{}")

    class AuthenticationRequiredError(Exception):
        pass

    def get(credential, endpoint, **kwargs):
        raise AuthenticationRequiredError("interaction required")

    monkeypatch.setattr(todo, "_ms_graph_get", get)
    assert add.main(["--lists"]) == 1
    assert "--test-auth" in capsys.readouterr().out
    assert identity.created[0]["disable_automatic_authentication"] is True
    assert identity.cache_names == ["pik-todo-write"]


def test_test_auth_signs_in_with_write_scope_and_separate_record(identity, tmp_path, monkeypatch):
    monkeypatch.setattr(add, "fetch_lists", lambda credential: LISTS)
    assert add.main(["--test-auth"]) == 0
    assert identity.authenticated == [["Tasks.ReadWrite"]]
    assert identity.cache_names == ["pik-todo-write"]
    assert "disable_automatic_authentication" not in identity.created[0]
    assert (tmp_path / "state" / "ms_auth_record_write.json").exists()
    assert not (tmp_path / "state" / "ms_auth_record.json").exists()


def test_reader_still_uses_read_scope_and_cache(identity, tmp_path, monkeypatch):
    todo._get_ms_credential()
    assert identity.authenticated == [["Tasks.Read"]]
    assert identity.cache_names == ["pik-todo"]
    assert "disable_automatic_authentication" not in identity.created[0]
    assert (tmp_path / "state" / "ms_auth_record.json").exists()
    assert not (tmp_path / "state" / "ms_auth_record_write.json").exists()

    scopes = []
    credential = SimpleNamespace(get_token=lambda s: scopes.append(s) or SimpleNamespace(token="t"))
    response = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"value": []})
    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(get=lambda url, **kw: response))
    todo._ms_graph_get(credential, "/me/todo/lists")
    assert scopes == ["Tasks.Read"]


# ---------- POST helper ----------

def _post_responses(monkeypatch, responses):
    sent = []

    def post(url, **kwargs):
        sent.append((url, kwargs["json"]))
        return responses.pop(0)

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=post))
    monkeypatch.setattr("time.sleep", lambda _: None)
    return sent


def _resp(status, headers=None):
    def raise_for_status():
        if status >= 400:
            raise RuntimeError(f"HTTP {status}")
    return SimpleNamespace(status_code=status, headers=headers or {},
                           raise_for_status=raise_for_status, json=lambda: {"id": "x"})


CRED = SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="t"))


def test_post_retries_only_throttling_with_retry_after(monkeypatch):
    sent = _post_responses(monkeypatch, [_resp(429, {"Retry-After": "2"}), _resp(201)])
    assert todo._ms_graph_post(CRED, "/me/todo/lists/a/tasks", {"title": "x"}, scope="s") == {"id": "x"}
    assert len(sent) == 2 and sent[0][0] == todo._MS_GRAPH_BASE + "/me/todo/lists/a/tasks"


@pytest.mark.parametrize("response", [_resp(429), _resp(503), _resp(500, {"Retry-After": "1"}), _resp(400)])
def test_post_does_not_retry_otherwise(monkeypatch, response):
    sent = _post_responses(monkeypatch, [response, _resp(201)])
    with pytest.raises(RuntimeError):
        todo._ms_graph_post(CRED, "/x", {}, scope="s")
    assert len(sent) == 1


def test_post_rejects_absolute_urls():
    with pytest.raises(ValueError):
        todo._ms_graph_post(CRED, "https://example.invalid/x", {}, scope="s")


def test_log_renders_titles_and_list_name_inert(graph, vault, monkeypatch):
    hostile = "<!-- hide --> ![x](https://evil.invalid/p.png) **bold** [l](u)"
    monkeypatch.setattr(add, "fetch_lists", lambda credential: [{"id": "e", "displayName": "<b>L</b>"}])
    assert add.main(["--list", "<b>L</b>", hostile, "--yes"]) == 0
    assert graph.posts[0][1]["title"] == hostile  # Graph gets the raw title
    log = log_text(vault)
    for raw in ("<!--", "<b>", "![x](", "**bold**", "[l](u)"):
        assert raw not in log
    assert "&lt;\\!\\-\\- hide \\-\\-&gt;" in log and "**List:** &lt;b&gt;L&lt;/b&gt;\n" in log


def test_emoji_and_accents_are_allowed():
    assert add.validate_titles(["Káva s Lepkou \u2615", "family \U0001F468\u200d\U0001F469"])


def test_graph_list_names_are_sanitized_for_display(graph, vault, monkeypatch, capsys):
    name = "Soc\u202eial\x1b[2J"
    monkeypatch.setattr(add, "fetch_lists", lambda credential: [{"id": "s", "displayName": name}])
    assert add.main(["--list", "Nope", "x"]) == 2
    assert add.main(["--lists"]) == 0
    assert add.main(["--list", name, "x", "--yes"]) == 0
    out = capsys.readouterr().out
    log = log_text(vault)
    for text in (out, log):
        assert "\u202e" not in text and "\x1b" not in text
    assert "Soc\ufffdial\ufffd" in out


def test_log_goes_through_append_activity_with_escaped_fields(graph, monkeypatch):
    calls = []
    monkeypatch.setattr(add, "append_activity", lambda action, fields: calls.append((action, fields)))
    monkeypatch.setattr(add, "fetch_lists", lambda credential: [{"id": "e", "displayName": "# <i>L</i>"}])
    graph.fail_on = "[b](x)"
    assert add.main(["--list", "# <i>L</i>", "# ok *a*", "[b](x)", "--yes"]) == 1
    assert [p["title"] for _, p in graph.posts] == ["# ok *a*"]  # raw only in the Graph payload
    assert calls == [("Tasks added to Microsoft To Do (1)", {
        "List": "\\# &lt;i&gt;L&lt;/i&gt;",
        "Created": ["\\# ok \\*a\\*"],
        "Stopped at failure": "\\[b\\]\\(x\\)",
    })]


# ---------- steps (checklist items) ----------

def step_posts(graph):
    return [(e, p["displayName"]) for e, p in graph.posts if "displayName" in p]


def task_posts(graph):
    return [p["title"] for _, p in graph.posts if "title" in p]


def write(tmp_path, text):
    f = tmp_path / "tasks.txt"
    f.write_text(text)
    return str(f)


def test_indented_lines_become_steps_of_the_task_above(graph, vault, tmp_path):
    f = write(tmp_path, "Pack\n    Charger\n\tHDMI adapter\n\nText Viktor\n  Ask about Friday \n")
    assert add.main(["--list", "Social", "--file", f, "--yes"]) == 0
    assert graph.posts == [
        ("/me/todo/lists/soc/tasks", graph.posts[0][1]),
        ("/me/todo/lists/soc/tasks/t1/checklistItems", {"displayName": "Charger"}),
        ("/me/todo/lists/soc/tasks/t1/checklistItems", {"displayName": "HDMI adapter"}),
        ("/me/todo/lists/soc/tasks", graph.posts[3][1]),
        ("/me/todo/lists/soc/tasks/t4/checklistItems", {"displayName": "Ask about Friday"}),
    ]
    assert task_posts(graph) == ["Pack", "Text Viktor"]
    log = log_text(vault)
    assert "- Pack (2 steps)\n" in log and "- Text Viktor (1 step)\n" in log


def test_preview_shows_steps_and_writes_nothing(graph, tmp_path, capsys):
    f = write(tmp_path, "Pack\n  Charger\n")
    assert add.main(["--list", "Social", "--file", f]) == 0
    assert graph.posts == []
    out = capsys.readouterr().out
    assert "  + Pack\n      [ ] Charger\n" in out


def test_positional_titles_come_first_and_have_no_steps(graph, tmp_path):
    f = write(tmp_path, "From file\n  Step\n")
    assert add.main(["--list", "Social", "Positional", "--file", f, "--yes"]) == 0
    assert task_posts(graph) == ["Positional", "From file"]
    assert step_posts(graph) == [("/me/todo/lists/soc/tasks/t2/checklistItems", "Step")]


def test_indented_first_line_is_an_error(graph, tmp_path, capsys):
    f = write(tmp_path, "\n   Orphan step\nTask\n")
    assert add.main(["--list", "Social", "Positional", "--file", f, "--yes"]) == 2
    assert "Line 2" in capsys.readouterr().out
    assert graph.posts == [] and graph.gets == []


def test_skipped_task_skips_its_steps(graph, tmp_path, capsys):
    f = write(tmp_path, "Existing task\n  Step A\nNew\n  Step B\n")
    assert add.main(["--list", "Social", "--file", f, "--yes"]) == 0
    assert task_posts(graph) == ["New"]
    assert [s for _, s in step_posts(graph)] == ["Step B"]
    assert "skip 'Existing task' (and its 1 step): already an open task" in capsys.readouterr().out


def test_task_id_is_quoted_in_step_url(graph, monkeypatch, tmp_path):
    monkeypatch.setattr(todo, "_ms_graph_post", lambda c, e, p, *, scope: (
        graph.posts.append((e, p)) or {"id": "a/b?c="}))
    f = write(tmp_path, "T\n  S\n")
    assert add.main(["--list", "Social", "--file", f, "--yes"]) == 0
    assert graph.posts[1][0] == "/me/todo/lists/soc/tasks/a%2Fb%3Fc%3D/checklistItems"


@pytest.mark.parametrize("response", [{}, {"id": ""}, {"id": 5}, None, {"id": "."}, {"id": ".."}])
def test_missing_task_id_stops_before_steps(graph, monkeypatch, tmp_path, capsys, response):
    monkeypatch.setattr(todo, "_ms_graph_post", lambda c, e, p, *, scope: (
        graph.posts.append((e, p)) or response))
    f = write(tmp_path, "T\n  S\nU\n")
    assert add.main(["--list", "Social", "--file", f, "--yes"]) == 1
    assert task_posts(graph) == ["T"] and step_posts(graph) == []
    out = capsys.readouterr().out
    assert "only 0 of 1 steps" in out and "no usable task id" in out
    assert "may still have been added" not in out  # no step request was sent
    assert out.split("Not created:")[1].split() == ["-", "U"]


def test_missing_task_id_is_fine_without_steps(graph, monkeypatch):
    monkeypatch.setattr(todo, "_ms_graph_post", lambda c, e, p, *, scope: graph.posts.append((e, p)) or {})
    assert add.main(["--list", "Social", "A", "B", "--yes"]) == 0
    assert task_posts(graph) == ["A", "B"]


def test_failed_step_stops_the_run_and_reports_partial_task(graph, vault, tmp_path, capsys):
    graph.fail_on = "S2"
    f = write(tmp_path, "Done\n  S0\nPartial\n  S1\n  S2\n  S3\nLater\n")
    assert add.main(["--list", "Social", "--file", f, "--yes"]) == 1
    assert task_posts(graph) == ["Done", "Partial"]
    assert [s for _, s in step_posts(graph)] == ["S0", "S1"]
    out = capsys.readouterr().out
    assert "Created 2 of 3" in out
    assert "'Partial' was created, but only 1 of 3 steps were added" in out
    assert "Failed on step 'S2'" in out and "A rerun skips this task" in out
    assert out.split("Steps not added:")[1].split("Not created:")[0].split() == ["-", "S2", "-", "S3"]
    assert out.split("Not created:")[1].split() == ["-", "Later"]
    log = log_text(vault)
    assert "- Done (1 step)\n" in log
    assert "**Stopped at failure:** Partial (task created with 1 of 3 steps; failed on step S2)\n" in log


def test_failed_step_on_last_task_has_no_not_created_section(graph, tmp_path, capsys):
    graph.fail_on = "S1"
    f = write(tmp_path, "Only\n  S1\n")
    assert add.main(["--list", "Social", "--file", f, "--yes"]) == 1
    out = capsys.readouterr().out
    assert "Created 1 of 1" in out and "Not created:" not in out


@pytest.mark.parametrize("text, message", [
    ("T\n" + "".join(f"  s{i}\n" for i in range(21)), "Too many steps under 'T': 21"),
    ("".join(f"T{t}\n" + "".join(f"  s{i}\n" for i in range(20)) for t in range(6)), "Too many steps: 120"),
    ("T\n  " + "x" * 256 + "\n", "Step longer than 255"),
    ("T\n  bad ‮ step\n", "Step contains a control or bidi character"),
    ("T\n  esc \x1b[2J\n", "Step contains a control or bidi character"),
])
def test_step_limits(graph, tmp_path, capsys, text, message):
    f = write(tmp_path, text)
    assert add.main(["--list", "Social", "--file", f, "--yes"]) == 2
    assert message in capsys.readouterr().out
    assert graph.posts == [] and graph.gets == []


def test_step_limits_allow_20_per_task_and_100_per_run():
    tasks = [add.Task(f"T{t}", tuple(f"s{i}" for i in range(20))) for t in range(5)]
    assert sum(len(t.steps) for t in add.validate_tasks(tasks)) == 100


def test_step_text_is_inert_in_the_log(graph, vault, tmp_path):
    graph.fail_on = "![x](https://evil.invalid/p.png)"
    f = write(tmp_path, "T\n  ![x](https://evil.invalid/p.png)\n")
    assert add.main(["--list", "Social", "--file", f, "--yes"]) == 1
    log = log_text(vault)
    assert "![x](" not in log and "\\!\\[x\\]\\(" in log


@pytest.mark.parametrize("text", [
    "Task\n  Safe\u0085Injected task\n",   # NEL: splitlines() would make it a new task
    "Task\n  Safe\x0bInjected\n",          # VT
    "Task\x0cInjected\n",                   # FF
    "Task\n  Safe\u2028Injected\n",        # LINE SEPARATOR
    "Task\u2029Injected\n",                 # PARAGRAPH SEPARATOR
    "Task\x1f\n",                           # trailing control that strip() would remove
    "\x1fTask\n",                           # leading one
    "Task\n  Step\x1c\n",                  # FILE SEPARATOR at the end of a step
    "Task\n\x0b\n",                        # a line of nothing but a control
    "Task\rInjected\n",                     # a lone CR is not a line break
])
def test_controls_in_file_are_refused_not_treated_as_line_breaks(graph, tmp_path, capsys, text):
    f = write(tmp_path, text)
    assert add.main(["--list", "Social", "--file", f, "--yes"]) == 2
    assert "control or bidi character" in capsys.readouterr().out
    assert graph.posts == [] and graph.gets == []


def test_crlf_files_and_blank_lines_still_work(graph, tmp_path):
    f = tmp_path / "tasks.txt"
    f.write_bytes(b"Task\r\n  Step\r\n\r\n \t \r\nOther\r\n")
    assert add.main(["--list", "Social", "--file", str(f), "--yes"]) == 0
    assert task_posts(graph) == ["Task", "Other"]
    assert [s for _, s in step_posts(graph)] == ["Step"]


@pytest.mark.parametrize("title", ["x\x1f", "\x1fx", "x\u2028y"])
def test_positional_titles_cannot_hide_controls_at_the_edges(graph, capsys, title):
    assert add.main(["--list", "Social", title, "--yes"]) == 2
    assert graph.posts == []


def test_failed_log_write_still_reports_the_outcome(graph, monkeypatch, tmp_path, capsys):
    def broken(*args):
        raise OSError("disk full")
    monkeypatch.setattr(add, "append_activity", broken)
    graph.fail_on = "S2"
    f = write(tmp_path, "T\n  S1\n  S2\n")
    assert add.main(["--list", "Social", "--file", f, "--yes"]) == 1
    out = capsys.readouterr().out
    assert "activity log entry was not written: disk full" in out
    assert "Steps not added:" in out and "- S2" in out
