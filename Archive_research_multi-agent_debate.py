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
import asyncio
import random
import sqlite3
import concurrent.futures
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timedelta
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent / "paper_matcher.db"


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

    async def run_debate(self, paper: Paper, problem: str) -> DebateResult:
        """Run debate rounds then 5 independent judge verdicts (async)."""
        context = self._build_paper_context(paper, problem)
        result = DebateResult(paper=paper)
        debate_history = ""

        # ── Debate rounds (sequential: skeptic depends on advocate) ──
        for round_num in range(1, self.debate_rounds + 1):
            advocate_prompt = (
                f"{context}\n\n{debate_history}"
                f"Round {round_num}: Present your argument FOR this paper's relevance."
            )
            advocate_arg = await self._call_llm(ADVOCATE_SYSTEM, advocate_prompt)

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


def _render_results_list(results: list[DebateResult], show_top_badge: bool = False):
    """Render a list of paper results with advocate/skeptic + judge panel."""
    for r in results:
        score_class = (
            "score-high" if r.avg_score >= 7
            else "score-mid" if r.avg_score >= 4
            else "score-low"
        )
        badge = "⭐ " if show_top_badge else ""

        st.markdown(
            f"<div class='paper-card'>"
            f"<span class='{score_class}'>{badge}{r.avg_score}/10</span>"
            f"&nbsp;&nbsp;<strong>{r.paper.title}</strong><br/>"
            f"<small>👤 {r.paper.authors} &nbsp;|&nbsp; 📅 {r.paper.published}</small>"
            f"</div>",
            unsafe_allow_html=True,
        )

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
        with col_b:
            if r.paper.url:
                st.link_button("📄 Open Paper", r.paper.url, use_container_width=True)

        # Advocate & Skeptic conversation (last round)
        if r.rounds:
            last_round = r.rounds[-1]
            with st.chat_message("user", avatar="🟢"):
                st.markdown("**Advocate (final round)**")
                st.write(last_round.advocate_argument)
            with st.chat_message("user", avatar="🔴"):
                st.markdown("**Skeptic (final round)**")
                st.write(last_round.skeptic_argument)

        with st.expander("Show Abstract"):
            st.write(r.paper.abstract)
        st.divider()


