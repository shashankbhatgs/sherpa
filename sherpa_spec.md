Project inspired by: https://sausheong.com/i-built-my-own-ai-agent-and-named-it-after-a-cat-471b456dc26c

# Engineering Specification: The Interview Sherpa
**Project State:** System Design Approved (MVP Scope Locked)
**Target Persona:** SDE2 Candidate (2 Years of Experience)
**Design Philosophy:** Local-first, plain-file storage, extreme reliability, and strict burnout protection. Inspired by the architectural principles of [Sau Sheong's Felix Agent](https://sausheong.com/i-built-my-own-ai-agent-and-named-it-after-a-cat-471b456dc26c).

---

## 1. Problem Statement & Context
Job hunting after a layoff is mentally exhausting. An SDE2 technical loop forces candidates to split their cognitive energy between two entirely different types of friction:
* **High-Ambiguity Technical Preparation:** Mastery of High-Level Design (HLD), Low-Level Design (LLD), Data Structures & Algorithms (DSA), and edge-case optimization under simulated interview pressure.
* **Low-Cognition Administrative Overhead:** Scouring job boards, tracking multiple application streams (LinkedIn, direct portals, referrals), drafting cold outreach messages, and maintaining status spreadsheets.

Without systemic boundaries, candidates frequently experience high friction, administrative overhead, and eventual burnout, which degrades interview performance.

---

## 2. Core Architecture & System Topology

The Interview Sherpa is designed around a **Core Service Abstraction Layer**. Both the command-line interface (`cli.py`) and browser dashboard (`app.py`) act as thin presentation shells that import and interact with a unified, headless backend framework.

```text
                  ┌──────────────────────────────┐
                  │   Streamlit Browser Web UI   │
                  │          (app.py)            │
                  └──────────────┬───────────────┘
                                 │ (Imports core)
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│              CORE SERVICE LAYER (sherpa/ package)              │
│                                                                 │
│ ┌───────────────────┐ ┌───────────────────┐ ┌─────────────────┐ │
│ │  interviewer.py   │ │    tracker.py     │ │    scout.py     │ │
│ │  (State Machine)  │ │ (gspread Engine)  │ │ (Resume Index)  │ │
│ └───────────────────┘ └───────────────────┘ └─────────────────┘ │
│ ┌───────────────────┐ ┌───────────────────┐ ┌─────────────────┐ │
│ │    config.py      │ │    telemetry.py   │ │   llm_router.py │ │
│ │ (Hot-Reload JSON) │ │ (Circuit Breaker) │ │ (Ollama/Cloud)  │ │
│ └───────────────────┘ └───────────────────┘ └─────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                                 ▲
                                 │ (Imports core)
                  ┌──────────────┴───────────────┐
                  │    Terminal Click CLI UI     │
                  │          (cli.py)            │
                  └──────────────────────────────┘
```

## Local Workspace Schema (~/.sherpa/)
All internal data, sessions, configuration states, and templates live inside a transparent file structure:
~/.sherpa/
├── config.json                 # API keys, model routing parameters, burnout rules
├── credentials.json            # Google Service Account JSON key block
├── profile.json                # User core metadata, technical competencies, target vectors
├── resume.md                   # Single master resume file in clean Markdown format
├── applications.jsonl          # Local append-only fallback ledger for tracking entries
├── interviews/                 # Directory holding transactional mock logs
│   └── session_20260522_1.md   # Crash-resilient interaction log with state headers
└── question_bank/              # Pre-seeded high-signal SDE2 target prompts
├── hld_distributed_cache.md
├── lld_parking_lot.md
└── dsa_lru_cache.md


3. Detailed Module Functional Specifications
   Module 1: The Job Scout & Outreach Engine
- Ingestion Mechanics: Simple text copy-paste clipboard block into the UI wrapper to completely avoid modern web-scraping or CAPTCHA limitations.

- The Index Pattern Enforcement: Instead of appending the full master resume to every AI prompt evaluation layer, the engine constructs a permanent 4-line metadata representation (The Resume Index) containing core tech competencies, years of experience, and structural flags.

- Execution Logic: The agent reads the index. If it detects a functional role alignment, it calls an explicit tool fetch_full_resume() to load the raw file on demand, synthesizing a highly tailored cold message highlighting the intersection of past production designs with the new job requirement.

Module 2: The Google Sheets Auto-Tracker
- Authentication Vector: Headless integration utilizing the gspread Python package communicating via a static Google Cloud Service Account credential key. The candidate simply shares the Google Sheet link with the service account bot email.

- Natural Language Extraction: Parses natural stream updates (e.g., "Applied to Stripe via referral from Sarah") into an explicit JSON extraction format using the default local LLM tier.

- Validation Failure Loop: If vital data parameters (such as company or current pipeline state) cannot be dynamically isolated with high confidence, the script pauses row insertion and generates a clear clarifying prompt to prompt the user for the missing parameter.

Module 3: The Technical Sparring Partner (HLD/LLD/DSA)
- Interview State Machine: Rather than relying on standard fluid chat behaviors, the conversation flow maps against 4 sequential code-enforced states recorded in the markdown ledger header:
1. CLARIFYING: Evaluates scale boundaries (DAU, QPS, Latency SLAs). Blocks subsequent design states until baseline expectations are identified.
2. HIGH_LEVEL: Manages API endpoint declarations, system separation boundaries, and high-level structural design notation using text descriptions or Mermaid syntax.
3. DEEP_DIVE: Forces intensive engineering discussion around distributed synchronization anomalies, single-points-of-failure, concurrency race conditions, and failover states.
4. FEEDBACK: Exits character and evaluates user performance across an industry-standard mid-level SDE2 rubric.
- Semantic Code Evaluation: Analyzes code structures using dedicated criteria prompts checking for thread safety, edge configurations, and asymptotic tracking ($O(N)$ execution metrics).

Module 4: The Burnout Guard (The Circuit Breaker)
- Telemetry Monitoring: Continuously tallies cumulative high-intensity study times by auditing transactional update times inside the execution files.

- Hard State-Shifting: When active daily preparation intervals surpass the thresholds configured in config.json, the service fires a script lockout mechanism. It shifts the core persona template to an advisory profile, actively declining additional engineering tasks while remaining open for low-stress tracking updates.

4. Architectural Trade-offs & Backlog
Architectural Dimension,Chosen Strategy for MVP,Structural Trade-off,Future Backlog Item
Language Runtime,Python 3.11+ in isolated virtual environment (venv),Highly accelerated script composition speed vs larger standalone package footprint.,Evaluate packaging dependencies via PyInstaller or rewrite performance-critical paths in Go.
Data Storage Layer,Append-only plain text formats (JSONL & Markdown),"Human-readable portability, explicit transparency, and simple text backups vs slower lookup processing at high scales.",Add directory index bounds or migrate older historical traces to an inline local SQLite database file.
Model Distribution,Local Inference Engine (Ollama) with Cloud API Override,Zero continuous operating cost vs minor performance drops in processing highly complex HLD systems on local weights.,Build a local metrics profile evaluator to compare grading deviations between local models and larger cloud nodes.
Code Validation,Semantic Static Analysis via targeted Rubric Prompts,Fast setup covering distributed patterns vs lack of true compilation error discovery.,Integrate local sandboxed Docker or native subprocessing environments to execute live dynamic unit tests (pytest).
User Interface,Dual Presentation Layout (Streamlit Web UI + Click CLI),Minor initial codebase expansion vs providing a unified experience for two different user styles.,Package the CLI into a global standalone pip configuration (pip install -e .).
Question Source,Pre-seeded Local MD Templates with Dynamic Generation,Ensures structural rubric accuracy vs potential drift/hallucination when generating dynamically.,Build a parser to ingest recent interview updates directly from engineering forum networks.
Session Integrity,Crash-Resilient File Hydration,Minor string mapping overhead on setup vs absolute session integrity during resource pressure events.,Refactor backend states to communicate over a lightweight background daemon process loop.

5. Implementation Step-by-Step Requirements
      We will build this incrementally to guarantee deep focus and zero code truncation bugs.

Our execution path will be:

- Phase 1: Core Workspace Setup, config.json managers, and llm_router.py (connecting cleanly to Ollama with cloud flexibility overrides).

- Phase 2: The tracker.py headless engine and Google Service Account sheet injection loops.

- Phase 3: The interviewer.py state machine, markdown parsing loops, and session ledger hydration logic.

- Phase 4: Interface skins development (cli.py and Streamlit app.py) hooked into the telemetry.py circuit breaker.