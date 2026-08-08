# 📚 arXiv CS.CL & ACL Anthology Paper Matcher

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![Google Gemini](https://img.shields.io/badge/Google_Gemini-2.5_Pro_%7C_Flash-4285F4.svg)](https://aistudio.google.com/)
[![SQLite](https://img.shields.io/badge/SQLite-WAL_Mode-003B57.svg)](https://www.sqlite.org/)
[![GCP / AWS](https://img.shields.io/badge/Cloud_Sync-GCS_%7C_S3-FF9900.svg)](https://cloud.google.com/storage)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Multi-Agent Debate • 5-Judge Consensus Panel • Gemini LLM Engine • SQLite & Cloud Storage Sync • Automated Schedules & Webhooks**

A full-stack AI research paper evaluation and discovery application powered by Google Gemini LLMs. It fetches recent research papers from **arXiv CS.CL** or the **ACL Anthology (ACL 2026)** and runs a multi-agent debate system to systematically analyze, debate, and score paper relevance against your custom research problem.

Evaluations can be executed interactively in real time with live streaming updates, asynchronously in the background, or automatically via scheduled headless batch jobs. All results are stored in a local SQLite database with automatic bi-directional sync to **Google Cloud Storage (GCS)** or **AWS S3**.

---

## 🏗️ Architecture & How It Works

```
┌─────────────────────────────────────────────────────────────┐
│                       Paper Sources                         │
│  📄 arXiv CS.CL (Live API)  •   🏛️ ACL Anthology (ACL 2026) │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                  Multi-Agent Debate Engine                  │
│   🟢 Advocate (FOR) ◄────────────► 🔴 Skeptic (AGAINST)     │
│                     (2 Rounds of Debate)                    │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                    🏛️ 5-Judge LLM Panel                      │
│   Independent evaluation calls (Temperatures: 0.5 ── 1.3)   │
│       Final Score = Panel Mean of all 5 Judge Scores        │
└──────────────────────────────┬──────────────────────────────┘
                               │
           ┌───────────────────┴───────────────────┐
           ▼                                       ▼
┌──────────────────────┐                ┌──────────────────────┐
│  💾 SQLite Database  │                │   ☁️ Cloud Sync      │
│   (`paper_matcher.db`)│                │  (GCS & AWS S3 Sync) │
└──────────┬───────────┘                └──────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│                   FastAPI + Modernist SPA                   │
│   Live SSE Stream  •  Background Jobs  •  Fetch-Only Mode   │
│   Search & Filter Index  •  Bulk Actions  •  Webhooks       │
└─────────────────────────────────────────────────────────────┘
```

### Evaluation Lifecycle

1. **Fetch & Pre-Filter** — Retrieves paper metadata and full text from **arXiv CS.CL** (live feed by paper count or date range) or **ACL Anthology 2026** (6,400+ papers across 8 track volumes).
2. **Advocate Agent** — Formulates an argument **FOR** the paper's relevance, highlighting core methodologies, theoretical contributions, and alignment with your research problem.
3. **Skeptic Agent** — Formulates a counter-argument **AGAINST** relevance, evaluating domain gaps, missing benchmarks, assumption mismatches, and execution limitations.
4. **5-Judge Consensus Panel** — 5 independent LLM judges grade the paper on a scale of 1–10 using distinct temperature settings (`0.5` to `1.3`) and random seeds to eliminate single-prompt LLM variance.
5. **Consensus Score & Representative Verdict** — The final paper score is the exact arithmetic mean of all 5 judges. The official verdict narrative is dynamically selected from the judge score closest to the panel average.
6. **Persist & Sync** — All paper metadata, extracted text, debate transcripts, and individual judge breakdown scores are saved to SQLite (`paper_matcher.db`) and automatically pushed to cloud storage (GCS / AWS S3).
7. **Webhook Notification** — Optionally dispatches structured JSON evaluation payloads directly to your specified webhook endpoint (`KACKERS_POST_URL`).

---

## ✨ Key Features

### 📄 Dual Paper Repositories
- **arXiv CS.CL**: Live query API supporting search by number of recent papers (1–100) or date range (last 1–90 days), with keyword filtering against title and abstract.
- **ACL Anthology (ACL 2026)**: Access over 6,400 pre-scraped ACL 2026 papers filterable by specific track volumes:
  - *All Tracks, Long Papers, Short Papers, Findings, Demos, Student Research Workshop (SRW), Industry Papers, and Workshops*.
- **Full-Text Analysis**: Automatically parses and injects full-text paper contents into LLM prompts for deep methodological evaluation.

### 🤖 Multi-Agent Debate & 5-Judge Panel
- **Adversarial Debate**: 2 rounds of structured debate between an Advocate (arguing relevance) and a Skeptic (challenging domain transfer and utility).
- **5-Judge Panel Variance Control**: 5 independent judge runs spanning temperatures `0.5`, `0.7`, `0.9`, `1.1`, and `1.3`.
- **Visual Score Badges**: Visual rating chips across the UI:
  - 🟢 **8.0 – 10.0**: High Relevance / Strong Match
  - 🟡 **5.0 – 7.9**: Moderate Relevance / Secondary Match
  - 🔴 **1.0 – 4.9**: Low Relevance / Out of Scope

### ⚡ Flexible Execution Modes
- **Interactive Live SSE Streaming**: Real-time Server-Sent Events (SSE) stream streaming Advocate arguments, Skeptic rebuttals, and live judge scores, with a one-click **Stop Evaluation** button to cancel execution instantly.
- **Background Evaluation Mode**: Non-blocking asynchronous task execution allowing users to navigate away or run concurrent tasks while live status updates track progress in the History tab.
- **Fetch-Only Mode**: Preview retrieved paper titles and abstracts first before selectively initiating multi-agent debate on chosen papers.

### 🗄️ Modernist Web SPA & History Inspector
- **Dual-Subtab History View**:
  - 📁 **Evaluation Runs**: Grouped view of past evaluation sessions with problem context, parameters, and paper lists.
  - 📋 **All Evaluated Papers Index**: Unified, paper-centric searchable index across all past evaluations.
- **Advanced Search & Filtering**: Instant filter by keyword search, minimum relevance score, repository source (`All`, `arXiv CS.CL`, `ACL Anthology 2026`), and sorting by score or evaluation date.
- **Bulk Paper Management & Split-Pane Inspector**: Select multiple papers via checkboxes to view side-by-side in a split-pane inspector, or perform bulk deletion.

### ⏰ Automated Schedules & Webhooks
- **Recurring Schedule Manager**: Automate daily or weekly paper matching jobs with 1:1 configuration parameter sync directly from the evaluation form.
- **GCP Cloud Scheduler Live Status & Controls**: Real-time detection and 1:1 bidirectional sync with GCP Cloud Scheduler (`hourly-paper-matcher-eval`). Features a prominent UI health banner (`🟢 ACTIVE` / `⚠️ PAUSED`) and direct one-click **Resume / Pause Cloud Scheduler** controls.
- **Headless Batch Runner**: CLI utility (`batch_runner.py`) for headless execution on GCP Cloud Run Jobs, Kubernetes CronJobs, or local crontab.
- **Webhook Alerts**: Automatically POST structured JSON evaluation summaries to custom HTTP webhooks (`KACKERS_POST_URL`).

### ☁️ Cloud Persistence & Storage
- Bi-directional database synchronization (`paper_matcher.db`) with **Google Cloud Storage (GCS)** and **AWS S3**.
- Write-Ahead Logging (WAL) checkpointing ensures database integrity prior to cloud upload.

---

## 🚀 Quick Start

### Prerequisites
- **Python 3.10+**
- **Google Gemini API Key** ([Get key from Google AI Studio](https://aistudio.google.com/apikey))

### 1. Clone & Install Dependencies

```bash
git clone https://github.com/prateek-kacker/arxiv-paper-matcher.git
cd arxiv-paper-matcher
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Set your Gemini API key and optional cloud storage / webhook configurations:

```bash
# Required
export GEMINI_API_KEY="your-gemini-api-key"

# Optional Cloud Storage Sync
export PAPER_MATCHER_DB_BUCKET="your-gcs-bucket-name"
export AWS_S3_BUCKET="your-s3-bucket-name"

# Optional Webhook Integration
export KACKERS_POST_URL="https://your-webhook-endpoint.com/api/notify"
export AUTO_POST_RESULTS="true"
```

### 3. Launch the Application Server

Start the FastAPI application server:

```bash
python server.py
```

Or using `uvicorn`:

```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

Open your browser and navigate to **`http://localhost:8000`**.

*(Note: Legacy Streamlit UI can still be launched via `streamlit run Archive_research_multi-agent_debate.py`)*

---

## ⚙️ Configuration Parameters

| Parameter | Description | Options / Defaults |
|-----------|-------------|-------------------|
| **Gemini Model** | LLM powering Advocate, Skeptic & 5-Judge Panel | `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.0-flash` |
| **Paper Source** | Target research paper database | `arXiv CS.CL`, `ACL Anthology 2026` |
| **ACL Track** | Track filter when ACL source selected | `All Tracks`, `Long Papers`, `Short Papers`, `Findings`, `Demos`, `SRW`, `Industry`, `Workshops` |
| **Fetch Mode** | Fetch strategy for arXiv queries | `By number of papers` (1–100), `By date range` (1–90 days) |
| **Keyword Filter** | Title & abstract text search query | Optional keyword string |
| **Min Relevance Score** | Score threshold for Top Matches tab | Default: `6` |
| **Parallel Workers** | Concurrent paper evaluation threads | `1` to `10` |

---

## 📡 API Reference

The FastAPI server exposes the following REST and SSE endpoints:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/config` | Returns system config (API key status, GCS/S3 buckets, webhook URL) |
| `POST` | `/api/fetch-papers` | Fetch papers from arXiv or ACL Anthology without running evaluation |
| `GET` | `/api/evaluate/stream` | **SSE Stream**: Execute evaluation with real-time streaming progress |
| `POST` | `/api/evaluate/stop` | Stop an in-progress SSE evaluation immediately |
| `POST` | `/api/evaluate/background` | Launch evaluation as a non-blocking background task |
| `GET` | `/api/evaluations` | List all historical evaluation runs |
| `GET` | `/api/evaluations/{id}` | Get complete evaluation run details, papers, and transcripts |
| `DELETE`| `/api/evaluations/{id}` | Delete an evaluation run and associated papers |
| `GET` | `/api/papers/all` | Fetch all evaluated papers across all runs for the history index |
| `POST` | `/api/papers/delete-bulk` | Bulk delete a list of paper IDs |
| `GET` | `/api/schedules` | List all active and inactive recurring schedules |
| `POST` | `/api/schedules` | Create a new recurring schedule |
| `PUT` | `/api/schedules/{id}` | Update an existing recurring schedule configuration |
| `POST` | `/api/schedules/{id}/toggle` | Enable or disable a recurring schedule |
| `DELETE`| `/api/schedules/{id}` | Delete a recurring schedule |
| `POST` | `/api/sync/cloud` | Trigger manual bi-directional cloud DB sync (GCS/S3) |

---

## 🤖 Headless Batch Runner & GCP Cloud Deployment

### Headless Batch Runner CLI

The CLI script `batch_runner.py` allows executing recurring evaluation schedules headlessly in background environments:

```bash
# Execute all due recurring schedules (based on cron frequency)
python batch_runner.py

# Force execution of all active recurring schedules regardless of last run time
python batch_runner.py --all

# Force execution of a specific recurring schedule ID
python batch_runner.py --schedule-id 1
```

### GCP Cloud Run & Cloud Scheduler Deployment

Deploy the full application to Google Cloud using the automated deployment script `deploy_gcp.sh`:

```bash
chmod +x deploy_gcp.sh
./deploy_gcp.sh
```

This script automatically:
1. Enables required GCP APIs (Cloud Run, Cloud Scheduler, Cloud Build, Artifact Registry, GCS).
2. Provisions a Google Cloud Storage bucket for `paper_matcher.db` persistence.
3. Builds and pushes the Docker container to Artifact Registry.
4. Deploys the FastAPI server to **GCP Cloud Run** (Live URL: [https://archive-paper-matcher-gr6ge7htzq-uc.a.run.app](https://archive-paper-matcher-gr6ge7htzq-uc.a.run.app)).
5. Sets up a **Cloud Run Job** (`archive-paper-matcher-job`) and **Cloud Scheduler** job (`hourly-paper-matcher-eval`) with automatic 1:1 status synchronization and web UI controls via `/api/schedules/cloud-scheduler/toggle`.

---

## 🗄️ Database Schema

The SQLite database (`paper_matcher.db`) maintains 5 relational tables:

| Table | Primary Keys / Details | Description |
|-------|-----------------------|-------------|
| `evaluations` | `id` (INTEGER AUTOINCREMENT) | Stores evaluation run metadata, research problem, source, parameters, timestamps, status. |
| `papers` | `id` (INTEGER AUTOINCREMENT), `eval_id` (FK) | Stores fetched paper titles, authors, published dates, abstracts, full_text, PDF URLs, avg_score, and top match flags. |
| `debate_rounds` | `id` (INTEGER AUTOINCREMENT), `paper_id` (FK) | Stores Advocate and Skeptic arguments and counter-arguments for each debate round. |
| `judge_verdicts` | `id` (INTEGER AUTOINCREMENT), `paper_id` (FK) | Stores individual scores, temperatures, random seeds, and written verdicts for all 5 judges. |
| `recurring_schedules` | `id` (INTEGER AUTOINCREMENT) | Stores automated recurring job configurations, cron schedule, status, and last execution timestamp. |

---

## ⚖️ How the 5-Judge Panel Works

To ensure robust evaluation and remove prompt sensitivity, each paper is evaluated by 5 distinct judge LLM instances running with varied temperature profiles:

| Judge | Temperature | Profile & Evaluation Style |
|-------|-------------|----------------------------|
| **Judge 1** | `0.5` | Strict, deterministic, highly conservative scoring |
| **Judge 2** | `0.7` | Standard balanced technical assessment |
| **Judge 3** | `0.9` | Nuanced analysis, evaluating subtle domain connections |
| **Judge 4** | `1.1` | Exploratory assessment, rewarding innovative cross-domain applications |
| **Judge 5** | `1.3` | Highly creative evaluation, considering high-risk high-reward potential |

- **Final Score**: Calculated as the exact mean average of all 5 judge scores.
- **Representative Verdict**: Selected from the judge whose score is nearest to the panel mean score.

---

## 📁 Project Structure

```
arxiv-paper-matcher/
├── server.py                                # FastAPI app server & REST API / SSE endpoints
├── core_engine.py                           # Multi-agent debate engine, 5-judge panel, SQLite & Cloud Sync
├── batch_runner.py                          # Headless CLI batch execution engine for scheduled jobs
├── deploy_gcp.sh                            # Automated GCP Cloud Run & Cloud Scheduler deployment script
├── Dockerfile                               # Production Docker container definition
├── requirements.txt                         # Python dependencies
├── static/                                  # Modernist SPA frontend static assets
│   ├── index.html                           # Single-Page UI application markup & layout
│   ├── app.js                               # Frontend logic, SSE stream handler, History index & modals
│   └── styles.css                           # Modern visual styling system
├── scratch/                                 # Inspection & verification helper scripts
├── tests/                                   # Application test suite
├── Archive_research_multi-agent_debate.py   # Legacy Streamlit UI implementation
├── paper_matcher.db                         # Local SQLite database (auto-created on first run)
└── README.md                                # Project documentation
```

---

## 📜 License

Distributed under the **MIT License**. See `LICENSE` for more information.
