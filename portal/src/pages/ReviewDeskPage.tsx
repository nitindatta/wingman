import { useState, useMemo, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchApplications,
  fetchApplicationDetail,
  approveApplication,
  discardApplication,
  enqueueApply,
  enqueueGate,
  enqueueSubmit,
  markSubmitted,
  cancelApplication,
} from "../api/applications";
import { applyStepResponseSchema, type Application, type ApplyStepResponse } from "../api/schemas";

// ---------------------------------------------------------------------------
// State metadata
// ---------------------------------------------------------------------------

const STATE_META: Record<string, { label: string; color: string }> = {
  preparing:       { label: "Preparing...",    color: "#6b7280" },
  prepared:        { label: "Ready to review", color: "#2563eb" },
  approved:        { label: "Approved",        color: "#16a34a" },
  applying:        { label: "Applying...",     color: "#6b7280" },
  needs_review:    { label: "Needs review",    color: "#d97706" },
  awaiting_submit: { label: "Ready to submit", color: "#7c3aed" },
  submitting:      { label: "Submitting...",   color: "#6b7280" },
  applied:         { label: "Applied",         color: "#16a34a" },
  failed:          { label: "Failed",          color: "#dc2626" },
  paused:          { label: "Paused",          color: "#d97706" },
  unsuitable:      { label: "Not a fit",       color: "#9ca3af" },
};

const ACTIVE_STATES = ["preparing", "applying", "submitting"];

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function elapsedTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ${secs % 60}s`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ${mins % 60}m`;
}

// ---------------------------------------------------------------------------
// Match evidence parsing + rendering (preserved from original)
// ---------------------------------------------------------------------------

type EvidenceItem = { rating: "STRONG" | "MODERATE" | "WEAK"; requirement: string; evidence: string };

function parseEvidence(raw: string): EvidenceItem[] {
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const m = line.match(/^\[(STRONG|MODERATE|WEAK)\]\s*(.+?)\s*→\s*(.+)$/);
      if (m) return { rating: m[1] as "STRONG" | "MODERATE" | "WEAK", requirement: m[2], evidence: m[3] };
      return null;
    })
    .filter((x): x is EvidenceItem => x !== null);
}

