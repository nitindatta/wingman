import { useQuery } from "@tanstack/react-query";
import { fetchApplications, fetchApplicationDetail } from "@/api/applications";
import { useState } from "react";
import type { Application, ApplicationDetail } from "@/api/schemas";

function ApplicationDetail({ appId, onClose }: { appId: string; onClose: () => void }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["application-detail", appId],
    queryFn: () => fetchApplicationDetail(appId),
  });

  if (isLoading) return <div className="p-6 text-slate-500 text-sm">Loading…</div>;
  if (isError || !data) return <div className="p-6 text-red-600 text-sm">Failed to load details.</div>;

  const job = data.job;
  const app = data.application;

  return (
    <div className="border-t bg-slate-50 p-6 space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="font-semibold text-slate-900">
            {job?.title ?? app.job_title ?? "Unknown role"}
          </h3>
          <p className="text-sm text-slate-500">
            {job?.company ?? app.job_company ?? ""}
            {job?.location ? ` · ${job.location}` : ""}
          </p>
          {job?.source_url && (
            <a
              href={job.source_url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-blue-600 hover:underline"
            >
              View job on SEEK
            </a>
          )}
        </div>
        <button
          onClick={onClose}
          className="text-slate-400 hover:text-slate-600 text-xs shrink-0"
        >
          Close
        </button>
      </div>

      {/* Cover letter */}
      {data.cover_letter ? (
        <div>
          <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
            Cover Letter
          </h4>
          <div className="bg-white border rounded p-4 text-sm text-slate-700 whitespace-pre-wrap leading-relaxed">
            {data.cover_letter}
          </div>
        </div>
      ) : (
        <p className="text-sm text-slate-400 italic">No cover letter stored.</p>
      )}

      {/* Match evidence */}
      {data.match_evidence && (
        <div>
          <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
            Match Evidence
          </h4>
          <div className="bg-white border rounded p-4 text-xs text-slate-600 whitespace-pre-wrap font-mono leading-relaxed">
            {data.match_evidence}
          </div>
        </div>
      )}

      {/* Applied date */}
      <p className="text-xs text-slate-400">
        Applied {new Date(app.updated_at).toLocaleDateString("en-AU", {
          day: "numeric", month: "short", year: "numeric",
          hour: "2-digit", minute: "2-digit",
        })}
      </p>
    </div>
  );
}

function ApplicationRow({ app }: { app: Application }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-lg border bg-white overflow-hidden">
      <button
        className="w-full flex items-center justify-between gap-4 px-4 py-3 hover:bg-slate-50 transition-colors text-left"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm text-slate-900 truncate">
              {app.job_title ?? "Unknown role"}
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 font-medium shrink-0">
              applied
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-0.5">
            {app.job_company ?? ""}
            {app.job_location ? ` · ${app.job_location}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <span className="text-xs text-slate-400">
            {new Date(app.updated_at).toLocaleDateString("en-AU", {
              day: "numeric", month: "short", year: "numeric",
            })}
          </span>
          <svg
            className={`w-4 h-4 text-slate-400 transition-transform ${expanded ? "rotate-180" : ""}`}
            fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </button>

      {expanded && (
        <ApplicationDetail appId={app.id} onClose={() => setExpanded(false)} />
      )}
    </div>
  );
}

export default function HistoryPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["applications", "applied"],
    queryFn: () => fetchApplications({ state: "applied" }),
  });

  const apps = data ?? [];

  return (
    <section className="space-y-6">
      <header className="flex items-end justify-between gap-4">
        <h1 className="text-2xl font-semibold">History</h1>
        {apps.length > 0 && (
          <span className="text-sm text-slate-500">{apps.length} application{apps.length !== 1 ? "s" : ""}</span>
        )}
      </header>

      {isLoading && <p className="text-slate-500">Loading…</p>}
      {isError && <p className="text-red-600">Failed to load applications.</p>}
      {!isLoading && apps.length === 0 && (
        <p className="text-slate-500">No submitted applications yet.</p>
      )}

      <div className="space-y-2">
        {apps.map((app) => (
          <ApplicationRow key={app.id} app={app} />
        ))}
      </div>
    </section>
  );
}
