# Agent Guidelines

## Engineering Principles

- Create unit tests first for new behavior and meaningful bug fixes where practical.
- Keep the design modular and documented.
- Follow DRY and SOLID principles.
- Prefer small, composable modules with clear responsibilities.

## Documentation Style

- Write code comments only where they add real value.
- Do not add comments for trivial conditions or obvious assignments.
- Keep documentation concise and high signal.
- Favor architecture and module-level documentation over noisy inline commentary.

## Implementation Expectations

- Avoid duplicating provider logic, browser logic, and orchestration logic across layers.
- Keep browser integration, provider behavior, and agent orchestration as separate concerns.
- When adding new modules, make their contract explicit and easy to reuse.
- Preserve maintainability over quick one-off patches.
- Don't build too many abstraction, it should be easily reviewable by humans.

## Debugging Protocol

Before writing any fix, collect enough evidence to understand the root cause. Guessing
is expensive — each wrong fix adds latency and introduces new bugs.

**Evidence first:**

1. **Read the logs** before touching code. The log message usually tells you exactly
   which node, which status, and which value was wrong.
2. **Capture a DOM snapshot** whenever a browser interaction fails or behaves
   unexpectedly. Selectors that worked in development can break when the live page
   renders differently — always verify against real page structure before writing
   selectors.
3. **Trace the full execution path**, not just the error line. A symptom (e.g. a
   click timeout) often has its root cause several steps earlier (e.g. a
   misclassification that set the wrong action label).

**Root cause before fix:**

- State "what is actually happening" and "what should be happening" before writing
  any code. If you cannot state both clearly, keep collecting evidence.
- When a condition guards a branch, verify the condition holds for ALL cases it is
  meant to cover — not just the case you first observed. A guard like
  `len(fields) == 0` fails silently when the real signal is in a different field
  (e.g. visible actions).
- Check for off-by-one reasoning: if detection logic uses one attribute (e.g. field
  count), but the actual distinguishing signal is a different attribute (e.g. button
  label), the fix must use the right attribute, not refine the wrong one.

**Selector robustness:**

- `getByRole` uses the ARIA accessible-name algorithm, which may differ from visible
  text content. When a button cannot be found by role/name, check whether its
  accessible name differs from its textContent (e.g. due to `aria-label`, icon
  children, or invisible Unicode characters).
- Always add a text-content fallback and a direct DOM fallback when clicking
  buttons that may not have a stable accessible name.
- Zero-width Unicode characters (`\u2060`, `\u200b`, etc.) appear in SPA-rendered
  button text and must be stripped before matching.

**Iterating:**

- After each fix, re-run the exact failing scenario and check logs before claiming
  success. Do not declare a fix done based on static analysis alone.
- If a fix changes behavior but a new error appears, treat the new error as a
  separate root cause — do not keep patching the same code path without
  re-reading the evidence.
