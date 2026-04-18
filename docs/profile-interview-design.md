# Envoy Profile Interview Design

Status: Draft for review
Last updated: 2026-04-17
Owner: Nitin

Related:
- [`design.md`](./design.md)
- [`requirements.md`](./requirements.md)

## 1. Purpose

Envoy should not stop at parsing resumes and generating cover letters. The real product value is helping the user extract strong, truthful, job-relevant evidence from their own experience.

The profile interview workflow is the system that does that.

Its job is to turn:

- a noisy uploaded resume, LinkedIn export, or profile JSON
- rough user answers
- partial evidence already captured in the canonical profile

into:

- approved STAR-style evidence items
- strong voice samples
- a canonical profile that downstream workflows can trust

This is the part of Envoy that should feel like working with a professional resume writer or interview coach.

## 2. Design Principles

1. Deterministic first, agentic second.
   - Parsing, storage, and provenance stay deterministic.
   - Interviewing, interpretation, and refinement are agentic.

2. The LLM is a coach, not a source of truth.
   - It can ask sharper questions.
   - It can rewrite rough answers into a cleaner draft.
   - It cannot invent facts, metrics, or achievements.

3. One question at a time.
   - A professional resume writer does not dump a form on the user.
   - Envoy should ask the single highest-value next question.

4. User confirmation is required before promotion to canonical truth.
   - Suggested rewrites are drafts.
   - Canonical profile entries are approved user-facing records.

5. Completeness beats verbosity.
   - The goal is to capture the key signals:
     - context
     - ownership
     - action
     - result
     - metric
   - Not every item needs a perfect essay.

## 3. Runtime Position

This workflow belongs in Python and should be implemented as a separate LangGraph graph.

It should sit between:

- raw profile ingestion
- cover letter generation

Flow:

`upload -> extract -> raw_profile -> profile_interview -> canonical_profile -> cover_letter/apply`

Search, prepare, and apply stay separate workflows. The profile interview is its own bounded workflow with pause/resume.

## 4. Why LangGraph Fits

This workflow needs:

- checkpointed state
- human-in-the-loop pauses
- iterative refinement
- controlled routing
- resumability across page reloads and service restarts

This is a strong fit for LangGraph, but it should be a bounded workflow, not an open-ended autonomous agent.

Good use of LangGraph here:

- planner node
- interviewer node
- rewrite node
- critic node
- interrupt/resume with user input
- route-to-next-step logic

Bad use here:

- unconstrained tool use
- self-directed exploration
- free-form long-horizon autonomy

## 5. Workflow Goal

Each evidence item should end in one of these states:

- `approved`
- `good_enough`
- `needs_clarification`
- `skipped`

Approved or good-enough items should contain:

- `situation`
- `task`
- `action`
- `outcome`
- `metrics`
- `skills`
- `domain`
- `proof_points`
- `confidence`

Plus an optional:

- `voice_sample_hint`

## 6. Agent Capabilities

The interview workflow should operate with these playbooks.

### 6.1 Profile Interviewer

Responsibilities:

- ask one focused next question
- detect vagueness
- probe for ownership, scope, and result
- avoid generic repeated prompts

Typical prompts:

- "What problem were you brought in to solve?"
- "What part of this did you personally own?"
- "What changed after your work was delivered?"
- "Do you have a number, range, or scale you can attach to that?"

### 6.2 STAR Rewriter

Responsibilities:

- convert rough user answers into a structured evidence draft
- preserve meaning while improving clarity
- avoid adding unsupported claims

### 6.3 Evidence Critic

Responsibilities:

- assess whether an item is still weak
- identify the highest-value missing signal
- decide whether another question is needed

Typical missing-signal labels:

- `missing_situation`
- `missing_ownership`
- `missing_result`
- `missing_metric`
- `too_generic`
- `needs_user_confirmation`

### 6.4 Job Relevance Selector

Responsibilities:

- prioritize evidence based on target role family
- select the strongest items for data engineering vs AI vs architecture jobs

### 6.5 Voice Editor

Responsibilities:

- keep polished text aligned with the user's tone
- avoid recruiter cliches and generic AI phrasing

### 6.6 Truth Guard

Responsibilities:

- reject unsupported claims
- mark approximate figures as approximate
- force confirmation before storing polished factual statements

## 7. State Model

Create a new state model, for example:

