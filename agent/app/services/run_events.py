"""Lightweight in-process event bus for streaming agent run events to the portal."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_EVENTS_LOG_PATH = Path(__file__).resolve().parents[3] / "logs" / "events.jsonl"

log = logging.getLogger("run_events")

_current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)
_last_run_id: str | None = None  # fallback when ContextVar doesn't cross LangGraph task boundaries
_current_node: str | None = None  # active LangGraph node name, set at top of each node function
_MAX_HISTORY = 200
_seq_counter = 0


@dataclass
class RunEvent:
    type: str
    run_id: str
    label: str
    data: dict[str, Any]
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    seq: int = 0


class EventBus:
    def __init__(self) -> None:
        self._history: deque[RunEvent] = deque(maxlen=_MAX_HISTORY)
        self._queues: list[asyncio.Queue[RunEvent]] = []
        _EVENTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: RunEvent) -> None:
        global _seq_counter
        _seq_counter += 1
        event.seq = _seq_counter
        self._history.append(event)
        try:
            with _EVENTS_LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "seq": event.seq,
                    "ts": event.ts,
                    "type": event.type,
                    "run_id": event.run_id,
                    "label": event.label,
                    "data_keys": list(event.data.keys()),
                }) + "\n")
        except Exception:
            pass
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, after_seq: int = 0) -> asyncio.Queue[RunEvent]:
        q: asyncio.Queue[RunEvent] = asyncio.Queue(maxsize=500)
        for ev in list(self._history):
            if ev.seq > after_seq:
                await q.put(ev)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[RunEvent]) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            pass


bus = EventBus()


def set_run_id(run_id: str) -> None:
    """Bind run_id to this async context so all emit() calls here tag events with it."""
    global _last_run_id
    _current_run_id.set(run_id)
    _last_run_id = run_id


def set_node(node_name: str | None) -> None:
    """Track which LangGraph node is currently executing (used to annotate LLM events)."""
    global _current_node
    _current_node = node_name


def emit(event_type: str, label: str, data: dict[str, Any], *, run_id: str | None = None) -> None:
    rid = run_id or _current_run_id.get() or "unknown"
    log.info("[event] type=%-12s run_id=%.12s  %s", event_type, rid, label)
    bus.emit(RunEvent(type=event_type, run_id=rid, label=label, data=data))


def install_llm_tracing() -> None:
    """Monkey-patch AsyncCompletions.create to emit llm_prompt/llm_response for every LLM call."""
    try:
        from openai.resources.chat.completions import AsyncCompletions
    except ImportError:
        return

    if getattr(AsyncCompletions, "_envoy_traced", False):
        return

    _orig = AsyncCompletions.create

    async def _traced(self, *, model: str, messages: list, **kwargs: Any):
        rid = _current_run_id.get() or _last_run_id or "unknown"
        call_label = kwargs.pop("_call_name", model)  # consumed here, never forwarded to OpenAI
        node = _current_node

        emit("llm_prompt", f"LLM › {call_label}", {
            "model": model,
            "call": call_label,
            "node": node,
            "messages": [
                {"role": m.get("role", ""), "content": str(m.get("content", ""))}
                for m in messages
            ],
            "max_tokens": kwargs.get("max_tokens"),
            "temperature": kwargs.get("temperature"),
        }, run_id=rid)

        response = await _orig(self, model=model, messages=messages, **kwargs)

        content = ""
        if response.choices:
            content = response.choices[0].message.content or ""

        emit("llm_response", f"LLM ‹ {call_label} ({len(content)} chars)", {
            "model": response.model,
            "call": call_label,
            "node": node,
            "content": content,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            },
        }, run_id=rid)

        return response

    AsyncCompletions.create = _traced  # type: ignore[method-assign]
    AsyncCompletions._envoy_traced = True  # type: ignore[attr-defined]
    log.info("[run_events] LLM tracing installed on AsyncCompletions.create")
