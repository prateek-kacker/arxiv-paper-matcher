"""
arXiv CS.CL Paper Matcher — FastAPI Application Server
======================================================
Provides REST APIs and Server-Sent Events (SSE) live streaming for the custom web app.
Serves static frontend assets from static/ directory.
"""

import os
import json
import asyncio
import time
from contextlib import asynccontextmanager
from typing import Optional
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

import core_engine as core
import sharded_eval
from core_engine import (
    sync_db_from_cloud,
    init_db,
    save_evaluation,
    update_evaluation_progress,
    save_paper,
    save_debate_round,
    save_judge_verdict,
    load_evaluation_paper_urls,
    load_past_evaluations,
    load_evaluation_papers,
    load_all_papers,
    delete_evaluation,
    delete_evaluations,
    delete_papers,
    create_recurring_schedule,
    update_recurring_schedule,
    load_recurring_schedules,
    set_recurring_schedule_active,
    delete_recurring_schedule,
    update_schedule_last_run,
    get_gcp_scheduler_status,
    toggle_gcp_scheduler,
    find_matching_past_papers,
    fetch_arxiv_papers,
    fetch_acl_papers,
    DebateEngine,
    DebateResult,
    _run_evaluation_headless,
    DB_GCS_BUCKET,
    AWS_S3_BUCKET,
    STORE_GCS_URI,
    STORE_S3_URI,
    EVALRUN_GCS_URI_PREFIX,
    EVALRUN_S3_URI_PREFIX,
)

# pylint: disable=unused-argument,redefined-outer-name
@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Keep startup sync before serving requests.
    sync_db_from_cloud()
    init_db()
    yield


