"""
Unit tests for core_engine.py (Database, Persistence, Schedules, Filtering, and Cloud Sync)
"""

import os
import pytest
from datetime import datetime, timedelta
import core_engine as core
from core_engine import Paper


def test_init_db(temp_db_env):
    """Test that init_db creates all required tables cleanly."""
    conn = core.get_db()
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    conn.close()
    
    expected = {"evaluations", "papers", "debate_rounds", "judge_verdicts", "recurring_schedules"}
    assert expected.issubset(set(tables))


def test_evaluation_crud(temp_db_env):
    """Test creating, updating, loading, and deleting evaluations."""
    eval_id = core.save_evaluation(problem="Translate Indic languages", model="gemini-3-flash-preview", sync_cloud=False)
    assert isinstance(eval_id, int)
    assert eval_id > 0
    
    # Create a Paper dataclass instance
    paper = Paper(
        title="Indic-Trans: Low-Resource NMT",
        authors="A. Sharma, B. Gupta",
        abstract="Novel data augmentation for low resource Indic NMT.",
        url="https://arxiv.org/abs/2401.00001",
        published="2026-01-15",
        categories="cs.CL",
        full_text="Full text content of Indic-Trans paper baseline..."
    )
    
    # Save paper for evaluation
    paper_id = core.save_paper(
        eval_id=eval_id,
        paper=paper,
        avg_score=8.5,
        sync_cloud=False
    )
    assert paper_id > 0
    
    # Save debate round and judge verdict
    core.save_debate_round(paper_id=paper_id, round_num=1, advocate="Strong approach", skeptic="Small test set", sync_cloud=False)
    core.save_judge_verdict(
        paper_id=paper_id,
        run=1,
        seed=42,
        score=9,
        verdict="Relevant",
        reasons=["Great BLEU improvements"],
        suggested="Fine-tuning baseline",
        sync_cloud=False
    )
    
    # Verify paper loading
    all_papers = core.load_all_papers()
    assert len(all_papers) == 1
    p = all_papers[0]
    assert p["title"] == "Indic-Trans: Low-Resource NMT"
    assert p["avg_score"] == 8.5
    
    # Verify evaluation detail loading
    eval_papers = core.load_evaluation_papers(eval_id)
    assert len(eval_papers) == 1
    assert len(eval_papers[0]["verdicts"]) == 1
    assert len(eval_papers[0]["debates"]) == 1
    
    # Test past paper matching
    matches = core.find_matching_past_papers(["https://arxiv.org/abs/2401.00001"])
    assert "https://arxiv.org/abs/2401.00001" in matches
    assert matches["https://arxiv.org/abs/2401.00001"][0]["avg_score"] == 8.5
    
    # Delete paper
    core.delete_papers([paper_id], sync_cloud=False)
    assert len(core.load_all_papers()) == 0
    
    # Delete evaluation
    core.delete_evaluation(eval_id, sync_cloud=False)
    assert len(core.load_past_evaluations()) == 0


def test_recurring_schedules_crud(temp_db_env):
    """Test creating, updating, toggling, and deleting recurring schedules."""
    sch_id = core.create_recurring_schedule(
        label="Daily MT Eval",
        problem_text="Low-resource translation",
        model_name="gemini-3-pro-preview",
        paper_source="arxiv",
        acl_track="all",
        fetch_mode="count",
        max_papers=10,
        days_back=None,
        keyword_filter="Indic",
        min_score=6,
        max_concurrent=3,
        run_time="09:00",
        sync_cloud=False
    )
    assert sch_id > 0
    
    schedules = core.load_recurring_schedules()
    assert len(schedules) == 1
    sch = schedules[0]
    assert sch["label"] == "Daily MT Eval"
    assert sch["is_active"] == 1
    
    # Toggle active status
    core.set_recurring_schedule_active(sch_id, False, sync_cloud=False)
    schedules = core.load_recurring_schedules()
    assert schedules[0]["is_active"] == 0
    
    core.set_recurring_schedule_active(sch_id, True, sync_cloud=False)
    
    # Update schedule
    core.update_recurring_schedule(
        schedule_id=sch_id,
        label="Updated Daily MT Eval",
        problem_text="Updated text",
        model_name="gemini-2.5-pro",
        paper_source="acl",
        acl_track="acl-long",
        fetch_mode="days",
        max_papers=None,
        days_back=5,
        keyword_filter="translation",
        min_score=7,
        max_concurrent=4,
        run_time="10:00",
        sync_cloud=False
    )
    
    updated_sch = core.load_recurring_schedules()[0]
    assert updated_sch["label"] == "Updated Daily MT Eval"
    assert updated_sch["model_name"] == "gemini-2.5-pro"
    assert updated_sch["run_time"] == "10:00"
    
    # Test delete schedule
    core.delete_recurring_schedule(sch_id, sync_cloud=False)
    assert len(core.load_recurring_schedules()) == 0


def test_cloud_sync_fallback(temp_db_env):
    """Test cloud sync status functions when GCS/S3 buckets are unconfigured."""
    ok, msg = core.sync_db_from_cloud()
    assert ok is False
    assert "disabled" in msg.lower()
    
    ok, msg = core.sync_db_to_cloud()
    assert ok is False
    assert "disabled" in msg.lower()
