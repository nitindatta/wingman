"""LangGraph apply workflow — Phase 3.

Nodes:
  launch        → open browser session, navigate to job, click Apply
  inspect       → inspect current form step → StepInfo
  propose       → resolve field values (profile / memory / LLM)
  gate          → HITL pause (interrupt_before pattern — portal fills approved_values)
  fill          → fill_and_continue → advance to next step
  finish        → close session, update application state

HITL pattern:
  compile(interrupt_before=["gate"]) — graph pauses BEFORE gate on every loop.
  Portal reads proposed_values from returned state, user edits.
  resume_apply calls graph.update_state() then graph.ainvoke(None, config).
  gate node runs with the updated state (approved_values and action_label already merged in).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

log = logging.getLogger("apply")

import aiosqlite
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.persistence.sqlite.applications import SqliteApplicationRepository, SqliteDraftRepository
from app.persistence.sqlite.question_cache import SqliteQuestionCacheRepository
from app.persistence.sqlite.workflow_runs import SqliteWorkflowRunRepository, SqliteBrowserSessionRepository
from app.services.answer_field import propose_field_values
from app.services.external_apply_harness import (
    EXTERNAL_USER_ANSWER_KEY,
    apply_external_user_answer,
    run_external_apply_step,
)
from app.settings import Settings
from app.state.apply import ApplyState, StepInfo
from app.tools.browser_client import (
    inspect_apply_step,
    fill_and_continue,
    close_session,
    launch_session,
    open_url,
    BrowserToolError,
)
from app.services.run_events import emit as _emit, set_node as _set_node
from app.tools.client import ToolClient, ToolServiceError


def _load_profile(settings: Settings) -> dict:
    path = settings.resolved_profile_path
    profile: dict[str, Any] = {}
    if path.exists():
        profile = json.loads(path.read_text(encoding="utf-8"))

    target_path = settings.resolved_target_profile_path
    if target_path.exists():
        canonical = json.loads(target_path.read_text(encoding="utf-8"))
        profile = _deep_merge_non_empty(profile, canonical)

    resume_path = settings.resolved_resume_path
    if resume_path is not None:
        profile["resume_path"] = str(resume_path)

    return profile


def _deep_merge_non_empty(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_non_empty(merged[key], value)
        else:
            merged[key] = value
    return merged


def _is_session_lost(env) -> bool:
    """Return True if the tools envelope signals a missing browser session."""
    return (
        env is not None
        and env.error is not None
        and env.error.type == "session_not_found"
    )


def build_apply_graph(
    settings: Settings,
    tool_client: ToolClient,
    app_repo: SqliteApplicationRepository,
    draft_repo: SqliteDraftRepository,
    run_repo: SqliteWorkflowRunRepository,
    session_repo: SqliteBrowserSessionRepository,
    db_conn: aiosqlite.Connection,
    question_cache: SqliteQuestionCacheRepository | None = None,
    workflow_run_id: str | None = None,
):
    profile = _load_profile(settings)

    def ev(event_type: str, label: str, data: dict) -> None:
        _emit(event_type, label, data, run_id=workflow_run_id)

    def _should_use_external_harness(state: ApplyState) -> bool:
        return (
            settings.external_apply_harness_enabled
            and state.current_step is not None
            and state.current_step.is_external_portal
        )

    def _status_for_external_harness(state: ApplyState) -> tuple[str, str | None]:
        external = state.external_apply
        if external is None:
            return "paused", "external_apply_unknown"
        if external.status == "failed":
            return "failed", None
        if external.status == "completed":
            return "completed", None
        if external.status == "ready_to_submit":
            return "paused", "external_apply_ready_to_submit"
        if external.status == "paused_for_approval":
            return "paused", "external_apply_needs_approval"
        if external.status == "paused_for_user":
            return "paused", "external_apply_needs_user"
        return "running", None

    # ── session recovery ───────────────────────────────────────────────────
    async def _recover_session(state: "ApplyState") -> str | None:
        """Re-launch a browser session and silently replay step_history.

        Called when the tools service has restarted and the original session_key
        is no longer valid. Replays every previously-filled step so the browser
        ends up at the same page the workflow was on before.

        Returns the new session_key, or None if recovery failed.
        """
        app = await app_repo.get(state.application_id)
        if app is None:
            log.warning("[recover] application not found: %s", state.application_id)
            return None

        provider = app.source_provider or "seek"
        try:
            new_key = await launch_session(tool_client, provider=provider)
            log.info("[recover] new session_key=%s — replaying %d history steps",
                     new_key, len(state.step_history))
        except (BrowserToolError, ToolServiceError) as exc:
            log.warning("[recover] launch_session failed: %s", exc)
            return None

        try:
            env = await tool_client.call(
                "/tools/providers/start_apply",
                {"session_key": new_key, "provider": provider, "job_url": app.source_url},
            )
            if env.status not in ("ok",):
                log.warning("[recover] start_apply returned status=%s", env.status)
                await close_session(tool_client, new_key)
                return None

            for i, entry in enumerate(state.step_history):
                filled = entry.get("filled_values", {})
                _step, fill_env = await fill_and_continue(
                    tool_client, new_key, filled, action_label="Continue"
                )
                if _is_session_lost(fill_env):
                    log.warning("[recover] session lost again during replay at step %d", i)
                    return None
                log.debug("[recover] replayed step %d/%d", i + 1, len(state.step_history))

            log.info("[recover] session recovery complete — new session_key=%s", new_key)
            return new_key

        except Exception as exc:
            log.warning("[recover] replay failed: %s", exc)
            try:
                await close_session(tool_client, new_key)
            except Exception:
                pass
            return None

    # ── launch ─────────────────────────────────────────────────────────────
    async def node_launch(state: ApplyState) -> dict[str, Any]:
        _set_node("launch")
        log.info("[launch] application_id=%s", state.application_id)
        ev("node", "launch: starting apply workflow", {
            "application_id": state.application_id,
            "external_start_url": state.external_start_url,
        })
        app = await app_repo.get(state.application_id)
        if app is None:
            log.warning("[launch] application not found: %s", state.application_id)
            ev("node", "launch: ERROR — application not found", {"application_id": state.application_id})
            return {"status": "failed", "error": f"application {state.application_id} not found"}

        provider = app.source_provider or "seek"
        ev("node", f"launch: opening browser session ({provider})", {
            "provider": provider,
            "job_title": app.title if hasattr(app, "title") else "",
            "job_url": app.source_url,
        })
        try:
            session_key = await launch_session(tool_client, provider=provider)
            log.info("[launch] session_key=%s url=%s", session_key, app.source_url)
            ev("node", f"launch: browser session opened — navigating to job", {
                "session_key": session_key,
                "provider": provider,
                "job_url": app.source_url,
            })
        except (BrowserToolError, ToolServiceError) as exc:
            log.error("[launch] launch_session failed: %s", exc)
            ev("node", f"launch: ERROR — could not open browser: {exc}", {"error": str(exc)})
            return {"status": "failed", "error": str(exc)}

        # Record session in DB for stale cleanup
        await session_repo.create(
            provider=provider,
            session_key=session_key,
            application_id=state.application_id,
        )

        if state.external_start_url:
            ev("node", f"launch: navigating to external start URL", {
                "url": state.external_start_url,
                "provider": provider,
            })
            try:
                final_url = await open_url(tool_client, session_key, state.external_start_url)
            except (BrowserToolError, ToolServiceError) as exc:
                log.error("[launch] external_start_url failed: %s", exc)
                ev("node", f"launch: ERROR — external URL navigation failed: {exc}", {"error": str(exc)})
                return {"session_key": session_key, "status": "failed", "error": str(exc)}

            await app_repo.update_target_application(
                state.application_id,
                target_application_url=final_url,
                target_portal=provider,
            )
            log.info("[launch] external harness direct start provider=%s url=%s", provider, final_url)
            ev("node", f"launch: external harness direct start at {final_url[:80]}", {
                "provider": provider,
                "final_url": final_url,
            })
            return {
                "session_key": session_key,
                "status": "running",
                "pause_reason": None,
                "current_step": StepInfo(
                    page_url=final_url,
                    page_type="external_redirect",
                    is_external_portal=True,
                    portal_type=provider,
                    fields=[],
                    visible_actions=[],
                ),
            }

        ev("node", f"launch: clicking Apply button on {provider}", {
            "session_key": session_key,
            "job_url": app.source_url,
        })
        try:
            env = await tool_client.call(
                "/tools/providers/start_apply",
                {"session_key": session_key, "provider": provider, "job_url": app.source_url},
            )
        except ToolServiceError as exc:
            log.error("[launch] start_apply failed: %s", exc)
            ev("node", f"launch: ERROR — start_apply failed: {exc}", {"error": str(exc)})
            return {"session_key": session_key, "status": "failed", "error": str(exc)}

        # Auth required — not logged in to SEEK
        if env.status == "needs_human":
            reason = (env.data or {}).get("reason", "auth_required")
            login_url = (env.data or {}).get("login_url", "")
            log.warning("[launch] auth required: reason=%s login_url=%s", reason, login_url)
            ev("node", f"launch: PAUSED — auth required ({reason})", {
                "reason": reason,
                "login_url": login_url,
                "provider": provider,
            })
            return {
                "session_key": session_key,
                "status": "paused",
                "pause_reason": reason,
                "current_step": StepInfo(
                    page_url=login_url,
                    page_type="auth_required",
                    fields=[],
                    visible_actions=[],
                ),
            }

        if env.status == "error":
            log.error("[launch] start_apply returned error: %s", env.error)
            err = env.error
            msg = f"{err.type}: {err.message}" if err else "start_apply failed"
            ev("node", f"launch: ERROR — {msg}", {"error": msg})
            return {"session_key": session_key, "status": "failed", "error": msg}

        apply_result = env.data or {}

        if apply_result.get("is_external_portal"):
            portal_type = apply_result.get("portal_type")
            apply_url = apply_result.get("apply_url", "")
            log.info("[launch] external portal detected: portal_type=%s url=%s", portal_type, apply_url)
            ev("node", f"launch: external portal detected — {portal_type}", {
                "portal_type": portal_type,
                "apply_url": apply_url,
                "harness_enabled": settings.external_apply_harness_enabled,
            })
            await app_repo.update_target_application(
                state.application_id,
                target_application_url=apply_url,
                target_portal=portal_type or provider,
            )
            current_step = StepInfo(
                page_url=apply_url,
                page_type="external_redirect",
                is_external_portal=True,
                portal_type=portal_type,
                fields=[],
                visible_actions=[],
            )
            if settings.external_apply_harness_enabled:
                return {
                    "session_key": session_key,
                    "status": "running",
                    "pause_reason": None,
                    "current_step": current_step,
                }
            return {
                "session_key": session_key,
                "status": "paused",
                "pause_reason": "external_portal",
                "current_step": current_step,
            }

        log.info("[launch] started successfully session_key=%s", session_key)
        ev("node", "launch: Apply clicked — loading first form page", {
            "session_key": session_key,
            "provider": provider,
        })
        return {"session_key": session_key}

    # ── inspect ────────────────────────────────────────────────────────────
    async def node_inspect(state: ApplyState) -> dict[str, Any]:
        _set_node("inspect")
        if state.status in ("failed", "aborted", "completed", "paused"):
            log.debug("[inspect] skipping — status=%s", state.status)
            return {}

        log.info("[inspect] session_key=%s", state.session_key)
        ev("node", "inspect: reading current page from browser", {
            "session_key": state.session_key,
            "step_index": len(state.step_history),
        })
        try:
            step, env = await inspect_apply_step(tool_client, state.session_key)
        except (BrowserToolError, ToolServiceError) as exc:
            log.error("[inspect] failed: %s", exc)
            ev("node", f"inspect: ERROR — browser call failed: {exc}", {"error": str(exc)})
            return {"status": "failed", "error": str(exc)}

        if step is None:
            if _is_session_lost(env):
                log.warning("[inspect] session_not_found — attempting recovery")
                ev("node", "inspect: session lost — attempting recovery replay", {
                    "steps_to_replay": len(state.step_history),
                })
                new_key = await _recover_session(state)
                if new_key:
                    step, env = await inspect_apply_step(tool_client, new_key)
                    if step:
                        log.info("[inspect] recovery succeeded — new session_key=%s", new_key)
                        ev("node", f"inspect: recovery OK — {step.page_type} ({len(step.fields)} fields)", {
                            "new_session_key": new_key,
                            "page_type": step.page_type,
                            "fields": len(step.fields),
                            "url": step.page_url,
                        })
                        return {"session_key": new_key, "current_step": step}
                log.warning("[inspect] recovery failed — pausing")
                ev("node", "inspect: PAUSED — session lost and recovery failed", {
                    "reason": "session_lost",
                })
                return {"status": "paused", "pause_reason": "session_lost",
                        "error": "Browser session was lost (service restarted). Re-approve to start again."}
            log.warning("[inspect] drift — env.status=%s", env.status)
            ev("node", f"inspect: PAUSED — unexpected browser state (drift)", {
                "env_status": env.status if env else None,
            })
            return {
                "status": "paused",
                "pause_reason": "drift",
                "current_step": StepInfo(
                    page_url="",
                    page_type="unknown",
                    fields=[],
                    visible_actions=[],
                ),
            }

        log.info("[inspect] page_type=%s fields=%d actions=%s",
                 step.page_type, len(step.fields), step.visible_actions)
        ev("node", f"inspect: {step.page_type} — {len(step.fields)} fields, {len(step.visible_actions)} buttons", {
            "page_type": step.page_type,
            "url": step.page_url,
            "fields": [{"id": f.id, "label": f.label, "type": f.field_type, "required": f.required} for f in step.fields],
            "actions": step.visible_actions,
            "step_index": step.step_index,
            "total_steps": step.total_steps_estimate,
            "is_external_portal": step.is_external_portal,
        })

        if step.page_type == "confirmation":
            log.info("[inspect] confirmation page — marking completed")
            ev("node", "inspect: CONFIRMATION page — application submitted!", {"url": step.page_url})
            return {"current_step": step, "status": "completed"}

        if step.page_type == "external_redirect":
            if settings.external_apply_harness_enabled:
                log.info("[inspect] external redirect — handing to external apply harness")
                ev("node", f"inspect: external redirect — handing to harness", {
                    "portal_type": step.portal_type,
                    "url": step.page_url,
                })
                return {"current_step": step, "status": "running", "pause_reason": None}
            log.info("[inspect] external redirect — pausing")
            ev("node", "inspect: PAUSED — external portal (harness disabled)", {
                "portal_type": step.portal_type,
                "url": step.page_url,
            })
            return {"current_step": step, "status": "paused", "pause_reason": "external_portal"}

        return {"current_step": step}

    # ── external apply harness ───────────────────────────────────────────────
    async def node_external_apply(state: ApplyState) -> dict[str, Any]:
        _set_node("external_apply")
        if not settings.external_apply_harness_enabled:
            log.debug("[external_apply] disabled — preserving manual external portal flow")
            return {"status": "paused", "pause_reason": "external_portal"}
        if state.status in ("failed", "aborted", "completed"):
            log.debug("[external_apply] skipping — status=%s", state.status)
            return {}
        if not state.session_key:
            ev("node", "external_apply: ERROR — no browser session", {})
            return {"status": "failed", "error": "external apply harness has no browser session"}

        previous_external = state.external_apply
        recent_actions = previous_external.completed_actions if previous_external else []
        log.info(
            "[external_apply] application_id=%s session_key=%s prior_actions=%d",
            state.application_id,
            state.session_key,
            len(recent_actions),
        )
        ev("node", f"external_apply: running harness step ({len(recent_actions)} prior actions)", {
            "application_id": state.application_id,
            "prior_actions": len(recent_actions),
            "current_url": state.current_step.page_url if state.current_step else "",
            "harness_status": previous_external.status if previous_external else "new",
        })

        try:
            external_state = await run_external_apply_step(
                settings,
                tool_client,
                session_key=state.session_key,
                application_id=state.application_id,
                profile_facts=profile,
                recent_actions=recent_actions,
            )
        except (BrowserToolError, ToolServiceError) as exc:
            log.error("[external_apply] harness step failed: %s", exc)
            return {"status": "failed", "error": str(exc)}

        next_state = state.model_copy(update={"external_apply": external_state})
        status, pause_reason = _status_for_external_harness(next_state)
        current_url = external_state.current_url or (state.current_step.page_url if state.current_step else "")
        current_step = (
            state.current_step.model_copy(update={"page_url": current_url})
            if state.current_step
            else StepInfo(
                page_url=current_url,
                page_type="external_redirect",
                is_external_portal=True,
                fields=[],
                visible_actions=[],
            )
        )

        log.info(
            "[external_apply] status=%s pause_reason=%s harness_status=%s action=%s",
            status,
            pause_reason,
            external_state.status,
            external_state.proposed_action.action_type if external_state.proposed_action else None,
        )
        ev("node", f"external_apply: step done — harness={external_state.status} workflow={status}", {
            "harness_status": external_state.status,
            "workflow_status": status,
            "pause_reason": pause_reason,
            "proposed_action": external_state.proposed_action.action_type if external_state.proposed_action else None,
            "completed_actions": len(external_state.completed_actions),
            "risk_flags": external_state.risk_flags,
            "error": external_state.error,
            "current_url": external_state.current_url,
        })
        return {
            "external_apply": external_state,
            "current_step": current_step,
            "status": status,
            "pause_reason": pause_reason,
            "error": external_state.error if status == "failed" else None,
        }

    async def node_external_gate(state: ApplyState) -> dict[str, Any]:
        _set_node("external_gate")
        log.info("[external_gate] resumed: status=%s action=%s", state.status, state.action_label)
        ev("node", f"external_gate: user resumed — action={state.action_label}", {
            "action_label": state.action_label,
            "status": state.status,
            "has_user_answer": bool(state.proposed_values.get(EXTERNAL_USER_ANSWER_KEY)),
        })
        if state.status == "aborted":
            ev("node", "external_gate: aborted by user", {})
            return {}
        external_answer = state.proposed_values.get(EXTERNAL_USER_ANSWER_KEY)
        if external_answer and state.external_apply is not None and state.session_key:
            log.info("[external_gate] applying user answer to external target")
            ev("node", "external_gate: applying user consent answer to element", {
                "answer": external_answer,
                "target_element": state.external_apply.pending_user_question.target_element_id if state.external_apply.pending_user_question else None,
            })
            external_state = await apply_external_user_answer(
                tool_client,
                session_key=state.session_key,
                external_state=state.external_apply,
                answer=external_answer,
            )
            next_state = state.model_copy(update={"external_apply": external_state})
            status, pause_reason = _status_for_external_harness(next_state)
            current_url = external_state.current_url or (state.current_step.page_url if state.current_step else "")
            current_step = (
                state.current_step.model_copy(update={"page_url": current_url})
                if state.current_step
                else StepInfo(
                    page_url=current_url,
                    page_type="external_redirect",
                    is_external_portal=True,
                    fields=[],
                    visible_actions=[],
                )
            )
            return {
                "external_apply": external_state,
                "current_step": current_step,
                "proposed_values": {},
                "status": status,
                "pause_reason": pause_reason,
                "error": external_state.error if status == "failed" else None,
            }
        return {"status": "running", "pause_reason": None, "error": None}

    # ── propose ────────────────────────────────────────────────────────────
    async def node_propose(state: ApplyState) -> dict[str, Any]:
        _set_node("propose")
        if state.status in ("failed", "aborted", "completed", "paused"):
            log.debug("[propose] skipping — status=%s", state.status)
            return {}
        if state.current_step is None or not state.current_step.fields:
            log.debug("[propose] no fields to propose")
            ev("node", "propose: no fields on this page — skipping", {
                "page_type": state.current_step.page_type if state.current_step else None,
            })
            return {"proposed_values": {}}

        step = state.current_step
        log.info("[propose] application_id=%s fields=%d page_type=%s",
                 state.application_id, len(step.fields), step.page_type)
        ev("node", f"propose: resolving {len(step.fields)} fields via profile/cache/LLM", {
            "page_type": step.page_type,
            "url": step.page_url,
            "step_index": step.step_index,
            "fields": [{"id": f.id, "label": f.label, "type": f.field_type, "required": f.required, "options": f.options[:6] if f.options else []} for f in step.fields],
            "has_cover_letter": False,  # updated below
        })

        cover_letter = await draft_repo.get_cover_letter(state.application_id)
        if not cover_letter:
            log.warning(
                "[propose] no cover_letter draft for application_id=%s — "
                "textarea fills will be empty and provider validation may fail",
                state.application_id,
            )
            ev("node", "propose: WARNING — no cover letter draft found", {
                "application_id": state.application_id,
            })

        proposed, low_conf_ids = await propose_field_values(
            fields=step.fields,
            profile=profile,
            cover_letter=cover_letter,
            settings=settings,
            question_cache=question_cache,
        )

        log.info("[propose] done: proposed=%d low_confidence=%s", len(proposed), low_conf_ids)
        ev("node", f"propose: done — {len(proposed)} values ({len(low_conf_ids)} need review)", {
            "proposed": {k: str(v)[:100] for k, v in proposed.items()},
            "low_confidence_ids": low_conf_ids,
            "auto_proceed": len(low_conf_ids) == 0,
        })
        return {"proposed_values": proposed, "low_confidence_ids": low_conf_ids}

    # ── gate ───────────────────────────────────────────────────────────────
    # With interrupt_before=["gate"], this node runs only on RESUME.
    # The portal-approved values are already merged into state via update_state().
    # This node is a validation pass-through; routing handles abort.
    async def node_gate(state: ApplyState) -> dict[str, Any]:
        _set_node("gate")
        log.info("[gate] resumed: action=%s approved_fields=%d",
                 "abort" if state.status == "aborted" else "continue", len(state.proposed_values))
        ev("node", f"gate: user resumed — {'ABORT' if state.status == 'aborted' else 'approved ' + str(len(state.proposed_values)) + ' fields'}", {
            "action": "abort" if state.status == "aborted" else "continue",
            "action_label": state.action_label,
            "approved_values": {k: str(v)[:80] for k, v in state.proposed_values.items()},
            "low_confidence_ids": state.low_confidence_ids,
        })
        return {}

    # ── submit_gate ────────────────────────────────────────────────────────
    # Mandatory HITL pause before the final submit click.
    # Runs only on RESUME — portal confirmed the user wants to submit to SEEK.
    async def node_submit_gate(state: ApplyState) -> dict[str, Any]:
        _set_node("submit_gate")
        log.info("[submit_gate] user confirmed submit: action_label=%r", state.submit_action_label)
        ev("node", f"submit_gate: user confirmed final submit via '{state.submit_action_label}'", {
            "submit_action_label": state.submit_action_label,
            "total_steps_filled": len(state.step_history),
        })
        return {"status": "running"}

    # ── submit ─────────────────────────────────────────────────────────────
    async def node_submit(state: ApplyState) -> dict[str, Any]:
        _set_node("submit")
        if state.status in ("failed", "aborted"):
            log.debug("[submit] skipping — status=%s", state.status)
            return {}

        log.info("[submit] clicking final submit: session=%s action=%r",
                 state.session_key, state.submit_action_label)
        ev("node", f"submit: clicking final submit button '{state.submit_action_label}'", {
            "action_label": state.submit_action_label,
            "session_key": state.session_key,
            "total_steps_filled": len(state.step_history),
        })
        try:
            next_step, env = await fill_and_continue(
                tool_client,
                state.session_key,
                fields={},
                action_label=state.submit_action_label,
            )
        except (BrowserToolError, ToolServiceError) as exc:
            log.error("[submit] fill_and_continue failed: %s", exc)
            ev("node", f"submit: ERROR — browser call failed: {exc}", {"error": str(exc)})
            return {"status": "failed", "error": str(exc)}

        if next_step is None:
            if _is_session_lost(env):
                log.warning("[submit] session_not_found — attempting recovery")
                ev("node", "submit: session lost mid-submit — attempting recovery", {
                    "steps_to_replay": len(state.step_history),
                })
                new_key = await _recover_session(state)
                if new_key:
                    next_step, env = await fill_and_continue(
                        tool_client, new_key, {}, action_label=state.submit_action_label
                    )
                    if next_step is not None:
                        log.info("[submit] recovery succeeded — new session_key=%s", new_key)
                        ev("node", "submit: recovery OK — submit completed", {"new_session_key": new_key})
                        return {"session_key": new_key, "current_step": next_step, "status": "completed"}
                log.warning("[submit] recovery failed — pausing")
                ev("node", "submit: PAUSED — session lost, recovery failed", {"reason": "session_lost"})
                return {"status": "paused", "pause_reason": "session_lost",
                        "error": "Browser session was lost (service restarted). Re-approve to start again."}
            log.error("[submit] error or drift after submit: %s", env.status)
            err = env.error
            msg = f"{err.type}: {err.message}" if err else "drift after submit"
            ev("node", f"submit: ERROR — {msg}", {"error": msg, "env_status": env.status if env else None})
            return {"status": "failed", "error": msg}

        if next_step.page_type == "confirmation":
            log.info("[submit] confirmed — application submitted")
            ev("node", "submit: SUCCESS — application submitted! Confirmation page reached", {
                "url": next_step.page_url,
                "total_steps_filled": len(state.step_history),
            })
            return {"current_step": next_step, "status": "completed"}

        # 0-field form page after submit click.
        if next_step.page_type == "form" and len(next_step.fields) == 0:
            _final_submit_kws = ("submit", "apply now")
            still_has_submit = any(
                any(kw in a.lower() for kw in _final_submit_kws)
                for a in next_step.visible_actions
            )
            if still_has_submit:
                log.warning("[submit] still on review page after submit click — pausing for retry")
                ev("node", "submit: PAUSED — still on review page, submit button still present", {
                    "url": next_step.page_url,
                    "actions": next_step.visible_actions,
                })
                return {
                    "current_step": next_step,
                    "status": "awaiting_submit",
                    "submit_action_label": state.submit_action_label,
                }
            log.warning("[submit] 0-field page without submit button after click — assuming submitted: url=%s actions=%s",
                        next_step.page_url, next_step.visible_actions)
            ev("node", "submit: SUCCESS (assumed) — 0-field page without submit button", {
                "url": next_step.page_url,
                "actions": next_step.visible_actions,
            })
            return {"current_step": next_step, "status": "completed"}

        log.warning("[submit] unexpected page after submit: page_type=%s fields=%d url=%s",
                    next_step.page_type, len(next_step.fields), next_step.page_url)
        err_msg = (
            f"Submit navigated to unexpected page ({next_step.page_type}) "
            f"at {next_step.page_url} — submission may not have completed"
        )
        ev("node", f"submit: ERROR — unexpected page after submit ({next_step.page_type})", {
            "page_type": next_step.page_type,
            "url": next_step.page_url,
            "fields": len(next_step.fields),
            "actions": next_step.visible_actions,
        })
        return {
            "current_step": next_step,
            "status": "failed",
            "error": err_msg,
        }

    # ── fill ───────────────────────────────────────────────────────────────
    async def node_fill(state: ApplyState) -> dict[str, Any]:
        _set_node("fill")
        if state.status in ("failed", "aborted", "completed", "paused"):
            log.debug("[fill] skipping — status=%s", state.status)
            return {}

        log.info("[fill] session_key=%s fields=%d action_label=%r",
                 state.session_key, len(state.proposed_values), state.action_label)
        ev("node", f"fill: submitting {len(state.proposed_values)} fields via '{state.action_label}'", {
            "fields_count": len(state.proposed_values),
            "action_label": state.action_label,
            "filled_values": {k: str(v)[:80] for k, v in state.proposed_values.items()},
            "step_number": len(state.step_history) + 1,
        })

        try:
            next_step, env = await fill_and_continue(
                tool_client,
                state.session_key,
                fields=state.proposed_values,
                action_label=state.action_label,
            )
        except (BrowserToolError, ToolServiceError) as exc:
            log.error("[fill] fill_and_continue failed: %s", exc)
            ev("node", f"fill: ERROR — browser call failed: {exc}", {"error": str(exc)})
            return {"status": "failed", "error": str(exc)}

        history_entry = {
            "step": state.current_step.model_dump() if state.current_step else {},
            "filled_values": state.proposed_values,
        }
        new_history = list(state.step_history) + [history_entry]

        if next_step is None:
            if _is_session_lost(env):
                log.warning("[fill] session_not_found — attempting recovery")
                ev("node", "fill: session lost — attempting recovery replay", {
                    "steps_to_replay": len(state.step_history),
                })
                new_key = await _recover_session(state)
                if new_key:
                    next_step, env = await fill_and_continue(
                        tool_client, new_key, state.proposed_values, action_label=state.action_label
                    )
                    if next_step is not None:
                        log.info("[fill] recovery succeeded — new session_key=%s", new_key)
                        ev("node", f"fill: recovery OK — next page {next_step.page_type}", {
                            "new_session_key": new_key,
                            "page_type": next_step.page_type,
                        })
                        return {
                            "session_key": new_key,
                            "current_step": next_step,
                            "step_history": new_history,
                            **({"status": "completed"} if next_step.page_type == "confirmation" else {}),
                        }
                log.warning("[fill] recovery failed — pausing")
                ev("node", "fill: PAUSED — session lost, recovery failed", {"reason": "session_lost"})
                return {"status": "paused", "pause_reason": "session_lost",
                        "error": "Browser session was lost (service restarted). Re-approve to start again.",
                        "step_history": new_history}
            if env.status == "error":
                log.error("[fill] error after fill: %s", env.error)
                err = env.error
                msg = f"{err.type}: {err.message}" if err else "fill failed"
                ev("node", f"fill: ERROR — {msg}", {"error": msg})
                return {"status": "failed", "error": msg, "step_history": new_history}
            log.warning("[fill] drift after fill — env.status=%s", env.status)
            ev("node", f"fill: PAUSED — unexpected browser state after fill (drift)", {
                "env_status": env.status if env else None,
            })
            return {
                "status": "paused",
                "pause_reason": "drift",
                "current_step": StepInfo(
                    page_url="",
                    page_type="unknown",
                    fields=[],
                    visible_actions=[],
                ),
                "step_history": new_history,
            }

        log.info("[fill] next page_type=%s fields=%d actions=%s",
                 next_step.page_type, len(next_step.fields), next_step.visible_actions)
        ev("node", f"fill: advanced to next page — {next_step.page_type} ({len(next_step.fields)} fields)", {
            "page_type": next_step.page_type,
            "url": next_step.page_url,
            "fields": len(next_step.fields),
            "actions": next_step.visible_actions,
            "step_number": len(new_history),
        })

        if next_step.page_type == "confirmation":
            log.info("[fill] application submitted — confirmation page")
            ev("node", "fill: CONFIRMATION — application submitted via fill!", {
                "url": next_step.page_url,
                "total_steps": len(new_history),
            })
            return {
                "current_step": next_step,
                "status": "completed",
                "step_history": new_history,
            }

        _final_submit_kws = ("submit", "apply now")
        _noise_kws = ("open app", "back", "cancel", "close", "sign", "log")

        final_label = next(
            (a for a in next_step.visible_actions
             if any(kw in a.lower() for kw in _final_submit_kws)
             and not any(ex in a.lower() for ex in _noise_kws)),
            None,
        )

        if next_step.page_type == "form" and final_label:
            log.info("[fill] final review page — submit_label=%r fields=%d all_actions=%s",
                     final_label, len(next_step.fields), next_step.visible_actions)
            ev("node", f"fill: PAUSED — final review page, waiting for submit confirmation", {
                "submit_label": final_label,
                "fields_on_page": len(next_step.fields),
                "all_actions": next_step.visible_actions,
                "url": next_step.page_url,
            })
            return {
                "current_step": next_step,
                "status": "awaiting_submit",
                "submit_action_label": final_label,
                "step_history": new_history,
            }

        if next_step.page_type == "form" and len(next_step.fields) == 0:
            auto_label = next(
                (a for a in next_step.visible_actions if "continue" in a.lower()),
                "Continue",
            )
            log.info("[fill] interim 0-field page — auto-clicking %r all_actions=%s",
                     auto_label, next_step.visible_actions)
            ev("node", f"fill: interim 0-field page — auto-clicking '{auto_label}'", {
                "auto_label": auto_label,
                "all_actions": next_step.visible_actions,
                "url": next_step.page_url,
            })
            return {
                "current_step": next_step,
                "proposed_values": {},
                "action_label": auto_label,
                "step_history": new_history,
            }

        return {
            "current_step": next_step,
            "proposed_values": {},
            "action_label": "Continue",
            "step_history": new_history,
        }

    # ── finish ─────────────────────────────────────────────────────────────
    async def node_finish(state: ApplyState) -> dict[str, Any]:
        _set_node("finish")
        log.info("[finish] application_id=%s workflow_run_id=%s final_status=%s",
                 state.application_id, state.workflow_run_id, state.status)

        target_app_state = {
            "completed": "applied",
            "failed": "failed",
            "aborted": "approved",  # back to queue
            "paused": "paused",
        }.get(state.status, state.status)

        ev("node", f"finish: workflow complete — status={state.status} → app={target_app_state}", {
            "workflow_status": state.status,
            "app_state": target_app_state,
            "total_steps_filled": len(state.step_history),
            "error": state.error,
            "pause_reason": state.pause_reason,
            "application_id": state.application_id,
            "workflow_run_id": state.workflow_run_id,
        })

        if state.session_key:
            await close_session(tool_client, state.session_key)
            await session_repo.close(state.session_key)
            log.debug("[finish] closed session_key=%s", state.session_key)

        await app_repo.update_state(state.application_id, target_app_state)
        await run_repo.finish(state.workflow_run_id, state.status)
        log.info("[finish] app state → %s steps=%d", target_app_state, len(state.step_history))

        return {}

    # ── routing ────────────────────────────────────────────────────────────
    def _route_after_launch(state: ApplyState) -> Literal["inspect", "external_apply", "finish"]:
        if state.status == "failed":
            return "finish"
        if _should_use_external_harness(state):
            return "external_apply"
        if state.status == "paused":
            return "finish"
        return "inspect"

    def _route_after_inspect(state: ApplyState) -> Literal["propose", "external_apply", "finish"]:
        if state.status in ("failed", "completed"):
            return "finish"
        if _should_use_external_harness(state):
            return "external_apply"
        if state.status == "paused":
            return "finish"
        return "propose"

    def _route_after_external_apply(state: ApplyState) -> Literal["external_apply", "external_gate", "finish"]:
        if state.status in ("failed", "completed", "aborted"):
            return "finish"
        if state.status == "running":
            return "external_apply"
        return "external_gate"

    def _route_after_external_gate(state: ApplyState) -> Literal["external_apply", "finish"]:
        if state.status in ("failed", "completed", "aborted"):
            return "finish"
        return "external_apply"

    def _route_after_gate(state: ApplyState) -> Literal["fill", "finish"]:
        if state.status == "aborted":
            return "finish"
        return "fill"

    def _route_after_propose(state: ApplyState) -> Literal["gate", "fill"]:
        """Skip the HITL gate when all fields were resolved with high confidence."""
        if state.status in ("failed", "completed", "aborted", "paused"):
            return "fill"  # fill will short-circuit due to status
        if state.low_confidence_ids:
            return "gate"   # human review needed
        return "fill"       # auto-proceed

    def _route_after_fill(state: ApplyState) -> Literal["propose", "submit_gate", "finish"]:
        if state.status in ("failed", "completed", "aborted", "paused"):
            return "finish"
        if state.status == "awaiting_submit":
            return "submit_gate"
        return "propose"

    def _route_after_submit_gate(state: ApplyState) -> Literal["submit", "finish"]:
        if state.status == "aborted":
            return "finish"
        return "submit"

    # ── assemble graph ─────────────────────────────────────────────────────
    graph = StateGraph(ApplyState)
    graph.add_node("launch", node_launch)
    graph.add_node("inspect", node_inspect)
    graph.add_node("external_apply", node_external_apply)
    graph.add_node("external_gate", node_external_gate)
    graph.add_node("propose", node_propose)
    graph.add_node("gate", node_gate)
    graph.add_node("fill", node_fill)
    graph.add_node("submit_gate", node_submit_gate)
    graph.add_node("submit", node_submit)
    graph.add_node("finish", node_finish)

    graph.set_entry_point("launch")
    graph.add_conditional_edges("launch", _route_after_launch)
    graph.add_conditional_edges("inspect", _route_after_inspect)
    graph.add_conditional_edges("external_apply", _route_after_external_apply)
    graph.add_conditional_edges("external_gate", _route_after_external_gate)
    graph.add_conditional_edges("propose", _route_after_propose)
    graph.add_conditional_edges("gate", _route_after_gate)
    graph.add_conditional_edges("fill", _route_after_fill)
    graph.add_conditional_edges("submit_gate", _route_after_submit_gate)
    graph.add_edge("submit", "finish")
    graph.add_edge("finish", END)

    return graph


def _make_compiled(graph_builder, checkpointer):
    return graph_builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["gate", "submit_gate", "external_gate"],
    )


async def run_apply(
    settings: Settings,
    tool_client: ToolClient,
    app_repo: SqliteApplicationRepository,
    draft_repo: SqliteDraftRepository,
    run_repo: SqliteWorkflowRunRepository,
    session_repo: SqliteBrowserSessionRepository,
    db_conn: aiosqlite.Connection,
    *,
    application_id: str,
    workflow_run_id: str,
    external_start_url: str | None = None,
    question_cache: SqliteQuestionCacheRepository | None = None,
) -> ApplyState:
    """Start a new apply workflow run. Runs until first interrupt (gate) or terminal state."""
    graph_builder = build_apply_graph(
        settings, tool_client, app_repo, draft_repo, run_repo, session_repo, db_conn, question_cache,
        workflow_run_id=workflow_run_id,
    )
    checkpointer = AsyncSqliteSaver(db_conn)
    graph = _make_compiled(graph_builder, checkpointer)

    config = {"configurable": {"thread_id": workflow_run_id}}
    initial = ApplyState(
        application_id=application_id,
        workflow_run_id=workflow_run_id,
        external_start_url=external_start_url,
    )
    _emit("node", "workflow: apply run STARTED", {
        "application_id": application_id,
        "workflow_run_id": workflow_run_id,
        "external_start_url": external_start_url,
    }, run_id=workflow_run_id)
    result = await graph.ainvoke(initial.model_dump(), config)
    return ApplyState.model_validate(result)


async def resume_apply(
    settings: Settings,
    tool_client: ToolClient,
    app_repo: SqliteApplicationRepository,
    draft_repo: SqliteDraftRepository,
    run_repo: SqliteWorkflowRunRepository,
    session_repo: SqliteBrowserSessionRepository,
    db_conn: aiosqlite.Connection,
    *,
    workflow_run_id: str,
    approved_values: dict[str, str],
    action_label: str = "Continue",
    action: str = "continue",  # "continue" | "abort"
    question_cache: SqliteQuestionCacheRepository | None = None,
) -> ApplyState:
    """Resume a paused workflow run with user-approved values."""
    graph_builder = build_apply_graph(
        settings, tool_client, app_repo, draft_repo, run_repo, session_repo, db_conn, question_cache,
        workflow_run_id=workflow_run_id,
    )
    checkpointer = AsyncSqliteSaver(db_conn)
    graph = _make_compiled(graph_builder, checkpointer)

    config = {"configurable": {"thread_id": workflow_run_id}}

    # Merge user edits into checkpointed state before resuming
    state_update: dict[str, Any] = {
        "proposed_values": approved_values,
        "action_label": action_label,
    }
    if action == "abort":
        state_update["status"] = "aborted"

    _emit("node", f"workflow: apply RESUMED — action='{action_label}' {'[ABORT]' if action == 'abort' else f'({len(approved_values)} approved values)'}", {
        "workflow_run_id": workflow_run_id,
        "action": action,
        "action_label": action_label,
        "approved_values_count": len(approved_values),
    }, run_id=workflow_run_id)
    await graph.aupdate_state(config, state_update)
    result = await graph.ainvoke(None, config)
    return ApplyState.model_validate(result)
