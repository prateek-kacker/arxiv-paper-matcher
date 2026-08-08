"""
Integration test suite for UI button actions:
Executes each button workflow (evaluate, fetch-only, schedule CRUD, run-now, cloud scheduler, delete history/paper)
and verifies reflection in history, API responses, and persistence store.
"""

# pylint: disable=unused-argument

import time
import pytest
from fastapi.testclient import TestClient

from server import app
import server
import core_engine as core
from core_engine import Paper, DebateResult


@pytest.fixture(name="api_client")
def _api_client(temp_db_env):
    return TestClient(app)


def test_background_evaluation_button_workflow(api_client, monkeypatch):
    """Test 'Fetch & Evaluate All' in Background Mode: launches evaluation, completes run, reflects in History."""
    sample_paper = Paper(
        title="Multi-Agent Systems for Orchestration",
        authors="A. Smith, B. Jones",
        abstract="Evaluating multi-agent collaboration LLMs.",
        url="https://arxiv.org/abs/2401.09999",
        published="2026-02-01",
        categories="cs.CL",
        full_text="Full text content"
    )
    mock_result = DebateResult(paper=sample_paper, combined_verdict="Highly relevant agentic system.", avg_score=9.0)

    monkeypatch.setattr(server, "resolve_gemini_api_key", lambda *args, **kwargs: "fake-api-key")
    monkeypatch.setattr(core, "_run_evaluation_headless", lambda **kwargs: (101, [mock_result], None))

    payload = {
        "problem_statement": "Autonomous multi-agent workflows",
        "model_name": "gemini-2.5-flash",
        "paper_source": "arxiv",
        "acl_track": "all",
        "max_papers": 5,
        "days_back": None,
        "keyword_filter": "agent",
        "max_concurrent": 2,
    }

    # 1. Click "Fetch & Evaluate All" in background mode -> POST /api/evaluate/background
    res = api_client.post("/api/evaluate/background", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    eval_id = data["eval_id"]
    assert eval_id > 0

    # 2. Check History endpoint GET /api/evaluations
    res_history = api_client.get("/api/evaluations")
    assert res_history.status_code == 200
    evals = res_history.json()["evaluations"]
    assert len(evals) == 1
    assert evals[0]["id"] == 101
    assert evals[0]["problem_text"] == "Autonomous multi-agent workflows"


def test_fetch_papers_only_button_workflow(api_client, monkeypatch):
    """Test 'Fetch Papers Only' button action: fetches arXiv preview papers without running evaluation."""
    sample_paper = Paper(
        title="Preview Paper on LLMs",
        authors="C. Davis",
        abstract="Abstract preview",
        url="https://arxiv.org/abs/2401.08888",
        published="2026-02-02",
        categories="cs.CL"
    )
    monkeypatch.setattr(server, "fetch_arxiv_papers", lambda **kwargs: [sample_paper])

    payload = {
        "paper_source": "arxiv",
        "acl_track": "all",
        "max_papers": 5,
        "days_back": None,
        "keyword_filter": "LLM",
    }

    # Click "Fetch Papers Only" -> POST /api/fetch-papers
    res = api_client.post("/api/fetch-papers", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["count"] == 1
    assert data["papers"][0]["title"] == "Preview Paper on LLMs"


def test_create_and_manage_schedules_button_workflow(api_client, monkeypatch):
    """Test 'Add to Recurring', 'Edit Schedule', 'Pause/Activate', and 'Delete Schedule' buttons."""
    monkeypatch.setattr(server, "toggle_gcp_scheduler", lambda active: {"success": True, "state": "ENABLED" if active else "PAUSED"})
    monkeypatch.setattr(server, "get_gcp_scheduler_status", lambda: {"job_name": "hourly-paper-matcher-eval", "state": "ENABLED", "location": "us-central1"})

    # 1. Click "Add to Recurring" -> POST /api/schedules
    payload = {
        "label": "Daily Agentic Matcher",
        "problem_text": "Agentic AI systems for enterprise",
        "model_name": "gemini-2.5-flash",
        "paper_source": "arxiv",
        "acl_track": "all",
        "fetch_mode": "count",
        "max_papers": 10,
        "days_back": None,
        "keyword_filter": "agent",
        "min_score": 7,
        "max_concurrent": 3,
        "run_time": "08:00"
    }
    res = api_client.post("/api/schedules", json=payload)
    assert res.status_code == 200
    sch_id = res.json()["schedule_id"]

    # Verify reflected in GET /api/schedules
    schedules = api_client.get("/api/schedules").json()["schedules"]
    assert len(schedules) == 1
    assert schedules[0]["label"] == "Daily Agentic Matcher"
    assert schedules[0]["is_active"] == 1

    # 2. Click "Pause" on Schedule -> POST /api/schedules/{id}/toggle
    res_toggle = api_client.post(f"/api/schedules/{sch_id}/toggle", json={"active": False})
    assert res_toggle.status_code == 200
    assert res_toggle.json()["active"] is False

    # 3. Click "Edit Schedule" modal submit -> PUT /api/schedules/{id}
    payload["label"] = "Updated Agentic Matcher"
    payload["run_time"] = "09:30"
    res_edit = api_client.put(f"/api/schedules/{sch_id}", json=payload)
    assert res_edit.status_code == 200

    schedules_updated = api_client.get("/api/schedules").json()["schedules"]
    assert schedules_updated[0]["label"] == "Updated Agentic Matcher"
    assert schedules_updated[0]["run_time"] == "09:30"

    # 4. Click "Delete Schedule" -> DELETE /api/schedules/{id}
    res_del = api_client.delete(f"/api/schedules/{sch_id}")
    assert res_del.status_code == 200
    assert len(api_client.get("/api/schedules").json()["schedules"]) == 0


def test_schedule_run_now_button_workflow(api_client, monkeypatch):
    """Test 'Run Now' button on schedule: triggers background execution, updates schedule last run info, reflects in History."""
    monkeypatch.setattr(server, "toggle_gcp_scheduler", lambda active: {"success": True, "state": "ENABLED" if active else "PAUSED"})
    monkeypatch.setattr(server, "get_gcp_scheduler_status", lambda: {"job_name": "hourly-paper-matcher-eval", "state": "ENABLED", "location": "us-central1"})
    monkeypatch.setattr(server, "resolve_gemini_api_key", lambda: "fake-key")

    sample_paper = Paper(
        title="Scheduled Match Paper",
        authors="D. Evans",
        abstract="Evaluation of scheduled match.",
        url="https://arxiv.org/abs/2401.07777",
        published="2026-02-03",
        categories="cs.CL"
    )
    mock_result = DebateResult(paper=sample_paper, combined_verdict="Excellent match", avg_score=8.8)

    def _mock_headless(**kwargs):
        # Save evaluation task in store and return
        eval_id = core.save_evaluation("Scheduled Problem Statement", "gemini-2.5-flash", sync_cloud=False)
        paper_id = core.save_paper(eval_id, sample_paper, 8.8, sync_cloud=False)
        core.update_evaluation_progress(eval_id, completed=1, total=1, status="COMPLETED", sync_cloud=False)
        return eval_id, [mock_result], None

    monkeypatch.setattr(server, "_run_evaluation_headless", _mock_headless)

    # 1. Create a schedule
    payload = {
        "label": "Schedule to Run Now",
        "problem_text": "Scheduled Problem Statement",
        "model_name": "gemini-2.5-flash",
        "paper_source": "arxiv",
        "acl_track": "all",
        "fetch_mode": "count",
        "max_papers": 5,
        "days_back": None,
        "keyword_filter": "agent",
        "min_score": 6,
        "max_concurrent": 2,
        "run_time": "10:00"
    }
    sch_id = api_client.post("/api/schedules", json=payload).json()["schedule_id"]

    # 2. Click "Run Now" -> POST /api/schedules/{id}/run
    res_run = api_client.post(f"/api/schedules/{sch_id}/run")
    assert res_run.status_code == 200
    assert res_run.json()["success"] is True

    # 3. Check GET /api/schedules last run info
    schedules = api_client.get("/api/schedules").json()["schedules"]
    sch = next(s for s in schedules if s["id"] == sch_id)
    assert sch["last_status"] == "success"
    assert sch["last_eval_id"] > 0

    # 4. Check GET /api/evaluations (History reflection)
    evals = api_client.get("/api/evaluations").json()["evaluations"]
    assert len(evals) == 1
    assert evals[0]["id"] == sch["last_eval_id"]
    assert evals[0]["status"] == "COMPLETED"


def test_delete_history_run_and_paper_buttons_workflow(api_client):
    """Test 'Delete Run' and 'Delete Paper' button actions from History tab."""
    # 1. Create evaluation and paper record directly in store
    eval_id = core.save_evaluation("Problem to delete", "gemini-2.5-flash", sync_cloud=False)
    sample_paper = Paper(
        title="Paper to Delete",
        authors="E. Frank",
        abstract="Temporary abstract",
        url="https://arxiv.org/abs/2401.06666",
        published="2026-02-04",
        categories="cs.CL"
    )
    paper_id = core.save_paper(eval_id, sample_paper, 7.5, sync_cloud=False)

    # Verify listed in History
    assert len(api_client.get("/api/evaluations").json()["evaluations"]) == 1
    assert len(api_client.get("/api/all-papers").json()["papers"]) == 1

    # 2. Click "Delete Paper" -> DELETE /api/papers
    res_del_paper = api_client.request("DELETE", "/api/papers", json={"paper_ids": [paper_id]})
    assert res_del_paper.status_code == 200
    assert len(api_client.get("/api/all-papers").json()["papers"]) == 0

    # 3. Click "Delete Run" -> POST /api/evaluations/delete-bulk
    res_del_run = api_client.post("/api/evaluations/delete-bulk", json={"eval_ids": [eval_id]})
    assert res_del_run.status_code == 200
    assert len(api_client.get("/api/evaluations").json()["evaluations"]) == 0
