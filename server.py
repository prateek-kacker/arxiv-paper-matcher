"""
arXiv CS.CL Paper Matcher — FastAPI Application Server
======================================================
Provides REST APIs and Server-Sent Events (SSE) live streaming for the custom web app.
Serves static frontend assets from static/ directory.
"""

import os
import json
import asyncio
import threading
from typing import Optional
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

import core_engine as core
from core_engine import (
    sync_db_from_cloud,
    sync_db_to_cloud,
    init_db,
    save_evaluation,
    save_paper,
    save_debate_round,
    save_judge_verdict,
    load_past_evaluations,
    load_evaluation_papers,
    load_all_papers,
    delete_evaluation,
    delete_papers,
    create_recurring_schedule,
    update_recurring_schedule,
    load_recurring_schedules,
    set_recurring_schedule_active,
    delete_recurring_schedule,
    load_due_recurring_schedules,
    update_schedule_last_run,
    find_matching_past_papers,
    fetch_arxiv_papers,
    DebateEngine,
    Paper,
    DebateResult,
    _post_results_to_webhook,
    _run_evaluation_headless,
    DB_GCS_BUCKET,
    AWS_S3_BUCKET,
)

app = FastAPI(title="arXiv CS.CL Paper Matcher", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Database on startup
@app.on_event("startup")
def startup_event():
    sync_db_from_cloud()
    init_db()


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
        "webhook_url": os.environ.get("KACKERS_POST_URL", "").strip(),
        "auto_post": os.environ.get("AUTO_POST_RESULTS", "false").strip().lower() == "true",
    }


@app.get("/api/cloud-sync/status")
async def cloud_sync_status():
    return {
        "gcs_bucket": DB_GCS_BUCKET,
        "s3_bucket": AWS_S3_BUCKET,
        "db_path": str(core.DB_PATH),
        "db_exists": core.DB_PATH.exists(),
    }


@app.post("/api/cloud-sync/trigger")
async def trigger_cloud_sync():
    ok, msg = sync_db_from_cloud()
    return {"success": ok, "message": msg}


@app.post("/api/fetch-papers")
async def api_fetch_papers(req: dict):
    search_query = req.get("keyword_filter") or None
    days_back = req.get("days_back")
    max_results = req.get("max_papers") or 50
    if days_back is not None:
        days_back = int(days_back)
    if max_results is not None:
        max_results = int(max_results)

    try:
        papers = fetch_arxiv_papers(
            max_results=max_results,
            search_query=search_query,
            days_back=days_back,
        )
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/evaluate/stream")
async def api_evaluate_stream(request: Request):
    data = await request.json()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip() or data.get("api_key", "").strip()
    problem_statement = data.get("problem_statement", "").strip()
    model_name = data.get("model_name", "gemini-3-pro-preview")
    max_papers = data.get("max_papers")
    days_back = data.get("days_back")
    keyword_filter = data.get("keyword_filter", "").strip()
    max_concurrent = int(data.get("max_concurrent", 3))

    if not api_key:
        raise HTTPException(status_code=400, detail="Gemini API key is required.")
    if not problem_statement:
        raise HTTPException(status_code=400, detail="Research problem description is required.")

    async def event_generator():
        yield {
            "event": "stage",
            "data": json.dumps({"stage": "fetching", "message": "Fetching papers from arXiv CS.CL..."})
        }

        try:
            papers = fetch_arxiv_papers(
                max_results=max_papers,
                search_query=keyword_filter,
                days_back=days_back,
            )
        except Exception as e:
            yield {
                "event": "error",
                "data": json.dumps({"error": f"Failed to fetch papers: {e}"})
            }
            return

        if not papers:
            yield {
                "event": "error",
                "data": json.dumps({"error": "No arXiv papers found matching filters."})
            }
            return

        yield {
            "event": "stage",
            "data": json.dumps({"stage": "evaluating", "total_papers": len(papers), "message": f"Fetched {len(papers)} papers. Starting multi-agent debate..."})
        }

        eval_id = save_evaluation(problem_statement, model_name, sync_cloud=False)
        from google import genai
        c = genai.Client(api_key=api_key)
        eng = DebateEngine(client=c, model_name=model_name)

        results = []
        for idx, paper in enumerate(papers, 1):
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
            except Exception as exc:
                result = DebateResult(paper=paper, combined_verdict=f"Failed: {exc}", avg_score=0.0)

            results.append(result)

            paper_id = save_paper(eval_id, paper, result.avg_score, sync_cloud=False)
            for rnd_idx, rnd in enumerate(result.rounds, 1):
                save_debate_round(paper_id, rnd_idx, rnd.advocate_argument, rnd.skeptic_argument, sync_cloud=False)
            for jv in result.judge_verdicts:
                save_judge_verdict(paper_id, jv.run, jv.seed, jv.relevance_score, jv.verdict, jv.key_reasons, jv.suggested_use, sync_cloud=False)

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

        sync_db_to_cloud()

        yield {
            "event": "eval_complete",
            "data": json.dumps({
                "eval_id": eval_id,
                "total_evaluated": len(results),
                "message": "Evaluation completed and saved to database!",
            })
        }

    return EventSourceResponse(event_generator())


