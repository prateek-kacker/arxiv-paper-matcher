"""
arXiv CS.CL Paper Matcher — Core Engine
========================================
Core data models, bucket-backed JSON persistence, Cloud Storage (GCS/S3) sync, arXiv API fetcher,
and Multi-Agent Debate Engine with 5-Judge Panel.
"""

import os
import json
import asyncio
import random
import threading
import time
import concurrent.futures
from dataclasses import dataclass, field
from typing import Optional, Callable, AsyncGenerator
from datetime import datetime, timedelta
from pathlib import Path
from urllib import request, error

import arxiv
from google import genai
from google.genai import types
from google.cloud import storage

# ──────────────────────────────────────────────────────────────────────────────
# Database & Cloud Sync Settings
# ──────────────────────────────────────────────────────────────────────────────

# Keep DB_PATH name for compatibility with server status endpoint/tests.
DB_PATH = Path(
    os.environ.get(
        "PAPER_MATCHER_STORE_PATH",
        os.environ.get("PAPER_MATCHER_DB_PATH", str(Path(__file__).parent / "paper_matcher_store.json")),
    )
)


def _parse_gs_uri(uri: str) -> tuple[str, str]:
    raw = (uri or "").strip()
    if not raw.startswith("gs://"):
        return "", ""
    body = raw[5:]
    if "/" in body:
        bucket, blob = body.split("/", 1)
    else:
        bucket, blob = body, ""
    return bucket.strip(), blob.strip()


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    raw = (uri or "").strip()
    if not raw.startswith("s3://"):
        return "", ""
    body = raw[5:]
    if "/" in body:
        bucket, key = body.split("/", 1)
    else:
        bucket, key = body, ""
    return bucket.strip(), key.strip()


_store_gcs_uri = os.environ.get("PAPER_MATCHER_STORE_GCS_URI", "").strip()
_uri_bucket, _uri_blob = _parse_gs_uri(_store_gcs_uri)
DB_GCS_BUCKET = _uri_bucket or os.environ.get("PAPER_MATCHER_DB_BUCKET", "").strip()
DB_GCS_BLOB = (
    _uri_blob
    or os.environ.get("PAPER_MATCHER_DB_BLOB", "archive-paper-matcher/store/paper_matcher_store.json").strip()
    or "archive-paper-matcher/store/paper_matcher_store.json"
)

_store_s3_uri = os.environ.get("PAPER_MATCHER_STORE_S3_URI", "").strip()
_s3_uri_bucket, _s3_uri_key = _parse_s3_uri(_store_s3_uri)
AWS_S3_BUCKET = _s3_uri_bucket or os.environ.get("AWS_S3_BUCKET", "").strip()
AWS_S3_KEY = (
    _s3_uri_key
    or os.environ.get("AWS_S3_KEY", "archive-paper-matcher/store/paper_matcher_store.json").strip()
    or "archive-paper-matcher/store/paper_matcher_store.json"
)

EVALRUN_GCS_PREFIX = (
    os.environ.get("PAPER_MATCHER_EVALRUN_GCS_PREFIX", "archive-paper-matcher/evalruns/").strip()
    or "archive-paper-matcher/evalruns/"
)
EVALRUN_S3_PREFIX = (
    os.environ.get("PAPER_MATCHER_EVALRUN_S3_PREFIX", "archive-paper-matcher/evalruns/").strip()
    or "archive-paper-matcher/evalruns/"
)
STORE_GCS_URI = f"gs://{DB_GCS_BUCKET}/{DB_GCS_BLOB}" if DB_GCS_BUCKET else ""
STORE_S3_URI = f"s3://{AWS_S3_BUCKET}/{AWS_S3_KEY}" if AWS_S3_BUCKET else ""
EVALRUN_GCS_URI_PREFIX = f"gs://{DB_GCS_BUCKET}/{EVALRUN_GCS_PREFIX.rstrip('/')}/" if DB_GCS_BUCKET else ""
EVALRUN_S3_URI_PREFIX = f"s3://{AWS_S3_BUCKET}/{EVALRUN_S3_PREFIX.rstrip('/')}/" if AWS_S3_BUCKET else ""
PAPER_EVAL_TIMEOUT_SECONDS = int(os.environ.get("PAPER_EVAL_TIMEOUT_SECONDS", "600"))
_DB_SYNC_LOCK = threading.Lock()
_STORE_LOCK = threading.RLock()


def _empty_store() -> dict:
    return {
        "meta": {
            "next_ids": {
                "evaluations": 1,
                "papers": 1,
                "debate_rounds": 1,
                "judge_verdicts": 1,
                "recurring_schedules": 1,
                "acl_papers": 1,
            }
        },
        "evaluations": [],
        "papers": [],
        "debate_rounds": [],
        "judge_verdicts": [],
        "recurring_schedules": [],
        "acl_papers": [],
    }


_STORE: dict = _empty_store()


def _ensure_store_schema(store: dict):
    store.setdefault("meta", {})
    store.setdefault("evaluations", [])
    store.setdefault("papers", [])
    store.setdefault("debate_rounds", [])
    store.setdefault("judge_verdicts", [])
    store.setdefault("recurring_schedules", [])
    store.setdefault("acl_papers", [])
    next_ids = store["meta"].setdefault("next_ids", {})
    for key in ["evaluations", "papers", "debate_rounds", "judge_verdicts", "recurring_schedules", "acl_papers"]:
        if key not in next_ids:
            max_id = max((int(item.get("id", 0)) for item in store.get(key, [])), default=0)
            next_ids[key] = max_id + 1


def ensure_store_schema(store: dict) -> None:
    _ensure_store_schema(store)


def _atomic_write_store(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True)
    tmp.replace(path)


def _save_store_local():
    _atomic_write_store(DB_PATH, _STORE)


def publish_local_store(store: dict) -> None:
    global _STORE
    with _STORE_LOCK:
        _STORE = store
        _save_store_local()


