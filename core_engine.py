"""
arXiv CS.CL Paper Matcher — Core Engine
========================================
Core data models, SQLite persistence, Cloud Storage (GCS/S3) sync, arXiv API fetcher,
and Multi-Agent Debate Engine with 5-Judge Panel.
"""

import os
import json
import asyncio
import random
import sqlite3
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

DB_PATH = Path(os.environ.get("PAPER_MATCHER_DB_PATH", str(Path(__file__).parent / "paper_matcher.db")))
DB_GCS_BUCKET = os.environ.get("PAPER_MATCHER_DB_BUCKET", "").strip()
DB_GCS_BLOB = os.environ.get("PAPER_MATCHER_DB_BLOB", "paper_matcher.db").strip() or "paper_matcher.db"
AWS_S3_BUCKET = os.environ.get("AWS_S3_BUCKET", "").strip()
AWS_S3_KEY = os.environ.get("AWS_S3_KEY", "paper_matcher.db").strip() or "paper_matcher.db"
PAPER_EVAL_TIMEOUT_SECONDS = int(os.environ.get("PAPER_EVAL_TIMEOUT_SECONDS", "240"))
_DB_SYNC_LOCK = threading.Lock()


def _db_sidecar_paths() -> list[Path]:
    """Return SQLite sidecar file paths for WAL mode."""
    return [
        Path(str(DB_PATH) + "-wal"),
        Path(str(DB_PATH) + "-shm"),
    ]


def _checkpoint_db():
    """Flush WAL pages into the main DB file before uploading."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    conn.close()


def sync_db_from_cloud() -> tuple[bool, str]:
    """Download DB file from Cloud Storage (GCS or AWS S3)."""
    if DB_GCS_BUCKET:
        try:
            client = storage.Client()
            bucket = client.bucket(DB_GCS_BUCKET)
            blob = bucket.blob(DB_GCS_BLOB)
            if blob.exists(client):
                DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                blob.download_to_filename(str(DB_PATH))
                for p in _db_sidecar_paths():
                    if p.exists():
                        p.unlink()
                return True, f"DB downloaded from GCS bucket `{DB_GCS_BUCKET}`"
        except Exception as e:
            return False, f"Failed to load DB from GCS: {e}"

    if AWS_S3_BUCKET:
        try:
            import boto3
            s3 = boto3.client("s3")
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(AWS_S3_BUCKET, AWS_S3_KEY, str(DB_PATH))
            for p in _db_sidecar_paths():
                if p.exists():
                    p.unlink()
            return True, f"DB downloaded from AWS S3 bucket `{AWS_S3_BUCKET}`"
        except Exception as e:
            return False, f"Failed to load DB from S3: {e}"

    return False, "Cloud persistence disabled (neither GCS nor S3 bucket set)"


def sync_db_to_cloud() -> tuple[bool, str]:
    """Upload local DB file to Cloud Storage (GCS and/or AWS S3)."""
    if not DB_PATH.exists():
        return False, "Local DB file not found"

    if not DB_GCS_BUCKET and not AWS_S3_BUCKET:
        return False, "Cloud persistence disabled"

    results = []
    with _DB_SYNC_LOCK:
        _checkpoint_db()
        if DB_GCS_BUCKET:
            try:
                client = storage.Client()
                bucket = client.bucket(DB_GCS_BUCKET)
                blob = bucket.blob(DB_GCS_BLOB)
                blob.upload_from_filename(str(DB_PATH))
                results.append(f"GCS (`{DB_GCS_BUCKET}`)")
            except Exception as e:
                results.append(f"GCS error: {e}")

        if AWS_S3_BUCKET:
            try:
                import boto3
                s3 = boto3.client("s3")
                s3.upload_file(str(DB_PATH), AWS_S3_BUCKET, AWS_S3_KEY)
                results.append(f"S3 (`{AWS_S3_BUCKET}`)")
            except Exception as e:
                results.append(f"S3 error: {e}")

    return True, f"Uploaded to: {', '.join(results)}"


def get_db() -> sqlite3.Connection:
    """Return a SQLite connection with WAL mode."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS evaluations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            problem_text    TEXT NOT NULL,
            model_name      TEXT NOT NULL,
            status          TEXT DEFAULT 'COMPLETED',
            total_papers    INTEGER DEFAULT 0,
            completed_papers INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS papers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            evaluation_id   INTEGER NOT NULL REFERENCES evaluations(id),
            title           TEXT NOT NULL,
            authors         TEXT,
            abstract        TEXT,
            url             TEXT,
            published       TEXT,
            categories      TEXT,
            avg_score       REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS debate_rounds (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id        INTEGER NOT NULL REFERENCES papers(id),
            round_num       INTEGER NOT NULL,
            advocate_arg    TEXT,
            skeptic_arg     TEXT
        );

        CREATE TABLE IF NOT EXISTS judge_verdicts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id        INTEGER NOT NULL REFERENCES papers(id),
            judge_run       INTEGER NOT NULL,
            seed            INTEGER,
            relevance_score INTEGER,
            verdict         TEXT,
            key_reasons     TEXT,
            suggested_use   TEXT
        );

        CREATE TABLE IF NOT EXISTS recurring_schedules (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            label           TEXT,
            problem_text    TEXT NOT NULL,
            model_name      TEXT NOT NULL,
            fetch_mode      TEXT NOT NULL,
            max_papers      INTEGER,
            days_back       INTEGER,
            keyword_filter  TEXT,
            min_score       INTEGER DEFAULT 6,
            max_concurrent  INTEGER DEFAULT 3,
            run_time        TEXT NOT NULL,
            is_active       INTEGER DEFAULT 1,
            last_run_date   TEXT,
            last_run_at     TEXT,
            last_status     TEXT,
            last_message    TEXT,
            last_eval_id    INTEGER,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS acl_papers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event_year      TEXT NOT NULL,
            paper_key       TEXT UNIQUE NOT NULL,
            title           TEXT NOT NULL,
            authors         TEXT,
            abstract        TEXT,
            url             TEXT,
            pdf_url         TEXT,
            published       TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()

    # Migration columns for background evaluation tracking
    for col_def in [
        "status TEXT DEFAULT 'COMPLETED'",
        "total_papers INTEGER DEFAULT 0",
        "completed_papers INTEGER DEFAULT 0",
    ]:
        try:
            conn.execute(f"ALTER TABLE evaluations ADD COLUMN {col_def}")
            conn.commit()
        except Exception:
            pass

    conn.close()


def save_evaluation(problem: str, model: str, status: str = 'COMPLETED', total: int = 0, sync_cloud: bool = True) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO evaluations (problem_text, model_name, status, total_papers, completed_papers) VALUES (?, ?, ?, ?, 0)",
        (problem, model, status, total),
    )
    conn.commit()
    eval_id = cur.lastrowid
    conn.close()
    if sync_cloud:
        sync_db_to_cloud()
    return eval_id