```python
class ProfileInterviewState(BaseModel):
    session_id: str
    profile_source_id: str
    canonical_profile_path: str
    mode: Literal["onboarding", "targeted_role"]

    candidate_item_id: str | None = None
    candidate_item_type: Literal["experience", "project", "voice", "summary"] | None = None

    draft_item: CanonicalEvidenceItem | None = None
    open_gaps: list[str] = Field(default_factory=list)
    asked_questions: list[str] = Field(default_factory=list)
    turn_count: int = 0

    last_question_id: str | None = None
    last_question: str | None = None
    last_user_answer: str | None = None
    last_rewrite: str | None = None

    completeness_score: float = 0.0
    confidence: Literal["draft", "user_confirmed", "good_enough", "approved"] = "draft"

    status: Literal[
        "selecting",
        "asking",
        "waiting_for_user",
        "rewriting",
        "reviewing",
        "approved",
        "skipped",
        "completed",
    ] = "selecting"
```

Important:

- keep the state small and serializable
- never embed raw document blobs in graph state
- refer to raw profile items by ID

## 8. Node Graph

Recommended graph:

### 8.1 `select_candidate_item`

Input:

- raw profile
- canonical profile
- prior interview progress

Responsibilities:

- pick the next item to refine
- prefer high-value items with missing STAR structure
- skip already approved items

Output:

- `candidate_item_id`
- `candidate_item_type`
- initial `draft_item`

### 8.2 `diagnose_gaps`

Responsibilities:

- inspect the current draft item
- determine what is missing
- rank the missing signals

Output:

- `open_gaps`
- `completeness_score`

### 8.3 `plan_next_question`

Responsibilities:

- choose the one best next question
- adapt wording based on:
  - what is already known
  - what the user previously answered
  - whether the user seems unsure

Output:

- `last_question_id`
- `last_question`

### 8.4 `interrupt_for_user`

Responsibilities:

- pause and wait for user answer
- expose:
  - question
  - current draft
  - missing signals

This is the main interrupt point.

### 8.5 `interpret_answer`

Responsibilities:

- parse rough user input into structured meaning
- identify:
  - direct facts
  - approximate facts
  - unclear statements

Output:

- normalized answer payload

### 8.6 `rewrite_draft_item`

Responsibilities:

- update the evidence item with interpreted answer
- rewrite affected STAR fields
- preserve previous confirmed fields

Output:

- updated `draft_item`

### 8.7 `critique_draft_item`

Responsibilities:

- score quality
- decide if item is:
  - still weak
  - good enough
  - ready for review

Output:

- updated `open_gaps`
- updated `completeness_score`
- routing decision

### 8.8 `approve_item`

Responsibilities:

- mark item approved or good-enough
- merge it into canonical profile
- persist interview artifacts

### 8.9 `complete_session`

Responsibilities:

- close session when no more priority items remain

## 9. Routing Logic

Suggested routing:

```text
select_candidate_item
  -> diagnose_gaps
  -> if no candidate: complete_session

diagnose_gaps
  -> if item already strong: approve_item
  -> else: plan_next_question

plan_next_question
  -> interrupt_for_user

interrupt_for_user
  -> interpret_answer

interpret_answer
  -> rewrite_draft_item

rewrite_draft_item
  -> critique_draft_item

critique_draft_item
  -> if approved/good enough: approve_item
  -> if too many turns or user uncertain: review state
  -> else: plan_next_question

approve_item
  -> select_candidate_item
```

## 10. Human-in-the-Loop Model

The user should not be trapped in a chat transcript.

The portal should show:

1. Current item being refined
2. The single current question
3. A compact answer box
4. The evolving evidence draft
5. What is still missing
6. Actions:
   - `Save answer`
   - `Skip for now`
   - `Approve draft`
   - `Edit draft directly`

This should feel like a guided workshop, not a chatbot.

## 11. Persistence Design

Add dedicated persistence for interview sessions.

### 11.1 Tables

```text
profile_interview_sessions
  id (pk)
  source_profile_path
  target_profile_path
  mode
  status
  current_item_id
  current_item_type
  started_at
  updated_at
  finished_at

profile_interview_turns
  id (pk)
  session_id (fk)
  item_id
  question_id
  question_text
  user_answer
  interpreted_answer_json
  created_at

profile_interview_item_drafts
  id (pk)
  session_id (fk)
  item_id
  version
  status                  -- draft | needs_review | good_enough | approved | skipped
  completeness_score
  item_json
  gap_summary_json
  created_at

profile_interview_item_reviews
  id (pk)
  session_id (fk)
  item_id
  review_type             -- approve | skip | edit
  actor                   -- user | agent
  payload_json
  created_at
```

