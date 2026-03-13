# 📚 arXiv CS.CL Paper Matcher

**Multi-Agent Debate • 5-Judge Panel • Gemini LLM • SQLite Persistence**

A Streamlit app that fetches recent papers from [arXiv CS.CL](https://arxiv.org/list/cs.CL/recent) and uses a multi-agent debate system powered by Google Gemini to assess each paper's relevance to your research problem. Results are persisted in a local SQLite database.

---

## How It Works

```
┌─────────────┐     ┌─────────────┐     ┌───────────────────────┐
│  🟢 Advocate │────▶│  🔴 Skeptic  │────▶│  🏛️ 5-Judge Panel     │
│  (argues FOR)│◀────│(argues AGAINST)│    │  (independent scores) │
│              │ x2  │              │     │  avg → final ranking  │
└─────────────┘     └─────────────┘     └───────────────────────┘
                                                  │
                                          ┌───────▼────────┐
                                          │ 💾 SQLite DB    │
                                          │ papers, debates │
                                          │ verdicts, scores│
                                          └────────────────┘
```

1. **Fetch** — Pulls recent CS.CL papers from arXiv (by count or date range)
2. **Advocate** — Argues FOR each paper's relevance to your problem
3. **Skeptic** — Argues AGAINST relevance, finds gaps and differences
4. **5 Judges** — Each independently scores 1–10 with different temperatures/seeds
5. **Average** — Final score = mean of 5 judge scores
6. **Persist** — Everything stored in SQLite for later review

The debate runs **2 rounds** per paper, so the Advocate and Skeptic refine their arguments before the Judge Panel decides.

---

## Features

- 🔍 **arXiv CS.CL fetcher** with optional keyword filtering
- 📅 **Flexible fetch modes** — by paper count (5–100) or by date range (last 1–90 days)
- 🤖 **Multi-agent debate** — Advocate vs Skeptic (2 rounds)
- 🏛️ **5-Judge Panel** — 5 independent LLM verdicts with varied temperatures & random seeds
- 📊 **Averaged scores** — more robust than a single judge call
- 🟢🔴 **Advocate & Skeptic in main view** — see both arguments at a glance
- ⚡ **Parallel evaluation** — configurable concurrency (1–10 workers)
- � **Fetch Only mode** — browse fetched papers first, then selectively evaluate individual ones- 📶 **Live evaluation status** — per-paper step tracking (Advocate, Skeptic, Judge Panel) during evaluation- 💾 **SQLite persistence** — all papers, debates, and verdicts stored locally
- 🗄️ **Past Evaluations tab** — browse historical evaluation runs with search, filters, and sorting
- 📋 **Three result views** — All Results, Top Matches, Full Debate Transcripts
- 📊 **Metrics dashboard** — at-a-glance counts for High / Moderate / Low relevance papers
- 🔍 **History search & filters** — keyword search, score range, date filter, and sort options
- 📑 **Interactive table** — multi-row selection to view full debate details inline
- 🗑️ **Delete management** — remove individual papers or entire evaluation runs from the database
- 🟢🟡🔴 **Color-coded scoring** — instant visual relevance triage with judge chips
- 📥 **JSON export** — download results for further analysis
- 🔧 **Model selection** — Gemini 3 Pro, 3 Flash, 2.5 Pro, 2.5 Flash, 2.0 Flash

---

## Quick Start

### Prerequisites

- Python 3.10+
- A [Google Gemini API key](https://aistudio.google.com/apikey) (free tier available)

### Installation

```bash
git clone https://github.com/prateek-kacker/arxiv-paper-matcher.git
cd arxiv-paper-matcher
pip install -r requirements.txt
```

### Run

```bash
streamlit run Archive_research_multi-agent_debate.py
```

Then:
1. Enter your **Gemini API key** in the sidebar
2. Describe your **research problem** in the text box
3. Click **🚀 Fetch & Evaluate All** to evaluate every paper, or **📡 Fetch Papers Only** to browse first and selectively evaluate
4. Check the **Past Evaluations** tab to search, filter, and review stored results

---

## Configuration (Sidebar)

| Setting | Description | Default |
|---------|-------------|---------|
| Gemini Model | Which Gemini model to use | `gemini-3-pro-preview` |
| Fetch mode | By paper count or by date range | By number of papers |
| Papers to fetch | Number of latest papers (count mode) | 50 |
| Papers from last N days | Date window (days mode) | 7 |
| Keyword filter | Extra arXiv search terms (ANDed with cs.CL) | — |
| Min relevance score | Threshold for "Top Matches" tab | 6 |
| Parallel evaluations | Concurrent paper evaluations | 3 |

---

## Database Schema

The app uses a local SQLite database (`paper_matcher.db`) with 4 tables:

| Table | Purpose |
|-------|---------|
| `evaluations` | Each run (problem text, model, timestamp) |
| `papers` | Fetched papers linked to an evaluation, with avg score |
| `debate_rounds` | Advocate & Skeptic arguments per round per paper |
| `judge_verdicts` | 5 independent judge scores, seeds, and verdicts per paper |

The database file is created automatically on first run.

---

## Project Structure

```
arxiv-paper-matcher/
├── Archive_research_multi-agent_debate.py   # Main Streamlit app (all-in-one)
├── requirements.txt                          # Python dependencies
├── paper_matcher.db                          # SQLite DB (auto-created at runtime)
└── README.md
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `streamlit` | Web UI framework |
| `arxiv` | arXiv API client |
| `google-genai` | Google Gemini SDK |
| `pandas` | Data tables in the Past Evaluations tab |
| `sqlite3` | Database (Python stdlib) |

---

## How the 5-Judge Panel Works

Each judge run uses a **different temperature** and **random seed** to introduce controlled diversity:

| Judge | Temperature | Effect |
|-------|-------------|--------|
| J1 | 0.5 | More deterministic, conservative |
| J2 | 0.7 | Balanced |
| J3 | 0.9 | Slightly creative |
| J4 | 1.1 | More exploratory |
| J5 | 1.3 | Most creative/varied |

The **final score** is the **average** of all 5 judge scores. The **verdict text** is taken from the judge whose score is closest to the average. This reduces noise and gives more stable, reliable ratings than a single LLM call.

---

## License

MIT

---

## Contributing

Pull requests welcome! For major changes, please open an issue first.