function MatchEvidencePanel({ evidence, showEvidence, setShowEvidence }: {
  evidence: EvidenceItem[];
  showEvidence: boolean;
  setShowEvidence: (v: boolean) => void;
}) {
  if (evidence.length === 0) return null;
  return (
    <div style={{ marginBottom: "1.5rem" }}>
      <button
        onClick={() => setShowEvidence(!showEvidence)}
        style={{
          background: "none",
          border: "none",
          cursor: "pointer",
          padding: 0,
          fontSize: 14,
          fontWeight: 600,
          color: "#374151",
          display: "flex",
          alignItems: "center",
          gap: "0.4rem",
          marginBottom: "0.5rem",
        }}
      >
        <span>{showEvidence ? "▾" : "▸"}</span>
        Match Breakdown
        <span style={{ fontWeight: 400, fontSize: 13, color: "#6b7280", marginLeft: "0.25rem" }}>
          ({evidence.filter((e) => e.rating === "STRONG").length} strong ·{" "}
          {evidence.filter((e) => e.rating === "MODERATE").length} moderate ·{" "}
          {evidence.filter((e) => e.rating === "WEAK").length} weak)
        </span>
      </button>
      {showEvidence && (
        <div style={{ border: "1px solid #e5e7eb", borderRadius: 6, overflow: "hidden" }}>
          {evidence.map((item, i) => {
            const colors = {
              STRONG:   { bg: "#f0fdf4", badge: "#16a34a", text: "#15803d" },
              MODERATE: { bg: "#fffbeb", badge: "#d97706", text: "#92400e" },
              WEAK:     { bg: "#fef2f2", badge: "#dc2626", text: "#991b1b" },
            }[item.rating];
            return (
              <div
                key={i}
                style={{
                  display: "grid",
                  gridTemplateColumns: "80px 1fr 1.2fr",
                  gap: "0.75rem",
                  alignItems: "start",
                  padding: "0.6rem 0.75rem",
                  background: i % 2 === 0 ? "#fff" : "#f9fafb",
                  borderTop: i > 0 ? "1px solid #f3f4f6" : "none",
                  fontSize: 13,
                }}
              >
                <span
                  style={{
                    display: "inline-block",
                    padding: "2px 8px",
                    borderRadius: 99,
                    background: colors.bg,
                    color: colors.badge,
                    border: `1px solid ${colors.badge}`,
                    fontWeight: 600,
                    fontSize: 11,
                    textAlign: "center",
                  }}
                >
                  {item.rating}
                </span>
                <span style={{ color: "#111827", lineHeight: 1.4 }}>{item.requirement}</span>
                <span style={{ color: "#6b7280", lineHeight: 1.4 }}>{item.evidence}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Spinner
// ---------------------------------------------------------------------------

function Spinner() {
  return (
    <span
      style={{
        display: "inline-block",
        width: 16,
        height: 16,
        border: "2px solid #e5e7eb",
        borderTopColor: "#6b7280",
        borderRadius: "50%",
        animation: "spin 0.8s linear infinite",
        flexShrink: 0,
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// Left panel item
// ---------------------------------------------------------------------------

function AppListItem({ app, selected, onClick }: {
  app: Application;
  selected: boolean;
  onClick: () => void;
}) {
  const meta = STATE_META[app.state] ?? { label: app.state, color: "#6b7280" };
  const isActive = ACTIVE_STATES.includes(app.state);
  const title = app.job_title ?? "Untitled";
  const company = app.job_company ?? "";
  return (
    <div
      onClick={onClick}
      style={{
        padding: "0.75rem",
        marginBottom: "0.5rem",
        border: selected ? "2px solid #2563eb" : "1px solid #e5e7eb",
        borderRadius: 6,
        cursor: "pointer",
        background: selected ? "#eff6ff" : "#fff",
        display: "flex",
        alignItems: "flex-start",
        gap: "0.6rem",
      }}
    >
      <div style={{ marginTop: 3, flexShrink: 0 }}>
        {isActive ? (
          <Spinner />
        ) : (
          <span
            style={{
              display: "inline-block",
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: meta.color,
              marginTop: 2,
            }}
          />
        )}
      </div>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontWeight: 600, fontSize: 14, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {title}
        </div>
        {company && (
          <div style={{ color: "#6b7280", fontSize: 12, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {company}
          </div>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", marginTop: 2 }}>
          <span style={{ fontSize: 11, color: meta.color, fontWeight: 500 }}>{meta.label}</span>
          <span style={{ fontSize: 11, color: "#9ca3af" }}>
            · {isActive ? elapsedTime(app.updated_at) : relativeTime(app.updated_at)}
          </span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right-panel state panels
// ---------------------------------------------------------------------------

function SpinnerPanel({ message, subtext }: { message: string; subtext?: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "3rem", textAlign: "center", gap: "1rem" }}>
      <div
        style={{
          width: 40,
          height: 40,
          border: "3px solid #e5e7eb",
          borderTopColor: "#6b7280",
          borderRadius: "50%",
          animation: "spin 0.8s linear infinite",
        }}
      />
      <p style={{ fontWeight: 600, fontSize: 16, margin: 0, color: "#374151" }}>{message}</p>
      {subtext && <p style={{ fontSize: 14, color: "#6b7280", margin: 0, maxWidth: 360 }}>{subtext}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Gate panel (needs_review)
// ---------------------------------------------------------------------------

function GatePanel({ appStep, gateAnswers, setGateAnswers, onSubmit, isPending, error }: {
  appStep: ApplyStepResponse;
  gateAnswers: Record<string, string>;
  setGateAnswers: (v: Record<string, string>) => void;
  onSubmit: () => void;
  isPending: boolean;
  error: string | null;
}) {
  const lowIds = appStep.low_confidence_ids ?? [];
  if (lowIds.length === 0) {
    return (
      <div style={{ padding: "1.25rem", background: "#f0f9ff", border: "1px solid #0ea5e9", borderRadius: 6 }}>
        <p style={{ margin: 0, color: "#0c4a6e" }}>Needs review — no specific fields flagged.</p>
      </div>
    );
  }
  return (
    <div style={{ padding: "1.25rem", background: "#f0f9ff", border: "1px solid #0ea5e9", borderRadius: 6 }}>
      <p style={{ fontWeight: 600, margin: "0 0 0.25rem", color: "#0c4a6e", fontSize: 15 }}>
        Screening {lowIds.length === 1 ? "question" : "questions"} — please answer
      </p>
      <p style={{ margin: "0 0 1rem", color: "#075985", fontSize: 13 }}>
        The AI wasn't confident about {lowIds.length === 1 ? "this question" : "these questions"}. Answer below to continue the application.
      </p>
      {lowIds.map((fieldId) => {
        const field = appStep.step?.fields?.find((f) => f.id === fieldId);
        const label = field?.label ?? fieldId;
        const proposed = appStep.proposed_values[fieldId] ?? "";
        const current = gateAnswers[fieldId] ?? proposed;
        return (
          <div key={fieldId} style={{ marginBottom: "1rem" }}>
            <label style={{ display: "block", fontWeight: 500, fontSize: 14, color: "#1e3a5f", marginBottom: "0.4rem" }}>
              {label}
              {proposed && (
                <span style={{ fontWeight: 400, color: "#0369a1", fontSize: 12, marginLeft: "0.5rem" }}>
                  (AI suggested: {proposed})
                </span>
              )}
            </label>
            {field?.options && field.options.length > 0 ? (
              <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
                {field.options.map((opt) => (
                  <label key={opt} style={{ display: "flex", alignItems: "center", gap: "0.35rem", cursor: "pointer", fontSize: 14 }}>
                    <input
                      type="radio"
                      name={fieldId}
                      value={opt}
                      checked={current === opt}
                      onChange={() => setGateAnswers({ ...gateAnswers, [fieldId]: opt })}
                    />
                    {opt}
                  </label>
                ))}
              </div>
            ) : (
              <input
                type="text"
                value={current}
                onChange={(e) => setGateAnswers({ ...gateAnswers, [fieldId]: e.target.value })}
                style={{
                  width: "100%",
                  padding: "0.4rem 0.6rem",
                  border: "1px solid #bae6fd",
                  borderRadius: 4,
                  fontSize: 14,
                  boxSizing: "border-box",
                }}
              />
            )}
          </div>
        );
      })}
      <button
        onClick={onSubmit}
        disabled={isPending || lowIds.some((id) => !(gateAnswers[id] ?? appStep.proposed_values[id]))}
        style={{
          padding: "0.5rem 1.5rem",
          background: "#0ea5e9",
          color: "#fff",
          border: "none",
          borderRadius: 6,
          cursor: isPending ? "not-allowed" : "pointer",
          fontSize: 14,
          fontWeight: 600,
        }}
      >
        {isPending ? "Continuing…" : "Continue Application"}
      </button>
      {error && <p style={{ color: "red", marginTop: 8, fontSize: 13 }}>{error}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Awaiting submit panel
// ---------------------------------------------------------------------------

function AwaitingSubmitPanel({ appStep, onSubmit, onCancel, isPendingSubmit, isPendingCancel, appId, error }: {
  appStep: ApplyStepResponse;
  onSubmit: (correctedValues: Record<string, string>) => void;
  onCancel: (appId: string) => void;
  isPendingSubmit: boolean;
  isPendingCancel: boolean;
  appId: string;
  error: string | null;
}) {
  // edits keyed by field label (label is stable, id can be synthetic)
  const [edits, setEdits] = useState<Record<string, string>>({});

  const setEdit = (label: string, value: string) =>
    setEdits((prev) => ({ ...prev, [label]: value }));

  const hasEdits = Object.keys(edits).length > 0;

  return (
    <div style={{ marginTop: "1rem" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: "0.75rem", marginBottom: "1rem" }}>
        <h3 style={{ margin: 0 }}>Review Filled Answers</h3>
        <span style={{ fontSize: 12, color: "#6b7280" }}>Click a value to correct it</span>
      </div>

      {hasEdits && (
        <div style={{ marginBottom: "1rem", padding: "0.6rem 0.875rem", background: "#fefce8", border: "1px solid #fde047", borderRadius: 6, fontSize: 12, color: "#713f12" }}>
          Corrections will be saved for future applications. The current SEEK submission uses the originally filled values.
        </div>
      )}

      {appStep.step_history.map((entry, si) => {
        const fields: Array<{ id: string; label: string; field_type?: string }> = entry.step.fields ?? [];
        const filled: Record<string, string> = entry.filled_values ?? {};
        const rows = fields.filter((f) => filled[f.id] !== undefined);
        if (rows.length === 0) return null;
        return (
          <div key={si} style={{ marginBottom: "1rem" }}>
            <div style={{ padding: "0.4rem 0.75rem", background: "#f3f4f6", borderRadius: "6px 6px 0 0", fontSize: 12, color: "#6b7280", fontWeight: 500 }}>
              Step {si + 1}
            </div>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, border: "1px solid #e5e7eb", borderTop: "none" }}>
              <tbody>
                {rows.map((f, ri) => {
                  const original = filled[f.id];
                  const edited = edits[f.label];
                  const isEdited = edited !== undefined && edited !== original;
                  return (
                    <tr key={f.id} style={{ background: ri % 2 === 0 ? "#fff" : "#f9fafb" }}>
                      <td style={{ padding: "0.5rem 0.75rem", color: "#374151", fontWeight: 500, width: "38%", borderBottom: "1px solid #f3f4f6", verticalAlign: "middle" }}>
                        {f.label}
                      </td>
                      <td style={{ padding: "0.35rem 0.75rem", borderBottom: "1px solid #f3f4f6", verticalAlign: "top" }}>
                        {f.field_type === "textarea" ? (
                          <textarea
                            defaultValue={original}
                            onChange={(e) => setEdit(f.label, e.target.value)}
                            rows={6}
                            style={{
                              width: "100%",
                              border: isEdited ? "1px solid #f59e0b" : "1px solid #e5e7eb",
                              background: isEdited ? "#fffbeb" : "#fff",
                              borderRadius: 4,
                              padding: "0.35rem 0.5rem",
                              fontSize: 13,
                              color: isEdited ? "#92400e" : "#374151",
                              outline: "none",
                              resize: "vertical",
                              boxSizing: "border-box",
                              lineHeight: 1.5,
                              fontFamily: "inherit",
                            }}
                          />
                        ) : (
                          <input
                            type="text"
                            defaultValue={original}
                            onChange={(e) => setEdit(f.label, e.target.value)}
                            style={{
                              width: "100%",
                              border: isEdited ? "1px solid #f59e0b" : "1px solid transparent",
                              background: isEdited ? "#fffbeb" : "transparent",
                              borderRadius: 4,
                              padding: "0.25rem 0.4rem",
                              fontSize: 13,
                              color: isEdited ? "#92400e" : "#6b7280",
                              outline: "none",
                              cursor: "text",
                              boxSizing: "border-box",
                            }}
                            onFocus={(e) => {
                              if (!isEdited) e.target.style.border = "1px solid #d1d5db";
                            }}
                            onBlur={(e) => {
                              if (!isEdited) e.target.style.border = "1px solid transparent";
                            }}
                          />
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        );
      })}

      <div style={{ display: "flex", gap: "0.75rem", marginTop: "1.5rem" }}>
        <button
          onClick={() => onSubmit(edits)}
          disabled={isPendingSubmit}
          style={{ padding: "0.5rem 1.5rem", background: "#7c3aed", color: "#fff", border: "none", borderRadius: 6, cursor: isPendingSubmit ? "not-allowed" : "pointer", fontSize: 14, fontWeight: 600 }}
        >
          {isPendingSubmit ? "Submitting…" : "Submit to SEEK"}
        </button>
        <button
          onClick={() => onCancel(appId)}
          disabled={isPendingCancel}
          style={{ padding: "0.5rem 1.25rem", background: "#fff", color: "#374151", border: "1px solid #d1d5db", borderRadius: 6, cursor: isPendingCancel ? "not-allowed" : "pointer", fontSize: 14 }}
        >
          Cancel
        </button>
      </div>
      {error && <p style={{ color: "red", marginTop: 8, fontSize: 13 }}>{error}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function ReviewDeskPage() {
  const queryClient = useQueryClient();
  const [selectedAppId, setSelectedAppId] = useState<string | null>(null);
  const [coverLetterText, setCoverLetterText] = useState<string>("");
  const [gateAnswers, setGateAnswers] = useState<Record<string, string>>({});
  const [showEvidence, setShowEvidence] = useState(true);

  // Spin keyframe injected once
  useEffect(() => {
    const id = "rdp-spin-style";
    if (!document.getElementById(id)) {
      const s = document.createElement("style");
      s.id = id;
      s.textContent = "@keyframes spin { to { transform: rotate(360deg); } }";
      document.head.appendChild(s);
    }
  }, []);

  // ---------------------------------------------------------------------------
  // Queries
  // ---------------------------------------------------------------------------

  const appsQuery = useQuery({
    queryKey: ["applications"],
    queryFn: () => fetchApplications(),
    refetchInterval: 3000,
    select: (data) => data.filter((a) => a.state !== "applied"),
  });

  const selectedApp = appsQuery.data?.find((a) => a.id === selectedAppId) ?? null;
  const isActiveState = selectedApp ? ACTIVE_STATES.includes(selectedApp.state) : false;

  const detailQuery = useQuery({
    queryKey: ["applicationDetail", selectedAppId],
    queryFn: () => fetchApplicationDetail(selectedAppId!),
    enabled: !!selectedAppId,
    refetchInterval: isActiveState ? 2000 : false,
  });

  // Sync cover letter from detail when it loads / changes (only if user hasn't edited yet)
  const detail = detailQuery.data ?? null;
  useEffect(() => {
    if (detail?.cover_letter !== undefined) {
      setCoverLetterText(detail.cover_letter);
    }
  }, [detail?.application?.id, detail?.cover_letter]);

  // Reset gate answers when selection changes
  useEffect(() => {
    setGateAnswers({});
    setShowEvidence(true);
  }, [selectedAppId]);

  // Sorted applications — most recently updated first
  const sortedApps = useMemo(() => {
    if (!appsQuery.data) return [];
    return [...appsQuery.data].sort(
      (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    );
  }, [appsQuery.data]);

  // Parse last_apply_step from detail
  const parsedApplyStep = useMemo((): ApplyStepResponse | null => {
    if (!detail?.last_apply_step) return null;
    try {
      const parsed = JSON.parse(detail.last_apply_step);
      return applyStepResponseSchema.parse(parsed);
    } catch {
      return null;
    }
  }, [detail?.last_apply_step]);

  // Parse match evidence from detail
  const parsedEvidence = useMemo(() => {
    if (!detail?.match_evidence) return [];
    return parseEvidence(detail.match_evidence);
  }, [detail?.match_evidence]);

  // ---------------------------------------------------------------------------
  // Mutations
  // ---------------------------------------------------------------------------

  const approveMutation = useMutation({
    mutationFn: ({ appId, coverLetter }: { appId: string; coverLetter: string }) =>
      approveApplication(appId, coverLetter),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["applicationDetail", selectedAppId] });
    },
  });

  const discardMutation = useMutation({
    mutationFn: (appId: string) => discardApplication(appId),
    onSuccess: () => {
      setSelectedAppId(null);
      queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
  });

  const applyMutation = useMutation({
    mutationFn: (appId: string) => enqueueApply(appId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["applicationDetail", selectedAppId] });
    },
  });

  const gateMutation = useMutation({
    mutationFn: ({ appId, runId, values }: { appId: string; runId: string; values: Record<string, string> }) =>
      enqueueGate(appId, runId, values),
    onSuccess: () => {
      setGateAnswers({});
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["applicationDetail", selectedAppId] });
    },
  });

  const submitMutation = useMutation({
    mutationFn: ({ appId, runId, label, correctedValues }: { appId: string; runId: string; label: string; correctedValues?: Record<string, string> }) =>
      enqueueSubmit(appId, runId, label, correctedValues),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["applicationDetail", selectedAppId] });
    },
  });

  const markSubmittedMutation = useMutation({
    mutationFn: (appId: string) => markSubmitted(appId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["applicationDetail", selectedAppId] });
    },
  });

  const cancelMutation = useMutation({
    mutationFn: (appId: string) => cancelApplication(appId),
    onSuccess: () => {
      setSelectedAppId(null);
      queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
  });

  // ---------------------------------------------------------------------------
  // Right panel content
  // ---------------------------------------------------------------------------

  function renderRightPanel() {
    if (!selectedAppId) {
      return (
        <p style={{ color: "#6b7280", marginTop: "2rem" }}>
          Select an application from the list to review it.
        </p>
      );
    }

    if (detailQuery.isLoading) {
      return <SpinnerPanel message="Loading…" />;
    }

    if (detailQuery.isError) {
      return (
        <p style={{ color: "#dc2626" }}>
          Failed to load application.{" "}
          {detailQuery.error instanceof Error ? detailQuery.error.message : "Unknown error"}
        </p>
      );
    }

    if (!detail || !selectedApp) return null;

    const state = selectedApp.state;
    const appId = selectedApp.id;
    const jobTitle = detail.job?.title ?? selectedApp.job_title ?? "Untitled";
    const jobCompany = detail.job?.company ?? selectedApp.job_company ?? "";
    const jobSourceUrl = detail.job?.source_url ?? selectedApp.job_source_url ?? null;
    const jobSummary = detail.job?.summary ?? selectedApp.job_summary ?? null;

    // Header shared across most panels
    const header = (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1.25rem" }}>
        <div>
          <h2 style={{ margin: "0 0 0.2rem", fontSize: 18 }}>
            {jobTitle}
            {jobCompany && <span style={{ fontWeight: 400, color: "#6b7280", fontSize: 15 }}> — {jobCompany}</span>}
          </h2>
          {selectedApp.job_location && (
            <div style={{ color: "#9ca3af", fontSize: 13 }}>{selectedApp.job_location}</div>
          )}
        </div>
        {jobSourceUrl && (
          <a href={jobSourceUrl} target="_blank" rel="noreferrer" style={{ fontSize: 13, color: "#2563eb", flexShrink: 0 }}>
            View on SEEK ↗
          </a>
        )}
      </div>
    );

    // --- preparing ---
    if (state === "preparing") {
      return (
        <>
          {header}
          <SpinnerPanel
            message="Preparing application…"
            subtext="Analysing job description and generating cover letter. This takes about 30 seconds."
          />
          <div style={{ marginTop: "1rem" }}>
            <button
              onClick={() => cancelMutation.mutate(appId)}
              disabled={cancelMutation.isPending}
              style={{ padding: "0.4rem 1rem", background: "#fff", color: "#6b7280", border: "1px solid #d1d5db", borderRadius: 6, cursor: cancelMutation.isPending ? "not-allowed" : "pointer", fontSize: 13 }}
            >
              {cancelMutation.isPending ? "Cancelling…" : "Cancel"}
            </button>
            {cancelMutation.isError && (
              <span style={{ marginLeft: "0.75rem", color: "#dc2626", fontSize: 12 }}>
                {cancelMutation.error instanceof Error ? cancelMutation.error.message : "Failed to cancel"}
              </span>
            )}
          </div>
        </>
      );
    }

    // --- applying / submitting ---
    if (state === "applying") {
      return (
        <>
          {header}
          <SpinnerPanel message="Applying…" subtext="Filling out the application form on SEEK. This may take a minute." />
          <div style={{ marginTop: "1rem" }}>
            <button
              onClick={() => cancelMutation.mutate(appId)}
              disabled={cancelMutation.isPending}
              style={{ padding: "0.4rem 1rem", background: "#fff", color: "#6b7280", border: "1px solid #d1d5db", borderRadius: 6, cursor: cancelMutation.isPending ? "not-allowed" : "pointer", fontSize: 13 }}
            >
              {cancelMutation.isPending ? "Cancelling…" : "Cancel"}
            </button>
            {cancelMutation.isError && (
              <span style={{ marginLeft: "0.75rem", color: "#dc2626", fontSize: 12 }}>
                {cancelMutation.error instanceof Error ? cancelMutation.error.message : "Failed to cancel"}
              </span>
            )}
          </div>
        </>
      );
    }

    if (state === "submitting") {
      return (
        <>
          {header}
          <SpinnerPanel message="Submitting…" subtext="Submitting your application to SEEK." />
          <div style={{ marginTop: "1rem" }}>
            <button
              onClick={() => cancelMutation.mutate(appId)}
              disabled={cancelMutation.isPending}
              style={{ padding: "0.4rem 1rem", background: "#fff", color: "#6b7280", border: "1px solid #d1d5db", borderRadius: 6, cursor: cancelMutation.isPending ? "not-allowed" : "pointer", fontSize: 13 }}
            >
              {cancelMutation.isPending ? "Cancelling…" : "Cancel"}
            </button>
            {cancelMutation.isError && (
              <span style={{ marginLeft: "0.75rem", color: "#dc2626", fontSize: 12 }}>
                {cancelMutation.error instanceof Error ? cancelMutation.error.message : "Failed to cancel"}
              </span>
            )}
          </div>
        </>
      );
    }

    // --- applied ---
    if (state === "applied") {
      return (
        <>
          {header}
          <div
            style={{
              padding: "1.25rem",
              background: "#f0fdf4",
              border: "1px solid #86efac",
              borderRadius: 6,
            }}
          >
            <p style={{ fontWeight: 700, color: "#15803d", fontSize: 16, margin: "0 0 0.5rem" }}>
              Application successfully submitted to SEEK
            </p>
            <p style={{ color: "#166534", fontSize: 13, margin: 0 }}>
              Submitted {relativeTime(selectedApp.updated_at)}
            </p>
          </div>
        </>
      );
    }

    // --- unsuitable ---
    if (state === "unsuitable") {
      return (
        <>
          {header}
          <div
            style={{
              padding: "1.25rem",
              background: "#fef3c7",
              border: "1px solid #d97706",
              borderRadius: 6,
              marginBottom: "1rem",
            }}
          >
            <p style={{ fontWeight: 600, margin: "0 0 0.5rem", color: "#92400e" }}>
              Profile does not match this role
            </p>
            {parsedEvidence.filter((e) => e.rating === "WEAK").length > 0 && (
              <ul style={{ margin: 0, paddingLeft: "1.25rem", color: "#78350f", fontSize: 14 }}>
                {parsedEvidence.filter((e) => e.rating === "WEAK").map((e, i) => (
                  <li key={i}>{e.requirement}</li>
                ))}
              </ul>
            )}
          </div>
          <button
            onClick={() => discardMutation.mutate(appId)}
            disabled={discardMutation.isPending}
            style={{
              padding: "0.5rem 1.25rem",
              background: "#fff",
              color: "#dc2626",
              border: "1px solid #dc2626",
              borderRadius: 6,
              cursor: "pointer",
              fontSize: 14,
            }}
          >
            Discard
          </button>
        </>
      );
    }

    // --- failed ---
    if (state === "failed") {
      return (
        <>
          {header}
          <div
            style={{
              padding: "1.25rem",
              background: "#fef2f2",
              border: "1px solid #fca5a5",
              borderRadius: 6,
              marginBottom: "1rem",
            }}
          >
            <p style={{ fontWeight: 600, margin: "0 0 0.5rem", color: "#991b1b" }}>Apply workflow failed</p>
            {parsedApplyStep?.error ? (
              <p style={{ margin: "0 0 1rem", fontFamily: "monospace", fontSize: 12, color: "#7f1d1d", background: "#fff1f2", border: "1px solid #fca5a5", borderRadius: 4, padding: "0.5rem 0.75rem", wordBreak: "break-all" }}>
                {parsedApplyStep.error}
              </p>
            ) : (
              <p style={{ margin: "0 0 1rem", color: "#7f1d1d", fontSize: 14 }}>
                Something went wrong during the automated application.
              </p>
            )}
            <div style={{ display: "flex", gap: "0.75rem" }}>
              <button
                onClick={() => approveMutation.mutate({ appId, coverLetter: coverLetterText })}
                disabled={approveMutation.isPending}
                style={{
                  padding: "0.5rem 1.25rem",
                  background: "#2563eb",
                  color: "#fff",
                  border: "none",
                  borderRadius: 6,
                  cursor: "pointer",
                  fontSize: 14,
                }}
              >
                Retry
              </button>
              <button
                onClick={() => discardMutation.mutate(appId)}
                disabled={discardMutation.isPending}
                style={{
                  padding: "0.5rem 1.25rem",
                  background: "#fff",
                  color: "#dc2626",
                  border: "1px solid #dc2626",
                  borderRadius: 6,
                  cursor: "pointer",
                  fontSize: 14,
                }}
              >
                Discard
              </button>
            </div>
          </div>
        </>
      );
    }

    // --- paused ---
    if (state === "paused") {
      const pageType = parsedApplyStep?.step?.page_type ?? null;

      if (pageType === "external_redirect") {
        const pageUrl = parsedApplyStep?.step?.page_url;
        const portalType = parsedApplyStep?.step?.portal_type;
        return (
          <>
            {header}
            <div
              style={{
                padding: "1.25rem",
                background: "#fffbeb",
                border: "1px solid #d97706",
                borderRadius: 6,
              }}
            >
              <p style={{ fontWeight: 600, margin: "0 0 0.5rem", color: "#92400e", fontSize: 15 }}>
                External application portal
                {portalType && portalType !== "unknown" && (
                  <span style={{ marginLeft: "0.5rem", padding: "2px 8px", background: "#fef3c7", border: "1px solid #d97706", borderRadius: 99, fontSize: 12, textTransform: "capitalize" }}>
                    {portalType}
                  </span>
                )}
              </p>
              <p style={{ margin: "0 0 1rem", color: "#78350f", fontSize: 14 }}>
                This job applies through an external portal. Open the link below, complete the application there, then click <strong>Mark as Submitted</strong>.
              </p>
              {pageUrl && (
                <a href={pageUrl} target="_blank" rel="noreferrer" style={{ display: "inline-block", marginBottom: "1rem", fontSize: 13, color: "#1d4ed8", wordBreak: "break-all" }}>
                  {pageUrl} ↗
                </a>
              )}
              <div style={{ display: "flex", gap: "0.75rem" }}>
                <button
                  onClick={() => markSubmittedMutation.mutate(appId)}
                  disabled={markSubmittedMutation.isPending}
                  style={{ padding: "0.5rem 1.25rem", background: "#16a34a", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer", fontSize: 14, fontWeight: 600 }}
                >
                  {markSubmittedMutation.isPending ? "Saving…" : "Mark as Submitted"}
                </button>
                <button
                  onClick={() => discardMutation.mutate(appId)}
                  disabled={discardMutation.isPending}
                  style={{ padding: "0.5rem 1.25rem", background: "#fff", color: "#dc2626", border: "1px solid #dc2626", borderRadius: 6, cursor: "pointer", fontSize: 14 }}
                >
                  Discard
                </button>
              </div>
            </div>
          </>
        );
      }

      // auth_required | session_lost | other paused
      const pauseReason = parsedApplyStep?.pause_reason ?? null;
      const isAuth = pageType === "auth_required";
      const isSessionLost = pauseReason === "session_lost";
      const pageUrl = parsedApplyStep?.step?.page_url;
      return (
        <>
          {header}
          <div
            style={{
              padding: "1.25rem",
              background: "#fef3c7",
              border: "1px solid #d97706",
              borderRadius: 6,
            }}
          >
            <p style={{ fontWeight: 600, margin: "0 0 0.5rem", color: "#92400e", fontSize: 15 }}>
              {isSessionLost ? "Browser session lost" : isAuth ? "SEEK session expired" : "Application workflow paused"}
            </p>
            <p style={{ margin: "0 0 1rem", color: "#78350f", fontSize: 14 }}>
              {isSessionLost
                ? "The tools service was restarted and the browser session was lost. Click Re-approve to start the application again from scratch."
                : isAuth
                ? "You need to log back in to SEEK. Open SEEK in a browser, log in, then click Try Again."
                : `Unexpected page encountered (${pageType ?? "unknown"}). You can try again or discard this application.`}
            </p>
            {pageUrl && (
              <a href={pageUrl} target="_blank" rel="noreferrer" style={{ display: "inline-block", marginBottom: "1rem", fontSize: 13, color: "#1d4ed8" }}>
                {pageUrl} ↗
              </a>
            )}
            <div style={{ display: "flex", gap: "0.75rem" }}>
              <button
                onClick={() => approveMutation.mutate({ appId, coverLetter: coverLetterText })}
                disabled={approveMutation.isPending}
                style={{ padding: "0.5rem 1.25rem", background: "#2563eb", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer", fontSize: 14 }}
              >
                {isSessionLost ? "Re-approve" : "Try Again"}
              </button>
              <button
                onClick={() => discardMutation.mutate(appId)}
                disabled={discardMutation.isPending}
                style={{ padding: "0.5rem 1.25rem", background: "#fff", color: "#dc2626", border: "1px solid #dc2626", borderRadius: 6, cursor: "pointer", fontSize: 14 }}
              >
                Discard
              </button>
            </div>
          </div>
        </>
      );
    }

    // --- needs_review ---
    if (state === "needs_review" && parsedApplyStep) {
      return (
        <>
          {header}
          <GatePanel
            appStep={parsedApplyStep}
            gateAnswers={gateAnswers}
            setGateAnswers={setGateAnswers}
            onSubmit={() => {
              const merged = { ...parsedApplyStep.proposed_values, ...gateAnswers };
              gateMutation.mutate({ appId, runId: parsedApplyStep.workflow_run_id, values: merged });
            }}
            isPending={gateMutation.isPending}
            error={gateMutation.isError ? (gateMutation.error instanceof Error ? gateMutation.error.message : "Unknown error") : null}
          />
        </>
      );
    }

    // --- awaiting_submit ---
    if (state === "awaiting_submit" && parsedApplyStep) {
      return (
        <>
          {header}
          <AwaitingSubmitPanel
            appStep={parsedApplyStep}
            onSubmit={(correctedValues) => submitMutation.mutate({ appId, runId: parsedApplyStep.workflow_run_id, label: parsedApplyStep.submit_action_label ?? "Submit Application", correctedValues })}
            onCancel={(id) => approveMutation.mutate({ appId: id, coverLetter: coverLetterText })}
            isPendingSubmit={submitMutation.isPending}
            isPendingCancel={approveMutation.isPending}
            appId={appId}
            error={submitMutation.isError ? (submitMutation.error instanceof Error ? submitMutation.error.message : "Unknown error") : null}
          />
        </>
      );
    }

    // --- approved ---
    if (state === "approved") {
      return (
        <>
          {header}
          <div
            style={{
              padding: "1.25rem",
              background: "#f0fdf4",
              border: "1px solid #86efac",
              borderRadius: 6,
              marginBottom: "1.25rem",
            }}
          >
            <p style={{ fontWeight: 600, color: "#15803d", margin: "0 0 0.75rem" }}>
              Application approved. Ready to apply to SEEK.
            </p>
            <button
              onClick={() => applyMutation.mutate(appId)}
              disabled={applyMutation.isPending}
              style={{
                padding: "0.5rem 1.5rem",
                background: "#2563eb",
                color: "#fff",
                border: "none",
                borderRadius: 6,
                cursor: applyMutation.isPending ? "not-allowed" : "pointer",
                fontSize: 14,
                fontWeight: 600,
              }}
            >
              {applyMutation.isPending ? "Queuing…" : "Start Applying"}
            </button>
            {applyMutation.isError && (
              <p style={{ color: "red", marginTop: 8, fontSize: 13 }}>
                {applyMutation.error instanceof Error ? applyMutation.error.message : "Unknown error"}
              </p>
            )}
          </div>
        </>
      );
    }

    // --- prepared ---
    if (state === "prepared") {
      return (
        <>
          {header}

          <MatchEvidencePanel
            evidence={parsedEvidence}
            showEvidence={showEvidence}
            setShowEvidence={setShowEvidence}
          />

          {/* Cover letter + Job description side by side */}
          <div style={{ display: "flex", gap: "1.5rem", marginBottom: "1.5rem" }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <h3 style={{ marginTop: 0 }}>Cover Letter</h3>
              <textarea
                value={coverLetterText}
                onChange={(e) => setCoverLetterText(e.target.value)}
                rows={20}
                style={{
                  width: "100%",
                  padding: "0.75rem",
                  border: "1px solid #d1d5db",
                  borderRadius: 6,
                  fontFamily: "inherit",
                  fontSize: 13,
                  resize: "vertical",
                  whiteSpace: "pre-wrap",
                  boxSizing: "border-box",
                }}
              />
            </div>
            {jobSummary && (
              <div style={{ flex: 1, minWidth: 0 }}>
                <h3 style={{ marginTop: 0 }}>Job Description</h3>
                <div
                  style={{
                    padding: "0.75rem",
                    background: "#f9fafb",
                    border: "1px solid #e5e7eb",
                    borderRadius: 6,
                    fontSize: 13,
                    color: "#374151",
                    lineHeight: 1.7,
                    whiteSpace: "pre-wrap",
                    overflowY: "auto",
                    maxHeight: "480px",
                  }}
                >
                  {jobSummary}
                </div>
              </div>
            )}
          </div>

          {/* Action buttons */}
          <div style={{ display: "flex", gap: "0.75rem" }}>
            <button
              onClick={() => approveMutation.mutate({ appId, coverLetter: coverLetterText })}
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
              {approveMutation.isPending ? "Approving…" : "Approve"}
            </button>
            <button
              onClick={() => discardMutation.mutate(appId)}
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
          {approveMutation.isError && (
            <p style={{ color: "red", marginTop: 8, fontSize: 13 }}>
              {approveMutation.error instanceof Error ? approveMutation.error.message : "Unknown error"}
            </p>
          )}
        </>
      );
    }

    // Fallback for unknown states
    return (
      <>
        {header}
        <p style={{ color: "#6b7280", fontSize: 14 }}>
          Application is in state: <strong>{state}</strong>. No action panel available.
        </p>
      </>
    );
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div style={{ display: "flex", gap: "1.5rem", padding: "1.5rem" }}>
      {/* Left panel */}
      <div style={{ width: 280, flexShrink: 0 }}>
        <h2 style={{ marginTop: 0, marginBottom: "1rem" }}>Review Desk</h2>

        {appsQuery.isLoading && <p style={{ color: "#6b7280", fontSize: 13 }}>Loading…</p>}
        {appsQuery.isError && (
          <p style={{ color: "#dc2626", fontSize: 13 }}>
            Failed to load applications.
          </p>
        )}
        {appsQuery.isSuccess && sortedApps.length === 0 && (
          <p style={{ color: "#6b7280", fontSize: 13 }}>
            No applications yet. Queue some jobs from the Jobs page.
          </p>
        )}

        {sortedApps.map((app) => (
          <AppListItem
            key={app.id}
            app={app}
            selected={app.id === selectedAppId}
            onClick={() => setSelectedAppId(app.id)}
          />
        ))}
      </div>

      {/* Right panel */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {renderRightPanel()}
      </div>
    </div>
  );
}
