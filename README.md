# Envoy

A local, review-first autonomous job application agent. Envoy searches SEEK for jobs matching your criteria, drafts personalised cover letters, fills out application forms, and asks you to confirm before submitting. Agent runs on your own machine.

---

## How it works

### Three services, one-way data flow

```
Portal (React)  →  Agent (Python)  →  Tools (Node/Playwright)
```

| Service | Path | Stack | Responsibility |
|---------|------|-------|----------------|
| **Agent** | `agent/` | Python 3.12, FastAPI, LangGraph | Orchestration, AI, persistence, HTTP API |
| **Tools** | `tools/` | Node 20, Fastify, Playwright | Browser automation and HTML parsing. Stateless — no DB |
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
- **Match Breakdown** — collapsible evidence table (STRONG / MODERATE / WEAK per requirement)
- **Cover letter** — editable textarea
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

### 7. External portals

Some SEEK jobs redirect to an external ATS (Workday, Greenhouse, Lever, etc.):

```
Worker: detects redirect to non-seek domain
  → sets state: paused, page_type: external_redirect

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
                     → paused (auth expired or external portal)
```

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
# Agent (port 8000)
cd agent && pip install -e . && uvicorn app.main:app --port 8000 --reload

# Tools (port 3001)
cd tools && npm install && npm run dev

# Portal (port 5200)
cd portal && npm install && npm run dev
```

Open http://localhost:5200

---

## Project structure

```
agent/
  app/
    api/           HTTP routes (jobs, applications, workflows)
    worker/        Background queue worker
    workflows/     LangGraph graphs (prepare, apply, search)
    services/      AI services (cover letter, answer field)
    persistence/   SQLite repositories + migrations
    state/         Pydantic models
    tools/         Tools service client wrappers
    policy/        Job filter rules

tools/
  src/
    browser/       Playwright session management, inspector, fill routes
    providers/     SEEK search parser, detail fetcher
  test/
    fixtures/      Captured HTML for offline parser tests

portal/
  src/
    pages/         JobsPage, ReviewDeskPage, HistoryPage
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