def update_evaluation_progress(eval_id: int, completed: int, total: Optional[int] = None, status: Optional[str] = None, sync_cloud: bool = True):
    conn = get_db()
    updates = ["completed_papers = ?"]
    params: list = [completed]
    if total is not None:
        updates.append("total_papers = ?")
        params.append(total)
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    params.append(eval_id)
    conn.execute(f"UPDATE evaluations SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    if sync_cloud:
        sync_db_to_cloud()


def save_paper(eval_id: int, paper: "Paper", avg_score: float, sync_cloud: bool = True) -> int:
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO papers
           (evaluation_id, title, authors, abstract, url, published, categories, avg_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (eval_id, paper.title, paper.authors, paper.abstract,
         paper.url, paper.published, paper.categories, avg_score),
    )
    conn.commit()
    paper_id = cur.lastrowid
    conn.close()
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
    conn = get_db()
    conn.execute(
        "INSERT INTO debate_rounds (paper_id, round_num, advocate_arg, skeptic_arg) VALUES (?, ?, ?, ?)",
        (paper_id, round_num, advocate, skeptic),
    )
    conn.commit()
    conn.close()
    if sync_cloud:
        sync_db_to_cloud()


def save_judge_verdict(paper_id: int, run: int, seed: int, score: int,
                       verdict: str, reasons: list[str], suggested: str,
                       sync_cloud: bool = True):
    conn = get_db()
    conn.execute(
        """INSERT INTO judge_verdicts
           (paper_id, judge_run, seed, relevance_score, verdict, key_reasons, suggested_use)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (paper_id, run, seed, score, verdict, json.dumps(reasons), suggested),
    )
    conn.commit()
    conn.close()
    if sync_cloud:
        sync_db_to_cloud()


def load_past_evaluations() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT e.id, e.problem_text, e.model_name,
                  COALESCE(e.status, 'COMPLETED') AS status,
                  COALESCE(e.total_papers, 0) AS total_papers,
                  COALESCE(e.completed_papers, COUNT(p.id)) AS completed_papers,
                  e.created_at,
                  COUNT(p.id) AS paper_count,
                  ROUND(AVG(p.avg_score), 1) AS overall_avg
           FROM evaluations e
           LEFT JOIN papers p ON p.evaluation_id = e.id
           GROUP BY e.id
           ORDER BY e.created_at DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_evaluation_papers(eval_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM papers WHERE evaluation_id = ? ORDER BY avg_score DESC",
        (eval_id,),
    ).fetchall()
    papers = [dict(r) for r in rows]
    for p in papers:
        pid = p["id"]
        debates = conn.execute("SELECT * FROM debate_rounds WHERE paper_id = ? ORDER BY round_num", (pid,)).fetchall()
        verdicts = conn.execute("SELECT * FROM judge_verdicts WHERE paper_id = ? ORDER BY judge_run", (pid,)).fetchall()
        p["debates"] = [dict(d) for d in debates]
        v_list = []
        for v in verdicts:
            vd = dict(v)
            try:
                vd["key_reasons"] = json.loads(vd["key_reasons"]) if vd["key_reasons"] else []
            except Exception:
                vd["key_reasons"] = []
            v_list.append(vd)
        p["verdicts"] = v_list
    conn.close()
    return papers


def delete_evaluation(eval_id: int, sync_cloud: bool = True):
    conn = get_db()
    paper_ids = [r['id'] for r in conn.execute("SELECT id FROM papers WHERE evaluation_id = ?", (eval_id,)).fetchall()]
    for pid in paper_ids:
        conn.execute("DELETE FROM judge_verdicts WHERE paper_id = ?", (pid,))
        conn.execute("DELETE FROM debate_rounds WHERE paper_id = ?", (pid,))
    conn.execute("DELETE FROM papers WHERE evaluation_id = ?", (eval_id,))
    conn.execute("DELETE FROM evaluations WHERE id = ?", (eval_id,))
    conn.commit()
    conn.close()
    if sync_cloud:
        sync_db_to_cloud()


def delete_papers(paper_ids: list[int], sync_cloud: bool = True):
    conn = get_db()
    for pid in paper_ids:
        conn.execute("DELETE FROM judge_verdicts WHERE paper_id = ?", (pid,))
        conn.execute("DELETE FROM debate_rounds WHERE paper_id = ?", (pid,))
        conn.execute("DELETE FROM papers WHERE id = ?", (pid,))
    conn.commit()
    conn.close()
    if sync_cloud:
        sync_db_to_cloud()


def load_all_papers() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT p.*, e.problem_text, e.model_name, e.created_at AS eval_date
           FROM papers p
           JOIN evaluations e ON e.id = p.evaluation_id
           ORDER BY p.avg_score DESC"""
    ).fetchall()
    papers = [dict(r) for r in rows]
    for p in papers:
        pid = p["id"]
        verdicts = conn.execute("SELECT relevance_score, judge_run FROM judge_verdicts WHERE paper_id = ? ORDER BY judge_run", (pid,)).fetchall()
        p["judge_scores"] = [{"run": v["judge_run"], "score": v["relevance_score"]} for v in verdicts]
    conn.close()
    return papers


def load_paper_detail(paper_id: int) -> dict:
    conn = get_db()
    row = conn.execute(
        """SELECT p.*, e.problem_text, e.model_name, e.created_at AS eval_date
           FROM papers p
           JOIN evaluations e ON e.id = p.evaluation_id
           WHERE p.id = ?""",
        (paper_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {}
    p = dict(row)
    debates = conn.execute("SELECT * FROM debate_rounds WHERE paper_id = ? ORDER BY round_num", (paper_id,)).fetchall()
    verdicts = conn.execute("SELECT * FROM judge_verdicts WHERE paper_id = ? ORDER BY judge_run", (paper_id,)).fetchall()
    p["debates"] = [dict(d) for d in debates]
    v_list = []
    for v in verdicts:
        vd = dict(v)
        try:
            vd["key_reasons"] = json.loads(vd["key_reasons"]) if vd["key_reasons"] else []
        except Exception:
            vd["key_reasons"] = []
        v_list.append(vd)
    p["verdicts"] = v_list
    conn.close()
    return p


def create_recurring_schedule(
    label: str,
    problem_text: str,
    model_name: str,
    fetch_mode: str,
    max_papers: Optional[int],
    days_back: Optional[int],
    keyword_filter: str,
    min_score: int,
    max_concurrent: int,
    run_time: str,
    sync_cloud: bool = True,
) -> int:
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO recurring_schedules
           (label, problem_text, model_name, fetch_mode, max_papers, days_back,
            keyword_filter, min_score, max_concurrent, run_time)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            label, problem_text, model_name, fetch_mode, max_papers, days_back,
            keyword_filter, min_score, max_concurrent, run_time,
        ),
    )
    conn.commit()
    schedule_id = cur.lastrowid
    conn.close()
    if sync_cloud:
        sync_db_to_cloud()
    return schedule_id


def update_recurring_schedule(
    schedule_id: int,
    label: str,
    problem_text: str,
    model_name: str,
    fetch_mode: str,
    max_papers: Optional[int],
    days_back: Optional[int],
    keyword_filter: str,
    min_score: int,
    max_concurrent: int,
    run_time: str,
    sync_cloud: bool = True,
):
    conn = get_db()
    conn.execute(
        """UPDATE recurring_schedules
           SET label = ?, problem_text = ?, model_name = ?, fetch_mode = ?,
               max_papers = ?, days_back = ?, keyword_filter = ?, min_score = ?,
               max_concurrent = ?, run_time = ?
           WHERE id = ?""",
        (
            label, problem_text, model_name, fetch_mode, max_papers, days_back,
            keyword_filter, min_score, max_concurrent, run_time, schedule_id,
        ),
    )
    conn.commit()
    conn.close()
    if sync_cloud:
        sync_db_to_cloud()


def load_recurring_schedules() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM recurring_schedules
           ORDER BY is_active DESC, run_time ASC, id DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_recurring_schedule_active(schedule_id: int, active: bool, sync_cloud: bool = True):
    conn = get_db()
    conn.execute("UPDATE recurring_schedules SET is_active = ? WHERE id = ?", (1 if active else 0, schedule_id))
    conn.commit()
    conn.close()
    if sync_cloud:
        sync_db_to_cloud()


def delete_recurring_schedule(schedule_id: int, sync_cloud: bool = True):
    conn = get_db()
    conn.execute("DELETE FROM recurring_schedules WHERE id = ?", (schedule_id,))
    conn.commit()
    conn.close()
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
    conn = get_db()
    conn.execute(
        """UPDATE recurring_schedules
           SET last_run_date = ?, last_run_at = ?, last_status = ?, last_message = ?, last_eval_id = ?
           WHERE id = ?""",
        (run_date, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), status, message, eval_id, schedule_id),
    )
    conn.commit()
    conn.close()
    if sync_cloud:
        sync_db_to_cloud()