def _render_debate_detail(r: DebateResult):
    """Full debate transcript and all 5 judge verdicts."""
    for i, rnd in enumerate(r.rounds, 1):
        st.markdown(f"### Round {i}")
        with st.chat_message("user", avatar="🟢"):
            st.markdown("**Advocate**")
            st.write(rnd.advocate_argument)
        with st.chat_message("user", avatar="🔴"):
            st.markdown("**Skeptic**")
            st.write(rnd.skeptic_argument)

    st.markdown("### 🏛️ Judge Panel (5 independent verdicts)")
    for jv in r.judge_verdicts:
        css = ("🟢" if jv.relevance_score >= 7
               else "🟡" if jv.relevance_score >= 4
               else "🔴")
        with st.container():
            st.markdown(
                f"{css} **Judge {jv.run}** (seed {jv.seed}) — "
                f"Score: **{jv.relevance_score}/10**"
            )
            st.caption(jv.verdict)
            if jv.key_reasons:
                st.caption("Reasons: " + " • ".join(jv.key_reasons))
            if jv.suggested_use:
                st.caption(f"Suggested use: {jv.suggested_use}")

    st.markdown(f"### 📊 Average Score: **{r.avg_score}/10**")


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
    </style>
    """, unsafe_allow_html=True)

    st.title("📚 arXiv CS.CL Paper Matcher")
    st.caption("Multi-Agent Debate  •  5-Judge Panel  •  Gemini LLM  •  SQLite Persistence")

    # ── Sidebar ──
    with st.sidebar:
        st.header("⚙️ Settings")
        api_key = st.text_input("Google Gemini API Key", type="password",
                                help="Get one at https://aistudio.google.com/apikey")
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

    # ── Page tabs ──
    page_new, page_history = st.tabs(["🔬 New Evaluation", "🗄️ Past Evaluations"])

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

        col1, col2 = st.columns([1, 1])
        with col1:
            run_button = st.button("🚀 Fetch & Evaluate Papers", type="primary",
                                   use_container_width=True)
        with col2:
            if st.button("🗑️ Clear Results", use_container_width=True):
                st.session_state.pop("results", None)
                st.rerun()

        # ── Execution ──
        if run_button:
            if not api_key:
                st.error("Please enter your Gemini API key in the sidebar.")
            elif not problem_statement.strip():
                st.error("Please describe your research problem.")
            else:
                client = genai.Client(api_key=api_key)
                engine = DebateEngine(client=client, model_name=model_name)

                # Step 1 — Fetch papers
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

                if not papers:
                    st.warning("No papers found. Try adjusting keyword filters.")
                else:
                    # Create evaluation record
                    eval_id = save_evaluation(problem_statement, model_name)

                    # Step 2 — Multi-agent debate (async judges, threaded papers)
                    results: list[DebateResult] = []
                    progress = st.progress(0, text="Evaluating papers...")
                    status_text = st.empty()

                    def evaluate_paper(paper: Paper) -> DebateResult:
                        """Each thread gets its own event loop for async judge calls."""
                        try:
                            return asyncio.run(engine.run_debate(paper, problem_statement))
                        except Exception as e:
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
                                text=f"Evaluated {completed}/{len(papers)} papers...")
                            status_text.text(
                                f"Latest: {futures[future].title[:80]}...")

                    progress.empty()
                    status_text.empty()

                    # Step 3 — Save to DB
                    for r in results:
                        paper_id = save_paper(eval_id, r.paper, r.avg_score)
                        for idx, rnd in enumerate(r.rounds, 1):
                            save_debate_round(paper_id, idx,
                                              rnd.advocate_argument, rnd.skeptic_argument)
                        for jv in r.judge_verdicts:
                            save_judge_verdict(paper_id, jv.run, jv.seed,
                                               jv.relevance_score, jv.verdict,
                                               jv.key_reasons, jv.suggested_use)

                    results.sort(key=lambda r: r.avg_score, reverse=True)
                    st.session_state["results"] = results
                    st.session_state["min_score"] = min_score
                    st.success(f"✅ Saved {len(results)} papers to database (eval #{eval_id})")

        # ── Display Results ──
        if "results" in st.session_state:
            results = st.session_state["results"]
            ms = st.session_state.get("min_score", min_score)

            st.divider()
            high = [r for r in results if r.avg_score >= 7]
            mid = [r for r in results if 4 <= r.avg_score < 7]
            low = [r for r in results if r.avg_score < 4]

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total Papers", len(results))
            m2.metric("🟢 Highly Relevant (7-10)", len(high))
            m3.metric("🟡 Moderate (4-6)", len(mid))
            m4.metric("🔴 Low Relevance (1-3)", len(low))

            tab_all, tab_top, tab_debate = st.tabs([
                "📋 All Results", "⭐ Top Matches", "🗣️ Debate Details"
            ])

            with tab_all:
                _render_results_list(results)

            with tab_top:
                top_results = [r for r in results if r.avg_score >= ms]
                if not top_results:
                    st.info(f"No papers scored ≥ {ms}. Try lowering the threshold.")
                _render_results_list(top_results, show_top_badge=True)

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
    # PAST EVALUATIONS TAB
    # ══════════════════════════════════════════════════════════════════════════
    with page_history:
        st.subheader("🗄️ Past Evaluations (SQLite)")
        if st.button("🔄 Refresh", key="refresh_db"):
            pass  # page reruns on button click
        evals = load_past_evaluations()
        if not evals:
            st.info("No past evaluations yet. Run your first evaluation above!")
        else:
            for ev in evals:
                avg_display = ev['overall_avg'] if ev['overall_avg'] is not None else 0
                with st.expander(
                    f"📅 {ev['created_at']}  •  {ev['paper_count']} papers  •  "
                    f"avg {avg_display}/10  •  {ev['model_name']}"
                ):
                    st.markdown(f"**Problem:** {ev['problem_text'][:500]}")
                    papers_db = load_evaluation_papers(ev['id'])
                    for p in papers_db:
                        score_class = (
                            "score-high" if p['avg_score'] >= 7
                            else "score-mid" if p['avg_score'] >= 4
                            else "score-low"
                        )
                        st.markdown(
                            f"<span class='{score_class}'>{p['avg_score']}/10</span> "
                            f"&nbsp; **{p['title']}**",
                            unsafe_allow_html=True,
                        )

                        # Judge scores chips
                        verdicts = load_paper_verdicts(p['id'])
                        chips_html = " ".join(
                            f"<span class='judge-chip "
                            f"{'chip-high' if v['relevance_score'] >= 7 else 'chip-mid' if v['relevance_score'] >= 4 else 'chip-low'}'>"
                            f"J{v['judge_run']}: {v['relevance_score']}</span>"
                            for v in verdicts
                        )
                        if chips_html:
                            st.markdown(f"Judges: {chips_html}", unsafe_allow_html=True)

                        # Debate rounds
                        rounds_db = load_paper_debates(p['id'])
                        if rounds_db:
                            for rnd in rounds_db:
                                with st.chat_message("user", avatar="🟢"):
                                    st.markdown(f"**Advocate (R{rnd['round_num']})**")
                                    st.write(rnd['advocate_arg'])
                                with st.chat_message("user", avatar="🔴"):
                                    st.markdown(f"**Skeptic (R{rnd['round_num']})**")
                                    st.write(rnd['skeptic_arg'])

                        # Individual judge verdicts
                        if verdicts:
                            for v in verdicts:
                                reasons = json.loads(v['key_reasons']) if v['key_reasons'] else []
                                reasons_str = " • ".join(reasons) if reasons else ""
                                st.caption(
                                    f"**Judge {v['judge_run']}** (seed {v['seed']}, "
                                    f"score {v['relevance_score']}): "
                                    f"{v['verdict']}  {reasons_str}"
                                )
                        st.divider()


if __name__ == "__main__":
    main()
