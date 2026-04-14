import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchJobs, ignoreJob, queueJob, runSearch } from "@/api/jobs";
import { useState } from "react";
import type { Job } from "@/api/schemas";

function JobCard({ job, onReview, onIgnore, isPending }: {
  job: Job;
  onReview: () => void;
  onIgnore: () => void;
  isPending: boolean;
}) {
  const meta = job.payload;
  const tags = meta.tags ?? [];
  const bullets = meta.bullet_points ?? [];

  // Build the subtitle chips: work_type · location · work_arrangement · salary
  const chips: string[] = [];
  if (meta.work_type) chips.push(meta.work_type);
  if (job.location) chips.push(job.location);
  if (meta.work_arrangement) chips.push(meta.work_arrangement);
  if (meta.salary) chips.push(meta.salary);

  return (
    <div className="flex gap-3 rounded-lg border bg-white p-4 hover:shadow-sm transition-shadow">
      {/* Company logo */}
      <div className="flex-shrink-0 w-12 h-12 rounded border bg-slate-50 flex items-center justify-center overflow-hidden">
        {meta.logo_url ? (
          <img
            src={meta.logo_url}
            alt={job.company}
            className="w-full h-full object-contain"
            onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
          />
        ) : (
          <span className="text-slate-400 text-xs font-medium text-center leading-tight px-1">
            {job.company.substring(0, 2).toUpperCase()}
          </span>
        )}
      </div>

      {/* Main content */}
      <div className="flex-1 min-w-0">
        {/* Title + badges row */}
        <div className="flex items-start gap-2 flex-wrap mb-0.5">
          <a
            href={job.source_url}
            target="_blank"
            rel="noreferrer"
            className="text-blue-600 font-semibold hover:underline text-sm leading-snug"
          >
            {job.title}
          </a>
          {tags.map((tag) => (
            <span
              key={tag}
              className="text-xs px-2 py-0.5 rounded-full bg-violet-100 text-violet-700 font-medium flex-shrink-0"
            >
              {tag}
            </span>
          ))}
        </div>

        {/* Company */}
        <div className="text-slate-600 text-sm mb-1">{job.company}</div>

        {/* Metadata chips: work_type · location · work_arrangement · salary */}
        {chips.length > 0 && (
          <div className="flex flex-wrap items-center gap-x-1 gap-y-0.5 text-slate-500 text-xs mb-2">
            {chips.map((chip, i) => (
              <span key={i} className="flex items-center gap-1">
                {i > 0 && <span className="text-slate-300">·</span>}
                <span>{chip}</span>
              </span>
            ))}
          </div>
        )}

        {/* Bullet points or plain snippet */}
        {bullets.length > 0 ? (
          <ul className="list-disc list-inside text-slate-600 text-xs space-y-0.5 mb-2">
            {bullets.map((b, i) => <li key={i}>{b}</li>)}
          </ul>
        ) : job.summary ? (
          <p className="text-slate-600 text-xs line-clamp-2 mb-2">{job.summary}</p>
        ) : null}

        {/* Footer: posted date + actions */}
        <div className="flex items-center justify-between gap-2 mt-1">
          <span className="text-slate-400 text-xs">{meta.posted_at ?? ""}</span>
          <div className="flex gap-2">
            <button
              onClick={onReview}
              disabled={isPending}
              className="rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              Review
            </button>
            <button
              onClick={onIgnore}
              disabled={isPending}
              className="rounded border px-3 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-50"
            >
              Ignore
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function JobsPage() {
  const queryClient = useQueryClient();
  const [keywords, setKeywords] = useState("python");
  const [location, setLocation] = useState("");
  const [maxPages, setMaxPages] = useState(3);

  const jobsQuery = useQuery({
    queryKey: ["jobs", "discovered"],
    queryFn: () => fetchJobs({ state: "discovered" }),
  });

  const searchMutation = useMutation({
    mutationFn: runSearch,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });

  const queueMutation = useMutation({
    mutationFn: queueJob,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const ignoreMutation = useMutation({
    mutationFn: ignoreJob,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });

  return (
    <section className="space-y-6">
      <header className="flex items-end justify-between gap-4">
        <h1 className="text-2xl font-semibold">Jobs</h1>
      </header>

      <form
        className="flex flex-wrap items-end gap-3 rounded-md border bg-white p-4"
        onSubmit={(e) => {
          e.preventDefault();
          searchMutation.mutate({ provider: "seek", keywords, location: location || undefined, max_pages: maxPages });
        }}
      >
        <label className="flex flex-col text-sm">
          <span className="mb-1 text-slate-600">Keywords</span>
          <input
            className="rounded border px-2 py-1"
            value={keywords}
            onChange={(e) => setKeywords(e.target.value)}
          />
        </label>
        <label className="flex flex-col text-sm">
          <span className="mb-1 text-slate-600">Location (optional)</span>
          <input
            className="rounded border px-2 py-1"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
          />
        </label>
        <label className="flex flex-col text-sm">
          <span className="mb-1 text-slate-600">Pages</span>
          <input
            type="number"
            min={1}
            max={10}
            className="rounded border px-2 py-1 w-16"
            value={maxPages}
            onChange={(e) => setMaxPages(Math.max(1, Math.min(10, Number(e.target.value))))}
          />
        </label>
        <button
          type="submit"
          disabled={searchMutation.isPending}
          className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {searchMutation.isPending ? "Searching…" : "Run search"}
        </button>
        {searchMutation.isSuccess && (
          <span className="text-sm text-slate-500">
            {searchMutation.data.persisted} new, {searchMutation.data.blocked} blocked
          </span>
        )}
        {searchMutation.isError && (
          <span className="text-sm text-red-600">{(searchMutation.error as Error).message}</span>
        )}
      </form>

      {jobsQuery.isLoading && <p className="text-slate-500">Loading…</p>}
      {jobsQuery.isError && (
        <p className="text-red-600">Failed to load jobs: {(jobsQuery.error as Error).message}</p>
      )}
      {jobsQuery.isSuccess && jobsQuery.data.length === 0 && (
        <p className="text-slate-500">No new jobs. Run a search to discover some.</p>
      )}

      {jobsQuery.isSuccess && jobsQuery.data.length > 0 && (
        <div className="space-y-3">
          {jobsQuery.data.map((job) => (
            <JobCard
              key={job.id}
              job={job}
              onReview={() => queueMutation.mutate(job.id)}
              onIgnore={() => ignoreMutation.mutate(job.id)}
              isPending={queueMutation.isPending || ignoreMutation.isPending}
            />
          ))}
        </div>
      )}
    </section>
  );
}
