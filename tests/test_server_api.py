"""
Integration tests for server.py REST APIs via FastAPI TestClient
"""

# pylint: disable=unused-argument

import asyncio
import pytest
from fastapi.testclient import TestClient

from server import app
import server
import core_engine as core
from core_engine import Paper


@pytest.fixture(name="api_client")
def _api_client(temp_db_env):
    return TestClient(app)


def test_serve_index(api_client):
    """Test serving index.html root route."""
    response = api_client.get("/")
    assert response.status_code == 200
    assert "arXiv CS.CL Paper Matcher" in response.text


def test_get_config(api_client):
    """Test /api/config endpoint."""
    response = api_client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "has_api_key" in data
    assert "gcs_bucket" in data
    assert "auto_post" in data


def test_cloud_sync_status(api_client):
    """Test /api/cloud-sync/status endpoint."""
    response = api_client.get("/api/cloud-sync/status")
    assert response.status_code == 200
    data = response.json()
    assert data["db_exists"] is True


def test_schedules_api_workflow(api_client):
    """Test full CRUD REST API workflow for recurring schedules."""
    # 1. GET schedules (empty list wrapped in dict)
    res = api_client.get("/api/schedules")
    assert res.status_code == 200
    schedules = res.json()["schedules"]
    assert len(schedules) == 0

    # 2. POST create schedule
    payload = {
        "label": "Test Schedule",
        "problem_text": "Summarize long documents",
        "model_name": "gemini-3-flash-preview",
        "paper_source": "arxiv",
        "acl_track": "all",
        "fetch_mode": "count",
        "max_papers": 15,
        "days_back": None,
        "keyword_filter": "summarization",
        "min_score": 6,
        "max_concurrent": 3,
        "run_time": "12:00"
    }
    res = api_client.post("/api/schedules", json=payload)
    assert res.status_code == 200
    sch_id = res.json()["schedule_id"]
    assert sch_id > 0

    # 3. GET schedules (contains 1)
    res = api_client.get("/api/schedules")
    assert len(res.json()["schedules"]) == 1
    assert res.json()["schedules"][0]["label"] == "Test Schedule"

    # 4. POST toggle active status
    res = api_client.post(f"/api/schedules/{sch_id}/toggle", json={"active": False})
    assert res.status_code == 200
    assert res.json()["active"] is False

    # 5. PUT update schedule
    payload["label"] = "Updated Test Schedule"
    payload["run_time"] = "14:00"
    res = api_client.put(f"/api/schedules/{sch_id}", json=payload)
    assert res.status_code == 200
    assert res.json()["success"] is True

    # 6. DELETE schedule
    res = api_client.delete(f"/api/schedules/{sch_id}")
    assert res.status_code == 200
    assert len(api_client.get("/api/schedules").json()["schedules"]) == 0


def test_cloud_scheduler_api_endpoint(api_client, monkeypatch):
    """Test GET /api/schedules cloud_scheduler metadata and toggle endpoint."""
    res = api_client.get("/api/schedules")
    assert res.status_code == 200
    data = res.json()
    assert "cloud_scheduler" in data
    assert "job_name" in data["cloud_scheduler"]

    # Mock toggle_gcp_scheduler in server module
    monkeypatch.setattr(server, "toggle_gcp_scheduler", lambda active: {"success": True, "state": "ENABLED" if active else "PAUSED"})
    monkeypatch.setattr(server, "get_gcp_scheduler_status", lambda: {"job_name": "hourly-paper-matcher-eval", "state": "ENABLED", "location": "us-central1"})

    toggle_res = api_client.post("/api/schedules/cloud-scheduler/toggle", json={"active": True})
    assert toggle_res.status_code == 200
    assert toggle_res.json()["success"] is True
    assert toggle_res.json()["cloud_scheduler"]["state"] == "ENABLED"