### 11.2 Why separate tables

This avoids overloading:

- `drafts`
- `question_answers`
- LangGraph checkpoint tables

The interview loop is its own domain concept and should be queryable independently.

## 12. API Design

Add new API surface, for example:

```text
POST   /api/profile-interview/start
GET    /api/profile-interview/{session_id}
POST   /api/profile-interview/{session_id}/answer
POST   /api/profile-interview/{session_id}/approve
POST   /api/profile-interview/{session_id}/skip
POST   /api/profile-interview/{session_id}/edit-draft
GET    /api/profile-interview/{session_id}/items
```

### 12.1 Start

Input:

- source profile reference
- canonical profile reference
- optional target role family

Output:

- `session_id`
- first selected item
- first question or approval-ready draft

### 12.2 Answer

Input:

- `session_id`
- `question_id`
- `answer`

Output:

- updated draft item
- next question or review-ready item

### 12.3 Approve

Input:

- `session_id`
- `item_id`

Output:

- updated canonical profile status
- next item

## 13. Prompt Strategy

Use multiple prompts with strict contracts instead of one large prompt.

### 13.1 Gap Diagnosis Prompt

Input:

- draft item
- current STAR fields
- raw source excerpts

Output:

- ranked missing signals
- completeness score

### 13.2 Question Planning Prompt

Input:

- draft item
- open gaps
- previous turns

Output:

- one best next question
- reason for asking it

### 13.3 Answer Interpretation Prompt

Input:

- user answer
- current question
- current draft item

Output:

- structured interpretation:
  - facts
  - approximate facts
  - ambiguities

### 13.4 Rewrite Prompt

Input:

- prior draft item
- interpreted answer

Output:

- updated draft item only

### 13.5 Critique Prompt

Input:

- updated draft item

Output:

- quality judgment
- remaining gap labels
- ready-for-approval boolean

## 14. Guardrails

The workflow must not:

- invent metrics
- turn guessed impact into fact
- promote unconfirmed rewrites directly into canonical truth
- ask endless repetitive questions
- lose provenance back to raw profile text

Rules:

1. If a metric is missing, ask for a metric.
2. If the user does not know, allow approximate scale but mark it as approximate.
3. If even approximate scale is unavailable, keep the item but lower confidence.
4. Every approved item should keep source excerpts or raw-item references for auditability.

## 15. When to Use LLM vs Deterministic Logic

### Keep deterministic

- document upload
- Docling parsing
- raw profile persistence
- canonical profile serialization
- answer save operations
- path routing and state persistence

### Use LLM

- choosing the next question
- interpreting rough human answers
- rewriting draft evidence
- critiquing evidence quality
- later role-specific prioritization

## 16. Initial Rollout Plan

### Phase A: Single-item interview loop

Build:

- one session
- one item at a time
- one question at a time
- rewrite + save

No advanced targeting yet.

### Phase B: Approval and queue

Build:

- item statuses
- approve / skip / edit controls
- evidence queue in portal

### Phase C: Role-aware prioritization

Build:

- choose next evidence item based on target role family
- ask different questions for architecture vs data engineering vs AI

### Phase D: Voice and learning

Build:

- better voice-preserving rewrites
- learn from accepted user edits

## 17. Open Questions

1. Should one interview session span the whole profile, or should each evidence item have its own sub-session?
   - Recommendation: one top-level session, item-level draft history.

2. Should the interviewer be role-aware during onboarding, or only after the user selects a job family?
   - Recommendation: role-agnostic first, role-aware as an optional refinement pass.

3. Should the user approve field-by-field or whole-item?
   - Recommendation: whole-item approval, field-level editability.

4. Should voice sampling be part of the same workflow?
   - Recommendation: yes, but only after 2-3 evidence items are approved.

## 18. Recommendation

Build this as Envoy's next major workflow.

If raw parsing gives us source material and cover-letter generation gives us output polish, the profile interview is the system that creates the actual value in between.

This is where Envoy stops being "an application bot" and becomes "a trustworthy career co-pilot."
