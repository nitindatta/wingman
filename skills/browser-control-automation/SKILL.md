---
name: browser-control-automation
description: Use when designing, implementing, or debugging browser-controlled automation agents, especially form-filling, external application flows, DOM observation, upload controls, login/identity gates, LLM planners, policy validation, and user-input escalation behavior.
---

# Browser Control Automation

## Core Rule

Treat browser automation as an evidence pipeline, not a selector script. Observe the page, classify the state, choose one safe action, execute it, then re-observe. If behavior is surprising, read logs and capture the DOM before changing code.

## Control Loop

Use this loop for every browser-controlled workflow:

1. Capture a structured observation: URL, title, visible text, fields, uploads, buttons, links, validation messages, disabled states, current values, nearby text, and page type.
2. Classify the phase: login, identity verification, profile form, screening questions, document upload, review, final submit, error, or unknown.
3. Build a plan from observed element IDs only. Never act on an element that was not observed.
4. Gate the plan with deterministic policy before execution.
5. Execute one action or a small batch of field actions.
6. Re-observe after field changes, uploads, navigation, or validation errors.
7. Stop at final submit or sensitive account-access gates unless explicit approval exists.

Avoid designing flows where the LLM directly operates the browser. Let the LLM propose actions; let browser tooling execute validated actions.

## Observation Caveats

DOM snapshots must include more than visible labels:

- Accessible names can differ from visible text because of `aria-label`, hidden spans, icons, or injected Unicode.
- SPA frameworks can insert zero-width characters; normalize text before matching.
- File inputs are often hidden behind styled buttons. Observe hidden file inputs when a visible upload widget clearly owns them.
- Nearby text should be taken from the enclosing question/container, not just the inner button. A nested upload button may say "Upload file" while the real label is "Please attach your resume".
- Current values matter. Skip fields that already contain useful values unless they are invalid.
- Validation text can be stale. A page may still display "resume required" after upload while the required upload control is gone. Cross-check observed controls and recent successful actions before stopping.
- Disabled buttons are state signals. Capture them, but do not click them.

When a browser interaction fails, capture a fresh DOM snapshot before writing a selector fix.

## Selectors And Actions

Prefer robust action targets:

- Use observed stable element IDs from the automation layer, not raw CSS guessed by the planner.
- For buttons, try role/name first, then normalized visible text, then direct DOM fallback.
- Strip zero-width Unicode and normalize whitespace before matching labels.
- Keep browser integration separate from planning and policy code.
- Use direct DOM fallback only after confirming the observed element is the intended action.

For repeated platforms, encode provider-specific quirks in the observer/executor, not in the LLM prompt.

## Planner Prompt Design

Tell the planner exactly what it may use:

- Use only observed element IDs.
- Use only available facts, approved memory, or explicit recent user answers for factual values.
- Allow grounded narrative answers when profile evidence supports them.
- Ask the user when the answer is missing, sensitive, ambiguous, or would require inventing an exact unsupported claim.
- Do not mix field actions with navigation in the same page plan unless the executor is designed for it.
- Never final-submit; return a final-submit gate.

Do not rely on vague instructions like "be autonomous". Autonomy needs explicit categories:

- Safe to fill from profile or approved memory.
- Safe to infer from grounded profile evidence.
- Safe to click because current page is resolved.
- Must ask user.
- Must stop for approval.

## Application Questions

Open-text questions are not automatically user-input blockers. Distinguish the kind of question:

- Motivation and fit: answer from profile, role context, and job context.
- Experience with a named skill/tool: answer from profile evidence if present.
- Leadership or project examples: answer from profile evidence if present.
- Exact years, exact headcount, certifications, clearances, or regulated declarations: use only evidenced values; otherwise ask or use clearly approximate qualitative wording if acceptable.
- Unevidenced yes/no claims, such as "Do you use Power BI?" when the profile does not say so: ask the user rather than invent.

Good planner instruction:

