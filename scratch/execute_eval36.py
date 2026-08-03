import os
import sys
import asyncio
from pathlib import Path

# Add workspace to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Non-secret runtime config
os.environ["PAPER_MATCHER_DB_BUCKET"] = "gen-lang-client-0096294200-paper-matcher-data"

import core_engine as core
from core_engine import (
    sync_db_from_cloud,
    sync_db_to_cloud,
    init_db,
    fetch_arxiv_papers,
    DebateEngine,
    save_paper,
    save_debate_round,
    save_judge_verdict,
    update_evaluation_progress,
)
from google import genai

async def main():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required in environment (Secret Manager / env var)")

    print("1. Syncing database from GCS...")
    synced, msg = sync_db_from_cloud()
    print("   Sync result:", msg)

    init_db()

    eval_id = 36
    problem_statement = (
        "I want to research feedback loops and continuous learning algorithms that would help scale "
        "and accelerate AI learning within the organization. Continuous learning can consume evals, traces, "
        "memory, and reasoning to create learning loops across different products and projects in the enterprise."
    )
    model_name = "gemini-2.5-flash"

    print(f"\n2. Fetching arXiv papers for Eval #{eval_id}...")
    papers = fetch_arxiv_papers(max_results=10, search_query="continuous learning feedback loop reasoning")
    if not papers:
        print("   No papers returned with specific query, fetching top CS.CL papers...")
        papers = fetch_arxiv_papers(max_results=10)

    print(f"   Fetched {len(papers)} papers.")

    client = genai.Client(api_key=api_key)
    engine = DebateEngine(client=client, model_name=model_name)

    print("\n3. Starting multi-agent debate for each paper...")
    update_evaluation_progress(eval_id, completed=0, total=len(papers), status="RUNNING", sync_cloud=False)

    sem = asyncio.Semaphore(3)
    completed_count = 0

    async def eval_one(idx, paper):
        nonlocal completed_count
        async with sem:
            print(f"   [{idx}/{len(papers)}] Evaluating: {paper.title[:60]}...")
            try:
                result = await engine.run_debate(paper, problem_statement)
                p_id = save_paper(eval_id, result.paper, result.avg_score, sync_cloud=False)
                for r_idx, r in enumerate(result.rounds, start=1):
                    save_debate_round(p_id, r_idx, r.advocate_argument, r.skeptic_argument, sync_cloud=False)
                for v in result.judge_verdicts:
                    save_judge_verdict(p_id, v.run, v.seed, v.relevance_score, v.verdict, v.key_reasons, v.suggested_use, sync_cloud=False)
                print(f"   [{idx}/{len(papers)}] Done! Score: {result.avg_score}/10")
            except Exception as e:
                print(f"   [{idx}/{len(papers)}] ERROR: {e}")

            completed_count += 1
            update_evaluation_progress(eval_id, completed=completed_count, status="RUNNING", sync_cloud=False)

    tasks = [eval_one(idx, p) for idx, p in enumerate(papers, 1)]
    await asyncio.gather(*tasks)

    print("\n4. Finalizing evaluation & uploading DB to GCS...")
    update_evaluation_progress(eval_id, completed=len(papers), total=len(papers), status="COMPLETED", sync_cloud=True)
    print("   Database successfully updated and uploaded to Cloud Storage!")

if __name__ == "__main__":
    asyncio.run(main())
