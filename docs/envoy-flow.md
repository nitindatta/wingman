# Envoy End-to-End Flow

Envoy is a local, review-first job application co-pilot. It turns a user's resume/profile into a canonical evidence store, uses that evidence to assess jobs, prepares grounded cover letters, and automates application forms only after human review.

## 1. System Overview

```mermaid
flowchart LR
    User["User"] --> Portal["Portal<br/>React TypeScript"]
    Portal --> AgentApi["Agent API<br/>FastAPI"]
    AgentApi --> LangGraph["LangGraph workflows<br/>search, profile interview, prepare, apply"]
    LangGraph --> SQLite[("SQLite<br/>golden operational state")]
    LangGraph --> ProfileFiles["Profile files<br/>raw + canonical mirrors"]
    LangGraph --> OpenAI["LLM provider<br/>reasoning, interview, drafting"]
    LangGraph --> ToolsApi["Tools API<br/>Fastify"]
    ToolsApi --> Browser["Playwright browser<br/>SEEK / employer portals"]

    SQLite --> AgentApi
    ProfileFiles --> LangGraph
    Browser --> ToolsApi
    ToolsApi --> LangGraph
    AgentApi --> Portal
    Portal --> User
```

Primary ownership:

- `portal/` owns UI, review, and user actions.
- `agent/` owns orchestration, state, LLM reasoning, persistence, and API contracts.
- `tools/` owns browser automation and provider-specific page interaction.
- SQLite is the golden operational source for sessions, applications, queues, drafts, fit metadata, and persisted profile state.
- Profile JSON files are mirrors/artifacts for inspectability and compatibility.

## 2. Profile Creation And Enrichment

```mermaid
flowchart TD
    Upload["User uploads resume/profile<br/>PDF, DOCX, JSON, text"] --> ParseDoc["Parse document<br/>Docling / fallback extractors"]
    ParseDoc --> RawProfile["Save raw profile<br/>non-canonical schema"]
    RawProfile --> TargetBuild["Build initial canonical profile<br/>structured evidence items"]
    TargetBuild --> ProfileState[("Persist profile state<br/>SQLite golden source")]
    ProfileState --> CanonicalMirror["Mirror canonical profile JSON"]

    ProfileState --> SelectItem["Select experience/project item"]
    SelectItem --> DiagnoseGap["Diagnose weakest STAR gap<br/>situation, task, action, outcome, metrics"]
    DiagnoseGap --> AskQuestion["Ask one coaching question"]
    AskQuestion --> UserAnswer["User answers, asks clarification,<br/>requests example, or says use this"]

    UserAnswer --> Intent{"Conversation intent"}
    Intent -->|"answer"| Interpret["Interpret answer<br/>extract STAR + voice signals"]
    Intent -->|"clarify / example / rephrase"| Coach["Coach response<br/>explain, example, rephrase"]
    Coach --> AskQuestion

    Interpret --> Assess["Score answer quality<br/>LLM assessment during interview"]
    Assess --> SaveDraft["Save draft evidence item<br/>DB first"]
    SaveDraft --> Reflect["Reflect back STAR version<br/>for approval or edits"]
    Reflect --> Confirm{"User confirms?"}
    Confirm -->|"approve / use this and continue"| Commit["Commit evidence item<br/>DB then canonical mirror"]
    Confirm -->|"edit"| UserAnswer
    Confirm -->|"finish for now"| Commit

    Commit --> MoreGaps{"More gaps in selected item?"}
    MoreGaps -->|"yes"| DiagnoseGap
    MoreGaps -->|"no"| NextItem{"Next incomplete item?"}
    NextItem -->|"yes"| SelectItem
    NextItem -->|"no"| Complete["Profile interview complete enough"]
```

Important behavior:

- The interview is intentionally conversational, not a static form.
- The user can select any experience/project and rerun the interview on it.
- Saved STAR answers go to SQLite first, then canonical JSON is updated as a mirror.
- The LLM is used during interview for coaching, interpretation, STAR synthesis, voice extraction, and answer-quality scoring.
- The deterministic completeness score and LLM answer-quality score are separate signals.

## 3. Job Discovery And Prepare Flow