def _load_store_local() -> bool:
    global _STORE
    if not DB_PATH.exists():
        return False
    with DB_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    _ensure_store_schema(data)
    _STORE = data
    return True


def _next_id(entity: str) -> int:
    cur = int(_STORE["meta"]["next_ids"][entity])
    _STORE["meta"]["next_ids"][entity] = cur + 1
    return cur


def _emit_evalrun_snapshot_to_cloud(eval_id: int):
    """Upload per-eval immutable snapshot to a unique cloud object key."""
    with _STORE_LOCK:
        ev = next((dict(e) for e in _STORE["evaluations"] if int(e.get("id", -1)) == int(eval_id)), None)
        if not ev:
            return
        papers = [dict(p) for p in _STORE["papers"] if int(p.get("evaluation_id", -1)) == int(eval_id)]
        paper_ids = {int(p["id"]) for p in papers}
        rounds = [dict(r) for r in _STORE["debate_rounds"] if int(r.get("paper_id", -1)) in paper_ids]
        verdicts = [dict(v) for v in _STORE["judge_verdicts"] if int(v.get("paper_id", -1)) in paper_ids]

    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    filename = f"eval_{int(eval_id):06d}_{timestamp}.json"
    payload = {
        "evaluation": ev,
        "papers": papers,
        "debate_rounds": rounds,
        "judge_verdicts": verdicts,
    }

    with _DB_SYNC_LOCK:
        if DB_GCS_BUCKET:
            try:
                client = storage.Client()
                bucket = client.bucket(DB_GCS_BUCKET)
                blob = bucket.blob(EVALRUN_GCS_PREFIX.rstrip("/") + "/" + filename)
                blob.upload_from_string(json.dumps(payload, ensure_ascii=True), content_type="application/json")
            except Exception:
                pass

        if AWS_S3_BUCKET:
            try:
                import boto3
                s3 = boto3.client("s3")
                s3.put_object(
                    Bucket=AWS_S3_BUCKET,
                    Key=EVALRUN_S3_PREFIX.rstrip("/") + "/" + filename,
                    Body=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
                    ContentType="application/json",
                )
            except Exception:
                pass


def sync_db_from_cloud() -> tuple[bool, str]:
    """Download store file from Cloud Storage (GCS or AWS S3)."""
    if DB_GCS_BUCKET:
        try:
            with _DB_SYNC_LOCK:
                client = storage.Client()
                bucket = client.bucket(DB_GCS_BUCKET)
                blob = bucket.blob(DB_GCS_BLOB)
                if not blob.exists(client):
                    return False, f"Store does not exist in GCS bucket `{DB_GCS_BUCKET}`"
                DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                with _STORE_LOCK:
                    blob.download_to_filename(str(DB_PATH))
                    loaded = _load_store_local()
                if loaded:
                    return True, f"Store downloaded from GCS bucket `{DB_GCS_BUCKET}`"
                return False, "Downloaded blob but failed to parse store"
        except Exception as e:
            return False, f"Failed to load store from GCS: {e}"

    if AWS_S3_BUCKET:
        try:
            with _DB_SYNC_LOCK:
                import boto3
                s3 = boto3.client("s3")
                DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                with _STORE_LOCK:
                    s3.download_file(AWS_S3_BUCKET, AWS_S3_KEY, str(DB_PATH))
                    loaded = _load_store_local()
            if loaded:
                return True, f"Store downloaded from AWS S3 bucket `{AWS_S3_BUCKET}`"
            return False, "Downloaded blob but failed to parse store"
        except Exception as e:
            return False, f"Failed to load store from S3: {e}"

    return False, "Cloud persistence disabled (neither GCS nor S3 bucket set)"


def sync_db_to_cloud() -> tuple[bool, str]:
    """Upload local store file to Cloud Storage (GCS and/or AWS S3)."""
    with _STORE_LOCK:
        _save_store_local()

    if not DB_GCS_BUCKET and not AWS_S3_BUCKET:
        return False, "Cloud persistence disabled"

    results = []
    with _DB_SYNC_LOCK:
        if DB_GCS_BUCKET:
            try:
                client = storage.Client()
                bucket = client.bucket(DB_GCS_BUCKET)
                blob = bucket.blob(DB_GCS_BLOB)
                blob.upload_from_filename(str(DB_PATH))
                results.append(f"GCS ({STORE_GCS_URI})")
            except Exception as e:
                results.append(f"GCS error: {e}")

        if AWS_S3_BUCKET:
            try:
                import boto3
                s3 = boto3.client("s3")
                s3.upload_file(str(DB_PATH), AWS_S3_BUCKET, AWS_S3_KEY)
                results.append(f"S3 ({STORE_S3_URI})")
            except Exception as e:
                results.append(f"S3 error: {e}")

    return True, f"Uploaded to: {', '.join(results)}"


class _CompatibilityCursor:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _CompatibilityConnection:
    """Compatibility shim for legacy scripts/tests that call get_db()."""

    def execute(self, sql: str, params=None):
        norm = " ".join(sql.lower().split())
        if "from metadata_tables" in norm:
            return _CompatibilityCursor([
                ("evaluations",),
                ("papers",),
                ("debate_rounds",),
                ("judge_verdicts",),
                ("recurring_schedules",),
                ("acl_papers",),
            ])
        raise RuntimeError("get_db() SQL compatibility is limited. Use core_engine data APIs instead.")

    def close(self):
        return None


def get_db() -> _CompatibilityConnection:
    return _CompatibilityConnection()


def init_db():
    """Initialize bucket-backed JSON store file if needed."""
    global _STORE
    with _STORE_LOCK:
        if DB_PATH.exists():
            try:
                if not _load_store_local():
                    _STORE = _empty_store()
                    _ensure_store_schema(_STORE)
                    _save_store_local()
                    return
            except Exception:
                _STORE = _empty_store()
                _ensure_store_schema(_STORE)
                _save_store_local()
                return
        else:
            _STORE = _empty_store()
        _ensure_store_schema(_STORE)
        _save_store_local()


