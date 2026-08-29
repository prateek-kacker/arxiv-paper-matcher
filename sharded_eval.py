"""Durable, retryable evaluation shards backed by immutable GCS objects."""

import asyncio
import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

import google.auth
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.auth.transport.requests import AuthorizedSession
from google.cloud import storage
from google import genai

import core_engine as core
from core_engine import DebateEngine, Paper


SHARDED_PREFIX = os.environ.get(
    "PAPER_MATCHER_SHARDED_PREFIX",
    "archive-paper-matcher/sharded-evaluations",
).strip("/")
SHARD_SIZE = int(os.environ.get("PAPER_MATCHER_SHARD_SIZE", "10"))
FINALIZE_RETRIES = 8


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _eval_prefix(eval_id: int) -> str:
    return f"{SHARDED_PREFIX}/eval_{int(eval_id):06d}"


def _manifest_name(eval_id: int) -> str:
    return f"{_eval_prefix(eval_id)}/manifest.json"


def _paper_result_name(eval_id: int, paper_index: int) -> str:
    return f"{_eval_prefix(eval_id)}/papers/{int(paper_index):06d}.json"


def _shard_marker_name(eval_id: int, shard_index: int) -> str:
    return f"{_eval_prefix(eval_id)}/shards/{int(shard_index):04d}.json"


def _bucket():
    if not core.DB_GCS_BUCKET:
        raise RuntimeError("PAPER_MATCHER_DB_BUCKET is required for sharded evaluations.")
    return storage.Client().bucket(core.DB_GCS_BUCKET)


def _upload_create_only(name: str, payload: dict) -> bool:
    try:
        _bucket().blob(name).upload_from_string(
            json.dumps(payload, ensure_ascii=True),
            content_type="application/json",
            if_generation_match=0,
        )
        return True
    except PreconditionFailed:
        return False


def _upload_json(name: str, payload: dict) -> None:
    _bucket().blob(name).upload_from_string(
        json.dumps(payload, ensure_ascii=True),
        content_type="application/json",
    )


def _download_json(name: str) -> Optional[dict]:
    try:
        return json.loads(_bucket().blob(name).download_as_bytes())
    except NotFound:
        return None


def _publish_local_store(store: dict) -> None:
    core.publish_local_store(store)


