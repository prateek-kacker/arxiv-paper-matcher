"""Resume production eval 12 through durable Cloud Run Job shards."""

import core_engine as core
import sharded_eval


EXPECTED_TOTAL = 131
EXPECTED_SAVED = 110


def main() -> None:
    synced, message = core.sync_db_from_cloud()
    if not synced:
        raise RuntimeError(message)
    core.init_db()
    papers = core.fetch_acl_papers(
        max_results=None,
        search_query="memory",
        volume_filter="all",
    )
    if len(papers) != EXPECTED_TOTAL:
        raise RuntimeError(f"Expected {EXPECTED_TOTAL} ACL papers, found {len(papers)}.")
    if len(core.load_evaluation_paper_urls(12)) != EXPECTED_SAVED:
        raise RuntimeError(f"Eval 12 no longer has exactly {EXPECTED_SAVED} saved papers.")

    manifest = sharded_eval.prepare_resume(eval_id=12, papers=papers, max_concurrent=2)
    if manifest["seeded_papers"] != EXPECTED_SAVED or manifest["missing_papers"] != 21:
        raise RuntimeError(f"Unexpected resume plan: {manifest}")
    operation = sharded_eval.launch_job(12, manifest["total_shards"])
    print({
        "eval_id": 12,
        "total_papers": manifest["total_papers"],
        "seeded_papers": manifest["seeded_papers"],
        "missing_papers": manifest["missing_papers"],
        "total_shards": manifest["total_shards"],
        "operation": operation,
    })


if __name__ == "__main__":
    main()