def test_papers_and_evaluations_api_workflow(api_client):
    """Test REST APIs for evaluations and paper records."""
    # Insert evaluation & paper into temp DB
    eval_id = core.save_evaluation("Benchmark LLM evaluation", "gemini-3-pro-preview", sync_cloud=False)
    paper = Paper(
        title="Benchmarking LLMs",
        authors="C. Lee",
        abstract="A new benchmark for LLM reasoning.",
        url="https://arxiv.org/abs/2401.99999",
        published="2026-02-01",
        categories="cs.CL",
        full_text="Full benchmark paper content..."
    )
    paper_id = core.save_paper(
        eval_id=eval_id,
        paper=paper,
        avg_score=9.0,
        sync_cloud=False
    )

    # 1. GET /api/evaluations
    res = api_client.get("/api/evaluations")
    assert res.status_code == 200
    assert len(res.json()) == 1

    # 2. GET /api/evaluations/{id}
    res = api_client.get(f"/api/evaluations/{eval_id}")
    assert res.status_code == 200
    assert len(res.json()["papers"]) == 1

    # 3. GET /api/all-papers
    res = api_client.get("/api/all-papers")
    assert res.status_code == 200
    assert len(res.json()["papers"]) == 1

    # 4. GET /api/papers/{paper_id}
    res = api_client.get(f"/api/papers/{paper_id}")
    assert res.status_code == 200
    assert res.json()["title"] == "Benchmarking LLMs"

    # 5. DELETE /api/papers
    res = api_client.request("DELETE", "/api/papers", json={"paper_ids": [paper_id]})
    assert res.status_code == 200
    assert len(api_client.get("/api/all-papers").json()["papers"]) == 0


def test_evaluate_stream_validation(api_client):
    """Test validation errors on /api/evaluate/stream endpoint."""
    # Missing problem statement
    res = api_client.post("/api/evaluate/stream", json={"problem_statement": ""})
    assert res.status_code == 400


def test_bulk_delete_evaluations(api_client):
    """Test POST /api/evaluations/delete-bulk endpoint."""
    eval_id1 = core.save_evaluation("Problem 1", "gemini-3-pro-preview", sync_cloud=False)
    eval_id2 = core.save_evaluation("Problem 2", "gemini-3-pro-preview", sync_cloud=False)

    res = api_client.post("/api/evaluations/delete-bulk", json={"eval_ids": [eval_id1, eval_id2]})
    assert res.status_code == 200
    assert res.json()["deleted_count"] == 2

    # Verify evaluations are deleted
    evals = api_client.get("/api/evaluations").json()["evaluations"]
    eval_ids = [e["id"] for e in evals]
    assert eval_id1 not in eval_ids
    assert eval_id2 not in eval_ids


def test_background_eval_is_queued_without_fetch_in_request(api_client, monkeypatch):
    """Background endpoint should queue work immediately without fetching papers inline."""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key")
    called = {"worker": False}

    def _should_not_be_called(*_args, **_kwargs):
        raise AssertionError("fetch_arxiv_papers should not run during request handling")

    async def _fake_worker(*_args, **_kwargs):
        called["worker"] = True

    monkeypatch.setattr(server, "fetch_arxiv_papers", _should_not_be_called)
    monkeypatch.setattr(server, "run_background_eval_task", _fake_worker)

    payload = {
        "problem_statement": "Test queued background flow",
        "model_name": "gemini-2.5-flash",
        "paper_source": "arxiv",
        "max_papers": 1,
        "days_back": 1,
        "keyword_filter": "llm",
        "max_concurrent": 1,
    }
    res = api_client.post("/api/evaluate/background", json=payload)

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["eval_id"] > 0
    assert data["total_papers"] is None
    assert "queued" in data["message"].lower()
    assert called["worker"] is True

    evals = api_client.get("/api/evaluations").json()["evaluations"]
    ev = next(e for e in evals if e["id"] == data["eval_id"])
    assert ev["status"] == "RUNNING"
    assert ev["total_papers"] == 0
    assert ev["completed_papers"] == 0


