# Envoy

A local, review-first autonomous job application agent. Envoy searches SEEK/Indeed/LinkedIn, evaluates fit against each job's must-have requirements, drafts grounded cover letters from your STAR evidence profile, fills out application forms on SEEK and external ATS portals, and asks you to confirm before submitting anything. Runs entirely on your own machine — your profile and applications never leave it.

---

## How it works

For a full visual map of the current product flow, see [docs/envoy-flow.md](docs/envoy-flow.md).

### Three services, one-way data flow

```
Portal (React)  →  Agent (Python)  →  Tools (Node/Playwright)
```

| Service | Path | Stack | Responsibility |
|---------|------|-------|----------------|
| **Agent** | `agent/` | Python 3.12, FastAPI, LangGraph | Orchestration, AI, persistence, HTTP API |
| **Tools** | `tools/` | Node 20, Fastify, Playwright | Browser automation and HTML parsing. Stateless — browser sessions in memory only |
| **Portal** | `portal/` | React 18, Vite, TypeScript, Tailwind | Review UI. Talks only to the agent |

Rules:
- Portal never knows tools exists
- Tools never call agent back
- Only agent writes to the database
- Tools holds browser sessions in memory only — lost on restart

---

## Application lifecycle

### 1. Search

```
Portal: enter keywords + location → click "Run Search"
  Agent: POST /workflows/search
    → calls Tools: POST /tools/providers/seek/search
      → Playwright fetches SEEK listing pages
      → parser extracts: title, company, location, salary, work type, tags, bullet points
    → policy filter: blocks internships, grad programs, etc.
    → persists new jobs to SQLite (state: discovered)
  Portal: shows job cards with Review / Ignore buttons
```

### 2. Prepare (async)

```
Portal: click "Review" on a job card
  Agent: POST /jobs/{id}/queue
    → creates application placeholder (state: preparing)
    → enqueues "prepare" item in work_queue
    → returns immediately (< 50ms)

Background worker (runs inside agent process):
  → picks up "prepare" item
  → fetches full job description from SEEK detail page (Tools)
  → AI: evaluates fit → generates cover letter in parallel
    → produces: cover letter, match evidence (STRONG/MODERATE/WEAK), is_suitable flag, gaps
  → saves drafts to SQLite
  → updates application state: preparing → prepared (or unsuitable)

Portal: polls every 3s → sees "prepared" → shows review panel
```

### 3. Human review

The portal shows:
- **Match Breakdown** — collapsible evidence table, one row per must-have requirement: `[STRONG]`, `[MODERATE]`, or `[WEAK]` match with the specific profile evidence cited
- **Gaps** — up to 3 missing must-have requirements surfaced by the fit evaluator (only shown when fit_score < 0.5 or close)
- **Fit score** — 0.0–1.0; jobs scoring below 0.5 are marked `unsuitable` and skipped automatically
- **Cover letter** — editable textarea pre-filled by the AI
- **Job description** — side by side for comparison
- **Approve** or **Discard**

Approve saves any cover letter edits and sets state → `approved`.

### 4. Apply (async)

```
Portal: click "Start Applying"
  Agent: POST /applications/{id}/apply
    → sets state: approved → applying
    → enqueues "apply" item
    → returns immediately

Background worker:
  → opens SEEK job page in Playwright (shared Chrome instance)
  → clicks Apply button → enters the application form
  → for each form step:
      → Inspector reads all fields (type, label, options, current value)
      → Agent AI proposes answers using your profile
      → Tools fills fields and clicks Continue
      → wait for page transition → read new step
  → if low-confidence fields found (screening questions):
      → saves state to DB
      → sets application state: needs_review

Portal: polls every 2s → sees "needs_review" → shows gate panel
```

### 5. Human gate (HITL)

When the AI isn't confident about a screening question (e.g. "Do you have Databricks experience?"):

```
Portal: shows only the uncertain fields
  → AI suggestion shown next to each field
  → user picks the correct answer
  → clicks "Continue Application"

Agent: POST /applications/{id}/gate
  → enqueues "resume" item with approved answers
  → worker resumes from where it paused
```

### 6. Submit confirmation

When all form steps are complete:

```
Worker: detects review page (no more fields to fill)
  → saves full step history to DB
  → sets state: awaiting_submit

Portal: shows "Review Filled Answers" panel
  → table of every field filled across all steps
  → "Submit to SEEK" button

Portal: click "Submit to SEEK"
  Agent: POST /applications/{id}/submit
    → enqueues final "resume" item
    → worker clicks the submit button on SEEK
    → detects confirmation page ("Thank you for applying")
    → sets state: applied
```

### 7. External portals (automated harness)