def load_due_recurring_schedules(now: Optional[datetime] = None) -> list[dict]:
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    now_hhmm = now.strftime("%H:%M")
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM recurring_schedules
           WHERE is_active = 1 AND run_time <= ? AND (last_run_date IS NULL OR last_run_date <> ?)
           ORDER BY run_time ASC""",
        (now_hhmm, today),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def find_matching_past_papers(urls: list[str]) -> dict[str, list[dict]]:
    if not urls:
        return {}
    conn = get_db()
    placeholders = ",".join("?" for _ in urls)
    rows = conn.execute(
        f"""SELECT p.*, e.problem_text, e.model_name, e.created_at AS eval_date
            FROM papers p
            JOIN evaluations e ON e.id = p.evaluation_id
            WHERE p.url IN ({placeholders})
            ORDER BY p.avg_score DESC""",
        urls,
    ).fetchall()
    conn.close()
    matches: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        matches.setdefault(d['url'], []).append(d)
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
        full_query = f"{category_query} AND ({search_query.strip()})"
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
    return papers


def fetch_acl_papers(
    max_results: Optional[int] = 50,
    search_query: Optional[str] = None,
    volume_filter: str = "all",
) -> list[Paper]:
    """
    Fetches ACL 2026 Papers filtered by track/volume (Long, Short, Findings, Demos, SRW, Industry, Workshops, All).
    Caches extracted papers in SQLite database and syncs to Google Cloud Storage.
    """
    conn = get_db()
    query_sql = "SELECT * FROM acl_papers WHERE event_year = '2026'"
    params: list = []

    if volume_filter == "acl-long":
        query_sql += " AND paper_key LIKE '%.acl-long.%'"
    elif volume_filter == "findings-acl":
        query_sql += " AND paper_key LIKE '%.findings-acl.%'"
    elif volume_filter == "acl-short":
        query_sql += " AND paper_key LIKE '%.acl-short.%'"
    elif volume_filter == "acl-industry":
        query_sql += " AND paper_key LIKE '%.acl-industry.%'"
    elif volume_filter == "acl-demo":
        query_sql += " AND (paper_key LIKE '%.acl-demo.%' OR paper_key LIKE '%.acl-demos.%')"
    elif volume_filter == "acl-srw":
        query_sql += " AND paper_key LIKE '%.acl-srw.%'"
    elif volume_filter == "workshops":
        query_sql += """ AND paper_key NOT LIKE '%.acl-long.%'
                         AND paper_key NOT LIKE '%.findings-acl.%'
                         AND paper_key NOT LIKE '%.acl-short.%'
                         AND paper_key NOT LIKE '%.acl-industry.%'
                         AND paper_key NOT LIKE '%.acl-demo.%'
                         AND paper_key NOT LIKE '%.acl-demos.%'
                         AND paper_key NOT LIKE '%.acl-srw.%'"""

    if search_query and search_query.strip():
        query_sql += " AND (title LIKE ? OR abstract LIKE ? OR authors LIKE ?)"
        q = f"%{search_query.strip()}%"
        params.extend([q, q, q])

    query_sql += " LIMIT ?"
    params.append(max_results or 50)

    rows = conn.execute(query_sql, params).fetchall()

    paper_objs = []
    for r in rows:
        pid = r["id"]
        title = r["title"]
        authors = r["authors"] or "ACL 2026 Authors"
        abstract = r["abstract"] or ""
        p_url = r["url"]
        pdf_url = r["pdf_url"]
        published = r["published"] or "2026-08"

        if not abstract and p_url:
            try:
                p_req = urllib.request.Request(p_url, headers={'User-Agent': 'Mozilla/5.0'})
                p_html = urllib.request.urlopen(p_req).read().decode('utf-8')
                m = re.search(r'class="card-body acl-abstract"[^>]*>(.*?)</div>', p_html, re.DOTALL)
                if m:
                    abstract = re.sub(r'<[^>]+>', '', m.group(1)).replace('Abstract', '', 1).strip()
                    conn.execute("UPDATE acl_papers SET abstract = ? WHERE id = ?", (abstract, pid))
                    conn.commit()
            except Exception:
                abstract = title

        paper_objs.append(Paper(
            title=title,
            authors=authors,
            abstract=abstract or title,
            url=pdf_url or p_url,
            published=published,
            categories="ACL-2026",
        ))

    conn.close()
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
    def __init__(self, client: genai.Client, model_name: str = "gemini-3-pro-preview"):
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

        context = (
            f"## User's Research Problem\n{problem}\n\n"
            f"## Paper Under Review\n"
            f"**Title:** {paper.title}\n"
            f"**Authors:** {paper.authors}\n"
            f"**Published:** {paper.published}\n"
            f"**Categories:** {paper.categories}\n"
            f"**Abstract:** {paper.abstract}\n"
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
        papers = fetch_arxiv_papers(max_results=max_papers, search_query=keyword_filter, days_back=days_back)
    except Exception as e:
        return None, [], f"Failed to fetch papers: {e}"

    if not papers:
        return None, [], "No papers found. Try adjusting keyword filters."

    _cb("evaluating", 0, len(papers))
    eval_id = save_evaluation(problem_statement, model_name, sync_cloud=False)
    results: list[DebateResult] = []
    completed = 0
    _lock = threading.Lock()

    def _evaluate(paper: Paper) -> DebateResult:
        try:
            c = genai.Client(api_key=api_key)
            eng = DebateEngine(client=c, model_name=model_name)
            return asyncio.run(asyncio.wait_for(eng.run_debate(paper, problem_statement), timeout=max(PAPER_EVAL_TIMEOUT_SECONDS, 30)))
        except TimeoutError:
            return DebateResult(paper=paper, combined_verdict="Evaluation timed out.", avg_score=0.0)
        except Exception as exc:
            return DebateResult(paper=paper, combined_verdict=f"Evaluation failed: {exc}", avg_score=0.0)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {executor.submit(_evaluate, p): p for p in papers}
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
            with _lock:
                completed += 1
            _cb("evaluating", completed, len(papers))

    _cb("saving", 0, len(results))
    for r in results:
        paper_id = save_paper(eval_id, r.paper, r.avg_score, sync_cloud=False)
        for idx, rnd in enumerate(r.rounds, 1):
            save_debate_round(paper_id, idx, rnd.advocate_argument, rnd.skeptic_argument, sync_cloud=False)
        for jv in r.judge_verdicts:
            save_judge_verdict(paper_id, jv.run, jv.seed, jv.relevance_score, jv.verdict, jv.key_reasons, jv.suggested_use, sync_cloud=False)

    _cb("syncing", 0, 0)
    sync_db_to_cloud()
    results.sort(key=lambda r: r.avg_score, reverse=True)
    return eval_id, results, None