```mermaid
flowchart TD
    SearchRequest["User starts job search"] --> SearchGraph["Search workflow<br/>LangGraph"]
    SearchGraph --> SearchTool["Tools API search provider"]
    SearchTool --> PersistJobs[("Persist jobs<br/>SQLite")]
    PersistJobs --> ReviewList["Portal job list"]

    ReviewList --> QueuePrepare["User queues job"]
    QueuePrepare --> AppShell["Create preparing application row"]
    AppShell --> WorkQueue[("Queue item<br/>prepare")]
    WorkQueue --> Worker["Agent worker claims queue item"]

    Worker --> FetchDetail["Fetch full job detail"]
    FetchDetail --> JDAnalysis["Parse/cache JD analysis<br/>must-have, duties, nice-to-have"]
    JDAnalysis --> EvidenceCatalog["Build evidence catalog<br/>from canonical profile"]
    EvidenceCatalog --> SelectEvidence["Select strongest evidence per requirement"]
    SelectEvidence --> EvaluateFit["Evaluate fit<br/>score, gaps, suitable/not suitable"]

    EvaluateFit --> FitGate{"Suitable?"}
    FitGate -->|"no"| SaveNotFit["Save unsuitable application<br/>fit_score, gaps_json, match_evidence"]
    SaveNotFit --> NotFitUI["Review Desk<br/>not-fit analysis + profile improvement hints"]

    FitGate -->|"yes"| PlanLetter["Plan cover letter<br/>STAR-backed narrative"]
    PlanLetter --> DraftLetter["Write draft<br/>using voice profile + evidence"]
    DraftLetter --> Critique["Critique/revise draft"]
    Critique --> SavePrepared["Save prepared application<br/>cover letter + match evidence + fit score"]
    SavePrepared --> ReviewDesk["Review Desk<br/>human approval"]
```

Fit metadata shown in the UI:

- `fit_score`: exact score from the prepare graph when available.
- `gaps_json`: blocking requirements that made the role weak or unsuitable.
- `match_evidence`: `[STRONG|MODERATE|WEAK] requirement -> evidence` lines.
- Review Desk can render this without an extra LLM call because the prepare workflow already produced it.

## 4. Review And Apply Flow

```mermaid
flowchart TD
    ReviewDesk["Review Desk"] --> Decision{"Human decision"}
    Decision -->|"discard"| Discard["Mark application discarded"]
    Decision -->|"edit cover letter"| EditDraft["Update draft in SQLite"]
    EditDraft --> ReviewDesk
    Decision -->|"approve"| Approved["Mark approved"]

    Approved --> StartApply["User starts apply"]
    StartApply --> ApplyQueue[("Queue item<br/>apply")]
    ApplyQueue --> ApplyGraph["Apply workflow<br/>LangGraph"]
    ApplyGraph --> InspectPage["Inspect current form/page<br/>Tools + Playwright"]
    InspectPage --> ClassifyPage["Classify page and fields"]
    ClassifyPage --> FillFields["Fill safe known fields"]
    FillFields --> NeedsReview{"Need user input<br/>or low confidence?"}

    NeedsReview -->|"yes"| Pause["Pause application<br/>save state/checkpoint"]
    Pause --> PortalPrompt["Portal asks user for missing values"]
    PortalPrompt --> ResumeApply["User submits correction"]
    ResumeApply --> ApplyGraph

    NeedsReview -->|"no"| Continue["Click continue / next action"]
    Continue --> MoreSteps{"More steps?"}
    MoreSteps -->|"yes"| InspectPage
    MoreSteps -->|"ready to submit"| SubmitGate["Final submit gate"]
    SubmitGate --> UserSubmit{"User confirms submit?"}
    UserSubmit -->|"no"| Pause
    UserSubmit -->|"yes"| Submit["Submit application"]
    Submit --> Applied["Mark applied"]
```

Apply workflow principles:

- Browser/provider logic stays in `tools/`.
- LangGraph owns state, decisions, pauses, and resumes.
- Portal owns human confirmation.
- SQLite keeps queue state, application state, drafts, apply-step state, and user-corrected answers.

## 5. LLM Call Map

```mermaid
flowchart LR
    ProfileInterview["Profile interview"] --> PIUses["LLM used for<br/>questioning, clarification, examples,<br/>STAR extraction, voice signals,<br/>answer scoring"]
    Prepare["Prepare application"] --> PrepUses["LLM used for<br/>JD parsing when cache misses,<br/>evidence selection, letter planning,<br/>drafting, critique/revision"]
    ReviewUI["Review Desk not-fit panel"] --> NoLLM["No LLM call<br/>renders persisted score, gaps, evidence"]
    Apply["Apply workflow"] --> ApplyUses["LLM only when needed<br/>field reasoning / uncertain answers<br/>bounded by pause and review"]
```

The important cost boundary is that Review Desk analysis should not call the LLM just because a user opens a job. It reads persisted outputs from the prepare workflow.

## 6. Data Flow Summary

```mermaid
flowchart TD
    Resume["Resume / profile upload"] --> Raw["raw_profile"]
    Raw --> Canonical["canonical_profile"]
    Interview["Profile interview answers"] --> Canonical
    Canonical --> Evidence["evidence_items + voice_profile"]

    Job["Job detail"] --> Requirements["requirements"]
    Evidence --> Fit["fit evaluation"]
    Requirements --> Fit
    Fit -->|"not suitable"| Gaps["fit_score + gaps + weak evidence"]
    Fit -->|"suitable"| Letter["cover letter draft"]

    Gaps --> Review["Review Desk"]
    Letter --> Review
    Review --> Apply["Apply automation"]
```

Downstream quality depends most on canonical profile evidence quality. Weak STAR items produce weak fit matches, weak cover letters, and more not-fit outcomes.
