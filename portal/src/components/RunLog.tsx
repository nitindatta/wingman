import { useEffect, useRef, useState } from "react";

type LogEvent = {
  type: string;
  run_id: string;
  label: string;
  ts: string;
  data: Record<string, unknown>;
};

const TYPE_CONFIG: Record<string, { bg: string; border: string; tag: string; textColor: string }> = {
  llm_prompt:   { bg: "bg-blue-50 dark:bg-blue-950/40",    border: "border-blue-200 dark:border-blue-800",    tag: "LLM >",   textColor: "text-blue-700 dark:text-blue-300" },
  llm_response: { bg: "bg-green-50 dark:bg-green-950/40",  border: "border-green-200 dark:border-green-800",  tag: "LLM <",   textColor: "text-green-700 dark:text-green-300" },
  observe:      { bg: "bg-violet-50 dark:bg-violet-950/40",border: "border-violet-200 dark:border-violet-800",tag: "OBSERVE", textColor: "text-violet-700 dark:text-violet-300" },
  plan:         { bg: "bg-amber-50 dark:bg-amber-950/40",  border: "border-amber-200 dark:border-amber-800",  tag: "PLAN",    textColor: "text-amber-700 dark:text-amber-300" },
  policy:       { bg: "bg-orange-50 dark:bg-orange-950/40",border: "border-orange-200 dark:border-orange-800",tag: "POLICY",  textColor: "text-orange-700 dark:text-orange-300" },
  execute:      { bg: "bg-teal-50 dark:bg-teal-950/40",   border: "border-teal-200 dark:border-teal-800",   tag: "EXEC",    textColor: "text-teal-700 dark:text-teal-300" },
  node:         { bg: "bg-slate-50 dark:bg-slate-800",     border: "border-slate-200 dark:border-slate-600", tag: "NODE",    textColor: "text-slate-600 dark:text-slate-300" },
};

const EXPANDABLE = new Set(["llm_prompt", "llm_response", "observe"]);

function inlineSummary(ev: LogEvent): string {
  const d = ev.data;
  switch (ev.type) {
    case "plan": {
      const at = String(d.action_type ?? "");
      const el = d.element_id ? ` -> ${d.element_id}` : "";
      const val = d.value ? ` = "${String(d.value).slice(0, 40)}"` : "";
      const q = d.question ? ` [ask: "${String(d.question).slice(0, 60)}"]` : "";
      return `${at}${el}${val}${q}`;
    }
    case "policy":
      return `${d.decision}${d.pause_reason ? ` (${d.pause_reason})` : ""}${Array.isArray(d.risk_flags) && d.risk_flags.length ? ` flags: ${(d.risk_flags as string[]).join(",")}` : ""}`;
    case "execute":
      return `${d.action_type} ${d.ok ? "ok" : `FAIL: ${d.message ?? ""}`}${d.new_url ? ` -> ${String(d.new_url).slice(0, 50)}` : ""}`;
    case "observe":
      return `${d.page_type} | ${d.fields_count} fields | ${d.buttons_count} buttons`;
    case "llm_prompt":
      return String(d.call ?? "");
    case "llm_response":
      return String(d.call ?? "");
    default:
      return String(d.reason ?? d.label ?? "");
  }
}

function EventRow({
  event,
  expanded,
  onToggle,
}: {
  event: LogEvent;
  expanded: boolean;
  onToggle: () => void;
}) {
  const cfg = TYPE_CONFIG[event.type] ?? TYPE_CONFIG.node;
  const canExpand = EXPANDABLE.has(event.type);
  const time = event.ts.slice(11, 19);
  const summary = inlineSummary(event);

  return (
    <div
      className={`rounded border ${cfg.bg} ${cfg.border} px-2 py-1.5 ${canExpand ? "cursor-pointer select-none" : ""}`}
      onClick={canExpand ? onToggle : undefined}
    >
      <div className={`flex items-baseline gap-1.5 font-mono text-xs ${cfg.textColor}`}>
        <span className="opacity-50 shrink-0 tabular-nums">{time}</span>
        <span className="font-bold shrink-0 w-[4.5rem] text-right">{cfg.tag}</span>
        <span className="truncate flex-1 font-medium">{event.label}</span>
        {canExpand && <span className="opacity-40 shrink-0">{expanded ? "▲" : "▼"}</span>}
      </div>
      {!expanded && summary && (
        <div className="mt-0.5 ml-[5.5rem] text-[11px] font-mono opacity-60 truncate">{summary}</div>
      )}
      {expanded && (
        <pre className="mt-1.5 text-[11px] font-mono whitespace-pre-wrap break-all leading-4 opacity-80 overflow-x-auto max-h-64">
          {JSON.stringify(event.data, null, 2)}
        </pre>
      )}
    </div>
  );
}

declare const __BACKEND_URL__: string;
const SSE_URL = `${__BACKEND_URL__}/api/events/stream`;

export default function RunLog({ runId }: { runId: string | null | undefined }) {
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [connected, setConnected] = useState(false);
  const [allCount, setAllCount] = useState(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!runId) return;
    setEvents([]);
    setExpanded(new Set());
    setAllCount(0);

    const es = new EventSource(SSE_URL);
    es.onopen = () => setConnected(true);
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data as string) as LogEvent;
        if (ev.type === "ping") return;
        setAllCount((n) => n + 1);
        if (ev.run_id !== runId) return;
        setEvents((prev) => [...prev, ev]);
      } catch {
        // ignore parse errors
      }
    };
    es.onerror = () => setConnected(false);

    return () => {
      es.close();
      setConnected(false);
    };
  }, [runId]);

  useEffect(() => {
    if (open && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [events.length, open]);

  if (!runId) return null;

  const toggle = (i: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });

  return (
    <div className="mt-4 rounded-lg border dark:border-slate-700 bg-white dark:bg-slate-800 overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-4 py-2.5 text-sm font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors"
      >
        <span className="flex items-center gap-2">
          Agent Log
          <span className="text-xs font-normal text-slate-400 dark:text-slate-500">
            {events.length} events
            {allCount > events.length && <span className="ml-1 text-amber-400" title="Events received on stream but filtered (different run_id)">({allCount} total)</span>}
          </span>
          <span
            className={`inline-block w-1.5 h-1.5 rounded-full ${connected ? "bg-green-500" : "bg-slate-300 dark:bg-slate-600"}`}
            title={connected ? `Connected — ${SSE_URL}` : "Connecting…"}
          />
        </span>
        <span className="text-slate-400">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="border-t dark:border-slate-700">
          <div className="max-h-[30rem] overflow-y-auto p-2 space-y-1">
            {events.length === 0 && (
              <p className="text-xs text-slate-400 dark:text-slate-500 font-mono py-2 px-1">
                {connected ? "Waiting for events…" : "Connecting…"}
              </p>
            )}
            {events.map((ev, i) => (
              <EventRow
                key={i}
                event={ev}
                expanded={expanded.has(i)}
                onToggle={() => toggle(i)}
              />
            ))}
            <div ref={bottomRef} />
          </div>
        </div>
      )}
    </div>
  );
}