def save_evaluation(problem: str, model: str, status: str = 'COMPLETED', total: int = 0, sync_cloud: bool = True) -> int:
    with _STORE_LOCK:
        eval_id = _next_id("evaluations")
        _STORE["evaluations"].append({
            "id": eval_id,
            "problem_text": problem,
            "model_name": model,
            "status": status,
            "total_papers": int(total or 0),
            "completed_papers": 0,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        _save_store_local()
    if sync_cloud:
        sync_db_to_cloud()
    return eval_id


def update_evaluation_progress(
    eval_id: int,
    completed: int,
    total: Optional[int] = None,
    status: Optional[str] = None,
    sync_cloud: bool = True,
    emit_snapshot: bool = False,
):
    with _STORE_LOCK:
        for ev in _STORE["evaluations"]:
            if int(ev["id"]) == int(eval_id):
                ev["completed_papers"] = int(completed)
                if total is not None:
                    ev["total_papers"] = int(total)
                if status is not None:
                    ev["status"] = status
                break
        _save_store_local()
    if sync_cloud:
        sync_db_to_cloud()
        if emit_snapshot or status in {"COMPLETED", "FAILED"}:
            _emit_evalrun_snapshot_to_cloud(eval_id)


def load_evaluation_paper_urls(eval_id: int) -> set[str]:
    """Return unique non-empty paper URLs already saved for an evaluation."""
    with _STORE_LOCK:
        return {
            str(p.get("url", ""))
            for p in _STORE["papers"]
            if int(p.get("evaluation_id", -1)) == int(eval_id) and str(p.get("url", "")).strip()
        }


def save_paper(
    eval_id: int,
    paper: "Paper",
    avg_score: float,
    sync_cloud: bool = True,
    generation_status: str = "COMPLETED",
    generation_stage: str = "Completed",
    generation_message: str = "",
) -> int:
    with _STORE_LOCK:
        paper_id = _next_id("papers")
        _STORE["papers"].append({
            "id": paper_id,
            "evaluation_id": int(eval_id),
            "title": paper.title,
            "authors": paper.authors,
            "abstract": paper.abstract,
            "full_text": paper.full_text,
            "url": paper.url,
            "published": paper.published,
            "categories": paper.categories,
            "avg_score": float(avg_score),
            "generation_status": generation_status,
            "generation_stage": generation_stage,
            "generation_message": generation_message,
        })
        _save_store_local()
    if sync_cloud:
        sync_db_to_cloud()
    return paper_id


def save_debate_round(
    paper_id: int,
    round_num: int,
    advocate: str,
    skeptic: str,
    sync_cloud: bool = True,
):
    with _STORE_LOCK:
        _STORE["debate_rounds"].append({
            "id": _next_id("debate_rounds"),
            "paper_id": int(paper_id),
            "round_num": int(round_num),
            "advocate_arg": advocate,
            "skeptic_arg": skeptic,
        })
        _save_store_local()
    if sync_cloud:
        sync_db_to_cloud()


def save_judge_verdict(paper_id: int, run: int, seed: int, score: int,
                       verdict: str, reasons: list[str], suggested: str,
                       sync_cloud: bool = True):
    with _STORE_LOCK:
        _STORE["judge_verdicts"].append({
            "id": _next_id("judge_verdicts"),
            "paper_id": int(paper_id),
            "judge_run": int(run),
            "seed": int(seed),
            "relevance_score": int(score),
            "verdict": verdict,
            "key_reasons": list(reasons or []),
            "suggested_use": suggested,
        })
        _save_store_local()
    if sync_cloud:
        sync_db_to_cloud()


def load_past_evaluations() -> list[dict]:
    with _STORE_LOCK:
        out: list[dict] = []
        papers_by_eval: dict[int, list[dict]] = {}
        for p in _STORE["papers"]:
            papers_by_eval.setdefault(int(p["evaluation_id"]), []).append(p)
        for ev in _STORE["evaluations"]:
            ev_id = int(ev["id"])
            papers = papers_by_eval.get(ev_id, [])
            paper_count = len(papers)
            overall_avg = round(sum(float(p.get("avg_score", 0.0)) for p in papers) / paper_count, 1) if paper_count else None
            out.append({
                "id": ev_id,
                "problem_text": ev.get("problem_text", ""),
                "model_name": ev.get("model_name", ""),
                "status": ev.get("status", "COMPLETED"),
                "total_papers": int(ev.get("total_papers", 0)),
                "completed_papers": int(ev.get("completed_papers", paper_count)),
                "created_at": ev.get("created_at", ""),
                "paper_count": paper_count,
                "overall_avg": overall_avg,
            })
        out.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return out


def load_evaluation_papers(eval_id: int) -> list[dict]:
    with _STORE_LOCK:
        papers = [dict(p) for p in _STORE["papers"] if int(p.get("evaluation_id", -1)) == int(eval_id)]
        papers.sort(key=lambda p: float(p.get("avg_score", 0.0)), reverse=True)
        for p in papers:
            pid = int(p["id"])
            debates = [dict(d) for d in _STORE["debate_rounds"] if int(d.get("paper_id", -1)) == pid]
            debates.sort(key=lambda d: int(d.get("round_num", 0)))
            verdicts = [dict(v) for v in _STORE["judge_verdicts"] if int(v.get("paper_id", -1)) == pid]
            verdicts.sort(key=lambda v: int(v.get("judge_run", 0)))
            for v in verdicts:
                if not isinstance(v.get("key_reasons"), list):
                    try:
                        v["key_reasons"] = json.loads(v.get("key_reasons") or "[]")
                    except Exception:
                        v["key_reasons"] = []
            p["debates"] = debates
            p["verdicts"] = verdicts
        return papers


def delete_evaluation(eval_id: int, sync_cloud: bool = True):
    delete_evaluations([eval_id], sync_cloud=sync_cloud)


def delete_evaluations(eval_ids: list[int], sync_cloud: bool = True):
    if not eval_ids:
        return
    target = {int(i) for i in eval_ids}
    with _STORE_LOCK:
        paper_ids = {int(p["id"]) for p in _STORE["papers"] if int(p.get("evaluation_id", -1)) in target}
        _STORE["judge_verdicts"] = [v for v in _STORE["judge_verdicts"] if int(v.get("paper_id", -1)) not in paper_ids]
        _STORE["debate_rounds"] = [d for d in _STORE["debate_rounds"] if int(d.get("paper_id", -1)) not in paper_ids]
        _STORE["papers"] = [p for p in _STORE["papers"] if int(p.get("id", -1)) not in paper_ids]
        _STORE["evaluations"] = [e for e in _STORE["evaluations"] if int(e.get("id", -1)) not in target]
        _save_store_local()
    if sync_cloud:
        sync_db_to_cloud()


def delete_papers(paper_ids: list[int], sync_cloud: bool = True):
    target = {int(i) for i in paper_ids}
    with _STORE_LOCK:
        _STORE["judge_verdicts"] = [v for v in _STORE["judge_verdicts"] if int(v.get("paper_id", -1)) not in target]
        _STORE["debate_rounds"] = [d for d in _STORE["debate_rounds"] if int(d.get("paper_id", -1)) not in target]
        _STORE["papers"] = [p for p in _STORE["papers"] if int(p.get("id", -1)) not in target]
        _save_store_local()
    if sync_cloud:
        sync_db_to_cloud()


def load_all_papers() -> list[dict]:
    with _STORE_LOCK:
        eval_by_id = {int(e["id"]): e for e in _STORE["evaluations"]}
        papers = []
        for p in _STORE["papers"]:
            ev = eval_by_id.get(int(p.get("evaluation_id", -1)))
            if not ev:
                continue
            row = dict(p)
            row["problem_text"] = ev.get("problem_text", "")
            row["model_name"] = ev.get("model_name", "")
            row["eval_date"] = ev.get("created_at", "")
            pid = int(row["id"])
            verdicts = [v for v in _STORE["judge_verdicts"] if int(v.get("paper_id", -1)) == pid]
            verdicts.sort(key=lambda v: int(v.get("judge_run", 0)))
            row["judge_scores"] = [{"run": int(v.get("judge_run", 0)), "score": int(v.get("relevance_score", 0))} for v in verdicts]
            papers.append(row)
        papers.sort(key=lambda r: float(r.get("avg_score", 0.0)), reverse=True)
        return papers


def load_paper_detail(paper_id: int) -> dict:
    with _STORE_LOCK:
        paper = next((dict(p) for p in _STORE["papers"] if int(p.get("id", -1)) == int(paper_id)), None)
        if not paper:
            return {}
        ev = next((e for e in _STORE["evaluations"] if int(e.get("id", -1)) == int(paper.get("evaluation_id", -1))), None)
        if ev:
            paper["problem_text"] = ev.get("problem_text", "")
            paper["model_name"] = ev.get("model_name", "")
            paper["eval_date"] = ev.get("created_at", "")
        debates = [dict(d) for d in _STORE["debate_rounds"] if int(d.get("paper_id", -1)) == int(paper_id)]
        debates.sort(key=lambda d: int(d.get("round_num", 0)))
        verdicts = [dict(v) for v in _STORE["judge_verdicts"] if int(v.get("paper_id", -1)) == int(paper_id)]
        verdicts.sort(key=lambda v: int(v.get("judge_run", 0)))
        for v in verdicts:
            if not isinstance(v.get("key_reasons"), list):
                try:
                    v["key_reasons"] = json.loads(v.get("key_reasons") or "[]")
                except Exception:
                    v["key_reasons"] = []
        paper["debates"] = debates
        paper["verdicts"] = verdicts
        return paper


def create_recurring_schedule(
    label: str,
    problem_text: str,
    model_name: str,
    paper_source: str,
    acl_track: str,
    fetch_mode: str,
    max_papers: Optional[int],
    days_back: Optional[int],
    keyword_filter: str,
    min_score: int,
    max_concurrent: int,
    run_time: str,
    sync_cloud: bool = True,
) -> int:
    with _STORE_LOCK:
        schedule_id = _next_id("recurring_schedules")
        _STORE["recurring_schedules"].append({
            "id": schedule_id,
            "label": label,
            "problem_text": problem_text,
            "model_name": model_name,
            "paper_source": paper_source,
            "acl_track": acl_track,
            "fetch_mode": fetch_mode,
            "max_papers": max_papers,
            "days_back": days_back,
            "keyword_filter": keyword_filter,
            "min_score": int(min_score),
            "max_concurrent": int(max_concurrent),
            "run_time": run_time,
            "is_active": 1,
            "last_run_date": None,
            "last_run_at": None,
            "last_status": None,
            "last_message": None,
            "last_eval_id": None,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        _save_store_local()
    if sync_cloud:
        sync_db_to_cloud()
    return schedule_id


def update_recurring_schedule(
    schedule_id: int,
    label: str,
    problem_text: str,
    model_name: str,
    paper_source: str,
    acl_track: str,
    fetch_mode: str,
    max_papers: Optional[int],
    days_back: Optional[int],
    keyword_filter: str,
    min_score: int,
    max_concurrent: int,
    run_time: str,
    sync_cloud: bool = True,
):
    with _STORE_LOCK:
        for s in _STORE["recurring_schedules"]:
            if int(s.get("id", -1)) == int(schedule_id):
                s.update({
                    "label": label,
                    "problem_text": problem_text,
                    "model_name": model_name,
                    "paper_source": paper_source,
                    "acl_track": acl_track,
                    "fetch_mode": fetch_mode,
                    "max_papers": max_papers,
                    "days_back": days_back,
                    "keyword_filter": keyword_filter,
                    "min_score": int(min_score),
                    "max_concurrent": int(max_concurrent),
                    "run_time": run_time,
                })
                break
        _save_store_local()
    if sync_cloud:
        sync_db_to_cloud()


def load_recurring_schedules() -> list[dict]:
    with _STORE_LOCK:
        rows = [dict(s) for s in _STORE["recurring_schedules"]]
    rows.sort(key=lambda s: (-(int(s.get("is_active", 0))), s.get("run_time", ""), -int(s.get("id", 0))))
    return rows


def set_recurring_schedule_active(schedule_id: int, active: bool, sync_cloud: bool = True):
    with _STORE_LOCK:
        for s in _STORE["recurring_schedules"]:
            if int(s.get("id", -1)) == int(schedule_id):
                s["is_active"] = 1 if active else 0
                break
        _save_store_local()
    if sync_cloud:
        sync_db_to_cloud()


def delete_recurring_schedule(schedule_id: int, sync_cloud: bool = True):
    with _STORE_LOCK:
        _STORE["recurring_schedules"] = [s for s in _STORE["recurring_schedules"] if int(s.get("id", -1)) != int(schedule_id)]
        _save_store_local()
    if sync_cloud:
        sync_db_to_cloud()


def update_schedule_last_run(
    schedule_id: int,
    run_date: str,
    status: str,
    message: str,
    eval_id: Optional[int],
    sync_cloud: bool = True,
):
    with _STORE_LOCK:
        for s in _STORE["recurring_schedules"]:
            if int(s.get("id", -1)) == int(schedule_id):
                s["last_run_date"] = run_date
                s["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                s["last_status"] = status
                s["last_message"] = message
                s["last_eval_id"] = eval_id
                break
        _save_store_local()
    if sync_cloud:
        sync_db_to_cloud()


def load_due_recurring_schedules(now: Optional[datetime] = None) -> list[dict]:
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    now_hhmm = now.strftime("%H:%M")
    with _STORE_LOCK:
        rows = [
            dict(s)
            for s in _STORE["recurring_schedules"]
            if int(s.get("is_active", 0)) == 1
            and str(s.get("run_time", "")) <= now_hhmm
            and (not s.get("last_run_date") or s.get("last_run_date") != today)
        ]
    rows.sort(key=lambda s: s.get("run_time", ""))
    return rows


def get_gcp_scheduler_status(job_name: str = "hourly-paper-matcher-eval", location: str = "us-central1") -> dict:
    """
    Queries state of GCP Cloud Scheduler job via Google REST API (or gcloud CLI fallback).
    Returns status dict with state ('PAUSED', 'ENABLED', or 'UNKNOWN').
    """
    project_id = os.environ.get("GCP_PROJECT_ID", os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", "gen-lang-client-0096294200"))).strip()
    try:
        import google.auth
        import google.auth.transport.requests
        from urllib import request as urllib_req

        creds, _ = google.auth.default()
        creds.refresh(google.auth.transport.requests.Request())
        url = f"https://cloudscheduler.googleapis.com/v1/projects/{project_id}/locations/{location}/jobs/{job_name}"
        req = urllib_req.Request(url, headers={"Authorization": f"Bearer {creds.token}"})
        with urllib_req.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return {
                "job_name": job_name,
                "state": data.get("state", "UNKNOWN"),
                "location": location,
                "last_attempt": data.get("lastAttemptTime"),
                "error": None,
            }
    except Exception:
        pass

    import subprocess
    try:
        cmd = [
            "gcloud", "scheduler", "jobs", "describe", job_name,
            f"--location={location}", "--format=json"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, shell=True)
        if res.returncode == 0 and res.stdout:
            data = json.loads(res.stdout)
            state = data.get("state", "UNKNOWN")
            last_attempt = data.get("lastAttemptTime")
            return {
                "job_name": job_name,
                "state": state,
                "location": location,
                "last_attempt": last_attempt,
                "error": None,
            }
    except Exception:
        pass

    return {
        "job_name": job_name,
        "state": "UNKNOWN",
        "location": location,
        "last_attempt": None,
        "error": "Could not fetch GCP Cloud Scheduler status",
    }


def toggle_gcp_scheduler(active: bool, job_name: str = "hourly-paper-matcher-eval", location: str = "us-central1") -> dict:
    """
    Resumes or pauses GCP Cloud Scheduler job via Google REST API (or gcloud CLI fallback).
    """
    project_id = os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", "gen-lang-client-0096294200")).strip()
    action = "resume" if active else "pause"
    try:
        import google.auth
        import google.auth.transport.requests
        from urllib import request as urllib_req

        creds, _ = google.auth.default()
        creds.refresh(google.auth.transport.requests.Request())
        url = f"https://cloudscheduler.googleapis.com/v1/projects/{project_id}/locations/{location}/jobs/{job_name}:{action}"
        req = urllib_req.Request(url, data=b"", headers={"Authorization": f"Bearer {creds.token}"}, method="POST")
        with urllib_req.urlopen(req, timeout=8) as resp:
            if resp.status == 200:
                return {"success": True, "state": "ENABLED" if active else "PAUSED"}
    except Exception:
        pass

    import subprocess
    try:
        cmd = [
            "gcloud", "scheduler", "jobs", action, job_name,
            f"--location={location}", "--quiet"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, shell=True)
        if res.returncode == 0:
            return {"success": True, "state": "ENABLED" if active else "PAUSED"}
        else:
            return {"success": False, "error": res.stderr.strip() or f"Failed to {action} scheduler job"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def find_matching_past_papers(urls: list[str]) -> dict[str, list[dict]]:
    if not urls:
        return {}
    with _STORE_LOCK:
        url_set = set(urls)
        eval_by_id = {int(e["id"]): e for e in _STORE["evaluations"]}
        rows = []
        for p in _STORE["papers"]:
            if p.get("url") not in url_set:
                continue
            ev = eval_by_id.get(int(p.get("evaluation_id", -1)))
            if not ev:
                continue
            d = dict(p)
            d["problem_text"] = ev.get("problem_text", "")
            d["model_name"] = ev.get("model_name", "")
            d["eval_date"] = ev.get("created_at", "")
            rows.append(d)
    rows.sort(key=lambda x: float(x.get("avg_score", 0.0)), reverse=True)
    matches: dict[str, list[dict]] = {}
    for d in rows:
        matches.setdefault(d["url"], []).append(d)
    return matches


# ──────────────────────────────────────────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────────────────────────────────────────

class LLMQuotaExhaustedError(Exception):
    """Raised when a model's daily quota is exhausted."""


@dataclass
class Paper:
    title: str
    authors: str
    abstract: str
    url: str
    published: str
    categories: str
    full_text: Optional[str] = None


@dataclass
class DebateRound:
    advocate_argument: str = ""
    skeptic_argument: str = ""


@dataclass
class JudgeVerdict:
    run: int = 0
    seed: int = 0
    relevance_score: int = 0
    verdict: str = ""
    key_reasons: list[str] = field(default_factory=list)
    suggested_use: str = ""


@dataclass
class DebateResult:
    paper: Paper
    rounds: list[DebateRound] = field(default_factory=list)
    judge_verdicts: list[JudgeVerdict] = field(default_factory=list)
    avg_score: float = 0.0
    combined_verdict: str = ""
    combined_reasons: list[str] = field(default_factory=list)
    combined_suggested_use: str = ""
    generation_status: str = "COMPLETED"
    generation_stage: str = "Completed"
    generation_message: str = ""


# ──────────────────────────────────────────────────────────────────────────────
# arXiv Fetcher
# ──────────────────────────────────────────────────────────────────────────────

def fetch_arxiv_papers(
    max_results: Optional[int] = 50,
    search_query: Optional[str] = None,
    days_back: Optional[int] = None,
) -> list[Paper]:
    category_query = "cat:cs.CL"
    if search_query and search_query.strip():
        q_term = search_query.strip()
        full_query = f"{category_query} AND (ti:\"{q_term}\" OR abs:\"{q_term}\")"
    else:
        full_query = category_query

    use_date_filter = days_back is not None
    cutoff_date = datetime.now() - timedelta(days=days_back) if use_date_filter else None
    fetch_limit = 500 if use_date_filter else (max_results or 50)

    client = arxiv.Client()
    search = arxiv.Search(
        query=full_query,
        max_results=fetch_limit,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )

    papers: list[Paper] = []
    max_retries = 4
    for attempt in range(max_retries):
        try:
            papers = []
            for result in client.results(search):
                pub_date = result.published.replace(tzinfo=None)
                if use_date_filter and pub_date < cutoff_date:
                    break
                papers.append(
                    Paper(
                        title=result.title,
                        authors=", ".join(a.name for a in result.authors[:5]) + ("..." if len(result.authors) > 5 else ""),
                        abstract=result.summary.replace("\n", " "),
                        url=result.entry_id,
                        published=result.published.strftime("%Y-%m-%d"),
                        categories=", ".join(result.categories),
                    )
                )
                if not use_date_filter and len(papers) >= fetch_limit:
                    break
            break
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            import time
            time.sleep(3 * (attempt + 1))
    return papers


def fetch_acl_papers(
    max_results: Optional[int] = None,
    search_query: Optional[str] = None,
    volume_filter: str = "all",
) -> list[Paper]:
    """
    Fetches ACL 2026 Papers filtered by track/volume (Long, Short, Findings, Demos, SRW, Industry, Workshops, All).
    Loads cached ACL papers from the bucket-backed JSON store.
    """
    with _STORE_LOCK:
        rows = [dict(r) for r in _STORE.get("acl_papers", []) if str(r.get("event_year", "")) == "2026"]

    def _track_match(paper_key: str) -> bool:
        k = (paper_key or "").lower()
        if volume_filter == "acl-long":
            return ".acl-long." in k
        if volume_filter == "findings-acl":
            return ".findings-acl." in k
        if volume_filter == "acl-short":
            return ".acl-short." in k
        if volume_filter == "acl-industry":
            return ".acl-industry." in k
        if volume_filter == "acl-demo":
            return ".acl-demo." in k or ".acl-demos." in k
        if volume_filter == "acl-srw":
            return ".acl-srw." in k
        if volume_filter == "workshops":
            blocked = [
                ".acl-long.",
                ".findings-acl.",
                ".acl-short.",
                ".acl-industry.",
                ".acl-demo.",
                ".acl-demos.",
                ".acl-srw.",
            ]
            return not any(x in k for x in blocked)
        return True

    rows = [r for r in rows if _track_match(str(r.get("paper_key", "")))]

    if search_query and search_query.strip():
        q = search_query.strip().lower()
        rows = [
            r for r in rows
            if q in str(r.get("title", "")).lower() or q in str(r.get("abstract", "")).lower()
        ]

    if max_results and max_results > 0:
        rows = rows[: int(max_results)]

    paper_objs = []
    for r in rows:
        title = str(r.get("title", "Untitled ACL paper"))
        authors = str(r.get("authors") or "ACL 2026 Authors")
        abstract = str(r.get("abstract") or title)
        full_text = r.get("full_text")
        p_url = str(r.get("url", ""))
        pdf_url = str(r.get("pdf_url", ""))
        published = str(r.get("published") or "2026-08")

        paper_objs.append(Paper(
            title=title,
            authors=authors,
            abstract=abstract,
            url=pdf_url or p_url,
            published=published,
            categories="ACL-2026",
            full_text=full_text,
        ))
    return paper_objs


# ──────────────────────────────────────────────────────────────────────────────
# Multi-Agent Debate System (with 5-Judge Panel)
# ──────────────────────────────────────────────────────────────────────────────

ADVOCATE_SYSTEM = """You are the ADVOCATE agent in a research paper relevance debate.
Your role is to argue FOR the relevance of the given paper to the user's research problem.
Find connections, potential applications, methodological overlaps, and useful insights.
Be specific — cite parts of the abstract that support your argument.
Keep your response concise (150 words max)."""

SKEPTIC_SYSTEM = """You are the SKEPTIC agent in a research paper relevance debate.
Your role is to argue AGAINST the relevance of the given paper to the user's research problem.
Identify differences in scope, methodology, domain, or focus that make the paper less useful.
Be specific — cite parts of the abstract that weaken the relevance claim.
Keep your response concise (150 words max)."""

JUDGE_SYSTEM = """You are the JUDGE agent in a research paper relevance debate.
You have seen arguments from an Advocate (arguing FOR relevance) and a Skeptic (arguing AGAINST).
Your job is to deliver a final, balanced verdict.

You MUST respond with valid JSON only (no markdown, no code fences):
{
    "relevance_score": <1-10 integer>,
    "verdict": "<2-3 sentence summary>",
    "key_reasons": ["<reason1>", "<reason2>", "<reason3>"],
    "suggested_use": "<how the user could leverage this paper, or 'Not directly applicable'>"
}"""

NUM_JUDGES = 5


def _parse_judge_json(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    cleaned = cleaned.strip()
    return json.loads(cleaned)


class DebateEngine:
    def __init__(self, client: genai.Client, model_name: str = "gemini-2.5-flash"):
        self.client = client
        self.model_name = model_name
        self.debate_rounds = 2

    async def _call_llm(self, system: str, user_prompt: str, temperature: float = 1.0) -> str:
        max_retries = 6
        for attempt in range(max_retries):
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model_name,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=temperature,
                    ),
                )
                return response.text
            except Exception as e:
                err_str = str(e)
                lower = err_str.lower()
                is_daily_quota = "per_day" in lower or "generate_requests_per_day" in lower
                if is_daily_quota:
                    return f"[Daily quota exhausted for model '{self.model_name}'. Switch models or try again later.]"
                is_rate_limit = ("resource" in lower and "exhausted" in lower) or "429" in lower or "rate" in lower or "quota" in lower
                if attempt == max_retries - 1:
                    return f"[LLM Error: {e}]"
                wait = (2 ** (attempt + 2) if is_rate_limit else 2 ** attempt) + random.uniform(0, 1)
                await asyncio.sleep(wait)

    async def run_debate(self, paper: Paper, problem: str, status_callback: Optional[Callable[[str], None]] = None) -> DebateResult:
        def _status(msg: str):
            if status_callback:
                status_callback(msg)

        full_text_block = (
            f"\n\n## Full Paper Text / Content\n{paper.full_text[:35000]}"
            if paper.full_text
            else f"\n\n## Full Paper Abstract & Technical Content\n{paper.abstract}"
        )

        context = (
            f"## User's Research Problem\n{problem}\n\n"
            f"## Paper Under Review\n"
            f"**Title:** {paper.title}\n"
            f"**Authors:** {paper.authors}\n"
            f"**Published:** {paper.published}\n"
            f"**Categories:** {paper.categories}\n"
            f"**Abstract:** {paper.abstract}"
            f"{full_text_block}\n"
        )
        result = DebateResult(paper=paper)
        debate_history = ""

        for round_num in range(1, self.debate_rounds + 1):
            _status(f"🟢 Advocate Round {round_num}/{self.debate_rounds}")
            adv_prompt = f"{context}\n\n{debate_history}Round {round_num}: Present your argument FOR this paper's relevance."
            adv_arg = await self._call_llm(ADVOCATE_SYSTEM, adv_prompt)

            _status(f"🔴 Skeptic Round {round_num}/{self.debate_rounds}")
            skep_prompt = f"{context}\n\n{debate_history}Round {round_num} — Advocate said:\n{adv_arg}\n\nNow present your counter-argument AGAINST this paper's relevance."
            skep_arg = await self._call_llm(SKEPTIC_SYSTEM, skep_prompt)

            result.rounds.append(DebateRound(advocate_argument=adv_arg, skeptic_argument=skep_arg))
            debate_history += f"\n--- Round {round_num} ---\nAdvocate: {adv_arg}\nSkeptic: {skep_arg}\n"

        _status("⚖️ 5 Judges Deliberating...")
        judge_base_prompt = f"{context}\n\n## Full Debate Transcript\n{debate_history}\n\nNow deliver your JSON verdict."
        seeds = [random.randint(1, 999_999) for _ in range(NUM_JUDGES)]
        temperatures = [0.5, 0.7, 0.9, 1.1, 1.3]

        async def _run_judge(i: int) -> JudgeVerdict:
            seeded = f"{judge_base_prompt}\n(Judge run {i + 1}/{NUM_JUDGES}, seed={seeds[i]}. Evaluate independently.)"
            raw = await self._call_llm(JUDGE_SYSTEM, seeded, temperature=temperatures[i])
            jv = JudgeVerdict(run=i + 1, seed=seeds[i])
            try:
                parsed = _parse_judge_json(raw)
                jv.relevance_score = int(parsed.get("relevance_score", 0))
                jv.verdict = parsed.get("verdict", "")
                jv.key_reasons = parsed.get("key_reasons", [])
                jv.suggested_use = parsed.get("suggested_use", "")
            except Exception:
                jv.verdict = raw
                jv.relevance_score = 0
            return jv

        result.judge_verdicts = list(await asyncio.gather(*[_run_judge(i) for i in range(NUM_JUDGES)]))
        valid_scores = [v.relevance_score for v in result.judge_verdicts if v.relevance_score > 0]
        result.avg_score = round(sum(valid_scores) / len(valid_scores), 1) if valid_scores else 0.0

        if valid_scores:
            best = min(result.judge_verdicts, key=lambda v: abs(v.relevance_score - result.avg_score))
            result.combined_verdict = best.verdict
            result.combined_reasons = best.key_reasons
            result.combined_suggested_use = best.suggested_use

        _status(f"✅ Done — Score: {result.avg_score}/10")
        return result


def _post_results_to_webhook(
    *,
    endpoint_url: str,
    results: list[DebateResult],
    evaluation_id: Optional[int],
    problem_text: str,
    model_name: str,
    trigger: str,
    schedule_id: Optional[int] = None,
    token: str = "",
) -> tuple[bool, str]:
    endpoint = (endpoint_url or "").strip()
    if not endpoint:
        return False, "Webhook URL not configured"

    export_list = []
    for r in results:
        export_list.append({
            "title": r.paper.title,
            "authors": r.paper.authors,
            "published": r.paper.published,
            "url": r.paper.url,
            "avg_score": r.avg_score,
            "verdict": r.combined_verdict,
            "key_reasons": r.combined_reasons,
            "suggested_use": r.combined_suggested_use,
        })

    payload = {
        "source": "archive-paper-matcher",
        "sent_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "trigger": trigger,
        "evaluation": {
            "id": evaluation_id,
            "problem_text": problem_text,
            "model_name": model_name,
            "schedule_id": schedule_id,
            "result_count": len(results),
        },
        "results": export_list,
    }

    headers = {"Content-Type": "application/json", "User-Agent": "archive-paper-matcher/1.0"}
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"

    data = json.dumps(payload).encode("utf-8")
    req = request.Request(endpoint, data=data, headers=headers, method="POST")

    try:
        with request.urlopen(req, timeout=30) as resp:
            status_code = getattr(resp, "status", 200)
            return (200 <= status_code < 300), f"Posted successfully (HTTP {status_code})"
    except error.HTTPError as e:
        return False, f"Webhook HTTP error: {e.code}"
    except Exception as e:
        return False, f"Webhook request failed: {e}"


def _run_evaluation_headless(
    *,
    api_key: str,
    problem_statement: str,
    model_name: str,
    paper_source: str = "arxiv",
    acl_track: str = "all",
    max_papers: Optional[int],
    days_back: Optional[int],
    keyword_filter: str,
    max_concurrent: int,
    min_score: int,
    progress_cb=None,
) -> tuple[Optional[int], list[DebateResult], Optional[str]]:
    def _cb(stage: str, done: int = 0, total: int = 0):
        if progress_cb:
            try:
                progress_cb(stage, done, total)
            except Exception:
                pass

    _cb("fetching", 0, 0)
    try:
        if paper_source == "acl":
            # ACL headless runs are intentionally uncapped.
            papers = fetch_acl_papers(max_results=None, search_query=keyword_filter, volume_filter=acl_track)
        else:
            papers = fetch_arxiv_papers(max_results=max_papers, search_query=keyword_filter, days_back=days_back)
    except Exception as e:
        return None, [], f"Failed to fetch papers: {e}"

    if not papers:
        return None, [], "No papers found. Try adjusting keyword filters."

    _cb("evaluating", 0, len(papers))
    eval_id = save_evaluation(problem_statement, model_name, sync_cloud=False)
    update_evaluation_progress(eval_id, completed=0, total=len(papers), status="RUNNING", sync_cloud=False)
    results: list[DebateResult] = []
    completed = 0
    _lock = threading.Lock()

    def _evaluate(paper: Paper) -> DebateResult:
        last_stage = "Starting Gemini evaluation"

        def _paper_status(stage: str):
            nonlocal last_stage
            last_stage = stage

        try:
            c = genai.Client(api_key=api_key)
            eng = DebateEngine(client=c, model_name=model_name)
            result = asyncio.run(asyncio.wait_for(
                eng.run_debate(paper, problem_statement, status_callback=_paper_status),
                timeout=max(PAPER_EVAL_TIMEOUT_SECONDS, 30),
            ))
            result.generation_stage = "Completed"
            return result
        except TimeoutError:
            message = f"Gemini timed out after {PAPER_EVAL_TIMEOUT_SECONDS} seconds during: {last_stage}"
            return DebateResult(
                paper=paper,
                combined_verdict=message,
                avg_score=0.0,
                generation_status="TIMED_OUT",
                generation_stage=last_stage,
                generation_message=message,
            )
        except Exception as exc:
            message = f"Evaluation failed during {last_stage}: {exc}"
            return DebateResult(
                paper=paper,
                combined_verdict=message,
                avg_score=0.0,
                generation_status="FAILED",
                generation_stage=last_stage,
                generation_message=message,
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {executor.submit(_evaluate, p): p for p in papers}
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
            with _lock:
                completed += 1
            update_evaluation_progress(eval_id, completed=completed, total=len(papers), status="RUNNING", sync_cloud=False)
            _cb("evaluating", completed, len(papers))

    _cb("saving", 0, len(results))
    for r in results:
        paper_id = save_paper(
            eval_id,
            r.paper,
            r.avg_score,
            sync_cloud=False,
            generation_status=r.generation_status,
            generation_stage=r.generation_stage,
            generation_message=r.generation_message,
        )
        for idx, rnd in enumerate(r.rounds, 1):
            save_debate_round(paper_id, idx, rnd.advocate_argument, rnd.skeptic_argument, sync_cloud=False)
        for jv in r.judge_verdicts:
            save_judge_verdict(paper_id, jv.run, jv.seed, jv.relevance_score, jv.verdict, jv.key_reasons, jv.suggested_use, sync_cloud=False)

    successful_results = [r for r in results if r.generation_status == "COMPLETED"]
    final_status = "COMPLETED" if successful_results else "FAILED"
    update_evaluation_progress(eval_id, completed=len(results), total=len(papers), status=final_status, sync_cloud=True)
    _cb("syncing", 0, 0)
    sync_db_to_cloud()
    results.sort(key=lambda r: r.avg_score, reverse=True)
    if not successful_results:
        return eval_id, results, "All paper evaluations failed or timed out."
    return eval_id, results, None