def test_background_worker_marks_failed_when_no_papers(monkeypatch):
    """Worker should mark evaluation FAILED when fetch returns no papers."""
    eval_id = core.save_evaluation("No papers scenario", "gemini-2.5-flash", status="RUNNING", total=0, sync_cloud=False)

    monkeypatch.setattr(server, "fetch_arxiv_papers", lambda **kwargs: [])

    asyncio.run(
        server.run_background_eval_task(
            eval_id=eval_id,
            api_key="dummy",
            problem="No papers scenario",
            model="gemini-2.5-flash",
            paper_source="arxiv",
            acl_track="all",
            max_papers=1,
            days_back=1,
            keyword_filter="",
            max_concurrent=1,
        )
    )

    ev = next(e for e in core.load_past_evaluations() if e["id"] == eval_id)
    assert ev["status"] == "FAILED"
    assert ev["total_papers"] == 0
    assert ev["completed_papers"] == 0


def test_background_eval_persists_start_state_to_cloud(api_client, monkeypatch):
    """Background endpoint should durably persist initial RUNNING state."""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key")
    saved = {"sync_cloud": None}

    def _fake_save_evaluation(*args, **kwargs):
        saved["sync_cloud"] = kwargs.get("sync_cloud")
        return 999

    async def _fake_worker(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, "save_evaluation", _fake_save_evaluation)
    monkeypatch.setattr(server, "run_background_eval_task", _fake_worker)

    payload = {
        "problem_statement": "Durable start",
        "model_name": "gemini-2.5-flash",
        "paper_source": "arxiv",
        "max_papers": 1,
        "days_back": 1,
    }
    res = api_client.post("/api/evaluate/background", json=payload)
    assert res.status_code == 200
    assert saved["sync_cloud"] is True


def test_background_worker_skips_already_saved_urls(monkeypatch):
    """Worker should skip URLs already saved for an eval and process only missing ones."""
    eval_id = core.save_evaluation("Resume test", "gemini-2.5-flash", status="RUNNING", total=0, sync_cloud=False)

    existing = Paper(
        title="Existing",
        authors="A",
        abstract="A",
        url="https://arxiv.org/abs/existing",
        published="2026-01-01",
        categories="cs.CL",
        full_text="",
    )
    core.save_paper(eval_id=eval_id, paper=existing, avg_score=7.0, sync_cloud=False)

    p_existing = Paper(
        title="Existing",
        authors="A",
        abstract="A",
        url="https://arxiv.org/abs/existing",
        published="2026-01-01",
        categories="cs.CL",
        full_text="",
    )
    p_new = Paper(
        title="New",
        authors="B",
        abstract="B",
        url="https://arxiv.org/abs/new",
        published="2026-01-02",
        categories="cs.CL",
        full_text="",
    )

    monkeypatch.setattr(server, "fetch_arxiv_papers", lambda **kwargs: [p_existing, p_new])

    from google import genai
    monkeypatch.setattr(genai, "Client", lambda api_key: object())

    processed_urls: list[str] = []

    class _FakeEngine:
        def __init__(self, client, model_name):
            self.client = client
            self.model_name = model_name

        async def run_debate(self, paper, problem):
            processed_urls.append(paper.url)
            return server.DebateResult(
                paper=paper,
                rounds=[],
                judge_verdicts=[],
                avg_score=8.0,
                combined_verdict="ok",
            )

    monkeypatch.setattr(server, "DebateEngine", _FakeEngine)

    asyncio.run(
        server.run_background_eval_task(
            eval_id=eval_id,
            api_key="dummy",
            problem="Resume test",
            model="gemini-2.5-flash",
            paper_source="arxiv",
            acl_track="all",
            max_papers=2,
            days_back=1,
            keyword_filter="",
            max_concurrent=1,
        )
    )

    papers = core.load_evaluation_papers(eval_id)
    urls = sorted(p["url"] for p in papers)
    assert urls == ["https://arxiv.org/abs/existing", "https://arxiv.org/abs/new"]
    assert processed_urls == ["https://arxiv.org/abs/new"]

    ev = next(e for e in core.load_past_evaluations() if e["id"] == eval_id)
    assert ev["status"] == "COMPLETED"
    assert ev["completed_papers"] == 2
    assert ev["total_papers"] == 2
