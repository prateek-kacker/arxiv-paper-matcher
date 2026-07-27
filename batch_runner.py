#!/usr/bin/env python3
"""
Headless Batch Runner for arXiv CS.CL Paper Matcher
===================================================
Executed periodically (e.g., via GCP Cloud Run Jobs + Cloud Scheduler) to run due
recurring evaluations without needing a browser or desktop UI.

Results are persisted into SQLite (`paper_matcher.db`) and uploaded to Cloud Storage
(GCS / S3), making them immediately visible in the web app.
"""

import sys
import os
import argparse
from datetime import datetime
from pathlib import Path

# Add project directory to path
sys.path.insert(0, str(Path(__file__).parent))

from core_engine import (
    sync_db_from_cloud,
    sync_db_to_cloud,
    init_db,
    load_due_recurring_schedules,
    load_recurring_schedules,
    _run_evaluation_headless,
    update_schedule_last_run,
    _post_results_to_webhook,
    DB_PATH,
    DB_GCS_BUCKET,
    AWS_S3_BUCKET,
)


def log(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Headless Batch Runner for arXiv Paper Matcher")
    parser.add_argument("--all", action="store_true", help="Force run all active recurring schedules")
    parser.add_argument("--schedule-id", type=int, help="Run a specific recurring schedule ID")
    args = parser.parse_args()

    log("🚀 Starting Headless Batch Runner")
    log(f"DB Path:      {DB_PATH}")
    log(f"GCS Bucket:   {DB_GCS_BUCKET or '(disabled)'}")
    log(f"S3 Bucket:    {AWS_S3_BUCKET or '(disabled)'}")

    # 1. Resolve API Key
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        log("❌ ERROR: GEMINI_API_KEY environment variable is not set!")
        sys.exit(1)

    # 2. Sync latest SQLite database from Cloud Storage
    log("📥 Syncing database from Cloud Storage...")
    synced, sync_msg = sync_db_from_cloud()
    log(f"   Sync result: {sync_msg}")

    # 3. Initialize SQLite tables if needed
    init_db()

    # 4. Determine which schedules to run
    schedules_to_run = []
    if args.schedule_id:
        all_schedules = load_recurring_schedules()
        schedules_to_run = [s for s in all_schedules if s["id"] == args.schedule_id]
        if not schedules_to_run:
            log(f"❌ ERROR: Schedule ID {args.schedule_id} not found.")
            sys.exit(1)
        log(f"🎯 Target schedule ID: {args.schedule_id}")
    elif args.all:
        all_schedules = load_recurring_schedules()
        schedules_to_run = [s for s in all_schedules if s.get("is_active", 1) == 1]
        log(f"⚡ Force running ALL active schedules ({len(schedules_to_run)} total)")
    else:
        schedules_to_run = load_due_recurring_schedules()
        log(f"⏰ Found {len(schedules_to_run)} due schedule(s)")

    if not schedules_to_run:
        log("✅ No recurring schedules due for execution.")
        sys.exit(0)

    # 5. Process each schedule
    webhook_url = os.environ.get("KACKERS_POST_URL", "").strip()
    webhook_token = os.environ.get("KACKERS_POST_TOKEN", "").strip()
    auto_push = os.environ.get("AUTO_PUSH_RECURRING_RESULTS", "true").strip().lower() == "true"

    success_count = 0
    fail_count = 0

    for sch in schedules_to_run:
        sch_id = sch["id"]
        label = sch.get("label") or f"Schedule #{sch_id}"
        log(f"\n==================================================")
        log(f"▶️ Executing Schedule #{sch_id}: {label}")
        log(f"   Model:     {sch['model_name']}")
        log(f"   Problem:   {sch['problem_text'][:80]}...")
        log(f"   Mode:      {sch['fetch_mode']}")
        log(f"==================================================")

        def progress_cb(stage: str, done: int, total: int):
            if stage == "fetching":
                log("   📡 Fetching arXiv papers...")
            elif stage == "evaluating":
                log(f"   ⚖️ Evaluated {done}/{total} papers...")
            elif stage == "saving":
                log("   💾 Saving results to local SQLite DB...")
            elif stage == "syncing":
                log("   ☁️ Preparing cloud storage upload...")

        today_str = datetime.now().strftime("%Y-%m-%d")
        eval_id, results, err = _run_evaluation_headless(
            api_key=api_key,
            problem_statement=sch["problem_text"],
            model_name=sch["model_name"],
            max_papers=sch.get("max_papers"),
            days_back=sch.get("days_back"),
            keyword_filter=sch.get("keyword_filter") or "",
            max_concurrent=sch.get("max_concurrent") or 3,
            min_score=sch.get("min_score") or 6,
            progress_cb=progress_cb,
        )

        if err:
            log(f"❌ Schedule #{sch_id} failed: {err}")
            update_schedule_last_run(sch_id, today_str, "failed", err, None, sync_cloud=False)
            fail_count += 1
        else:
            log(f"✅ Schedule #{sch_id} completed successfully!")
            log(f"   Evaluation ID: {eval_id}")
            log(f"   Papers evaluated: {len(results)}")
            if results:
                log(f"   Top Paper Score: {results[0].avg_score}/10 — {results[0].paper.title[:60]}")

            update_schedule_last_run(
                sch_id, today_str, "success",
                f"Saved {len(results)} papers", eval_id, sync_cloud=False
            )
            success_count += 1

            if auto_push and webhook_url:
                log("   🌐 Posting results to Webhook...")
                post_ok, post_msg = _post_results_to_webhook(
                    endpoint_url=webhook_url,
                    results=results,
                    evaluation_id=eval_id,
                    problem_text=sch["problem_text"],
                    model_name=sch["model_name"],
                    trigger="recurring_cloud_run_job",
                    schedule_id=sch_id,
                    token=webhook_token,
                )
                log(f"   Webhook status: {post_msg}")

    # 6. Sync updated DB back to Cloud Storage
    log("\n📤 Uploading updated database to Cloud Storage...")
    synced_to, to_msg = sync_db_to_cloud()
    log(f"   Upload result: {to_msg}")

    log(f"\n==================================================")
    log(f"🏁 Batch Runner finished: {success_count} succeeded, {fail_count} failed")
    log(f"==================================================")


if __name__ == "__main__":
    main()