```text
For career narrative and profile-grounded experience questions, draft concise answers grounded only in available facts and page/job context. Use evidenced numbers when present; otherwise use careful qualitative wording instead of inventing exact years, counts, certifications, or usage claims.
```

## File Uploads

Uploads are fragile and need strict target matching:

- Resume/CV files may only go to observed resume/CV upload controls.
- Cover letters may only go to observed cover-letter upload controls or cover-letter text areas.
- Optional additional-document fields should be skipped unless a matching approved file exists.
- If a generated cover letter exists as text and the control is a text area, paste it.
- If a generated cover letter exists as text and the control is a file upload, write a per-application text file and upload that file.
- After upload success, re-observe. If required upload controls disappear and only optional uploads remain, continue even if stale text still mentions a missing document.

Validate file actions by both target label and configured file path. Never upload a resume to a generic "additional document" field just because it is the only file input left.

## Login And Identity Gates

Treat identity verification and account access as sensitive:

- Safe: enter known email, click a clearly chosen emailed one-time-code path when policy allows that default.
- Ask user: choose between passkey, password, emailed code, social login, or account creation when no portal-specific method is established.
- Never create passwords, passkeys, accounts, or use social login without explicit user direction.
- UI prompts for identity choices should be text-choice prompts, not password-value prompts, even when the option label contains "Create Password".
- If the page asks for a one-time code, ask for the code only when the page is actually on the code-entry step. Do not infer a code prompt from unrelated upload or form pages.

Persist portal-specific login decisions only when the user explicitly establishes them.

## User Input Escalation

Ask the user for one field per question. Do not bundle multiple required answers into one prompt.

Ask when:

- The answer is sensitive or regulated.
- The answer is an unevidenced factual self-report.
- The planner confidence is below the configured action threshold and no deterministic safe action exists.
- The page presents account-access choices with no established preference.
- The available facts conflict with the observed page.

Do not ask merely because a question is written in first person. Most application questions are; the real test is whether profile evidence supports a truthful answer.

## Policy Gate

Use policy as a deterministic validator, not as another planner:

- Reject unknown element IDs.
- Pause unapproved value sources.
- Allow `inferred` only for well-defined grounded narrative/experience text answers.
- Validate profile-sourced values against known profile facts.
- Validate upload target type and file path.
- Pause final submit.
- Pause optional consents unless configured consent defaults exist.
- Reject planner `stop_failed` only after confirming no safe observed action exists.

Prompt changes and policy changes must move together. If the prompt allows a new action class but policy still blocks it, the UI will regress into unnecessary approval prompts.

## Recovery Patterns

When a workflow gets stuck:

- Read the latest planner transcript and agent log.
- Compare requested action, policy decision, executor result, and next observation.
- Identify whether the root cause is observation, prompt, policy, executor, or stale page state.
- Fix the layer that owns the wrong assumption.
- Add a regression test at that layer and, when relevant, an end-to-end harness test.

Common root causes:

- Observer labeled the wrong container.
- Planner asked user because prompt category was too narrow.
- Policy rejected a newly valid planner source.
- Executor selector relied on accessible name but the page rendered different text.
- Page validation message was stale after a successful upload.
- Optional fields were treated as required.
- Identity option prompts were misclassified as password entry.

## Test Matrix

For browser-control systems, include tests for:

- Hidden upload inputs with visible upload widgets.
- Nested question containers where the inner button label is generic.
- Resume upload, cover-letter upload, cover-letter text paste, and optional additional documents.
- Stale validation text after successful upload.
- Planner prompt payload contains enough profile evidence for grounded answers.
- Policy allows inferred grounded narrative answers and blocks unsupported claims.
- Identity choice prompts with "Create Password" remain normal text-choice prompts.
- Disabled navigation buttons and final-submit gates pause correctly.
- Zero-width Unicode and accessible-name mismatches in button text.

When changing behavior, run the narrow failing test first, then the adjacent policy/planner/executor suite.
