"""Focused tests for durable sharded evaluation behavior."""

import asyncio
from copy import deepcopy

from fastapi.testclient import TestClient

import core_engine as core
import sharded_eval as sharded
from core_engine import DebateResult, DebateRound, JudgeVerdict, Paper


def _paper(index: int) -> Paper:
    return Paper(
        title=f"Paper {index}",
        authors="A. Author",
        abstract="Abstract",
        url=f"https://example.test/{index}",
        published="2026-08-08",
        categories="cs.CL",
    )


def _result(index: int) -> dict:
    result = DebateResult(
        paper=_paper(index),
        rounds=[DebateRound("Advocate", "Skeptic")],
        judge_verdicts=[JudgeVerdict(1, 42, 8, "Relevant", ["Reason"], "Use")],
        avg_score=8.0,
    )
    return sharded._serialize_result(index, result, "Completed")


def test_shard_bounds_use_fixed_groups_of_ten():
    assert sharded.shard_bounds(131, 10, 0) == (0, 10)
    assert sharded.shard_bounds(131, 10, 12) == (120, 130)
    assert sharded.shard_bounds(131, 10, 13) == (130, 131)


def test_create_manifest_persists_exact_paper_order(monkeypatch):
    captured = {}
    monkeypatch.setattr(sharded, "_upload_create_only", lambda name, payload: captured.update(name=name, payload=payload) or True)

    manifest = sharded.create_manifest(
        eval_id=12,
        problem="Problem",
        model="model",
        papers=[_paper(index) for index in range(21)],
        max_concurrent=8,
    )

    assert manifest["total_shards"] == 3
    assert manifest["worker_concurrency"] == 2
    assert [paper["url"] for paper in manifest["papers"]] == [f"https://example.test/{index}" for index in range(21)]
    assert captured["name"].endswith("eval_000012/manifest.json")


def test_merge_is_idempotent_and_preserves_existing_resume_results():
    store = core._empty_store()
    store["evaluations"].append({
        "id": 12,
        "problem_text": "Problem",
        "model_name": "model",
        "status": "RUNNING",
        "total_papers": 2,
        "completed_papers": 1,
    })
    existing = _paper(0)
    store["papers"].append({
        "id": 1,
        "evaluation_id": 12,
        **existing.__dict__,
        "avg_score": 7.0,
        "generation_status": "COMPLETED",
        "generation_stage": "Completed",
        "generation_message": "",
    })
    store["meta"]["next_ids"]["papers"] = 2
    manifest = {"eval_id": 12, "total_papers": 2}

    merged = sharded._merge_results_into_store(deepcopy(store), manifest, [_result(0), _result(1)])
    merged_twice = sharded._merge_results_into_store(merged, manifest, [_result(0), _result(1)])

    papers = [paper for paper in merged_twice["papers"] if paper["evaluation_id"] == 12]
    assert len(papers) == 2
    assert len(merged_twice["debate_rounds"]) == 1
    assert len(merged_twice["judge_verdicts"]) == 1
    assert merged_twice["evaluations"][0]["status"] == "COMPLETED"
    assert merged_twice["evaluations"][0]["completed_papers"] == 2


def test_merge_marks_mixed_terminal_results_partial():
    store = core._empty_store()
    store["evaluations"].append({"id": 7, "status": "RUNNING"})
    completed = _result(0)
    timed_out = sharded._serialize_failure(1, _paper(1), {
        "status": "TIMED_OUT",
        "stage": "Judge 3/5",
        "error": "Timed out after 600 seconds",
    })

    merged = sharded._merge_results_into_store(
        store,
        {"eval_id": 7, "total_papers": 2},
        [completed, timed_out],
    )

    assert merged["evaluations"][0]["status"] == "PARTIAL"
    assert merged["papers"][1]["generation_status"] == "TIMED_OUT"
    assert merged["papers"][1]["generation_stage"] == "Judge 3/5"


def test_retried_shard_skips_existing_paper_result(monkeypatch):
    manifest = {
        "eval_id": 9,
        "problem": "Problem",
        "model": "model",
        "papers": [_paper(0).__dict__],
        "total_papers": 1,
        "shard_size": 10,
        "total_shards": 1,
        "worker_concurrency": 1,
    }
    marker = {}
    monkeypatch.setattr(sharded, "load_manifest", lambda _: manifest)
    monkeypatch.setattr(sharded, "_download_json", lambda name: {"already": "complete"})
    monkeypatch.setattr(sharded, "_upload_json", lambda name, payload: marker.update(payload))
    monkeypatch.setattr(sharded, "try_finalize", lambda _: False)
    monkeypatch.setattr(sharded.genai, "Client", lambda **_: object())

    class FailIfCalledEngine:
        def __init__(self, **_):
            pass

        async def run_debate(self, *_args, **_kwargs):
            raise AssertionError("Completed paper was evaluated again")

    monkeypatch.setattr(sharded, "DebateEngine", FailIfCalledEngine)

    result = asyncio.run(sharded.run_shard(9, 0, "secret"))

    assert result["status"] == "COMPLETED"
    assert marker["shard_index"] == 0


def test_production_background_endpoint_launches_retryable_shards(monkeypatch):
    import server

    api_client = TestClient(server.app)
    papers = [_paper(index) for index in range(21)]
    monkeypatch.setattr(server.core, "DB_GCS_BUCKET", "test-bucket")
    monkeypatch.setattr(server, "resolve_gemini_api_key", lambda *_: "configured-secret")
    monkeypatch.setattr(server, "fetch_arxiv_papers", lambda **_: papers)
    monkeypatch.setattr(sharded, "create_evaluation", lambda problem, model, total: 42)
    monkeypatch.setattr(
        sharded,
        "create_manifest",
        lambda **_: {"eval_id": 42, "total_shards": 3},
    )
    launched = {}
    monkeypatch.setattr(
        sharded,
        "launch_job",
        lambda eval_id, total_shards: launched.update(eval_id=eval_id, total_shards=total_shards) or "operations/1",
    )

    response = api_client.post("/api/evaluate/background", json={
        "problem_statement": "Problem",
        "model_name": "model",
        "paper_source": "arxiv",
        "max_papers": 21,
    })

    assert response.status_code == 200
    assert response.json()["eval_id"] == 42
    assert response.json()["total_papers"] == 21
    assert response.json()["total_shards"] == 3
    assert launched == {"eval_id": 42, "total_shards": 3}