When SEEK redirects to an external ATS (Workday, Greenhouse, Lever, etc.), Envoy switches into a policy-gated automated harness rather than deferring entirely to the user:

```
Worker: detects redirect to non-SEEK domain
  → starts external apply harness (LangGraph loop)

Harness loop (one action per iteration):
  → Browser observer reads current page (fields, buttons, links, visible text)
  → LLM planner proposes a single action with confidence + risk level
  → Deterministic policy gate validates the action:
      - allowed    → Tools executes immediately
      - paused     → saves state, sets application state: needs_review
      - rejected   → planner retries or stops

Pause reasons (require user input via gate panel):
  → low_confidence      — planner below 0.75 confidence threshold
  → sensitive           — salary, visa, disability, diversity fields
  → needs_approval      — high-risk action or unapproved value source
  → final_submit        — ready to submit; requires explicit user approval
  → needs_user_input    — planner explicitly asked for clarification

Auto-approved actions (no gate needed):
  → required privacy/consent checkboxes (e.g. "I agree to the Privacy Policy")
  → profile-sourced fields that match verified profile facts
  → career narrative text fields (cover letter, why interested, etc.)
```

If the external portal cannot be automated (captcha, unusual ATS), the harness falls back:

```
Worker: sets state: paused, page_type: external_redirect

Portal: shows link to external portal + "Mark as Submitted" button
  → user applies manually on the external site
  → clicks "Mark as Submitted" → state: applied
```

---

## Async queue architecture

All long-running work is async. HTTP calls from the portal return in under 50ms. The browser automation runs in the background.

```
HTTP request → enqueue item → return {state: "applying"}
                                          ↓
              Background worker (asyncio task in agent process)
                → claim item from work_queue
                → run workflow (browser, AI, DB writes)
                → write result to last_apply_step_json
                → update application state
                                          ↓
              Portal polls GET /applications/{id} every 2s
                → reads state + last_apply_step_json
                → renders appropriate panel
```

The portal is a read/write UI only. It has no persistent connection to the agent. Closing the portal does not stop the queue — the worker continues as long as the agent process is running.

Queue item states: `pending → processing → done | failed`

---

## State machine

```
discovered → in_review (job states)

preparing → prepared → approved → applying → needs_review → applying (loop)
                                           → awaiting_submit → submitting → applied
                     → unsuitable
                     → failed
                     → paused (auth expired or external portal fallback)

External apply harness states (within applying):
  running → paused_for_user       (sensitive / low-confidence field)
          → paused_for_approval   (high-risk action)
          → ready_to_submit       (all fields complete; awaits explicit confirm)
          → completed
          → failed
```

---

## Profile coaching interview

Before Envoy can write grounded cover letters and answer screening questions, it needs structured evidence from your work history. The profile interview is a guided STAR coaching session that builds this evidence item by item.

### What it builds

Each evidence item represents one job or project and has four STAR fields:

| Field | Purpose |
|---|---|
| **Situation** | The starting problem, constraint, or scale that made this work worth doing |
| **Task** | What you personally owned end-to-end (separated from broader team contribution) |
| **Action** | What you actually built or did |
| **Outcome** | What changed for the business, users, or delivery process after your work landed |
| **Metrics** | Numbers, scale markers, or measurable impact (even approximations count) |

It also builds a **voice profile** from your natural answers — sentence rhythm, formality, use of contractions, opening style — so cover letters sound like you wrote them, not like AI did.

### How a session works

```
For each evidence item with gaps (missing STAR fields):
  → AI identifies the highest-priority gap (situation → task → outcome → metrics)
  → Asks one targeted question with a cautious suggested answer (based only on what's already in the profile)
  → User responds in their own words

  On each answer:
    → AI maps the answer to the correct STAR field (normalises rough notes)
    → Reflects the interpretation back: "I've captured the outcome as: ..."
    → User confirms or corrects

  User controls:
    → "Clarify" — plain-English explanation of what the question is asking and why
    → "Rephrase" — ask for the same thing differently
    → "Example" — show a cautious draft based on existing evidence
    → "Defer" — skip this item and come back later
    → "Approve" — mark item as complete and move on

When all gaps are filled or deferred → interview complete
```

### Scoring

- **Completeness score** — fraction of STAR fields filled per item (0–4 gaps → 0–1.0)
- **Answer quality score** — assessed on: specificity, ownership, outcome_strength, metric_usefulness, groundedness
- **Overall profile score** — combines completeness (40%) and answer quality (60%)

### How it feeds into applications

Once evidence items are approved, the cover letter workflow uses them as grounded talking points rather than raw resume bullets. Approved items are preferred over draft items when both cover the same requirement. Items marked ★ (with quantified metrics) get cited directly in cover letters.

---

## Fit evaluation and gap analysis

