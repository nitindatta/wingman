# Autonomous Job Agent

A local, review-first autonomous job application agent. Three services in one repo:

| Service   | Path       | Stack                                             | Role                                                                 |
| --------- | ---------- | ------------------------------------------------- | -------------------------------------------------------------------- |
| Agent     | `agent/`   | Python 3.12, FastAPI, LangGraph                   | Workflow brain, AI services, persistence, portal API                 |
| Tools     | `node/`    | Node 20, Fastify, Playwright                      | Deterministic browser + provider tools. Called by Python over HTTP.  |
| Portal    | `portal/`  | React 18, Vite, TypeScript, Tailwind, shadcn/ui   | Review UI. Talks only to the Python API.                             |

## Architecture rules

1. **Agent is the brain.** All reasoning, ranking, drafting, and workflow orchestration lives in `Agent/`.
2. **Node is a dumb tool service.** It drives Chrome and parses provider DOMs. It never calls Python back.
3. **Portal only talks to Python**, at `http://127.0.0.1:8005/api/...`. The portal never knows Node exists.
4. Data flows: `portal → Agent → node`. Never the other way.

See `docs/` for the full design and requirements documents, and
`.claude/agents/*.md` for the conventions enforced by the per-service
sub-agents (`python-builder`, `node-builder`, `frontend-builder`,
`parser-generator`, `principle-reviewer`).

## Quick start

Each service has its own README with full instructions.

```bash
# Python agent (FastAPI on :8005)
cd agent && uv sync && uv run uvicorn app.main:app --host 127.0.0.1 --port 8005 --reload

# Node tool service (Fastify on :8123)
cd node && pnpm install && pnpm dev

# React portal (Vite on :5173)
cd portal && pnpm install && pnpm dev
```

The Python and Node services share an internal auth secret via the
`INTERNAL_AUTH_SECRET` environment variable.

## Layout

```
python/    FastAPI + LangGraph agent service
node/      Fastify + Playwright tool service
portal/    React + Vite review UI
docs/      Design and requirements
.claude/   Sub-agent definitions and hooks
```

## Status

Seek Integrated
