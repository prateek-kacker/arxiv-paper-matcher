"""
Integration tests for server.py REST APIs via FastAPI TestClient
"""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from server import app
import core_engine as core
from core_engine import Paper


@pytest.fixture
def client(temp_db_env):
    return TestClient(app)


def test_serve_index(client):
    """Test serving index.html root route."""
    response = client.get("/")
    assert response.status_code == 200
    assert "arXiv CS.CL Paper Matcher" in response.text


def test_get_config(client):
    """Test /api/config endpoint."""
    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "has_api_key" in data
    assert "gcs_bucket" in data
    assert "auto_post" in data


def test_cloud_sync_status(client):
    """Test /api/cloud-sync/status endpoint."""
    response = client.get("/api/cloud-sync/status")
    assert response.status_code == 200
    data = response.json()
    assert data["db_exists"] is True


def test_schedules_api_workflow(client):
    """Test full CRUD REST API workflow for recurring schedules."""
    # 1. GET schedules (empty list wrapped in dict)
    res = client.get("/api/schedules")
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
    res = client.post("/api/schedules", json=payload)
    assert res.status_code == 200
    sch_id = res.json()["schedule_id"]
    assert sch_id > 0

    # 3. GET schedules (contains 1)
    res = client.get("/api/schedules")
    assert len(res.json()["schedules"]) == 1
    assert res.json()["schedules"][0]["label"] == "Test Schedule"

    # 4. POST toggle active status
    res = client.post(f"/api/schedules/{sch_id}/toggle", json={"active": False})
    assert res.status_code == 200
    assert res.json()["active"] is False

    # 5. PUT update schedule
    payload["label"] = "Updated Test Schedule"
    payload["run_time"] = "14:00"
    res = client.put(f"/api/schedules/{sch_id}", json=payload)
    assert res.status_code == 200
    assert res.json()["success"] is True

    # 6. DELETE schedule
    res = client.delete(f"/api/schedules/{sch_id}")
    assert res.status_code == 200
    assert len(client.get("/api/schedules").json()["schedules"]) == 0


def test_papers_and_evaluations_api_workflow(client):
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
    res = client.get("/api/evaluations")
    assert res.status_code == 200
    assert len(res.json()) == 1

    # 2. GET /api/evaluations/{id}
    res = client.get(f"/api/evaluations/{eval_id}")
    assert res.status_code == 200
    assert len(res.json()["papers"]) == 1

    # 3. GET /api/all-papers
    res = client.get("/api/all-papers")
    assert res.status_code == 200
    assert len(res.json()["papers"]) == 1

    # 4. GET /api/papers/{paper_id}
    res = client.get(f"/api/papers/{paper_id}")
    assert res.status_code == 200
    assert res.json()["title"] == "Benchmarking LLMs"

    # 5. DELETE /api/papers
    res = client.request("DELETE", "/api/papers", json={"paper_ids": [paper_id]})
    assert res.status_code == 200
    assert len(client.get("/api/all-papers").json()["papers"]) == 0


def test_evaluate_stream_validation(client):
    """Test validation errors on /api/evaluate/stream endpoint."""
    # Missing problem statement
    res = client.post("/api/evaluate/stream", json={"problem_statement": ""})
    assert res.status_code == 400


def test_bulk_delete_evaluations(client):
    """Test POST /api/evaluations/delete-bulk endpoint."""
    eval_id1 = core.save_evaluation("Problem 1", "gemini-3-pro-preview", sync_cloud=False)
    eval_id2 = core.save_evaluation("Problem 2", "gemini-3-pro-preview", sync_cloud=False)

    res = client.post("/api/evaluations/delete-bulk", json={"eval_ids": [eval_id1, eval_id2]})
    assert res.status_code == 200
    assert res.json()["deleted_count"] == 2

    # Verify evaluations are deleted
    evals = client.get("/api/evaluations").json()["evaluations"]
    eval_ids = [e["id"] for e in evals]
    assert eval_id1 not in eval_ids
    assert eval_id2 not in eval_ids
