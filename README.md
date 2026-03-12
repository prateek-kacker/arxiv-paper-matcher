# 📚 arXiv CS.CL Paper Matcher

**Multi-Agent Debate • Gemini LLM-as-Judge • Find papers that solve YOUR problem**

A Streamlit app that fetches recent papers from [arXiv CS.CL](https://arxiv.org/list/cs.CL/recent) and uses a multi-agent debate system powered by Google Gemini to assess each paper's relevance to your research problem.

---

## How It Works

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  🟢 Advocate │────▶│  🔴 Skeptic  │────▶│  🏛️ Judge   │
│  (argues FOR)│◀────│(argues AGAINST)│     │(scores 1-10)│
│              │ x2  │              │     │  + verdict  │
└─────────────┘     └─────────────┘     └─────────────┘
```

1. **Fetch** — Pulls recent CS.CL papers from arXiv (by count or date range)
2. **Advocate** — Argues FOR each paper's relevance to your problem
3. **Skeptic** — Argues AGAINST relevance, finds gaps and differences
4. **Judge** — Weighs both sides, delivers a score (1–10), verdict, key reasons, and suggested use
5. **Rank** — Results sorted by relevance score

The debate runs **2 rounds** per paper, so the Advocate and Skeptic refine their arguments before the Judge decides.

---

## Features

- 🔍 **arXiv CS.CL fetcher** with optional keyword filtering
- 📅 **Flexible fetch modes** — by paper count (5–100) or by date range (last 1–90 days)
- 🤖 **Multi-agent debate** — Advocate vs Skeptic → Judge verdict
- ⚡ **Parallel evaluation** — configurable concurrency (1–10 workers)
- 📊 **Three result views** — All Results, Top Matches, Full Debate Transcripts
- 🟢🟡🔴 **Color-coded scoring** — instant visual relevance triage
- 📥 **JSON export** — download results for further analysis
- 🔧 **Model selection** — Gemini 3 Pro, 3 Flash, 2.5 Pro, 2.5 Flash, 2.0 Flash

---

## Screenshots

| Main View | Debate Details |
|-----------|---------------|
| Papers ranked by relevance with verdicts | Full Advocate vs Skeptic transcript |

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
3. Click **🚀 Fetch & Evaluate Papers**

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

## Project Structure

```
arxiv-paper-matcher/
├── Archive_research_multi-agent_debate.py   # Main Streamlit app
├── requirements.txt                          # Python dependencies
├── .gitignore
└── README.md
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `streamlit` | Web UI framework |
| `arxiv` | arXiv API client |
| `google-genai` | Google Gemini SDK |

---

## How the Debate Works (Detail)

For each paper, the system runs a structured debate:

### Round 1
- **Advocate** reads the paper abstract + your problem → argues FOR relevance
- **Skeptic** reads the same + Advocate's argument → argues AGAINST

### Round 2
- **Advocate** sees Round 1 history → refines and strengthens FOR argument
- **Skeptic** sees Round 1 + new Advocate argument → refines AGAINST argument

### Verdict
The **Judge** receives the full transcript and returns:
```json
{
  "relevance_score": 8,
  "verdict": "This paper directly addresses...",
  "key_reasons": ["Shared methodology", "Applicable dataset", "Transfer learning overlap"],
  "suggested_use": "Use their data augmentation pipeline as a baseline..."
}
```

---

## License

MIT

---

## Contributing

Pull requests welcome! For major changes, please open an issue first.
