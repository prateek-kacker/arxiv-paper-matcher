"""
arXiv CS.CL Paper Matcher — Multi-Agent Debate with Gemini LLM-as-Judge
=========================================================================
Fetches recent papers from arXiv CS.CL, then runs a multi-agent debate
(Advocate, Skeptic, Judge) to assess each paper's relevance to your
research problem.
"""

import streamlit as st
import arxiv
from google import genai
from google.genai import types
import json
import time
import concurrent.futures
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timedelta

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
class DebateResult:
    paper: Paper
    rounds: list[DebateRound] = field(default_factory=list)
    judge_verdict: str = ""
    relevance_score: int = 0
    key_reasons: list[str] = field(default_factory=list)
    suggested_use: str = ""


# ──────────────────────────────────────────────────────────────────────────────
# arXiv Fetcher
# ──────────────────────────────────────────────────────────────────────────────

def fetch_arxiv_papers(
    max_results: Optional[int] = 50,
    search_query: Optional[str] = None,
    days_back: Optional[int] = None,
) -> list[Paper]:
    """Fetch recent CS.CL papers from arXiv.
    
    If days_back is set, fetches all papers from the last N days (up to 500).
    Otherwise fetches up to max_results most recent papers.
    """
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

    papers = []
    for result in client.results(search):
        pub_date = result.published.replace(tzinfo=None)
        # If date-filtering, skip papers older than cutoff
        if use_date_filter and pub_date < cutoff_date:
            break  # sorted desc, so no more matches after this
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
# Multi-Agent Debate System
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


class DebateEngine:
    """Runs a multi-agent debate (Advocate vs Skeptic → Judge) using Gemini."""

    def __init__(self, client: genai.Client, model_name: str = "gemini-3-pro-preview"):
        self.client = client
        self.model_name = model_name
        self.debate_rounds = 2  # number of back-and-forth rounds

    def _call_llm(self, system: str, user_prompt: str) -> str:
        """Single Gemini call with retry."""
        for attempt in range(3):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                    ),
                )
                return response.text
            except Exception as e:
                if attempt == 2:
                    return f"[LLM Error: {e}]"
                time.sleep(2 ** attempt)

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

    def run_debate(self, paper: Paper, problem: str) -> DebateResult:
        """Run a full multi-round debate for one paper."""
        context = self._build_paper_context(paper, problem)
        result = DebateResult(paper=paper)
        debate_history = ""

        for round_num in range(1, self.debate_rounds + 1):
            # Advocate turn
            advocate_prompt = (
                f"{context}\n\n{debate_history}"
                f"Round {round_num}: Present your argument FOR this paper's relevance."
            )
            advocate_arg = self._call_llm(ADVOCATE_SYSTEM, advocate_prompt)

            # Skeptic turn (sees advocate's argument)
            skeptic_prompt = (
                f"{context}\n\n{debate_history}"
                f"Round {round_num} — Advocate said:\n{advocate_arg}\n\n"
                f"Now present your counter-argument AGAINST this paper's relevance."
            )
            skeptic_arg = self._call_llm(SKEPTIC_SYSTEM, skeptic_prompt)

            dr = DebateRound(advocate_argument=advocate_arg, skeptic_argument=skeptic_arg)
            result.rounds.append(dr)

            debate_history += (
                f"\n--- Round {round_num} ---\n"
                f"Advocate: {advocate_arg}\n"
                f"Skeptic: {skeptic_arg}\n"
            )

        # Judge verdict
        judge_prompt = (
            f"{context}\n\n"
            f"## Full Debate Transcript\n{debate_history}\n\n"
            f"Now deliver your JSON verdict."
        )
        judge_raw = self._call_llm(JUDGE_SYSTEM, judge_prompt)

        try:
            # Clean potential markdown fences
            cleaned = judge_raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
            cleaned = cleaned.strip()

            verdict = json.loads(cleaned)
            result.relevance_score = int(verdict.get("relevance_score", 0))
            result.judge_verdict = verdict.get("verdict", "")
            result.key_reasons = verdict.get("key_reasons", [])
            result.suggested_use = verdict.get("suggested_use", "")
        except (json.JSONDecodeError, ValueError):
            result.judge_verdict = judge_raw
            result.relevance_score = 0

        return result


