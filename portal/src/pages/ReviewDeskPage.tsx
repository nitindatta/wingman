import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchJobs } from "../api/jobs";
import {
  triggerPrepare,
  approveApplication,
  discardApplication,
} from "../api/applications";
import type { PrepareResponse } from "../api/schemas";
import type { Job } from "../api/schemas";

export default function ReviewDeskPage() {
  const queryClient = useQueryClient();
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [prepared, setPrepared] = useState<PrepareResponse | null>(null);
  const [applicationId, setApplicationId] = useState<string | null>(null);
  const [actionDone, setActionDone] = useState<"approved" | "discarded" | null>(null);
  const [coverLetterText, setCoverLetterText] = useState<string>("");

  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: () => fetchJobs(),
  });

  const prepareMutation = useMutation({
    mutationFn: (jobId: string) => triggerPrepare(jobId),
    onSuccess: (data) => {
      setPrepared(data);
      setApplicationId(data.application_id);
      setActionDone(null);
      setCoverLetterText(data.cover_letter ?? "");
    },
  });

  const approveMutation = useMutation({
    mutationFn: (appId: string) => approveApplication(appId, coverLetterText),
    onSuccess: () => {
      setActionDone("approved");
      queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
  });

  const discardMutation = useMutation({
    mutationFn: (appId: string) => discardApplication(appId),
    onSuccess: () => {
      setActionDone("discarded");
      queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
  });

  function handleSelectJob(job: Job) {
    setSelectedJob(job);
    setPrepared(null);
    setApplicationId(null);
    setActionDone(null);
  }

  return (
    <div style={{ display: "flex", gap: "1.5rem", padding: "1.5rem" }}>
      {/* Job list */}
      <div style={{ width: 320, flexShrink: 0 }}>
        <h2 style={{ marginTop: 0 }}>Jobs</h2>
        {jobsQuery.isLoading && <p>Loading…</p>}
        {jobsQuery.isError && <p style={{ color: "red" }}>Failed to load jobs.</p>}
        {jobsQuery.data?.map((job) => (
          <div
            key={job.id}
            onClick={() => handleSelectJob(job)}
            style={{
              padding: "0.75rem",
              marginBottom: "0.5rem",
              border: selectedJob?.id === job.id ? "2px solid #2563eb" : "1px solid #e5e7eb",
              borderRadius: 6,
              cursor: "pointer",
              background: selectedJob?.id === job.id ? "#eff6ff" : "#fff",
            }}
          >
            <div style={{ fontWeight: 600, fontSize: 14 }}>{job.title}</div>
            <div style={{ color: "#6b7280", fontSize: 13 }}>{job.company}</div>
            {job.location && (
              <div style={{ color: "#9ca3af", fontSize: 12 }}>{job.location}</div>
            )}
          </div>
        ))}
      </div>

      {/* Review panel */}
      <div style={{ flex: 1 }}>
        {!selectedJob && (
          <p style={{ color: "#6b7280" }}>Select a job from the list to prepare an application.</p>
        )}

        {selectedJob && !prepared && (
          <div>
            <h2 style={{ marginTop: 0 }}>
              {selectedJob.title} — {selectedJob.company}
            </h2>
            {selectedJob.location && <p style={{ color: "#6b7280", margin: "0 0 0.25rem" }}>{selectedJob.location}</p>}
            <a href={selectedJob.source_url} target="_blank" rel="noreferrer" style={{ fontSize: 13 }}>
              View on SEEK ↗
            </a>
            {selectedJob.summary && (
              <div
                style={{
                  margin: "1rem 0",
                  padding: "0.75rem",
                  background: "#f9fafb",
                  border: "1px solid #e5e7eb",
                  borderRadius: 6,
                  fontSize: 14,
                  color: "#374151",
                  lineHeight: 1.6,
                }}
              >
                {selectedJob.summary}
              </div>
            )}
            <div style={{ marginTop: "1rem" }}>
              <button
                onClick={() => prepareMutation.mutate(selectedJob.id)}
                disabled={prepareMutation.isPending}
                style={{
                  padding: "0.5rem 1.25rem",
                  background: "#2563eb",
                  color: "#fff",
                  border: "none",
                  borderRadius: 6,
                  cursor: prepareMutation.isPending ? "not-allowed" : "pointer",
                  fontSize: 14,
                }}
              >
                {prepareMutation.isPending
                  ? "Preparing… (fetching & generating)"
                  : "Prepare Application"}
              </button>
              {prepareMutation.isError && (
                <p style={{ color: "red", marginTop: 8 }}>
                  Error:{" "}
                  {prepareMutation.error instanceof Error
                    ? prepareMutation.error.message
                    : "Unknown error"}
                </p>
              )}
            </div>
          </div>
        )}

        {prepared && selectedJob && (
          <div>
            <h2 style={{ marginTop: 0 }}>
              {selectedJob.title} — {selectedJob.company}
            </h2>

            {/* Not a fit banner */}
            {prepared.is_suitable === false && (
              <div
                style={{
                  padding: "1rem",
                  background: "#fef3c7",
                  border: "1px solid #d97706",
                  borderRadius: 6,
                  marginBottom: "1.5rem",
                }}
              >
                <p style={{ fontWeight: 600, margin: "0 0 0.5rem", color: "#92400e" }}>
                  Profile does not sufficiently match this role
                </p>
                {prepared.gaps && prepared.gaps.length > 0 && (
                  <ul style={{ margin: 0, paddingLeft: "1.25rem", color: "#78350f", fontSize: 14 }}>
                    {prepared.gaps.map((gap, i) => (
                      <li key={i}>{gap}</li>
                    ))}
                  </ul>
                )}
                <p style={{ margin: "0.75rem 0 0", fontSize: 13, color: "#92400e" }}>
                  You can still discard this job or apply manually.
                </p>
              </div>
            )}

            {/* Cover letter — only shown when suitable */}
            {prepared.is_suitable !== false && (
            <section style={{ marginBottom: "1.5rem" }}>
              <h3>Cover Letter</h3>
              <textarea
                value={coverLetterText}
                onChange={(e) => setCoverLetterText(e.target.value)}
                rows={12}
                style={{
                  width: "100%",
                  padding: "0.75rem",
                  border: "1px solid #d1d5db",
                  borderRadius: 6,
                  fontFamily: "inherit",
                  fontSize: 14,
                  resize: "vertical",
                  whiteSpace: "pre-wrap",
                }}
              />
            </section>
            )}

            {/* Predicted Q&A */}
            {prepared.questions.length > 0 && (
              <section style={{ marginBottom: "1.5rem" }}>
                <h3>Predicted Interview Questions</h3>
                {prepared.questions.map((qa, i) => (
                  <div
                    key={i}
                    style={{
                      marginBottom: "1rem",
                      padding: "0.75rem",
                      background: "#f9fafb",
                      border: "1px solid #e5e7eb",
                      borderRadius: 6,
                    }}
                  >
                    <p style={{ fontWeight: 600, margin: "0 0 0.5rem" }}>
                      Q{i + 1}: {qa.question}
                    </p>
                    <p style={{ margin: 0, color: "#374151" }}>{qa.answer}</p>
                  </div>
                ))}
              </section>
            )}

            {/* Action buttons */}
            {!actionDone && applicationId && (
              <div style={{ display: "flex", gap: "0.75rem" }}>
                <button
                  onClick={() => approveMutation.mutate(applicationId)}
                  disabled={approveMutation.isPending}
                  style={{
                    padding: "0.5rem 1.25rem",
                    background: "#16a34a",
                    color: "#fff",
                    border: "none",
                    borderRadius: 6,
                    cursor: "pointer",
                    fontSize: 14,
                  }}
                >
                  Approve
                </button>
                <button
                  onClick={() => discardMutation.mutate(applicationId)}
                  disabled={discardMutation.isPending}
                  style={{
                    padding: "0.5rem 1.25rem",
                    background: "#dc2626",
                    color: "#fff",
                    border: "none",
                    borderRadius: 6,
                    cursor: "pointer",
                    fontSize: 14,
                  }}
                >
                  Discard
                </button>
              </div>
            )}

            {actionDone && (
              <p
                style={{
                  fontWeight: 600,
                  color: actionDone === "approved" ? "#16a34a" : "#dc2626",
                }}
              >
                Application {actionDone}.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
