"""
arXiv CS.CL Paper Matcher — Multi-Agent Debate with Gemini LLM-as-Judge
=========================================================================
Fetches recent papers from arXiv CS.CL, then runs a multi-agent debate
(Advocate, Skeptic, Judge-Panel) to assess each paper's relevance to your
research problem.  All papers, debate transcripts, and judge verdicts are
persisted in a local SQLite database.
"""

import streamlit as st
import arxiv
from google import genai
from google.genai import types
import json
import os
import asyncio
import random
import sqlite3
import threading
import concurrent.futures
from dataclasses import dataclass, field
from typing import Optional, Callable
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd

# ──────────────────────────────────────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────────────────────────────────────

DB_PATH = Path(os.environ.get("PAPER_MATCHER_DB_PATH", str(Path(__file__).parent / "paper_matcher.db")))


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
    """)
    conn.commit()
    conn.close()


def save_evaluation(problem: str, model: str) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO evaluations (problem_text, model_name) VALUES (?, ?)",
        (problem, model),
    )
    conn.commit()
    eval_id = cur.lastrowid
    conn.close()
    return eval_id


def save_paper(eval_id: int, paper: "Paper", avg_score: float) -> int:
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
    return paper_id


def save_debate_round(paper_id: int, round_num: int, advocate: str, skeptic: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO debate_rounds (paper_id, round_num, advocate_arg, skeptic_arg) VALUES (?, ?, ?, ?)",
        (paper_id, round_num, advocate, skeptic),
    )
    conn.commit()
    conn.close()


def save_judge_verdict(paper_id: int, run: int, seed: int, score: int,
                       verdict: str, reasons: list[str], suggested: str):
    conn = get_db()
    conn.execute(
        """INSERT INTO judge_verdicts
           (paper_id, judge_run, seed, relevance_score, verdict, key_reasons, suggested_use)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (paper_id, run, seed, score, verdict, json.dumps(reasons), suggested),
    )
    conn.commit()
    conn.close()


def load_past_evaluations() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT e.id, e.problem_text, e.model_name, e.created_at,
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
    conn.close()
    return [dict(r) for r in rows]