# ──────────────────────────────────────────────────────────────────────────────
# Streamlit UI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="arXiv CS.CL Paper Matcher",
        page_icon="📚",
        layout="wide",
    )

    # ── Custom CSS ──
    st.markdown("""
    <style>
    .score-high { color: #00c853; font-weight: bold; font-size: 1.4em; }
    .score-mid  { color: #ffab00; font-weight: bold; font-size: 1.4em; }
    .score-low  { color: #ff1744; font-weight: bold; font-size: 1.4em; }
    .paper-card {
        border: 1px solid #333;
        border-radius: 10px;
        padding: 1.2em;
        margin-bottom: 1em;
    }
    </style>
    """, unsafe_allow_html=True)

    st.title("📚 arXiv CS.CL Paper Matcher")
    st.caption("Multi-Agent Debate  •  Gemini LLM-as-Judge  •  Find papers that solve YOUR problem")

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
        st.markdown("**How it works**\n"
                    "1. Fetches recent CS.CL papers from arXiv\n"
                    "2. For each paper, an **Advocate** argues FOR relevance\n"
                    "3. A **Skeptic** argues AGAINST relevance\n"
                    "4. A **Judge** scores relevance 1-10\n"
                    "5. Results sorted by score")

    # ── Main area ──
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
        run_button = st.button("🚀 Fetch & Evaluate Papers", type="primary", use_container_width=True)
    with col2:
        if st.button("🗑️ Clear Results", use_container_width=True):
            st.session_state.pop("results", None)
            st.rerun()

    # ── Execution ──
    if run_button:
        if not api_key:
            st.error("Please enter your Gemini API key in the sidebar.")
            return
        if not problem_statement.strip():
            st.error("Please describe your research problem.")
            return

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
                mode_label = f"from the last **{days_back}** days" if days_back else f"(latest **{max_papers}**)"
                st.write(f"✅ Fetched **{len(papers)}** papers {mode_label}")
                status.update(label=f"Fetched {len(papers)} papers", state="complete")
            except Exception as e:
                st.error(f"Failed to fetch papers: {e}")
                return

        if not papers:
            st.warning("No papers found. Try adjusting keyword filters.")
            return

        # Step 2 — Multi-agent debate evaluation
        results: list[DebateResult] = []
        progress = st.progress(0, text="Evaluating papers...")
        status_text = st.empty()

        def evaluate_paper(paper: Paper) -> DebateResult:
            return engine.run_debate(paper, problem_statement)

        completed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {executor.submit(evaluate_paper, p): p for p in papers}
            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    p = futures[future]
                    results.append(DebateResult(
                        paper=p,
                        judge_verdict=f"Evaluation failed: {e}",
                        relevance_score=0,
                    ))
                completed += 1
                progress.progress(completed / len(papers),
                                  text=f"Evaluated {completed}/{len(papers)} papers...")
                status_text.text(f"Latest: {futures[future].title[:80]}...")

        progress.empty()
        status_text.empty()

        # Sort by relevance score (descending)
        results.sort(key=lambda r: r.relevance_score, reverse=True)
        st.session_state["results"] = results
        st.session_state["min_score"] = min_score

    # ── Display Results ──
    if "results" in st.session_state:
        results = st.session_state["results"]
        ms = st.session_state.get("min_score", min_score)

        # Summary metrics
        st.divider()
        high = [r for r in results if r.relevance_score >= 7]
        mid = [r for r in results if 4 <= r.relevance_score < 7]
        low = [r for r in results if r.relevance_score < 4]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Papers", len(results))
        m2.metric("🟢 Highly Relevant (7-10)", len(high))
        m3.metric("🟡 Moderate (4-6)", len(mid))
        m4.metric("🔴 Low Relevance (1-3)", len(low))

        # Tabs
        tab_all, tab_top, tab_debate = st.tabs([
            "📋 All Results", "⭐ Top Matches", "🗣️ Debate Details"
        ])

        with tab_all:
            for r in results:
                score_class = (
                    "score-high" if r.relevance_score >= 7
                    else "score-mid" if r.relevance_score >= 4
                    else "score-low"
                )
                with st.container():
                    st.markdown(f"""<div class='paper-card'>
                    <span class='{score_class}'>{r.relevance_score}/10</span>
                    &nbsp;&nbsp;<strong>{r.paper.title}</strong><br/>
                    <small>👤 {r.paper.authors} &nbsp;|&nbsp; 📅 {r.paper.published}</small>
                    </div>""", unsafe_allow_html=True)

                    col_a, col_b = st.columns([3, 1])
                    with col_a:
                        st.markdown(f"**Verdict:** {r.judge_verdict}")
                        if r.key_reasons:
                            st.markdown("**Key Reasons:** " + " • ".join(r.key_reasons))
                        if r.suggested_use:
                            st.markdown(f"**Suggested Use:** {r.suggested_use}")
                    with col_b:
                        st.link_button("📄 Open Paper", r.paper.url, use_container_width=True)

                    with st.expander("Show Abstract"):
                        st.write(r.paper.abstract)
                    st.divider()

        with tab_top:
            top_results = [r for r in results if r.relevance_score >= ms]
            if not top_results:
                st.info(f"No papers scored ≥ {ms}. Try lowering the threshold in the sidebar.")
            for r in top_results:
                with st.container():
                    st.subheader(f"⭐ {r.relevance_score}/10 — {r.paper.title}")
                    st.write(f"👤 {r.paper.authors}  |  📅 {r.paper.published}")
                    st.write(r.judge_verdict)
                    if r.key_reasons:
                        for reason in r.key_reasons:
                            st.markdown(f"- {reason}")
                    if r.suggested_use:
                        st.success(f"💡 {r.suggested_use}")
                    st.link_button("Open on arXiv", r.paper.url)
                    st.divider()

        with tab_debate:
            st.info("Expand any paper below to see the full Advocate / Skeptic debate transcript.")
            for r in results:
                with st.expander(f"{'🟢' if r.relevance_score >= 7 else '🟡' if r.relevance_score >= 4 else '🔴'} [{r.relevance_score}/10] {r.paper.title}"):
                    for i, rnd in enumerate(r.rounds, 1):
                        st.markdown(f"### Round {i}")
                        c1, c2 = st.columns(2)
                        with c1:
                            st.markdown("**🟢 Advocate**")
                            st.info(rnd.advocate_argument)
                        with c2:
                            st.markdown("**🔴 Skeptic**")
                            st.warning(rnd.skeptic_argument)
                    st.markdown("### 🏛️ Judge Verdict")
                    st.success(r.judge_verdict)

        # Export
        st.divider()
        export_data = []
        for r in results:
            export_data.append({
                "title": r.paper.title,
                "authors": r.paper.authors,
                "published": r.paper.published,
                "url": r.paper.url,
                "relevance_score": r.relevance_score,
                "verdict": r.judge_verdict,
                "key_reasons": r.key_reasons,
                "suggested_use": r.suggested_use,
            })
        st.download_button(
            "📥 Download Results (JSON)",
            data=json.dumps(export_data, indent=2),
            file_name="arxiv_paper_matches.json",
            mime="application/json",
        )


if __name__ == "__main__":
    main()