# ──────────────────────────────────────────────────────────────────────────────
# Schedules API
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/schedules")
async def get_schedules():
    return {"schedules": load_recurring_schedules()}


@app.post("/api/schedules")
async def add_schedule(req: dict):
    schedule_id = create_recurring_schedule(
        label=req.get("label", "").strip() or "Daily Schedule",
        problem_text=req["problem_text"].strip(),
        model_name=req.get("model_name", "gemini-3-pro-preview"),
        fetch_mode=req.get("fetch_mode", "count"),
        max_papers=req.get("max_papers"),
        days_back=req.get("days_back"),
        keyword_filter=req.get("keyword_filter", "").strip(),
        min_score=int(req.get("min_score", 6)),
        max_concurrent=int(req.get("max_concurrent", 3)),
        run_time=req.get("run_time", "08:00"),
    )
    return {"success": True, "schedule_id": schedule_id}


@app.put("/api/schedules/{schedule_id}")
async def edit_schedule(schedule_id: int, req: dict):
    update_recurring_schedule(
        schedule_id=schedule_id,
        label=req.get("label", "").strip() or f"Schedule #{schedule_id}",
        problem_text=req["problem_text"].strip(),
        model_name=req.get("model_name", "gemini-3-pro-preview"),
        fetch_mode=req.get("fetch_mode", "count"),
        max_papers=req.get("max_papers"),
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
    return {"success": True, "active": active}


@app.delete("/api/schedules/{schedule_id}")
async def remove_schedule(schedule_id: int):
    delete_recurring_schedule(schedule_id)
    return {"success": True}


@app.post("/api/schedules/{schedule_id}/run")
async def trigger_schedule_run(schedule_id: int, background_tasks: BackgroundTasks):
    schedules = load_recurring_schedules()
    target = next((s for s in schedules if s["id"] == schedule_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Schedule not found.")

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY environment variable is not configured.")

    def _run_bg():
        today_str = datetime.now().strftime("%Y-%m-%d")
        eval_id, results, err = _run_evaluation_headless(
            api_key=api_key,
            problem_statement=target["problem_text"],
            model_name=target["model_name"],
            max_papers=target.get("max_papers"),
            days_back=target.get("days_back"),
            keyword_filter=target.get("keyword_filter") or "",
            max_concurrent=target.get("max_concurrent") or 3,
            min_score=target.get("min_score") or 6,
        )
        if err:
            update_schedule_last_run(schedule_id, today_str, "failed", err, None)
        else:
            update_schedule_last_run(schedule_id, today_str, "success", f"Saved {len(results)} papers", eval_id)

    background_tasks.add_task(_run_bg)
    return {"success": True, "message": f"Triggered background evaluation for schedule #{schedule_id}"}


# ──────────────────────────────────────────────────────────────────────────────
# Past Evaluations & History API
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/evaluations")
async def list_evaluations():
    return {"evaluations": load_past_evaluations()}


@app.get("/api/evaluations/{eval_id}")
async def get_evaluation_detail(eval_id: int):
    papers = load_evaluation_papers(eval_id)
    return {"eval_id": eval_id, "papers": papers}


@app.delete("/api/evaluations/{eval_id}")
async def remove_evaluation(eval_id: int):
    delete_evaluation(eval_id)
    return {"success": True}


@app.delete("/api/papers")
async def remove_papers(req: dict):
    paper_ids = req.get("paper_ids", [])
    if paper_ids:
        delete_papers(paper_ids)
    return {"success": True, "deleted_count": len(paper_ids)}


@app.get("/api/all-papers")
async def list_all_papers():
    return {"papers": load_all_papers()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8080, reload=True)