def load_paper_debates(paper_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM debate_rounds WHERE paper_id = ? ORDER BY round_num",
        (paper_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_paper_verdicts(paper_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM judge_verdicts WHERE paper_id = ? ORDER BY judge_run",
        (paper_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_evaluation(eval_id: int):
    """Delete an evaluation and all its papers, debates, and verdicts."""
    conn = get_db()
    paper_ids = [r['id'] for r in conn.execute(
        "SELECT id FROM papers WHERE evaluation_id = ?", (eval_id,)
    ).fetchall()]
    for pid in paper_ids:
        conn.execute("DELETE FROM judge_verdicts WHERE paper_id = ?", (pid,))
        conn.execute("DELETE FROM debate_rounds WHERE paper_id = ?", (pid,))
    conn.execute("DELETE FROM papers WHERE evaluation_id = ?", (eval_id,))
    conn.execute("DELETE FROM evaluations WHERE id = ?", (eval_id,))
    conn.commit()
    conn.close()


def delete_papers(paper_ids: list[int]):
    """Delete specific papers and their debates/verdicts."""
    conn = get_db()
    for pid in paper_ids:
        conn.execute("DELETE FROM judge_verdicts WHERE paper_id = ?", (pid,))
        conn.execute("DELETE FROM debate_rounds WHERE paper_id = ?", (pid,))
        conn.execute("DELETE FROM papers WHERE id = ?", (pid,))
    conn.commit()
    conn.close()


def load_all_papers() -> list[dict]:
    """Load all papers across all evaluations, joined with evaluation info."""
    conn = get_db()
    rows = conn.execute(
        """SELECT p.*, e.problem_text, e.model_name, e.created_at AS eval_date
           FROM papers p
           JOIN evaluations e ON e.id = p.evaluation_id
           ORDER BY p.avg_score DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
) -> int:
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO recurring_schedules
           (label, problem_text, model_name, fetch_mode, max_papers, days_back,
            keyword_filter, min_score, max_concurrent, run_time)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            label,
            problem_text,
            model_name,
            fetch_mode,
            max_papers,
            days_back,
            keyword_filter,
            min_score,
            max_concurrent,
            run_time,
        ),
    )
    conn.commit()
    schedule_id = cur.lastrowid
    conn.close()
    return schedule_id


def load_recurring_schedules() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM recurring_schedules
           ORDER BY is_active DESC, run_time ASC, id DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_recurring_schedule_active(schedule_id: int, active: bool):
    conn = get_db()
    conn.execute(
        "UPDATE recurring_schedules SET is_active = ? WHERE id = ?",
        (1 if active else 0, schedule_id),
    )
    conn.commit()
    conn.close()


def delete_recurring_schedule(schedule_id: int):
    conn = get_db()
    conn.execute("DELETE FROM recurring_schedules WHERE id = ?", (schedule_id,))
    conn.commit()
    conn.close()


def update_schedule_last_run(
    schedule_id: int,
    run_date: str,
    status: str,
    message: str,
    eval_id: Optional[int],
):
    conn = get_db()
    conn.execute(
        """UPDATE recurring_schedules
           SET last_run_date = ?,
               last_run_at = ?,
               last_status = ?,
               last_message = ?,
               last_eval_id = ?
           WHERE id = ?""",
        (
            run_date,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            status,
            message,
            eval_id,
            schedule_id,
        ),
    )
    conn.commit()
    conn.close()


def load_due_recurring_schedules(now: Optional[datetime] = None) -> list[dict]:
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    now_hhmm = now.strftime("%H:%M")
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM recurring_schedules
           WHERE is_active = 1
             AND run_time <= ?
             AND (last_run_date IS NULL OR last_run_date <> ?)
           ORDER BY run_time ASC""",
        (now_hhmm, today),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def find_matching_past_papers(urls: list[str]) -> dict[str, list[dict]]:
    """Find papers in the DB whose URL matches any of the given URLs.

    Returns a dict mapping URL -> list of past paper records (with eval info).
    """
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
    """Fetch recent CS.CL papers from arXiv."""
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
                authors=", ".join(a.name for a in result.authors[:5])
                + ("..." if len(result.authors) > 5 else ""),
                abstract=result.summary.replace("\n", " "),
                url=result.entry_id,
                published=result.published.strftime("%Y-%m-%d"),
                categories=", ".join(result.categories),
            )
        )
        if not use_date_filter and len(papers) >= fetch_limit:
            break
    return papers


# ──────────────────────────────────────────────────────────────────────────────
# Multi-Agent Debate System  (with 5-Judge Panel)
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
    """Best-effort parse of judge JSON output."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    cleaned = cleaned.strip()
    return json.loads(cleaned)


class DebateEngine:
    """Runs a multi-agent debate (Advocate vs Skeptic -> 5-Judge Panel) using Gemini."""

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
                err_str = str(e).lower()
                is_rate_limit = ("resource" in err_str and "exhausted" in err_str) \
                    or "429" in err_str \
                    or "rate" in err_str \
                    or "quota" in err_str
                if attempt == max_retries - 1:
                    return f"[LLM Error: {e}]"
                if is_rate_limit:
                    # Longer backoff for rate limits: 4s, 8s, 16s, 32s, 64s
                    wait = (2 ** (attempt + 2)) + random.uniform(0, 2)
                else:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                await asyncio.sleep(wait)

    def _build_paper_context(self, paper: Paper, problem: str) -> str:
        return (
            f"## User's Research Problem\n{problem}\n\n"
            f"## Paper Under Review\n"
            f"**Title:** {paper.title}\n"
            f"**Authors:** {paper.authors}\n"
            f"**Published:** {paper.published}\n"
            f"**Categories:** {paper.categories}\n"
            f"**Abstract:** {paper.abstract}\n"
        )

    async def run_debate(self, paper: Paper, problem: str,
                         status_callback: Optional[Callable[[str], None]] = None) -> DebateResult:
        """Run debate rounds then 5 independent judge verdicts (async)."""
        def _status(msg: str):
            if status_callback:
                status_callback(msg)

        context = self._build_paper_context(paper, problem)
        result = DebateResult(paper=paper)
        debate_history = ""

        # ── Debate rounds (sequential: skeptic depends on advocate) ──
        for round_num in range(1, self.debate_rounds + 1):
            _status(f"🟢 Advocate Round {round_num}/{self.debate_rounds}")
            advocate_prompt = (
                f"{context}\n\n{debate_history}"
                f"Round {round_num}: Present your argument FOR this paper's relevance."
            )
            advocate_arg = await self._call_llm(ADVOCATE_SYSTEM, advocate_prompt)

            _status(f"🔴 Skeptic Round {round_num}/{self.debate_rounds}")
            skeptic_prompt = (
                f"{context}\n\n{debate_history}"
                f"Round {round_num} — Advocate said:\n{advocate_arg}\n\n"
                f"Now present your counter-argument AGAINST this paper's relevance."
            )
            skeptic_arg = await self._call_llm(SKEPTIC_SYSTEM, skeptic_prompt)

            result.rounds.append(DebateRound(
                advocate_argument=advocate_arg,
                skeptic_argument=skeptic_arg,
            ))
            debate_history += (
                f"\n--- Round {round_num} ---\n"
                f"Advocate: {advocate_arg}\n"
                f"Skeptic: {skeptic_arg}\n"
            )

        # ── 5-Judge Panel (all judges run concurrently) ──
        _status("⚖️ Judge Panel (5 judges deliberating)")
        judge_base_prompt = (
            f"{context}\n\n"
            f"## Full Debate Transcript\n{debate_history}\n\n"
            f"Now deliver your JSON verdict."
        )

        seeds = [random.randint(1, 999_999) for _ in range(NUM_JUDGES)]
        temperatures = [0.5, 0.7, 0.9, 1.1, 1.3]

        async def _run_judge(i: int) -> JudgeVerdict:
            seed = seeds[i]
            temp = temperatures[i]
            seeded_prompt = (
                f"{judge_base_prompt}\n"
                f"(Judge run {i + 1}/{NUM_JUDGES}, seed={seed}. "
                f"Evaluate independently.)"
            )
            judge_raw = await self._call_llm(JUDGE_SYSTEM, seeded_prompt, temperature=temp)
            jv = JudgeVerdict(run=i + 1, seed=seed)
            try:
                parsed = _parse_judge_json(judge_raw)
                jv.relevance_score = int(parsed.get("relevance_score", 0))
                jv.verdict = parsed.get("verdict", "")
                jv.key_reasons = parsed.get("key_reasons", [])
                jv.suggested_use = parsed.get("suggested_use", "")
            except (json.JSONDecodeError, ValueError):
                jv.verdict = judge_raw
                jv.relevance_score = 0
            return jv

        result.judge_verdicts = list(await asyncio.gather(
            *[_run_judge(i) for i in range(NUM_JUDGES)]
        ))

        # ── Aggregate scores ──
        valid_scores = [v.relevance_score for v in result.judge_verdicts if v.relevance_score > 0]
        result.avg_score = round(sum(valid_scores) / len(valid_scores), 1) if valid_scores else 0.0

        # Pick the verdict from the judge closest to the average
        if valid_scores:
            best = min(result.judge_verdicts,
                       key=lambda v: abs(v.relevance_score - result.avg_score))
            result.combined_verdict = best.verdict
            result.combined_reasons = best.key_reasons
            result.combined_suggested_use = best.suggested_use

        _status(f"✅ Done — Score: {result.avg_score}/10")
        return result
# ──────────────────────────────────────────────────────────────────────────────
# Rendering helpers
# ──────────────────────────────────────────────────────────────────────────────

def _judge_chips_html(verdicts: list[JudgeVerdict]) -> str:
    """Build coloured HTML chips for each judge score."""
    chips = []
    for v in verdicts:
        css = ("chip-high" if v.relevance_score >= 7
               else "chip-mid" if v.relevance_score >= 4
               else "chip-low")
        chips.append(
            f"<span class='judge-chip {css}'>J{v.run}: {v.relevance_score}</span>"
        )
    return " ".join(chips)


def _render_results_list(results: list[DebateResult], show_top_badge: bool = False,
                         key_prefix: str = "all", past_matches: dict | None = None):
    """Render a list of paper results with advocate/skeptic + judge panel."""
    if past_matches is None:
        past_matches = {}
    for idx, r in enumerate(results):
        score_class = (
            "score-high" if r.avg_score >= 7
            else "score-mid" if r.avg_score >= 4
            else "score-low"
        )
        badge = "⭐ " if show_top_badge else ""
        is_match = r.paper.url in past_matches
        card_class = "paper-card-match" if is_match else "paper-card"
        match_html = " <span class='match-badge'>🔄 Previously Evaluated</span>" if is_match else ""

        st.markdown(
            f"<div class='{card_class}'>"
            f"<span class='{score_class}'>{badge}{r.avg_score}/10</span>"
            f"&nbsp;&nbsp;<strong>{r.paper.title}</strong>{match_html}<br/>"
            f"<small>👤 {r.paper.authors} &nbsp;|&nbsp; 📅 {r.paper.published}</small>"
            f"</div>",
            unsafe_allow_html=True,
        )

        # Show past evaluation comparison if matched
        if is_match:
            past_records = past_matches[r.paper.url]
            with st.expander(f"🔄 Past Evaluation ({len(past_records)} match{'es' if len(past_records) > 1 else ''})"):
                for pr in past_records:
                    past_score = pr.get('avg_score', 0)
                    past_icon = "🟢" if past_score >= 7 else "🟡" if past_score >= 4 else "🔴"
                    st.markdown(
                        f"**Past research problem:**\n> {pr.get('problem_text', 'N/A')[:200]}"
                        f"{'...' if len(pr.get('problem_text', '')) > 200 else ''}"
                    )
                    st.markdown(
                        f"**Past score:** {past_icon} **{past_score}/10** &nbsp;|&nbsp; "
                        f"**Model:** {pr.get('model_name', 'N/A')} &nbsp;|&nbsp; "
                        f"**Date:** {(pr.get('eval_date') or '')[:10]}"
                    )
                    st.divider()

        # Abstract and paper link
        st.write(r.paper.abstract)
        if r.paper.url:
            st.link_button("📄 Open Paper", r.paper.url)

        # Judge panel scores
        if r.judge_verdicts:
            st.markdown(
                f"**Judge Panel:** {_judge_chips_html(r.judge_verdicts)}  "
                f"&rarr;  **Avg: {r.avg_score}**",
                unsafe_allow_html=True,
            )

        col_a, col_b = st.columns([3, 1])
        with col_a:
            st.markdown(f"**Verdict:** {r.combined_verdict}")
            if r.combined_reasons:
                st.markdown("**Key Reasons:** " + " • ".join(r.combined_reasons))
            if r.combined_suggested_use:
                st.markdown(f"**Suggested Use:** {r.combined_suggested_use}")

        # Full advocate/skeptic conversation sequence
        if r.rounds:
            for i, rnd in enumerate(r.rounds, 1):
                with st.chat_message("user", avatar="🟢"):
                    st.markdown(f"**Advocate** (Round {i})")
                    st.write(rnd.advocate_argument)
                with st.chat_message("user", avatar="🔴"):
                    st.markdown(f"**Skeptic** (Round {i})")
                    st.write(rnd.skeptic_argument)

        # All 5 judge verdicts
        if r.judge_verdicts:
            for jv in r.judge_verdicts:
                icon = "🟢" if jv.relevance_score >= 7 else "🟡" if jv.relevance_score >= 4 else "🔴"
                with st.chat_message("user", avatar="⚖️"):
                    st.markdown(f"**Judge {jv.run}** (seed {jv.seed}) — Score: **{icon} {jv.relevance_score}/10**")
                    if jv.verdict:
                        st.write(jv.verdict)
                    if jv.key_reasons:
                        st.caption("Reasons: " + " • ".join(jv.key_reasons))
                    if jv.suggested_use:
                        st.caption(f"Suggested use: {jv.suggested_use}")

            # Final decision
            st.markdown(
                f"### 🏆 Final Decision: **{r.avg_score}/10**\n\n"
                f"**Verdict:** {r.combined_verdict}\n\n"
                + (f"**Key Reasons:** {' • '.join(r.combined_reasons)}\n\n" if r.combined_reasons else "")
                + (f"**Suggested Use:** {r.combined_suggested_use}" if r.combined_suggested_use else "")
            )

        st.divider()


def _render_debate_detail(r: DebateResult):
    """Full debate transcript and all 5 judge verdicts."""
    st.markdown("### 🗣️ Debate Transcript")
    for i, rnd in enumerate(r.rounds, 1):
        with st.chat_message("user", avatar="🟢"):
            st.markdown(f"**Advocate** (Round {i})")
            st.write(rnd.advocate_argument)
        with st.chat_message("user", avatar="🔴"):
            st.markdown(f"**Skeptic** (Round {i})")
            st.write(rnd.skeptic_argument)

    st.markdown("### 🏛️ Judge Panel (5 independent verdicts)")
    for jv in r.judge_verdicts:
        icon = "🟢" if jv.relevance_score >= 7 else "🟡" if jv.relevance_score >= 4 else "🔴"
        with st.chat_message("user", avatar="⚖️"):
            st.markdown(
                f"**Judge {jv.run}** (seed {jv.seed}) — "
                f"Score: **{icon} {jv.relevance_score}/10**"
            )
            if jv.verdict:
                st.write(jv.verdict)
            if jv.key_reasons:
                st.caption("Reasons: " + " • ".join(jv.key_reasons))
            if jv.suggested_use:
                st.caption(f"Suggested use: {jv.suggested_use}")

    st.markdown(
        f"### 🏆 Final Decision: **{r.avg_score}/10**\n\n"
        f"**Verdict:** {r.combined_verdict}\n\n"
        + (f"**Key Reasons:** {' • '.join(r.combined_reasons)}\n\n" if r.combined_reasons else "")
        + (f"**Suggested Use:** {r.combined_suggested_use}" if r.combined_suggested_use else "")
    )


def _build_export(results: list[DebateResult]) -> list[dict]:
    export = []
    for r in results:
        export.append({
            "title": r.paper.title,
            "authors": r.paper.authors,
            "published": r.paper.published,
            "url": r.paper.url,
            "avg_score": r.avg_score,
            "verdict": r.combined_verdict,
            "key_reasons": r.combined_reasons,
            "suggested_use": r.combined_suggested_use,
            "judge_scores": [
                {
                    "run": jv.run, "seed": jv.seed,
                    "score": jv.relevance_score, "verdict": jv.verdict,
                    "reasons": jv.key_reasons, "suggested_use": jv.suggested_use,
                }
                for jv in r.judge_verdicts
            ],
            "debate_rounds": [
                {"round": i + 1, "advocate": rnd.advocate_argument,
                 "skeptic": rnd.skeptic_argument}
                for i, rnd in enumerate(r.rounds)
            ],
        })
    return export


def _resolve_api_key(user_key: str) -> str:
    """Prefer UI key; fallback to GEMINI_API_KEY for scheduled runs."""
    return (user_key or "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()


def _run_full_evaluation(
    *,
    api_key: str,
    problem_statement: str,
    model_name: str,
    max_papers: Optional[int],
    days_back: Optional[int],
    keyword_filter: str,
    max_concurrent: int,
    min_score: int,
    save_to_session: bool,
    status_prefix: str = "",
) -> tuple[Optional[int], list[DebateResult], Optional[str]]:
    """Run the same end-to-end evaluation pipeline used by New Evaluation."""
    prefix = f"{status_prefix} " if status_prefix else ""
    with st.status(f"{prefix}📡 Fetching papers from arXiv CS.CL...", expanded=True) as status:
        try:
            papers = fetch_arxiv_papers(
                max_results=max_papers,
                search_query=keyword_filter,
                days_back=days_back,
            )
            mode_label = (f"from the last **{days_back}** days"
                          if days_back else f"(latest **{max_papers}**)")
            st.write(f"✅ Fetched **{len(papers)}** papers {mode_label}")
            status.update(label=f"{prefix}Fetched {len(papers)} papers", state="complete")
        except Exception as e:
            return None, [], f"Failed to fetch papers: {e}"

    if not papers:
        return None, [], "No papers found. Try adjusting keyword filters."

    eval_id = save_evaluation(problem_statement, model_name)
    results: list[DebateResult] = []
    progress = st.progress(0, text=f"{prefix}Evaluating papers...")
    live_status = st.empty()

    paper_status: dict[str, str] = {}
    status_lock = threading.Lock()

    def _make_status_callback(title: str):
        short = title[:60] + ("..." if len(title) > 60 else "")

        def _cb(msg: str):
            with status_lock:
                paper_status[short] = msg

        return _cb

    def _refresh_status():
        with status_lock:
            snapshot = dict(paper_status)
        if snapshot:
            lines = [f"| {t} | {s} |" for t, s in snapshot.items()]
            md = "| Paper | Status |\n|---|---|\n" + "\n".join(lines)
            live_status.markdown(md)

    def evaluate_paper(paper: Paper) -> DebateResult:
        cb = _make_status_callback(paper.title)
        cb("⏳ Queued")
        try:
            thread_client = genai.Client(api_key=api_key)
            thread_engine = DebateEngine(client=thread_client, model_name=model_name)
            return asyncio.run(
                thread_engine.run_debate(paper, problem_statement, status_callback=cb))
        except Exception as e:
            cb(f"❌ Failed: {e}")
            return DebateResult(
                paper=paper,
                combined_verdict=f"Evaluation failed: {e}",
                avg_score=0.0,
            )

    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {executor.submit(evaluate_paper, p): p for p in papers}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            completed += 1
            progress.progress(
                completed / len(papers),
                text=f"{prefix}Evaluated {completed}/{len(papers)} papers...",
            )
            _refresh_status()

    progress.empty()
    live_status.empty()

    for r in results:
        paper_id = save_paper(eval_id, r.paper, r.avg_score)
        for idx, rnd in enumerate(r.rounds, 1):
            save_debate_round(paper_id, idx, rnd.advocate_argument, rnd.skeptic_argument)
        for jv in r.judge_verdicts:
            save_judge_verdict(
                paper_id,
                jv.run,
                jv.seed,
                jv.relevance_score,
                jv.verdict,
                jv.key_reasons,
                jv.suggested_use,
            )

    results.sort(key=lambda r: r.avg_score, reverse=True)
    if save_to_session:
        st.session_state["results"] = results
        st.session_state["min_score"] = min_score

    return eval_id, results, None


# ──────────────────────────────────────────────────────────────────────────────
# Streamlit UI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="arXiv CS.CL Paper Matcher",
        page_icon="📚",
        layout="wide",
    )
    init_db()

    # ── Custom CSS ──
    st.markdown("""
    <style>
    .score-high { color: #00c853; font-weight: bold; font-size: 1.4em; }
    .score-mid  { color: #ffab00; font-weight: bold; font-size: 1.4em; }
    .score-low  { color: #ff1744; font-weight: bold; font-size: 1.4em; }
    .judge-chip {
        display: inline-block; padding: 2px 10px; border-radius: 12px;
        margin: 2px 4px; font-weight: 600; font-size: 0.9em;
    }
    .chip-high { background: #00c85322; color: #00c853; }
    .chip-mid  { background: #ffab0022; color: #ffab00; }
    .chip-low  { background: #ff174422; color: #ff1744; }
    .paper-card {
        border: 1px solid #333; border-radius: 10px;
        padding: 1.2em; margin-bottom: 1em;
    }
    .paper-card-match {
        border: 2px solid #ff9800; border-radius: 10px;
        padding: 1.2em; margin-bottom: 1em;
        background: #ff980010;
    }
    .match-badge {
        display: inline-block; padding: 2px 10px; border-radius: 12px;
        background: #ff980033; color: #ff9800; font-weight: 600; font-size: 0.85em;
    }
    </style>
    """, unsafe_allow_html=True)

    st.title("📚 arXiv CS.CL Paper Matcher")
    st.caption("Multi-Agent Debate  •  5-Judge Panel  •  Gemini LLM  •  SQLite Persistence")

    # ── Sidebar ──
    with st.sidebar:
        st.header("⚙️ Settings")
        _env_key = os.environ.get("GEMINI_API_KEY", "")
        api_key = st.text_input(
            "Google Gemini API Key",
            type="password",
            value=_env_key,
            help=(
                "Pre-filled from GEMINI_API_KEY secret when running on Cloud Run. "
                "Override here if needed. "
                "Get one at https://aistudio.google.com/apikey"
            ),
        )
        model_name = st.selectbox("Gemini Model", [
            "gemini-3-pro-preview",
            "gemini-3-flash-preview",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.0-flash",
        ], index=0)
        fetch_mode = st.radio("Fetch mode", ["By number of papers", "By last N days"],
                              horizontal=True)
        if fetch_mode == "By number of papers":
            max_papers = st.slider("Papers to fetch", 5, 100, 50, step=5)
            days_back = None
        else:
            days_back = st.slider("Papers from last N days", 1, 90, 7, step=1,
                                  help="Fetch all CS.CL papers submitted within this many days")
            max_papers = None
        keyword_filter = st.text_input("Optional keyword filter",
                                       placeholder="e.g. summarization, translation",
                                       help="Extra arXiv search keywords (ANDed with cs.CL)")
        min_score = st.slider("Min relevance score to highlight", 1, 10, 6)
        max_concurrent = st.slider("Parallel evaluations", 1, 10, 3,
                                   help="Number of papers evaluated concurrently")
        st.divider()
        st.markdown(
            "**How it works**\n"
            "1. Fetches recent CS.CL papers from arXiv\n"
            "2. For each paper, **Advocate** argues FOR\n"
            "3. **Skeptic** argues AGAINST (2 rounds)\n"
            "4. **5 Judges** independently score 1-10\n"
            "5. Average score → final ranking\n"
            "6. Everything stored in SQLite DB"
        )

    effective_api_key = _resolve_api_key(api_key)
    due_schedules = load_due_recurring_schedules()
    if due_schedules:
        st.divider()
        st.subheader("⏰ Running Due Recurring Evaluations")
        if not effective_api_key:
            st.warning(
                "Recurring evaluations are due, but no API key is available. "
                "Set GEMINI_API_KEY in environment or enter a key in the sidebar."
            )
        else:
            today = datetime.now().strftime("%Y-%m-%d")
            for sch in due_schedules:
                label = sch.get("label") or f"Schedule #{sch['id']}"
                st.caption(f"Running {label} ({sch['run_time']})")
                eval_id, results, err = _run_full_evaluation(
                    api_key=effective_api_key,
                    problem_statement=sch["problem_text"],
                    model_name=sch["model_name"],
                    max_papers=sch.get("max_papers"),
                    days_back=sch.get("days_back"),
                    keyword_filter=sch.get("keyword_filter") or "",
                    max_concurrent=sch.get("max_concurrent") or 3,
                    min_score=sch.get("min_score") or 6,
                    save_to_session=False,
                    status_prefix=f"[Recurring #{sch['id']}]",
                )
                if err:
                    update_schedule_last_run(sch["id"], today, "failed", err, None)
                    st.error(f"{label}: {err}")
                else:
                    update_schedule_last_run(
                        sch["id"],
                        today,
                        "success",
                        f"Saved {len(results)} papers",
                        eval_id,
                    )
                    st.success(f"{label}: completed (eval #{eval_id}, {len(results)} papers)")
            st.divider()

    # ── Page tabs ──
    page_new, page_recurring, page_history = st.tabs([
        "🔬 New Evaluation",
        "⏰ Recurring Evaluations",
        "🗄️ Past Evaluations",
    ])

    # ══════════════════════════════════════════════════════════════════════════
    # NEW EVALUATION TAB
    # ══════════════════════════════════════════════════════════════════════════
    with page_new:
        problem_statement = st.text_area(
            "🔬 Describe your research problem",
            height=150,
            placeholder=(
                "Example: I am building a low-resource machine translation system "
                "for Indic languages and need methods that work well with limited "
                "parallel corpus data, including data augmentation and transfer "
                "learning techniques..."
            ),
        )

        recurring_time_new = st.time_input(
            "Recurring run time (server local time)",
            value=datetime.now().replace(second=0, microsecond=0).time(),
            key="new_eval_recurring_time",
        )

        col1, col2, col3, col4 = st.columns([1, 1, 1, 1])
        with col1:
            run_button = st.button("🚀 Fetch & Evaluate All", type="primary",
                                   use_container_width=True)
        with col2:
            fetch_only = st.button("📡 Fetch Papers Only",
                                   use_container_width=True)
        with col3:
            if st.button("🗑️ Clear Results", use_container_width=True):
                st.session_state.pop("results", None)
                st.session_state.pop("fetched_papers", None)
                st.session_state.pop("single_results", None)
                st.rerun()
        with col4:
            add_recurring_from_new = st.button(
                "⏰ Add to Recurring",
                use_container_width=True,
                help="Save this exact evaluation configuration as a daily recurring job.",
            )

        if add_recurring_from_new:
            if not problem_statement.strip():
                st.error("Please describe your research problem before adding a recurring schedule.")
            else:
                fetch_mode_key = "count" if fetch_mode == "By number of papers" else "days"
                schedule_label = f"New Eval Schedule {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                schedule_id = create_recurring_schedule(
                    label=schedule_label,
                    problem_text=problem_statement.strip(),
                    model_name=model_name,
                    fetch_mode=fetch_mode_key,
                    max_papers=max_papers,
                    days_back=days_back,
                    keyword_filter=keyword_filter.strip(),
                    min_score=min_score,
                    max_concurrent=max_concurrent,
                    run_time=recurring_time_new.strftime("%H:%M"),
                )
                st.success(
                    f"Added recurring schedule #{schedule_id} at "
                    f"{recurring_time_new.strftime('%H:%M')} (daily)."
                )

        # ── Execution ──
        if run_button:
            if not effective_api_key:
                st.error("Please enter your Gemini API key in the sidebar.")
            elif not problem_statement.strip():
                st.error("Please describe your research problem.")
            else:
                eval_id, results, err = _run_full_evaluation(
                    api_key=effective_api_key,
                    problem_statement=problem_statement.strip(),
                    model_name=model_name,
                    max_papers=max_papers,
                    days_back=days_back,
                    keyword_filter=keyword_filter,
                    max_concurrent=max_concurrent,
                    min_score=min_score,
                    save_to_session=True,
                )
                if err:
                    st.warning(err)
                else:
                    st.success(f"✅ Saved {len(results)} papers to database (eval #{eval_id})")

        # ── Fetch Only ──
        if fetch_only:
            if not effective_api_key:
                st.error("Please enter your Gemini API key in the sidebar.")
            elif not problem_statement.strip():
                st.error("Please describe your research problem.")
            else:
                with st.status("📡 Fetching papers from arXiv CS.CL...", expanded=True) as status:
                    try:
                        papers = fetch_arxiv_papers(
                            max_results=max_papers,
                            search_query=keyword_filter,
                            days_back=days_back,
                        )
                        mode_label = (f"from the last **{days_back}** days"
                                      if days_back else f"(latest **{max_papers}**)")
                        st.write(f"✅ Fetched **{len(papers)}** papers {mode_label}")
                        status.update(label=f"Fetched {len(papers)} papers", state="complete")
                    except Exception as e:
                        st.error(f"Failed to fetch papers: {e}")
                        papers = []
                if papers:
                    st.session_state["fetched_papers"] = papers
                    st.session_state["single_results"] = {}
                    st.session_state["problem_for_fetch"] = problem_statement
                else:
                    st.warning("No papers found. Try adjusting keyword filters.")

        # ── Fetched Papers (per-paper evaluate) ──
        if "fetched_papers" in st.session_state:
            fetched = st.session_state["fetched_papers"]
            single_results: dict[str, DebateResult] = st.session_state.get("single_results", {})
            stored_problem = st.session_state.get("problem_for_fetch", problem_statement)

            # Detect papers that already exist in past evaluations
            past_matches = find_matching_past_papers([p.url for p in fetched])
            match_count = sum(1 for p in fetched if p.url in past_matches)

            st.divider()
            st.subheader(f"📄 Fetched Papers ({len(fetched)})")
            if match_count:
                st.info(
                    f"🔄 **{match_count}** paper(s) found in past evaluations "
                    f"(highlighted in orange). You can remove them before evaluating."
                )
            st.caption("Click **Evaluate** on any paper to run the multi-agent debate.")

            for idx, paper in enumerate(fetched):
                paper_key = f"{paper.title}|{paper.url}"
                already_evaluated = paper_key in single_results
                is_match = paper.url in past_matches

                with st.container():
                    # Title row with match badge
                    card_class = "paper-card-match" if is_match else "paper-card"
                    match_html = " <span class='match-badge'>🔄 Previously Evaluated</span>" if is_match else ""
                    st.markdown(
                        f"<div class='{card_class}'>"
                        f"<strong>{paper.title}</strong>{match_html}<br/>"
                        f"<small>👤 {paper.authors} &nbsp;|&nbsp; 📅 {paper.published}</small>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                    # Show past evaluation info if matched
                    if is_match:
                        past_records = past_matches[paper.url]
                        with st.expander(f"🔄 Past Evaluation ({len(past_records)} match{'es' if len(past_records) > 1 else ''})", expanded=False):
                            st.markdown(f"**Current research problem:**\n> {stored_problem[:200]}{'...' if len(stored_problem) > 200 else ''}")
                            st.divider()
                            for pr in past_records:
                                past_score = pr.get('avg_score', 0)
                                past_icon = "🟢" if past_score >= 7 else "🟡" if past_score >= 4 else "🔴"
                                st.markdown(
                                    f"**Past research problem:**\n> {pr.get('problem_text', 'N/A')[:200]}"
                                    f"{'...' if len(pr.get('problem_text', '')) > 200 else ''}"
                                )
                                st.markdown(
                                    f"**Past score:** {past_icon} **{past_score}/10** &nbsp;|&nbsp; "
                                    f"**Model:** {pr.get('model_name', 'N/A')} &nbsp;|&nbsp; "
                                    f"**Date:** {(pr.get('eval_date') or '')[:10]}"
                                )
                                st.divider()
                        # Remove button
                        if st.button("🗑️ Remove from list", key=f"remove_{idx}",
                                     use_container_width=False):
                            st.session_state["fetched_papers"] = [
                                p for i, p in enumerate(fetched) if i != idx
                            ]
                            st.rerun()

                    col_info, col_btn = st.columns([4, 1])
                    with col_info:
                        pass  # Title already shown above
                    with col_btn:
                        if already_evaluated:
                            r = single_results[paper_key]
                            score_color = "🟢" if r.avg_score >= 7 else "🟡" if r.avg_score >= 4 else "🔴"
                            st.markdown(f"{score_color} **{r.avg_score}/10**")
                        else:
                            if st.button("🔬 Evaluate", key=f"eval_{idx}",
                                         use_container_width=True):
                                if not effective_api_key:
                                    st.error("Please enter your Gemini API key.")
                                else:
                                    single_status = st.empty()
                                    def _single_cb(msg: str):
                                        single_status.markdown(f"**Status:** {msg}")
                                    try:
                                        eval_client = genai.Client(api_key=effective_api_key)
                                        eval_engine = DebateEngine(
                                            client=eval_client, model_name=model_name)
                                        result = asyncio.run(
                                            eval_engine.run_debate(
                                                paper, stored_problem,
                                                status_callback=_single_cb))
                                    except Exception as e:
                                        result = DebateResult(
                                            paper=paper,
                                            combined_verdict=f"Evaluation failed: {e}",
                                            avg_score=0.0,
                                        )
                                    single_status.empty()
                                    # Save to DB
                                    eval_id = save_evaluation(stored_problem, model_name)
                                    paper_id = save_paper(eval_id, paper, result.avg_score)
                                    for rd_idx, rnd in enumerate(result.rounds, 1):
                                        save_debate_round(paper_id, rd_idx,
                                                          rnd.advocate_argument,
                                                          rnd.skeptic_argument)
                                    for jv in result.judge_verdicts:
                                        save_judge_verdict(paper_id, jv.run, jv.seed,
                                                           jv.relevance_score, jv.verdict,
                                                           jv.key_reasons, jv.suggested_use)
                                    single_results[paper_key] = result
                                    st.session_state["single_results"] = single_results
                                    st.rerun()

                    # Abstract and paper link
                    with st.expander("Show Abstract"):
                        st.write(paper.abstract)
                    if paper.url:
                        st.link_button("📄 Open Paper", paper.url)

                    # Show result inline if evaluated
                    if already_evaluated:
                        r = single_results[paper_key]
                        with st.expander(f"📊 Debate Result — {r.avg_score}/10"):
                            _render_debate_detail(r)

                    st.divider()

        # ── Display Results ──
        if "results" in st.session_state:
            results = st.session_state["results"]
            ms = st.session_state.get("min_score", min_score)

            st.divider()
            high = [r for r in results if r.avg_score >= 7]
            mid = [r for r in results if 4 <= r.avg_score < 7]
            low = [r for r in results if r.avg_score < 4]

            # Detect matches with past evaluations
            result_urls = [r.paper.url for r in results]
            results_past_matches = find_matching_past_papers(result_urls)
            match_count = sum(1 for r in results if r.paper.url in results_past_matches)

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total Papers", len(results))
            m2.metric("🟢 Highly Relevant (7-10)", len(high))
            m3.metric("🟡 Moderate (4-6)", len(mid))
            m4.metric("🔴 Low Relevance (1-3)", len(low))

            if match_count:
                st.info(f"🔄 **{match_count}** paper(s) were previously evaluated (highlighted in orange).")

            tab_all, tab_top, tab_debate = st.tabs([
                "📋 All Results", "⭐ Top Matches", "🗣️ Debate Details"
            ])

            with tab_all:
                _render_results_list(results, key_prefix="all", past_matches=results_past_matches)

            with tab_top:
                top_results = [r for r in results if r.avg_score >= ms]
                if not top_results:
                    st.info(f"No papers scored ≥ {ms}. Try lowering the threshold.")
                _render_results_list(top_results, show_top_badge=True, key_prefix="top",
                                     past_matches=results_past_matches)

            with tab_debate:
                st.info("Expand any paper to see the full debate transcript and all 5 judge scores.")
                for r in results:
                    icon = '🟢' if r.avg_score >= 7 else '🟡' if r.avg_score >= 4 else '🔴'
                    with st.expander(f"{icon} [{r.avg_score}/10] {r.paper.title}"):
                        _render_debate_detail(r)

            # Export
            st.divider()
            export_data = _build_export(results)
            st.download_button(
                "📥 Download Results (JSON)",
                data=json.dumps(export_data, indent=2),
                file_name="arxiv_paper_matches.json",
                mime="application/json",
            )

    # ══════════════════════════════════════════════════════════════════════════
    # RECURRING EVALUATIONS TAB
    # ══════════════════════════════════════════════════════════════════════════
    with page_recurring:
        st.subheader("⏰ Recurring Evaluations")
        st.caption(
            "Runs once per day at the configured time (server local time). "
            "Due jobs run when this app is active."
        )

        st.markdown("#### ➕ Create Recurring Schedule")
        with st.form("create_recurring_form"):
            schedule_label = st.text_input(
                "Schedule label",
                value=f"Daily schedule {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            )
            recurring_problem = st.text_area(
                "Research problem",
                value=st.session_state.get("problem_for_fetch", ""),
                height=120,
                placeholder="Describe the research problem for recurring evaluation.",
            )
            rec_model = st.selectbox(
                "Gemini model",
                [
                    "gemini-3-pro-preview",
                    "gemini-3-flash-preview",
                    "gemini-2.5-pro",
                    "gemini-2.5-flash",
                    "gemini-2.0-flash",
                ],
                index=0,
                key="rec_model_name",
            )
            rec_fetch_mode = st.radio(
                "Fetch mode",
                ["By number of papers", "By last N days"],
                horizontal=True,
                key="rec_fetch_mode",
            )
            if rec_fetch_mode == "By number of papers":
                rec_max_papers = st.slider("Papers to fetch", 5, 100, 50, step=5, key="rec_max_papers")
                rec_days_back = None
            else:
                rec_days_back = st.slider(
                    "Papers from last N days",
                    1,
                    90,
                    7,
                    step=1,
                    key="rec_days_back",
                )
                rec_max_papers = None
            rec_keyword = st.text_input(
                "Optional keyword filter",
                placeholder="e.g. summarization, translation",
                key="rec_keyword",
            )
            rec_min_score = st.slider("Min relevance score", 1, 10, 6, key="rec_min_score")
            rec_max_concurrent = st.slider("Parallel evaluations", 1, 10, 3, key="rec_max_concurrent")
            rec_run_time = st.time_input(
                "Daily run time (server local time)",
                value=datetime.now().replace(second=0, microsecond=0).time(),
                key="rec_run_time",
            )
            create_schedule = st.form_submit_button("➕ Create Schedule", type="primary")

        if create_schedule:
            if not recurring_problem.strip():
                st.error("Please provide a research problem for the recurring schedule.")
            else:
                rec_fetch_mode_key = "count" if rec_fetch_mode == "By number of papers" else "days"
                schedule_id = create_recurring_schedule(
                    label=schedule_label.strip() or f"Schedule {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    problem_text=recurring_problem.strip(),
                    model_name=rec_model,
                    fetch_mode=rec_fetch_mode_key,
                    max_papers=rec_max_papers,
                    days_back=rec_days_back,
                    keyword_filter=rec_keyword.strip(),
                    min_score=rec_min_score,
                    max_concurrent=rec_max_concurrent,
                    run_time=rec_run_time.strftime("%H:%M"),
                )
                st.success(f"Created recurring schedule #{schedule_id}.")
                st.rerun()

        st.divider()
        st.markdown("#### 📋 Existing Schedules")
        schedules = load_recurring_schedules()
        if not schedules:
            st.info("No recurring schedules yet. Create one above or from New Evaluation.")
        else:
            for sch in schedules:
                active_icon = "🟢" if sch.get("is_active") else "⏸️"
                label = sch.get("label") or f"Schedule #{sch['id']}"
                freq = (
                    f"Latest {sch.get('max_papers')} papers"
                    if sch.get("fetch_mode") == "count"
                    else f"Last {sch.get('days_back')} day(s)"
                )
                with st.expander(
                    f"{active_icon} #{sch['id']} • {label} • {sch['run_time']} daily",
                    expanded=False,
                ):
                    st.markdown(f"**Model:** {sch.get('model_name')}")
                    st.markdown(f"**Fetch:** {freq}")
                    st.markdown(f"**Keyword filter:** {sch.get('keyword_filter') or 'None'}")
                    st.markdown(f"**Parallel evaluations:** {sch.get('max_concurrent') or 3}")
                    st.markdown(f"**Problem:**\n> {sch.get('problem_text', '')}")
                    st.caption(
                        f"Last run: {sch.get('last_run_at') or 'Never'} | "
                        f"Status: {sch.get('last_status') or 'N/A'} | "
                        f"Message: {sch.get('last_message') or 'N/A'}"
                    )

                    a1, a2, a3 = st.columns([1, 1, 1])
                    with a1:
                        toggle_label = "⏸️ Pause" if sch.get("is_active") else "▶️ Activate"
                        if st.button(toggle_label, key=f"toggle_rec_{sch['id']}", use_container_width=True):
                            set_recurring_schedule_active(sch["id"], not bool(sch.get("is_active")))
                            st.rerun()
                    with a2:
                        if st.button("▶️ Run Now", key=f"run_rec_{sch['id']}", use_container_width=True):
                            if not effective_api_key:
                                st.error("Enter API key in sidebar or set GEMINI_API_KEY.")
                            else:
                                today = datetime.now().strftime("%Y-%m-%d")
                                eval_id, results, err = _run_full_evaluation(
                                    api_key=effective_api_key,
                                    problem_statement=sch["problem_text"],
                                    model_name=sch["model_name"],
                                    max_papers=sch.get("max_papers"),
                                    days_back=sch.get("days_back"),
                                    keyword_filter=sch.get("keyword_filter") or "",
                                    max_concurrent=sch.get("max_concurrent") or 3,
                                    min_score=sch.get("min_score") or 6,
                                    save_to_session=False,
                                    status_prefix=f"[Recurring #{sch['id']}]",
                                )
                                if err:
                                    update_schedule_last_run(sch["id"], today, "failed", err, None)
                                    st.error(err)
                                else:
                                    update_schedule_last_run(
                                        sch["id"],
                                        today,
                                        "success",
                                        f"Saved {len(results)} papers",
                                        eval_id,
                                    )
                                    st.success(f"Run complete. Created evaluation #{eval_id}.")
                                    st.rerun()
                    with a3:
                        if st.button("🗑️ Delete", key=f"del_rec_{sch['id']}", use_container_width=True):
                            delete_recurring_schedule(sch["id"])
                            st.success(f"Deleted schedule #{sch['id']}.")
                            st.rerun()

    # ══════════════════════════════════════════════════════════════════════════
    # PAST EVALUATIONS TAB
    # ══════════════════════════════════════════════════════════════════════════
    with page_history:
        st.subheader("🗄️ Past Evaluations")

        all_papers = load_all_papers()
        if not all_papers:
            st.info("No past evaluations yet. Run your first evaluation above!")
        else:
            # ── Filters ──
            st.markdown("#### 🔍 Filters")
            f1, f2, f3, f4 = st.columns([2, 1, 1, 1])
            with f1:
                keyword_search = st.text_input(
                    "Search title / abstract",
                    key="hist_keyword",
                    placeholder="e.g. translation, summarization",
                )
            with f2:
                score_range = st.slider(
                    "Score range", 0.0, 10.0, (0.0, 10.0),
                    step=0.5, key="hist_score_range",
                )
            with f3:
                eval_dates = sorted(set(
                    (p.get('eval_date') or 'Unknown')[:10] for p in all_papers
                ), reverse=True)
                date_filter = st.multiselect(
                    "Evaluation date",
                    options=eval_dates,
                    default=[],
                    key="hist_date_filter",
                    placeholder="All dates",
                )
            with f4:
                sort_by = st.selectbox(
                    "Sort by",
                    ["Score (high → low)", "Score (low → high)",
                     "Title (A-Z)", "Date (newest)"],
                    key="hist_sort",
                )

            # Apply filters
            filtered = all_papers
            if keyword_search.strip():
                kw = keyword_search.strip().lower()
                filtered = [
                    p for p in filtered
                    if kw in (p.get('title') or '').lower()
                    or kw in (p.get('abstract') or '').lower()
                ]
            filtered = [
                p for p in filtered
                if score_range[0] <= (p['avg_score'] or 0) <= score_range[1]
            ]
            if date_filter:
                filtered = [
                    p for p in filtered
                    if (p.get('eval_date') or 'Unknown')[:10] in date_filter
                ]
            if sort_by == "Score (high → low)":
                filtered.sort(key=lambda p: p['avg_score'] or 0, reverse=True)
            elif sort_by == "Score (low → high)":
                filtered.sort(key=lambda p: p['avg_score'] or 0)
            elif sort_by == "Title (A-Z)":
                filtered.sort(key=lambda p: (p.get('title') or '').lower())
            else:
                filtered.sort(key=lambda p: p.get('eval_date') or '', reverse=True)

            # ── Metrics ──
            high = [p for p in filtered if (p['avg_score'] or 0) >= 7]
            mid = [p for p in filtered if 4 <= (p['avg_score'] or 0) < 7]
            low = [p for p in filtered if (p['avg_score'] or 0) < 4]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Showing", len(filtered))
            m2.metric("🟢 High (7-10)", len(high))
            m3.metric("🟡 Moderate (4-6)", len(mid))
            m4.metric("🔴 Low (1-3)", len(low))

            # ── Table view ──
            st.divider()
            table_data = []
            for p in filtered:
                score = p['avg_score'] or 0
                relevance = "🟢 High" if score >= 7 else "🟡 Moderate" if score >= 4 else "🔴 Low"
                table_data.append({
                    "Score": score,
                    "Relevance": relevance,
                    "Title": p.get('title', ''),
                    "Authors": p.get('authors', ''),
                    "Published": p.get('published', ''),
                    "Eval Date": (p.get('eval_date') or '')[:10],
                    "Model": p.get('model_name', ''),
                    "_id": p['id'],
                })

            if table_data:
                df = pd.DataFrame(table_data)
                display_df = df.drop(columns=["_id"])

                # Interactive table with selection
                event = st.dataframe(
                    display_df,
                    use_container_width=True,
                    hide_index=True,
                    on_select="rerun",
                    selection_mode="multi-row",
                    key="hist_table",
                )

                # ── Delete selected rows ──
                selected_rows = event.selection.rows if event.selection else []
                if selected_rows:
                    selected_paper_ids = [table_data[i]["_id"] for i in selected_rows]
                    st.caption(f"{len(selected_rows)} paper(s) selected")
                    if st.button(f"🗑️ Delete {len(selected_rows)} selected paper(s)",
                                 key="del_selected", type="secondary"):
                        st.session_state["confirm_del_papers"] = selected_paper_ids

                if st.session_state.get("confirm_del_papers"):
                    ids_to_del = st.session_state["confirm_del_papers"]
                    st.warning(f"Delete {len(ids_to_del)} paper(s) and all their data?")
                    c_yes, c_no = st.columns(2)
                    with c_yes:
                        if st.button("✅ Yes, delete", key="confirm_yes_papers",
                                     type="primary", use_container_width=True):
                            delete_papers(ids_to_del)
                            st.session_state.pop("confirm_del_papers", None)
                            st.success("Papers deleted.")
                            st.rerun()
                    with c_no:
                        if st.button("❌ Cancel", key="confirm_no_papers",
                                     use_container_width=True):
                            st.session_state.pop("confirm_del_papers", None)
                            st.rerun()

                # ── Detail view for selected paper ──
                st.divider()
                if selected_rows:
                    for row_idx in selected_rows:
                        p = table_data[row_idx]
                        pid = p["_id"]
                        score = p["Score"]
                        paper_row = next(pp for pp in filtered if pp['id'] == pid)

                        score_class = (
                            "score-high" if score >= 7
                            else "score-mid" if score >= 4
                            else "score-low"
                        )
                        st.markdown(
                            f"<div class='paper-card'>"
                            f"<span class='{score_class}'>{score}/10</span>"
                            f"&nbsp;&nbsp;<strong>{p['Title']}</strong><br/>"
                            f"<small>👤 {p['Authors']} &nbsp;|&nbsp; 📅 {p['Published']}"
                            f" &nbsp;|&nbsp; Eval: {p['Eval Date']}"
                            f" &nbsp;|&nbsp; Model: {p['Model']}</small>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                        # Abstract and paper link
                        st.write(paper_row.get('abstract', ''))
                        if paper_row.get('url'):
                            st.link_button("📄 Open Paper", paper_row['url'])

                        # Research problem
                        with st.expander("📝 Research Problem"):
                            st.write(paper_row.get('problem_text', ''))

                        # Judge panel chips
                        verdicts = load_paper_verdicts(pid)
                        if verdicts:
                            chips = []
                            for v in verdicts:
                                css = ("chip-high" if v['relevance_score'] >= 7
                                       else "chip-mid" if v['relevance_score'] >= 4
                                       else "chip-low")
                                chips.append(
                                    f"<span class='judge-chip {css}'>"
                                    f"J{v['judge_run']}: {v['relevance_score']}</span>"
                                )
                            st.markdown(
                                f"**Judge Panel:** {' '.join(chips)}  "
                                f"&rarr;  **Avg: {score}**",
                                unsafe_allow_html=True,
                            )

                        # Combined verdict
                        if verdicts:
                            # Pick the verdict closest to the average score
                            best_v = min(verdicts, key=lambda v: abs(v['relevance_score'] - score))
                            best_reasons = json.loads(best_v['key_reasons']) if best_v['key_reasons'] else []
                            col_a, col_b = st.columns([3, 1])
                            with col_a:
                                st.markdown(f"**Verdict:** {best_v['verdict']}")
                                if best_reasons:
                                    st.markdown("**Key Reasons:** " + " • ".join(best_reasons))
                                if best_v['suggested_use']:
                                    st.markdown(f"**Suggested Use:** {best_v['suggested_use']}")

                        # Advocate/Skeptic debate rounds
                        rounds_db = load_paper_debates(pid)
                        if rounds_db:
                            for rnd in rounds_db:
                                with st.chat_message("user", avatar="🟢"):
                                    st.markdown(f"**Advocate** (Round {rnd['round_num']})")
                                    st.write(rnd['advocate_arg'])
                                with st.chat_message("user", avatar="🔴"):
                                    st.markdown(f"**Skeptic** (Round {rnd['round_num']})")
                                    st.write(rnd['skeptic_arg'])

                        # All 5 judge verdicts
                        if verdicts:
                            for v in verdicts:
                                v_icon = "🟢" if v['relevance_score'] >= 7 else "🟡" if v['relevance_score'] >= 4 else "🔴"
                                reasons = json.loads(v['key_reasons']) if v['key_reasons'] else []
                                with st.chat_message("user", avatar="⚖️"):
                                    st.markdown(
                                        f"**Judge {v['judge_run']}** (seed {v['seed']}) — "
                                        f"Score: **{v_icon} {v['relevance_score']}/10**"
                                    )
                                    if v['verdict']:
                                        st.write(v['verdict'])
                                    if reasons:
                                        st.caption("Reasons: " + " • ".join(reasons))
                                    if v['suggested_use']:
                                        st.caption(f"Suggested use: {v['suggested_use']}")

                            # Final decision
                            st.markdown(
                                f"### 🏆 Final Decision: **{score}/10**\n\n"
                                f"**Verdict:** {best_v['verdict']}\n\n"
                                + (f"**Key Reasons:** {' • '.join(best_reasons)}\n\n" if best_reasons else "")
                                + (f"**Suggested Use:** {best_v['suggested_use']}" if best_v['suggested_use'] else "")
                            )

                        st.divider()
                else:
                    st.info("Select a row in the table above to view its full debate and verdicts.")

            # ── Delete entire evaluation ──
            st.divider()
            evals = load_past_evaluations()
            if evals:
                st.markdown("#### 🗑️ Manage Evaluations")
                eval_options = {
                    ev['id']: (
                        f"#{ev['id']}  •  📅 {ev['created_at']}  •  "
                        f"{ev['paper_count']} papers  •  {ev['model_name']}"
                    )
                    for ev in evals
                }
                del_eval_id = st.selectbox(
                    "Select evaluation to delete",
                    options=list(eval_options.keys()),
                    format_func=lambda x: eval_options[x],
                    key="del_eval_select",
                )
                if st.button("🗑️ Delete Entire Evaluation", key="del_eval",
                             type="secondary"):
                    st.session_state["confirm_del_eval"] = del_eval_id

                if st.session_state.get("confirm_del_eval"):
                    eid = st.session_state["confirm_del_eval"]
                    st.warning(f"Delete evaluation #{eid} and all its papers?")
                    c_yes, c_no = st.columns(2)
                    with c_yes:
                        if st.button("✅ Yes, delete evaluation", key="confirm_yes_eval",
                                     type="primary", use_container_width=True):
                            delete_evaluation(eid)
                            st.session_state.pop("confirm_del_eval", None)
                            st.success("Evaluation deleted.")
                            st.rerun()
                    with c_no:
                        if st.button("❌ Cancel", key="confirm_no_eval",
                                     use_container_width=True):
                            st.session_state.pop("confirm_del_eval", None)
                            st.rerun()


if __name__ == "__main__":
    main()