app = FastAPI(title="arXiv CS.CL Paper Matcher", version="2.0.0", lifespan=lifespan)
FETCH_TIMEOUT_SECONDS = int(os.environ.get("PAPER_FETCH_TIMEOUT_SECONDS", "45"))
CHECKPOINT_EVERY_N_PAPERS = int(os.environ.get("PAPER_CHECKPOINT_EVERY_N_PAPERS", "1"))
CHECKPOINT_EVERY_SECONDS = int(os.environ.get("PAPER_CHECKPOINT_EVERY_SECONDS", "30"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>arXiv CS.CL Paper Matcher Server Running</h1><p>Frontend static files missing.</p>")


# ──────────────────────────────────────────────────────────────────────────────
# API Routes
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/config")
async def get_config():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    return {
        "has_api_key": bool(api_key),
        "gcs_bucket": DB_GCS_BUCKET,
        "s3_bucket": AWS_S3_BUCKET,
        "store_gcs_uri": STORE_GCS_URI,
        "store_s3_uri": STORE_S3_URI,
        "evalrun_gcs_prefix": EVALRUN_GCS_URI_PREFIX,
        "evalrun_s3_prefix": EVALRUN_S3_URI_PREFIX,
        "webhook_url": os.environ.get("KACKERS_POST_URL", "").strip(),
        "auto_post": os.environ.get("AUTO_POST_RESULTS", "false").strip().lower() == "true",
    }


@app.get("/api/cloud-sync/status")
async def cloud_sync_status():
    return {
        "gcs_bucket": DB_GCS_BUCKET,
        "s3_bucket": AWS_S3_BUCKET,
        "store_gcs_uri": STORE_GCS_URI,
        "store_s3_uri": STORE_S3_URI,
        "evalrun_gcs_prefix": EVALRUN_GCS_URI_PREFIX,
        "evalrun_s3_prefix": EVALRUN_S3_URI_PREFIX,
        "store_path": str(core.DB_PATH),
        "store_exists": core.DB_PATH.exists(),
        # Backward-compatible fields retained for existing clients.
        "db_path": str(core.DB_PATH),
        "db_exists": core.DB_PATH.exists(),
    }


@app.post("/api/cloud-sync/trigger")
async def trigger_cloud_sync():
    ok, msg = sync_db_from_cloud()
    return {"success": ok, "message": msg}


@app.post("/api/fetch-papers")
async def api_fetch_papers(req: dict):
    paper_source = req.get("paper_source", "arxiv")
    acl_track = req.get("acl_track", "all")
    search_query = req.get("keyword_filter") or None
    days_back = req.get("days_back")
    max_results = req.get("max_papers")
    if days_back is not None:
        days_back = int(days_back)
    if max_results is not None:
        max_results = int(max_results)
    if paper_source != "acl" and max_results is None:
        max_results = 50

    try:
        if paper_source == "acl":
            # ACL fetches are intentionally uncapped; ignore any client max_papers value.
            papers = await asyncio.wait_for(
                asyncio.to_thread(
                    fetch_acl_papers,
                    max_results=None,
                    search_query=search_query,
                    volume_filter=acl_track,
                ),
                timeout=FETCH_TIMEOUT_SECONDS,
            )
        else:
            papers = await asyncio.wait_for(
                asyncio.to_thread(
                    fetch_arxiv_papers,
                    max_results=max_results,
                    search_query=search_query,
                    days_back=days_back,
                ),
                timeout=FETCH_TIMEOUT_SECONDS,
            )
    except asyncio.TimeoutError as e:
        raise HTTPException(status_code=504, detail=f"Timed out fetching {paper_source.upper()} papers after {FETCH_TIMEOUT_SECONDS}s") from e
    try:
        paper_dicts = []
        urls = [p.url for p in papers]
        past_matches = find_matching_past_papers(urls)

        for p in papers:
            is_match = p.url in past_matches
            past_records = past_matches.get(p.url, [])
            paper_dicts.append({
                "title": p.title,
                "authors": p.authors,
                "abstract": p.abstract,
                "url": p.url,
                "published": p.published,
                "categories": p.categories,
                "is_previously_evaluated": is_match,
                "past_records": past_records,
            })
        return {"success": True, "count": len(paper_dicts), "papers": paper_dicts}
    except (TypeError, ValueError, KeyError, AttributeError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/evaluate/stream")
async def api_evaluate_stream(request: Request):
    data = await request.json()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip() or data.get("api_key", "").strip()
    problem_statement = data.get("problem_statement", "").strip()
    model_name = data.get("model_name", "gemini-2.5-flash")
    max_papers = data.get("max_papers")
    days_back = data.get("days_back")
    keyword_filter = data.get("keyword_filter", "").strip()
    paper_source = data.get("paper_source", "arxiv")
    acl_track = data.get("acl_track", "all")
    if paper_source == "acl":
        max_papers = None

    if not api_key:
        raise HTTPException(status_code=400, detail="Gemini API key is required.")
    if not problem_statement:
        raise HTTPException(status_code=400, detail="Research problem description is required.")

    async def event_generator():
        try:
            if paper_source == "acl":
                track_label = acl_track.upper() if acl_track != "all" else "ALL 2026"
                yield {
                    "event": "stage",
                    "data": json.dumps({"stage": "fetching", "message": f"Fetching papers from ACL 2026 Anthology ({track_label} Track)..."})
                }
                papers = await asyncio.wait_for(
                    asyncio.to_thread(
                        fetch_acl_papers,
                        max_results=None,
                        search_query=keyword_filter,
                        volume_filter=acl_track,
                    ),
                    timeout=FETCH_TIMEOUT_SECONDS,
                )
            else:
                yield {
                    "event": "stage",
                    "data": json.dumps({"stage": "fetching", "message": "Fetching papers from arXiv CS.CL..."})
                }
                papers = await asyncio.wait_for(
                    asyncio.to_thread(
                        fetch_arxiv_papers,
                        max_results=max_papers,
                        search_query=keyword_filter,
                        days_back=days_back,
                    ),
                    timeout=FETCH_TIMEOUT_SECONDS,
                )
        except asyncio.TimeoutError:
            yield {
                "event": "error",
                "data": json.dumps({"error": f"Timed out fetching papers after {FETCH_TIMEOUT_SECONDS}s"})
            }
            return
        except (TypeError, ValueError, RuntimeError, OSError) as e:
            yield {
                "event": "error",
                "data": json.dumps({"error": f"Failed to fetch papers from {paper_source.upper()}: {e}"})
            }
            return

        if not papers:
            yield {
                "event": "error",
                "data": json.dumps({"error": f"No {paper_source.upper()} papers found matching filters."})
            }
            return

        yield {
            "event": "stage",
            "data": json.dumps({"stage": "evaluating", "total_papers": len(papers), "message": f"Fetched {len(papers)} papers. Starting multi-agent debate..."})
        }

        eval_id = save_evaluation(
            problem_statement,
            model_name,
            status="RUNNING",
            total=len(papers),
            sync_cloud=True,
        )
        from google import genai
        c = genai.Client(api_key=api_key)
        eng = DebateEngine(client=c, model_name=model_name)

        results = []
        disconnected = False
        last_checkpoint_completed = 0
        last_checkpoint_at = time.monotonic()
        for idx, paper in enumerate(papers, 1):
            if await request.is_disconnected():
                print(f"[SSE] Client disconnected. Stopping evaluation loop at paper {idx}/{len(papers)} for eval_id={eval_id}", flush=True)
                disconnected = True
                break

            yield {
                "event": "paper_start",
                "data": json.dumps({
                    "paper_index": idx,
                    "total_papers": len(papers),
                    "title": paper.title,
                    "authors": paper.authors,
                    "url": paper.url,
                    "published": paper.published,
                })
            }

            def _status_cb(msg: str):
                pass  # progress tracking

            try:
                result = await eng.run_debate(paper, problem_statement, status_callback=_status_cb)
            except (RuntimeError, ValueError, OSError) as exc:
                result = DebateResult(paper=paper, combined_verdict=f"Failed: {exc}", avg_score=0.0)

            results.append(result)

            paper_id = save_paper(eval_id, paper, result.avg_score, sync_cloud=False)
            for rnd_idx, rnd in enumerate(result.rounds, 1):
                save_debate_round(paper_id, rnd_idx, rnd.advocate_argument, rnd.skeptic_argument, sync_cloud=False)
            for jv in result.judge_verdicts:
                save_judge_verdict(paper_id, jv.run, jv.seed, jv.relevance_score, jv.verdict, jv.key_reasons, jv.suggested_use, sync_cloud=False)

            update_evaluation_progress(
                eval_id,
                completed=len(results),
                total=len(papers),
                status="RUNNING",
                sync_cloud=False,
            )

            now = time.monotonic()
            count_delta = len(results) - last_checkpoint_completed
            due_count = CHECKPOINT_EVERY_N_PAPERS > 0 and count_delta >= CHECKPOINT_EVERY_N_PAPERS
            due_time = CHECKPOINT_EVERY_SECONDS > 0 and (now - last_checkpoint_at) >= CHECKPOINT_EVERY_SECONDS
            if due_count or due_time:
                update_evaluation_progress(
                    eval_id,
                    completed=len(results),
                    total=len(papers),
                    status="RUNNING",
                    sync_cloud=True,
                    emit_snapshot=True,
                )
                last_checkpoint_completed = len(results)
                last_checkpoint_at = now

            yield {
                "event": "paper_done",
                "data": json.dumps({
                    "paper_index": idx,
                    "total_papers": len(papers),
                    "paper_id": paper_id,
                    "title": paper.title,
                    "avg_score": result.avg_score,
                    "verdict": result.combined_verdict,
                    "reasons": result.combined_reasons,
                    "suggested_use": result.combined_suggested_use,
                    "judge_scores": [
                        {
                            "run": jv.run,
                            "score": jv.relevance_score,
                            "seed": jv.seed,
                            "verdict": jv.verdict,
                            "reasons": jv.key_reasons,
                            "suggested_use": jv.suggested_use
                        }
                        for jv in result.judge_verdicts
                    ],
                    "rounds": [
                        {"advocate": rnd.advocate_argument, "skeptic": rnd.skeptic_argument}
                        for rnd in result.rounds
                    ],
                })
            }

        final_status = "COMPLETED" if not disconnected and len(results) == len(papers) else "FAILED"
        update_evaluation_progress(
            eval_id,
            completed=len(results),
            total=len(papers),
            status=final_status,
            sync_cloud=True,
        )

        yield {
            "event": "eval_complete",
            "data": json.dumps({
                "eval_id": eval_id,
                "total_evaluated": len(results),
                "message": "Evaluation completed and saved to database!" if final_status == "COMPLETED" else "Evaluation ended early and partial results were saved.",
            })
        }

    return EventSourceResponse(event_generator())


@app.post("/api/evaluate/background")
async def evaluate_background(data: dict, background_tasks: BackgroundTasks):
    api_key = resolve_gemini_api_key(data.get("api_key"))
    problem_statement = data.get("problem_statement", "").strip()
    model_name = data.get("model_name", "gemini-2.5-flash")
    paper_source = data.get("paper_source", "arxiv")
    acl_track = data.get("acl_track", "all")
    max_papers = data.get("max_papers")
    days_back = data.get("days_back")
    if paper_source == "acl":
        max_papers = None
    if max_papers is None and days_back is None and paper_source != "acl":
        max_papers = 10
    keyword_filter = data.get("keyword_filter", "").strip()
    max_concurrent = int(data.get("max_concurrent", 3))

    if not api_key:
        raise HTTPException(status_code=400, detail="Gemini API key is required.")
    if not problem_statement:
        raise HTTPException(status_code=400, detail="Research problem description is required.")

    if core.DB_GCS_BUCKET:
        try:
            if paper_source == "acl":
                papers = await asyncio.wait_for(
                    asyncio.to_thread(
                        fetch_acl_papers,
                        max_results=None,
                        search_query=keyword_filter,
                        volume_filter=acl_track,
                    ),
                    timeout=FETCH_TIMEOUT_SECONDS,
                )
            else:
                papers = await asyncio.wait_for(
                    asyncio.to_thread(
                        fetch_arxiv_papers,
                        max_results=max_papers,
                        search_query=keyword_filter,
                        days_back=days_back,
                    ),
                    timeout=FETCH_TIMEOUT_SECONDS,
                )
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail=f"Timed out fetching {paper_source.upper()} papers after {FETCH_TIMEOUT_SECONDS}s",
            ) from exc
        except (RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(status_code=502, detail=f"Failed to fetch {paper_source.upper()} papers: {exc}") from exc

        if not papers:
            raise HTTPException(status_code=404, detail=f"No {paper_source.upper()} papers found matching filters.")

        eval_id = await asyncio.to_thread(
            sharded_eval.create_evaluation,
            problem_statement,
            model_name,
            len(papers),
        )
        try:
            manifest = await asyncio.to_thread(
                sharded_eval.create_manifest,
                eval_id=eval_id,
                problem=problem_statement,
                model=model_name,
                papers=papers,
                max_concurrent=max_concurrent,
            )
            operation_name = await asyncio.to_thread(
                sharded_eval.launch_job,
                eval_id,
                manifest["total_shards"],
            )
        except (RuntimeError, ValueError, OSError) as exc:
            await asyncio.to_thread(sharded_eval.update_evaluation_status, eval_id, "FAILED")
            raise HTTPException(status_code=502, detail=f"Failed to launch distributed evaluation: {exc}") from exc

        return {
            "status": "success",
            "eval_id": eval_id,
            "total_papers": len(papers),
            "total_shards": manifest["total_shards"],
            "operation_name": operation_name,
            "message": (
                f"Distributed evaluation #{eval_id} launched with "
                f"{manifest['total_shards']} retryable shard(s)."
            ),
        }

    # Create record immediately so request returns quickly and avoids gateway timeouts.
    eval_id = save_evaluation(problem=problem_statement, model=model_name, status="RUNNING", total=0, sync_cloud=True)

    # Add async background worker
    background_tasks.add_task(
        run_background_eval_task,
        eval_id,
        api_key,
        problem_statement,
        model_name,
        paper_source,
        acl_track,
        max_papers,
        days_back,
        keyword_filter,
        max_concurrent,
    )

    return {
        "status": "success",
        "eval_id": eval_id,
        "total_papers": None,
        "message": f"Background evaluation #{eval_id} queued. Paper fetch and evaluation are running asynchronously."
    }


async def run_background_eval_task(
    eval_id: int,
    api_key: str,
    problem: str,
    model: str,
    paper_source: str,
    acl_track: str,
    max_papers: Optional[int],
    days_back: Optional[int],
    keyword_filter: str,
    max_concurrent: int,
):
    completed_count = 0
    last_checkpoint_completed = 0
    last_checkpoint_at = time.monotonic()
    checkpoint_lock = asyncio.Lock()

    async def _maybe_checkpoint(total_papers: int, force: bool = False):
        nonlocal last_checkpoint_completed, last_checkpoint_at
        now = time.monotonic()
        count_delta = completed_count - last_checkpoint_completed
        due_count = CHECKPOINT_EVERY_N_PAPERS > 0 and count_delta >= CHECKPOINT_EVERY_N_PAPERS
        due_time = CHECKPOINT_EVERY_SECONDS > 0 and (now - last_checkpoint_at) >= CHECKPOINT_EVERY_SECONDS
        if not (force or due_count or due_time):
            return
        async with checkpoint_lock:
            now_inner = time.monotonic()
            count_delta_inner = completed_count - last_checkpoint_completed
            due_count_inner = CHECKPOINT_EVERY_N_PAPERS > 0 and count_delta_inner >= CHECKPOINT_EVERY_N_PAPERS
            due_time_inner = CHECKPOINT_EVERY_SECONDS > 0 and (now_inner - last_checkpoint_at) >= CHECKPOINT_EVERY_SECONDS
            if not (force or due_count_inner or due_time_inner):
                return
            update_evaluation_progress(
                eval_id,
                completed=completed_count,
                total=total_papers,
                status="RUNNING",
                sync_cloud=True,
                emit_snapshot=True,
            )
            last_checkpoint_completed = completed_count
            last_checkpoint_at = now_inner
    try:
        if paper_source == "acl":
            papers = await asyncio.wait_for(
                asyncio.to_thread(
                    fetch_acl_papers,
                    max_results=None,
                    search_query=keyword_filter,
                    volume_filter=acl_track,
                ),
                timeout=FETCH_TIMEOUT_SECONDS,
            )
        else:
            papers = await asyncio.wait_for(
                asyncio.to_thread(
                    fetch_arxiv_papers,
                    max_results=max_papers,
                    search_query=keyword_filter,
                    days_back=days_back,
                ),
                timeout=FETCH_TIMEOUT_SECONDS,
            )

        if not papers:
            update_evaluation_progress(eval_id, completed=0, total=0, status="FAILED", sync_cloud=True)
            return

        existing_urls = load_evaluation_paper_urls(eval_id)
        seen_urls = set(existing_urls)
        seen_urls_lock = asyncio.Lock()
        papers_to_process = [p for p in papers if p.url not in existing_urls]
        completed_count = len(existing_urls)
        total_papers = len(papers)

        update_evaluation_progress(eval_id, completed=completed_count, total=total_papers, status="RUNNING", sync_cloud=False)

        from google import genai
        c = genai.Client(api_key=api_key)
        engine = DebateEngine(client=c, model_name=model)

        sem = asyncio.Semaphore(max_concurrent)

        async def eval_one(paper):
            nonlocal completed_count
            async with sem:
                try:
                    result = await engine.run_debate(paper, problem)
                    should_persist = False
                    async with seen_urls_lock:
                        if result.paper.url not in seen_urls:
                            seen_urls.add(result.paper.url)
                            should_persist = True
                    if should_persist:
                        p_id = save_paper(eval_id, result.paper, result.avg_score, sync_cloud=False)
                        for r_idx, r in enumerate(result.rounds, start=1):
                            save_debate_round(p_id, r_idx, r.advocate_argument, r.skeptic_argument, sync_cloud=False)
                        for v in result.judge_verdicts:
                            save_judge_verdict(p_id, v.run, v.seed, v.relevance_score, v.verdict, v.key_reasons, v.suggested_use, sync_cloud=False)
                except (RuntimeError, ValueError, OSError) as e:
                    print(f"[Background Eval Worker error on '{paper.title}'] {e}", flush=True)

                completed_count += 1
                update_evaluation_progress(
                    eval_id,
                    completed=completed_count,
                    total=total_papers,
                    status="RUNNING",
                    sync_cloud=False,
                )
                await _maybe_checkpoint(total_papers)

        tasks = [eval_one(p) for p in papers_to_process]
        await asyncio.gather(*tasks)

        await _maybe_checkpoint(total_papers, force=True)

        # Finalize and sync DB to cloud
        update_evaluation_progress(eval_id, completed=total_papers, total=total_papers, status="COMPLETED", sync_cloud=True)
    except asyncio.TimeoutError:
        print(f"[Background Eval Worker Failed #{eval_id}] Timed out fetching papers after {FETCH_TIMEOUT_SECONDS}s", flush=True)
        update_evaluation_progress(eval_id, completed=completed_count, status="FAILED", sync_cloud=True)
    except (RuntimeError, ValueError, OSError) as err:
        print(f"[Background Eval Worker Failed #{eval_id}] {err}", flush=True)
        update_evaluation_progress(eval_id, completed=completed_count, status="FAILED", sync_cloud=True)


# ──────────────────────────────────────────────────────────────────────────────
# Schedules API
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/debug/scheduler")
async def debug_scheduler_route():
    import traceback
    project_id = os.environ.get("GCP_PROJECT_ID", os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", "gen-lang-client-0096294200"))).strip()
    res = {"project_id": project_id, "env": dict(os.environ)}
    try:
        import google.auth
        import google.auth.transport.requests
        from urllib import request as urllib_req

        creds, proj = google.auth.default()
        res["auth_project"] = proj
        res["service_account"] = getattr(creds, "service_account_email", "N/A")
        creds.refresh(google.auth.transport.requests.Request())
        res["token_valid"] = creds.valid
        url = f"https://cloudscheduler.googleapis.com/v1/projects/{project_id}/locations/us-central1/jobs/hourly-paper-matcher-eval"
        req = urllib_req.Request(url, headers={"Authorization": f"Bearer {creds.token}"})
        with urllib_req.urlopen(req, timeout=8) as resp:
            res["api_status"] = resp.status
            res["api_body"] = json.loads(resp.read().decode())
    except Exception as e:
        res["exception"] = str(e)
        res["traceback"] = traceback.format_exc()
    return res


@app.get("/api/schedules")
async def get_schedules():
    return {
        "schedules": load_recurring_schedules(),
        "cloud_scheduler": get_gcp_scheduler_status()
    }


@app.post("/api/schedules/cloud-scheduler/toggle")
async def toggle_cloud_scheduler_route(req: dict):
    active = bool(req.get("active", True))
    result = toggle_gcp_scheduler(active)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to update GCP Cloud Scheduler status"))
    return {
        "success": True,
        "cloud_scheduler": get_gcp_scheduler_status()
    }


@app.post("/api/schedules")
async def add_schedule(req: dict):
    is_acl = req.get("paper_source", "arxiv") == "acl"
    schedule_id = create_recurring_schedule(
        label=req.get("label", "").strip() or "Daily Schedule",
        problem_text=req["problem_text"].strip(),
        model_name=req.get("model_name", "gemini-2.5-flash"),
        paper_source=req.get("paper_source", "arxiv"),
        acl_track=req.get("acl_track", "all"),
        fetch_mode=req.get("fetch_mode", "count"),
        max_papers=None if is_acl else req.get("max_papers"),
        days_back=req.get("days_back"),
        keyword_filter=req.get("keyword_filter", "").strip(),
        min_score=int(req.get("min_score", 6)),
        max_concurrent=int(req.get("max_concurrent", 3)),
        run_time=req.get("run_time", "08:00"),
    )
    # Automatically ensure GCP Cloud Scheduler is active when creating a schedule
    toggle_gcp_scheduler(True)
    return {"success": True, "schedule_id": schedule_id, "cloud_scheduler": get_gcp_scheduler_status()}


@app.put("/api/schedules/{schedule_id}")
async def edit_schedule(schedule_id: int, req: dict):
    is_acl = req.get("paper_source", "arxiv") == "acl"
    update_recurring_schedule(
        schedule_id=schedule_id,
        label=req.get("label", "").strip() or f"Schedule #{schedule_id}",
        problem_text=req["problem_text"].strip(),
        model_name=req.get("model_name", "gemini-2.5-flash"),
        paper_source=req.get("paper_source", "arxiv"),
        acl_track=req.get("acl_track", "all"),
        fetch_mode=req.get("fetch_mode", "count"),
        max_papers=None if is_acl else req.get("max_papers"),
        days_back=req.get("days_back"),
        keyword_filter=req.get("keyword_filter", "").strip(),
        min_score=int(req.get("min_score", 6)),
        max_concurrent=int(req.get("max_concurrent", 3)),
        run_time=req.get("run_time", "08:00"),
    )
    return {"success": True}


@app.post("/api/schedules/{schedule_id}/toggle")
async def toggle_schedule(schedule_id: int, req: dict):
    active = bool(req.get("active", True))
    set_recurring_schedule_active(schedule_id, active)
    
    # Sync GCP Cloud Scheduler: if active, ensure Cloud Scheduler is resumed.
    # If all schedules are inactive, pause Cloud Scheduler.
    all_schedules = load_recurring_schedules()
    any_active = any(s.get("is_active", 0) == 1 for s in all_schedules)
    if active or any_active:
        toggle_gcp_scheduler(True)
    else:
        toggle_gcp_scheduler(False)

    return {
        "success": True,
        "active": active,
        "cloud_scheduler": get_gcp_scheduler_status()
    }


@app.delete("/api/schedules/{schedule_id}")
async def remove_schedule(schedule_id: int):
    delete_recurring_schedule(schedule_id)
    return {"success": True}


def resolve_gemini_api_key(provided_key: Optional[str] = None) -> str:
    key = (provided_key or "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        try:
            from google.cloud import secretmanager
            proj = os.environ.get("GCP_PROJECT_ID", "gen-lang-client-0096294200")
            client = secretmanager.SecretManagerServiceClient()
            secret_name = f"projects/{proj}/secrets/GEMINI_API_KEY/versions/latest"
            key = client.access_secret_version(request={"name": secret_name}).payload.data.decode("UTF-8").strip()
        except Exception:
            pass
    return key


@app.post("/api/schedules/{schedule_id}/run")
async def trigger_schedule_run(schedule_id: int, background_tasks: BackgroundTasks):
    schedules = load_recurring_schedules()
    target = next((s for s in schedules if s["id"] == schedule_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Schedule not found.")

    api_key = resolve_gemini_api_key()
    if not api_key:
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY environment variable is not configured.")

    def _run_bg():
        today_str = datetime.now().strftime("%Y-%m-%d")
        eval_id, results, err = _run_evaluation_headless(
            api_key=api_key,
            problem_statement=target["problem_text"],
            model_name=target["model_name"],
            paper_source=target.get("paper_source") or "arxiv",
            acl_track=target.get("acl_track") or "all",
            max_papers=target.get("max_papers"),
            days_back=target.get("days_back"),
            keyword_filter=target.get("keyword_filter") or "",
            max_concurrent=target.get("max_concurrent") or 3,
            min_score=target.get("min_score") or 6,
        )
        if err:
            update_schedule_last_run(schedule_id, today_str, "failed", err, eval_id)
        else:
            update_schedule_last_run(schedule_id, today_str, "success", f"Saved {len(results)} papers", eval_id)

    background_tasks.add_task(_run_bg)
    return {"success": True, "message": f"Triggered background evaluation for schedule #{schedule_id}"}


# ──────────────────────────────────────────────────────────────────────────────
# Past Evaluations & History API
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/evaluations")
async def list_evaluations():
    if core.DB_GCS_BUCKET or core.AWS_S3_BUCKET:
        await asyncio.to_thread(sync_db_from_cloud)
    evaluations = load_past_evaluations()
    for evaluation in evaluations:
        if evaluation.get("status") != "RUNNING" or not core.DB_GCS_BUCKET:
            continue
        try:
            progress = await asyncio.to_thread(sharded_eval.get_progress, int(evaluation["id"]))
        except (RuntimeError, ValueError, OSError):
            progress = None
        if progress:
            evaluation.update(progress)
    return {"evaluations": evaluations}


@app.get("/api/evaluations/{eval_id}")
async def get_evaluation_detail(eval_id: int):
    papers = load_evaluation_papers(eval_id)
    return {"eval_id": eval_id, "papers": papers}


@app.delete("/api/evaluations/{eval_id}")
async def remove_evaluation(eval_id: int):
    delete_evaluation(int(eval_id))
    return {"success": True}


@app.post("/api/evaluations/delete-bulk")
async def remove_evaluations_bulk(req: dict):
    eval_ids = [int(i) for i in req.get("eval_ids", []) if str(i).isdigit()]
    if eval_ids:
        delete_evaluations(eval_ids)
    return {"success": True, "deleted_count": len(eval_ids)}


@app.delete("/api/papers")
async def remove_papers(req: dict):
    paper_ids = [int(i) for i in req.get("paper_ids", []) if str(i).isdigit()]
    if paper_ids:
        delete_papers(paper_ids)
    return {"success": True, "deleted_count": len(paper_ids)}


@app.get("/api/all-papers")
async def list_all_papers():
    if core.DB_GCS_BUCKET or core.AWS_S3_BUCKET:
        await asyncio.to_thread(sync_db_from_cloud)
    return {"papers": load_all_papers()}


@app.get("/api/papers/{paper_id}")
async def get_paper_detail_endpoint(paper_id: int):
    detail = core.load_paper_detail(paper_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Paper not found")
    return detail


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=os.environ.get("HOST", "127.0.0.1"), port=8080, reload=True)