When a job is prepared, Envoy runs a multi-step AI pipeline to decide whether to proceed and what to write.

```
JD parsing (parse_jd node):
  → LLM splits the job description into three buckets:
      must_have   — skills, experience, qualifications the candidate must bring
      duties      — what the person will do day-to-day (not used for fit scoring)
      nice_to_have — explicitly optional or preferred items
  → Also extracts contact_name if found near application-related language
  → Results are cached in SQLite so repeat preparation does not re-call the LLM

Evidence matching (match_profile node):
  → LLM maps each must-have requirement to the closest item from the candidate's profile
  → Rates each match as STRONG, MODERATE, or WEAK
  → Prefers approved evidence items over draft items
  → Prefers items with quantified metrics (★) and cites the number directly
  → Always finds a match — assessment happens next, not here

Fit evaluation (evaluate_and_write node — runs in parallel with draft writing):
  → LLM scores overall fit: 0.0–1.0
      fit_score >= 0.5 → suitable → proceed to write cover letter
      fit_score < 0.5  → not_suitable → store gaps, skip letter, state = unsuitable
  → gaps: up to 3 must-have requirements the candidate clearly cannot cover
      (nice-to-have items never appear in gaps)

Cover letter (evaluate_and_write node — concurrent with fit eval):
  → Written speculatively while fit is being evaluated (saves ~10s per job)
  → Input: only STRONG and MODERATE evidence lines, plus candidate's voice profile
  → 3 short paragraphs, ~320 words, ghostwritten to sound like the candidate
  → Every claim is anchored to an outcome or metric — no activity-only sentences
  → Discarded if fit_score < 0.5
```

### What the portal shows after prepare

The **Review Desk** displays the evidence table so you can see exactly why a job was rated suitable or unsuitable before approving:

```
[STRONG]   5+ years Python experience  →  Built data pipelines at Envoy (★ cut latency 4h→30min)
[MODERATE] dbt experience              →  Used dbt within Databricks at previous role
[WEAK]     Salesforce CRM              →  General CRM exposure; no direct Salesforce work
```

Gaps (missing must-haves) are shown with short labels: e.g. `"Salesforce CRM"`, `"AWS certification"`. These help you decide whether to discard or approve despite the gap.

---

## Setup

### Prerequisites

- Python 3.12+
- Node 20+
- Google Chrome (Playwright uses the system Chrome or downloads Chromium)
- A SEEK account (logged in to Chrome)

### Environment

Create `agent/.env`:

```env
INTERNAL_AUTH_SECRET=your-secret-here
OPENAI_COMPAT_BASE_URL=http://127.0.0.1:8123/v1   # or OpenAI/Anthropic compatible endpoint
OPENAI_COMPAT_API_KEY=your-key
OPENAI_COMPAT_MODEL=gpt-4o
```

Create `tools/.env`:

```env
INTERNAL_AUTH_SECRET=your-secret-here   # must match agent
```

### Your profile

Create `profile/your_name_profile.json` with your work history, skills, and preferences. This is used by the AI to personalise cover letters and answer screening questions. See `profile/example_profile.json` for the schema.

### Run

```bash
# One-liner (checks prerequisites, creates .env files, starts all three services)
./start.sh          # Mac/Linux
.\start.ps1         # Windows

# Or start each service manually:

# Agent (port 8000)
cd agent && pip install -e . && uvicorn app.main:app --port 8000 --reload

# Tools (port 4320)
cd tools && npm install && npm run dev

# Portal (port 5200)
cd portal && npm install && npm run dev
```

Open http://localhost:5200 — the setup page walks you through LLM configuration and SEEK login on first run.

---

## Project structure

```
agent/
  app/
    api/           HTTP routes (jobs, applications, workflows, profile-interview, setup)
    worker/        Background queue worker
    workflows/     LangGraph graphs (search, prepare, apply, profile_interview)
    services/      AI services (cover letter, answer field, fit eval, external_apply_policy)
    persistence/   SQLite repositories + migrations
    state/         Pydantic models (apply, jobs, external_apply, canonical_profile, …)
    tools/         Tools service client wrappers
    policy/        Job filter rules

tools/
  src/
    browser/       Playwright session management, inspector, fill routes
    providers/     SEEK, Indeed, LinkedIn search/detail parsers
  test/
    fixtures/      Captured HTML for offline parser tests

portal/
  src/
    pages/         SetupPage, JobsPage, ReviewDeskPage, ApplyPage,
                   QueuePage, HistoryPage, DriftPage, ReviewPage, SettingsPage
    api/           Typed fetch wrappers
```

---

## Running tests

```bash
# Agent
cd agent && python -m pytest tests/ -v

# Tools
cd tools && npm test
```