def create_evaluation(problem: str, model: str, total_papers: int) -> int:
    """Allocate an evaluation ID without overwriting a concurrent store writer."""
    store_blob = _bucket().blob(core.DB_GCS_BLOB)
    for _ in range(FINALIZE_RETRIES):
        store_blob.reload()
        generation = int(store_blob.generation)
        store = json.loads(store_blob.download_as_bytes(if_generation_match=generation))
        core.ensure_store_schema(store)
        eval_id = int(store["meta"]["next_ids"]["evaluations"])
        store["meta"]["next_ids"]["evaluations"] = eval_id + 1
        store["evaluations"].append({
            "id": eval_id,
            "problem_text": problem,
            "model_name": model,
            "status": "RUNNING",
            "total_papers": int(total_papers),
            "completed_papers": 0,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        try:
            store_blob.upload_from_string(
                json.dumps(store, ensure_ascii=True),
                content_type="application/json",
                if_generation_match=generation,
            )
            _publish_local_store(store)
            return eval_id
        except PreconditionFailed:
            continue
    raise RuntimeError("Could not create evaluation after concurrent store updates.")


def update_evaluation_status(eval_id: int, status: str) -> None:
    store_blob = _bucket().blob(core.DB_GCS_BLOB)
    for _ in range(FINALIZE_RETRIES):
        store_blob.reload()
        generation = int(store_blob.generation)
        store = json.loads(store_blob.download_as_bytes(if_generation_match=generation))
        evaluation = next(
            (item for item in store.get("evaluations", []) if int(item.get("id", -1)) == int(eval_id)),
            None,
        )
        if not evaluation:
            raise RuntimeError(f"Evaluation {eval_id} is missing from the primary store.")
        evaluation["status"] = status
        try:
            store_blob.upload_from_string(
                json.dumps(store, ensure_ascii=True),
                content_type="application/json",
                if_generation_match=generation,
            )
            _publish_local_store(store)
            return
        except PreconditionFailed:
            continue
    raise RuntimeError(f"Could not update evaluation {eval_id} after concurrent store updates.")


def create_manifest(
    *,
    eval_id: int,
    problem: str,
    model: str,
    papers: list[Paper],
    max_concurrent: int,
    shard_size: int = SHARD_SIZE,
) -> dict:
    if not papers:
        raise ValueError("Cannot create a sharded evaluation without papers.")
    if shard_size < 1:
        raise ValueError("Shard size must be at least one.")

    total_shards = (len(papers) + shard_size - 1) // shard_size
    manifest = {
        "version": 1,
        "eval_id": int(eval_id),
        "problem": problem,
        "model": model,
        "papers": [asdict(paper) for paper in papers],
        "total_papers": len(papers),
        "shard_size": shard_size,
        "total_shards": total_shards,
        "worker_concurrency": max(1, min(int(max_concurrent), 2)),
        "status": "RUNNING",
        "created_at": _utc_now(),
    }
    if not _upload_create_only(_manifest_name(eval_id), manifest):
        raise RuntimeError(f"Manifest already exists for evaluation {eval_id}.")
    return manifest


def prepare_resume(
    *,
    eval_id: int,
    papers: list[Paper],
    max_concurrent: int = 2,
) -> dict:
    """Create a manifest and seed immutable outputs from already-saved results."""
    store_blob = _bucket().blob(core.DB_GCS_BLOB)
    store_blob.reload()
    store = json.loads(store_blob.download_as_bytes(if_generation_match=int(store_blob.generation)))
    evaluation = next(
        (item for item in store.get("evaluations", []) if int(item.get("id", -1)) == int(eval_id)),
        None,
    )
    if not evaluation:
        raise RuntimeError(f"Evaluation {eval_id} is missing from the primary store.")

    candidate_urls = {paper.url for paper in papers}
    saved_papers = [
        item for item in store.get("papers", [])
        if int(item.get("evaluation_id", -1)) == int(eval_id)
    ]
    unexpected_urls = sorted(
        str(item.get("url", "")) for item in saved_papers
        if str(item.get("url", "")) not in candidate_urls
    )
    if unexpected_urls:
        raise ValueError(
            f"Resume paper set does not contain {len(unexpected_urls)} saved URL(s); refusing recovery."
        )

    manifest = create_manifest(
        eval_id=eval_id,
        problem=evaluation["problem_text"],
        model=evaluation["model_name"],
        papers=papers,
        max_concurrent=max_concurrent,
    )
    paper_by_url = {str(item.get("url", "")): item for item in saved_papers}
    rounds_by_paper: dict[int, list[dict]] = {}
    for item in store.get("debate_rounds", []):
        rounds_by_paper.setdefault(int(item.get("paper_id", -1)), []).append(item)
    verdicts_by_paper: dict[int, list[dict]] = {}
    for item in store.get("judge_verdicts", []):
        verdicts_by_paper.setdefault(int(item.get("paper_id", -1)), []).append(item)

    seeded = 0
    for paper_index, paper in enumerate(papers):
        saved = paper_by_url.get(paper.url)
        if not saved:
            continue
        paper_id = int(saved["id"])
        rounds = sorted(rounds_by_paper.get(paper_id, []), key=lambda item: int(item.get("round_num", 0)))
        verdicts = sorted(verdicts_by_paper.get(paper_id, []), key=lambda item: int(item.get("judge_run", 0)))
        payload = {
            "paper_index": paper_index,
            "paper_key": hashlib.sha256(paper.url.encode("utf-8")).hexdigest(),
            "paper": asdict(paper),
            "avg_score": float(saved.get("avg_score", 0)),
            "rounds": [{
                "advocate_argument": item.get("advocate_arg", ""),
                "skeptic_argument": item.get("skeptic_arg", ""),
            } for item in rounds],
            "verdicts": [{
                "run": int(item.get("judge_run", 0)),
                "seed": int(item.get("seed", 0)),
                "relevance_score": int(item.get("relevance_score", 0)),
                "verdict": item.get("verdict", ""),
                "key_reasons": item.get("key_reasons", []),
                "suggested_use": item.get("suggested_use", ""),
            } for item in verdicts],
            "generation_status": saved.get("generation_status", "COMPLETED"),
            "generation_stage": saved.get("generation_stage", "Completed"),
            "generation_message": saved.get("generation_message", ""),
            "completed_at": _utc_now(),
            "seeded_from_primary_store": True,
        }
        if _upload_create_only(_paper_result_name(eval_id, paper_index), payload):
            seeded += 1

    return {**manifest, "seeded_papers": seeded, "missing_papers": len(papers) - len(saved_papers)}


def load_manifest(eval_id: int) -> dict:
    manifest = _download_json(_manifest_name(eval_id))
    if not manifest:
        raise RuntimeError(f"Manifest not found for evaluation {eval_id}.")
    return manifest


def shard_bounds(total_papers: int, shard_size: int, shard_index: int) -> tuple[int, int]:
    start = int(shard_index) * int(shard_size)
    return start, min(start + int(shard_size), int(total_papers))


def get_progress(eval_id: int) -> Optional[dict]:
    manifest = _download_json(_manifest_name(eval_id))
    if not manifest:
        return None
    prefix = _eval_prefix(eval_id)
    names = [blob.name for blob in _bucket().list_blobs(prefix=f"{prefix}/")]
    completed = sum(1 for name in names if name.startswith(f"{prefix}/papers/") and name.endswith(".json"))
    completed_shards = sum(1 for name in names if name.startswith(f"{prefix}/shards/") and name.endswith(".json"))
    retry_events = sum(1 for name in names if name.startswith(f"{prefix}/attempts/") and name.endswith(".json"))
    total = int(manifest["total_papers"])
    stage = "Finalizing results" if completed >= total else "Generating debates and judge verdicts"
    return {
        "completed_papers": completed,
        "total_papers": total,
        "completed_shards": completed_shards,
        "total_shards": int(manifest["total_shards"]),
        "retry_events": retry_events,
        "generation_stage": stage,
        "generation_message": (
            f"{completed_shards}/{manifest['total_shards']} batches complete; "
            f"{retry_events} retry event(s) recorded."
        ),
    }


def _serialize_result(paper_index: int, result, stage: str) -> dict:
    return {
        "paper_index": int(paper_index),
        "paper_key": hashlib.sha256(result.paper.url.encode("utf-8")).hexdigest(),
        "paper": asdict(result.paper),
        "avg_score": float(result.avg_score),
        "rounds": [asdict(round_) for round_ in result.rounds],
        "verdicts": [asdict(verdict) for verdict in result.judge_verdicts],
        "generation_status": "COMPLETED",
        "generation_stage": stage or "Completed",
        "generation_message": "",
        "completed_at": _utc_now(),
    }


def _serialize_failure(paper_index: int, paper: Paper, failure: dict) -> dict:
    status = failure.get("status", "FAILED")
    return {
        "paper_index": int(paper_index),
        "paper_key": hashlib.sha256(paper.url.encode("utf-8")).hexdigest(),
        "paper": asdict(paper),
        "avg_score": 0.0,
        "rounds": [],
        "verdicts": [],
        "generation_status": status,
        "generation_stage": failure.get("stage", "Unknown stage"),
        "generation_message": failure.get("error", "Generation failed after retries."),
        "completed_at": _utc_now(),
    }


async def run_shard(eval_id: int, shard_index: int, api_key: str) -> dict:
    manifest = load_manifest(eval_id)
    total_shards = int(manifest["total_shards"])
    if shard_index < 0 or shard_index >= total_shards:
        raise ValueError(f"Shard index {shard_index} is outside 0..{total_shards - 1}.")

    start, end = shard_bounds(
        int(manifest["total_papers"]),
        int(manifest["shard_size"]),
        shard_index,
    )
    client = genai.Client(api_key=api_key)
    engine = DebateEngine(client=client, model_name=manifest["model"])
    semaphore = asyncio.Semaphore(int(manifest.get("worker_concurrency", 1)))
    failures: list[dict] = []

    async def evaluate_one(paper_index: int) -> None:
        result_name = _paper_result_name(eval_id, paper_index)
        if _download_json(result_name):
            return

        paper = Paper(**manifest["papers"][paper_index])
        last_stage = "Starting Gemini evaluation"

        def status_callback(stage: str) -> None:
            nonlocal last_stage
            last_stage = stage

        async with semaphore:
            try:
                result = await asyncio.wait_for(
                    engine.run_debate(paper, manifest["problem"], status_callback=status_callback),
                    timeout=max(core.PAPER_EVAL_TIMEOUT_SECONDS, 30),
                )
                payload = _serialize_result(paper_index, result, "Completed")
                _upload_create_only(result_name, payload)
            except (TimeoutError, RuntimeError, ValueError, OSError) as exc:
                failures.append({
                    "paper_index": paper_index,
                    "title": paper.title,
                    "stage": last_stage,
                    "error": str(exc) or type(exc).__name__,
                    "status": "TIMED_OUT" if isinstance(exc, TimeoutError) else "FAILED",
                })

    await asyncio.gather(*(evaluate_one(index) for index in range(start, end)))

    missing = [index for index in range(start, end) if not _download_json(_paper_result_name(eval_id, index))]
    if missing:
        attempt = int(os.environ.get("CLOUD_RUN_TASK_ATTEMPT", "0"))
        max_retries = int(os.environ.get("PAPER_MATCHER_MAX_TASK_RETRIES", "3"))
        _upload_json(
            f"{_eval_prefix(eval_id)}/attempts/shard_{shard_index:04d}_attempt_{attempt:02d}.json",
            {"status": "FAILED", "missing": missing, "failures": failures, "recorded_at": _utc_now()},
        )
        if attempt < max_retries:
            raise RuntimeError(f"Shard {shard_index} has {len(missing)} incomplete paper(s): {missing}")

        failures_by_index = {int(failure["paper_index"]): failure for failure in failures}
        for paper_index in missing:
            paper = Paper(**manifest["papers"][paper_index])
            failure = failures_by_index.get(paper_index, {
                "status": "FAILED",
                "stage": "Persisting result",
                "error": "Result remained incomplete after all task retries.",
            })
            _upload_create_only(
                _paper_result_name(eval_id, paper_index),
                _serialize_failure(paper_index, paper, failure),
            )

    marker = {
        "eval_id": int(eval_id),
        "shard_index": int(shard_index),
        "start": start,
        "end": end,
        "status": "PARTIAL" if failures else "COMPLETED",
        "completed_at": _utc_now(),
    }
    _upload_json(_shard_marker_name(eval_id, shard_index), marker)
    finalized = try_finalize(eval_id)
    return {**marker, "evaluation_finalized": finalized}


def _merge_results_into_store(store: dict, manifest: dict, results: list[dict]) -> dict:
    eval_id = int(manifest["eval_id"])
    evaluation = next(
        (item for item in store.get("evaluations", []) if int(item.get("id", -1)) == eval_id),
        None,
    )
    if not evaluation:
        raise RuntimeError(f"Evaluation {eval_id} is missing from the primary store.")

    meta = store.setdefault("meta", {}).setdefault("next_ids", {})
    for entity in ("papers", "debate_rounds", "judge_verdicts"):
        if entity not in meta:
            meta[entity] = max((int(item.get("id", 0)) for item in store.get(entity, [])), default=0) + 1

    existing_urls = {
        str(item.get("url", ""))
        for item in store.get("papers", [])
        if int(item.get("evaluation_id", -1)) == eval_id
    }

    for result in sorted(results, key=lambda item: int(item["paper_index"])):
        paper_data = result["paper"]
        if paper_data["url"] in existing_urls:
            continue
        paper_id = int(meta["papers"])
        meta["papers"] = paper_id + 1
        store.setdefault("papers", []).append({
            "id": paper_id,
            "evaluation_id": eval_id,
            **paper_data,
            "avg_score": float(result["avg_score"]),
            "generation_status": result.get("generation_status", "COMPLETED"),
            "generation_stage": result.get("generation_stage", "Completed"),
            "generation_message": result.get("generation_message", ""),
        })
        existing_urls.add(paper_data["url"])

        for round_number, round_data in enumerate(result.get("rounds", []), start=1):
            round_id = int(meta["debate_rounds"])
            meta["debate_rounds"] = round_id + 1
            store.setdefault("debate_rounds", []).append({
                "id": round_id,
                "paper_id": paper_id,
                "round_num": round_number,
                "advocate_arg": round_data.get("advocate_argument", ""),
                "skeptic_arg": round_data.get("skeptic_argument", ""),
            })

        for verdict_data in result.get("verdicts", []):
            verdict_id = int(meta["judge_verdicts"])
            meta["judge_verdicts"] = verdict_id + 1
            store.setdefault("judge_verdicts", []).append({
                "id": verdict_id,
                "paper_id": paper_id,
                "judge_run": int(verdict_data.get("run", 0)),
                "seed": int(verdict_data.get("seed", 0)),
                "relevance_score": int(verdict_data.get("relevance_score", 0)),
                "verdict": verdict_data.get("verdict", ""),
                "key_reasons": verdict_data.get("key_reasons", []),
                "suggested_use": verdict_data.get("suggested_use", ""),
            })

    completed = sum(
        1
        for item in store.get("papers", [])
        if int(item.get("evaluation_id", -1)) == eval_id
    )
    evaluation["completed_papers"] = completed
    evaluation["total_papers"] = int(manifest["total_papers"])
    eval_results = [
        result for result in results
        if int(result.get("paper_index", -1)) < int(manifest["total_papers"])
    ]
    successful = sum(1 for result in eval_results if result.get("generation_status") == "COMPLETED")
    if successful == len(eval_results):
        evaluation["status"] = "COMPLETED"
    elif successful == 0:
        evaluation["status"] = "FAILED"
    else:
        evaluation["status"] = "PARTIAL"
    evaluation["updated_at"] = _utc_now()
    return store


def _load_all_results(manifest: dict) -> Optional[list[dict]]:
    results = []
    eval_id = int(manifest["eval_id"])
    for paper_index in range(int(manifest["total_papers"])):
        result = _download_json(_paper_result_name(eval_id, paper_index))
        if not result:
            return None
        results.append(result)
    return results


def try_finalize(eval_id: int) -> bool:
    manifest = load_manifest(eval_id)
    results = _load_all_results(manifest)
    if results is None:
        return False

    bucket = _bucket()
    store_blob = bucket.blob(core.DB_GCS_BLOB)
    merged_store = None
    for _ in range(FINALIZE_RETRIES):
        store_blob.reload()
        generation = int(store_blob.generation)
        store = json.loads(store_blob.download_as_bytes(if_generation_match=generation))
        merged_store = _merge_results_into_store(store, manifest, results)
        try:
            store_blob.upload_from_string(
                json.dumps(merged_store, ensure_ascii=True),
                content_type="application/json",
                if_generation_match=generation,
            )
            break
        except PreconditionFailed:
            merged_store = None
    if merged_store is None:
        raise RuntimeError(f"Could not finalize evaluation {eval_id} after concurrent store updates.")

    _publish_local_store(merged_store)

    final_evaluation = next(item for item in merged_store["evaluations"] if int(item["id"]) == int(eval_id))
    manifest["status"] = final_evaluation["status"]
    manifest["completed_at"] = _utc_now()
    _upload_json(_manifest_name(eval_id), manifest)

    evaluation = final_evaluation
    papers = [item for item in merged_store["papers"] if int(item["evaluation_id"]) == int(eval_id)]
    paper_ids = {int(item["id"]) for item in papers}
    snapshot = {
        "evaluation": evaluation,
        "papers": papers,
        "debate_rounds": [item for item in merged_store["debate_rounds"] if int(item["paper_id"]) in paper_ids],
        "judge_verdicts": [item for item in merged_store["judge_verdicts"] if int(item["paper_id"]) in paper_ids],
    }
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    _upload_create_only(
        f"{core.EVALRUN_GCS_PREFIX.rstrip('/')}/eval_{int(eval_id):06d}_{timestamp}.json",
        snapshot,
    )
    return True


def launch_job(eval_id: int, total_shards: int) -> str:
    project = os.environ.get("GCP_PROJECT_ID", "").strip()
    region = os.environ.get("GCP_LOCATION", os.environ.get("GCP_REGION", "us-central1")).strip()
    job_name = os.environ.get("PAPER_MATCHER_JOB_NAME", "archive-paper-matcher-job").strip()
    if not project:
        raise RuntimeError("GCP_PROJECT_ID is required to launch sharded jobs.")

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    session = AuthorizedSession(credentials)
    url = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{job_name}:run"
    response = session.post(
        url,
        json={
            "overrides": {
                "taskCount": int(total_shards),
                "containerOverrides": [{
                    "args": ["batch_runner.py", "--sharded-worker", "--eval-id", str(int(eval_id))],
                }],
            }
        },
        timeout=30,
    )
    if response.status_code not in {200, 201}:
        raise RuntimeError(f"Cloud Run Job launch failed ({response.status_code}): {response.text[:500]}")
    operation = response.json()
    return operation.get("name", "")