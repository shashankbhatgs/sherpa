import json
import sherpa.workspace as ws
from sherpa.scout import resume_index, fetch_full_resume, FETCH_RESUME_TOOL, SCOUT_REGISTRY


def test_resume_index_populated_profile(tmp_workspace):
    ws.init_workspace()
    profile = {
        "name": "Alice",
        "years_experience": 5,
        "core_competencies": ["distributed systems", "Go"],
        "target_roles": ["SDE2", "Senior SDE"],
    }
    (tmp_workspace / "profile.json").write_text(json.dumps(profile))

    result = resume_index()
    assert "Alice" in result
    assert "5" in result
    assert "distributed systems" in result
    assert "SDE2" in result


def test_resume_index_empty_profile(tmp_workspace):
    ws.init_workspace()
    (tmp_workspace / "profile.json").write_text(json.dumps({}))
    result = resume_index()
    assert isinstance(result, str)
    assert "Name:" in result


def test_resume_index_missing_file(tmp_workspace):
    ws.init_workspace()
    (tmp_workspace / "profile.json").unlink()
    result = resume_index()
    assert isinstance(result, str)


def test_fetch_full_resume_reads_content(tmp_workspace):
    ws.init_workspace()
    (tmp_workspace / "resume.md").write_text("# Jane Doe\nSDE2 at Acme")
    content = fetch_full_resume()
    assert "Jane Doe" in content


def test_fetch_full_resume_missing_returns_empty(tmp_workspace):
    ws.init_workspace()
    (tmp_workspace / "resume.md").unlink()
    assert fetch_full_resume() == ""


def test_fetch_resume_tool_schema():
    fn = FETCH_RESUME_TOOL["function"]
    assert fn["name"] == "fetch_full_resume"
    assert "description" in fn
    assert fn["parameters"]["type"] == "object"


def test_scout_registry_callable(tmp_workspace):
    ws.init_workspace()
    assert callable(SCOUT_REGISTRY["fetch_full_resume"])
