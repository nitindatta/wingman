import { useState, useMemo, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import RunLog from "../components/RunLog";
import { ExternalUserQuestionsPanel } from "../components/ExternalUserQuestionsPanel";
import {
  EXTERNAL_USER_ANSWER_PREFIX,
  EXTERNAL_USER_QUESTION_PREFIX,
  fetchApplications,
  fetchApplicationDetail,
  approveApplication,
  discardApplication,
  enqueueApply,
  enqueueExternalHarness,
  enqueueGate,
  enqueueSubmit,
  markSubmitted,
  cancelApplication,
  resetApplication,
} from "../api/applications";
import { applyStepResponseSchema, type Application, type ApplyStepResponse, type FieldInfo } from "../api/schemas";

// ---------------------------------------------------------------------------
// State metadata
// ---------------------------------------------------------------------------

const STATE_META: Record<string, { label: string; color: string }> = {
  preparing:       { label: "Preparing...",    color: "var(--text-muted)" },
  prepared:        { label: "Ready to review", color: "#2563eb" },
  approved:        { label: "Approved",        color: "#16a34a" },
  applying:        { label: "Applying...",     color: "var(--text-muted)" },
  needs_review:    { label: "Needs review",    color: "#d97706" },
  awaiting_submit: { label: "Ready to submit", color: "#7c3aed" },
  submitting:      { label: "Submitting...",   color: "var(--text-muted)" },
  applied:         { label: "Applied",         color: "#16a34a" },
  failed:          { label: "Failed",          color: "#dc2626" },
  paused:          { label: "Paused",          color: "#d97706" },
  unsuitable:      { label: "Not a fit",       color: "var(--text-subtle)" },
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

type FitSummary = {
  score: number;
  strong: number;
  moderate: number;
  weak: number;
  total: number;
  topWeak: EvidenceItem[];
};

function parseEvidence(raw: string): EvidenceItem[] {
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const m = line.match(/^\[(STRONG|MODERATE|WEAK)\]\s*(.+?)\s*(?:→|->)\s*(.+)$/);
      if (m) return { rating: m[1] as "STRONG" | "MODERATE" | "WEAK", requirement: m[2], evidence: m[3] };
      return null;
    })
    .filter((x): x is EvidenceItem => x !== null);
}

function parseJsonStringArray(raw: string | null | undefined): string[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(String).filter(Boolean) : [];
  } catch {
    return [];
  }
}

function buildFitSummary(evidence: EvidenceItem[]): FitSummary {
  const strong = evidence.filter((item) => item.rating === "STRONG").length;
  const moderate = evidence.filter((item) => item.rating === "MODERATE").length;
  const weak = evidence.filter((item) => item.rating === "WEAK").length;
  const total = evidence.length;
  const score = total === 0 ? 0 : Math.round(((strong + moderate * 0.68 + weak * 0.25) / total) * 100);
  return {
    score,
    strong,
    moderate,
    weak,
    total,
    topWeak: evidence.filter((item) => item.rating === "WEAK").slice(0, 4),
  };
}

function improvementHintForWeakEvidence(item: EvidenceItem): string {
  const text = `${item.requirement} ${item.evidence}`.toLowerCase();
  if (text.includes("no safe claim")) {
    return "Add or refine a canonical evidence item that directly proves this requirement.";
  }
  if (text.includes("rapid") || text.includes("prototype") || text.includes("production-ready") || text.includes("vibe coding")) {
    return "Make the matching project explicitly say how quickly you prototyped, how it became production-ready, and how AI-assisted development was used.";
  }
  if (text.includes("year") || text.includes("experience")) {
    return "Add explicit years/scope to the relevant evidence item instead of relying on role titles alone.";
  }
  if (text.includes("security") || text.includes("permission") || text.includes("prompt injection")) {
    return "Add security guardrails, permissioning, or prompt-safety proof points if you have real examples.";
  }
  if (text.includes("cost") || text.includes("token")) {
    return "Add concrete cost-control or token-usage examples if they are true.";
  }
  return "Strengthen the matched evidence with a clearer task, outcome, metric, or exact keyword from the role.";
}

function FitAnalysisPanel({ evidence, gaps, fitScore }: { evidence: EvidenceItem[]; gaps: string[]; fitScore?: number | null }) {
  const summary = buildFitSummary(evidence);
  const hasExactScore = typeof fitScore === "number";
  const displayScore = hasExactScore ? Math.round(fitScore * 100) : summary.score;
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: "1rem",
        marginBottom: "1rem",
        background: "var(--surface)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "1rem" }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--text-muted)" }}>
            Fit analysis
          </div>
          <div style={{ marginTop: 4, fontSize: 14, color: "var(--text-secondary)" }}>
            {hasExactScore ? "Fit score" : "Evidence score estimate"}: <strong>{displayScore}%</strong>
          </div>
        </div>
        <div style={{ display: "flex", gap: "0.5rem", fontSize: 12 }}>
          <span style={{ color: "#15803d" }}>{summary.strong} strong</span>
          <span style={{ color: "#92400e" }}>{summary.moderate} moderate</span>
          <span style={{ color: "#991b1b" }}>{summary.weak} weak</span>
        </div>
      </div>

      {gaps.length > 0 && (
        <div style={{ marginTop: "0.85rem" }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", marginBottom: 6 }}>
            Blocking gaps
          </div>
          <ul style={{ margin: 0, paddingLeft: "1.25rem", color: "#4b5563", fontSize: 13, lineHeight: 1.5 }}>
            {gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </div>
      )}

      {summary.topWeak.length > 0 && (
        <div style={{ marginTop: "0.85rem" }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", marginBottom: 6 }}>
            How to improve the profile for this role
          </div>
          <div style={{ display: "grid", gap: "0.5rem" }}>
            {summary.topWeak.map((item) => (
              <div key={`${item.requirement}-${item.evidence}`} style={{ border: "1px solid var(--border-subtle)", borderRadius: 6, padding: "0.65rem", background: "var(--surface-subtle)" }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>{item.requirement}</div>
                <div style={{ marginTop: 4, fontSize: 12, color: "var(--text-muted)" }}>
                  Current evidence: {item.evidence}
                </div>
                <div style={{ marginTop: 4, fontSize: 12, color: "#92400e" }}>
                  {improvementHintForWeakEvidence(item)}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
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
          color: "var(--text-secondary)",
          display: "flex",
          alignItems: "center",
          gap: "0.4rem",
          marginBottom: "0.5rem",
        }}
      >
        <span>{showEvidence ? "▾" : "▸"}</span>
        Match Breakdown
        <span style={{ fontWeight: 400, fontSize: 13, color: "var(--text-muted)", marginLeft: "0.25rem" }}>
          ({evidence.filter((e) => e.rating === "STRONG").length} strong ·{" "}
          {evidence.filter((e) => e.rating === "MODERATE").length} moderate ·{" "}
          {evidence.filter((e) => e.rating === "WEAK").length} weak)
        </span>
      </button>
      {showEvidence && (
        <div style={{ border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
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
                  background: i % 2 === 0 ? "var(--surface)" : "var(--surface-subtle)",
                  borderTop: i > 0 ? "1px solid var(--border-subtle)" : "none",
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
                <span style={{ color: "var(--text-primary)", lineHeight: 1.4 }}>{item.requirement}</span>
                <span style={{ color: "var(--text-muted)", lineHeight: 1.4 }}>{item.evidence}</span>
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
        border: "2px solid var(--border)",
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
  const meta = STATE_META[app.state] ?? { label: app.state, color: "var(--text-muted)" };
  const isActive = ACTIVE_STATES.includes(app.state);
  const title = app.job_title ?? "Untitled";
  const company = app.job_company ?? "";
  return (
    <div
      onClick={onClick}
      style={{
        padding: "0.75rem",
        marginBottom: "0.5rem",
        border: selected ? "2px solid var(--selected-border)" : "1px solid var(--border)",
        borderRadius: 6,
        cursor: "pointer",
        background: selected ? "var(--selected-bg)" : "var(--surface)",
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
        <div style={{ fontWeight: 600, fontSize: 14, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", color: "var(--text-primary)" }}>
          {title}
        </div>
        {company && (
          <div style={{ color: "var(--text-muted)", fontSize: 12, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {company}
          </div>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", marginTop: 2 }}>
          <span style={{ fontSize: 11, color: meta.color, fontWeight: 500 }}>{meta.label}</span>
          <span style={{ fontSize: 11, color: "var(--text-subtle)" }}>
            · {isActive ? elapsedTime(app.updated_at) : relativeTime(app.updated_at)}
          </span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared cover letter section
// ---------------------------------------------------------------------------

function CoverLetterSection({ text, onChange, readOnly }: {
  text: string;
  onChange?: (v: string) => void;
  readOnly?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(false);
  if (!text) return null;
  return (
    <div style={{ marginBottom: "1.5rem" }}>
      <button
        onClick={() => setCollapsed(!collapsed)}
        style={{ background: "none", border: "none", cursor: "pointer", padding: 0, fontSize: 14, fontWeight: 600, color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: "0.4rem", marginBottom: "0.5rem" }}
      >
        <span>{collapsed ? "▸" : "▾"}</span> Cover Letter
      </button>
      {!collapsed && (
        <textarea
          value={text}
          onChange={onChange ? (e) => onChange(e.target.value) : undefined}
          readOnly={readOnly}
          rows={12}
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
            background: readOnly ? "var(--surface-subtle)" : "var(--surface)",
            color: "var(--text-secondary)",
          }}
        />
      )}
    </div>
  );
}

function ReviewMaterialsSection({
  evidence,
  gaps,
  fitScore,
  showEvidence,
  setShowEvidence,
  coverLetterText,
  setCoverLetterText,
  jobSummary,
}: {
  evidence: EvidenceItem[];
  gaps: string[];
  fitScore?: number | null;
  showEvidence: boolean;
  setShowEvidence: (v: boolean) => void;
  coverLetterText: string;
  setCoverLetterText: (value: string) => void;
  jobSummary: string | null;
}) {
  const hasFitContext = evidence.length > 0 || gaps.length > 0 || typeof fitScore === "number";

  return (
    <>
      {hasFitContext && (
        <FitAnalysisPanel evidence={evidence} gaps={gaps} fitScore={fitScore} />
      )}

      <MatchEvidencePanel
        evidence={evidence}
        showEvidence={showEvidence}
        setShowEvidence={setShowEvidence}
      />

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
                background: "var(--surface-subtle)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                fontSize: 13,
                color: "var(--text-secondary)",
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
    </>
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
          border: "3px solid var(--border)",
          borderTopColor: "#6b7280",
          borderRadius: "50%",
          animation: "spin 0.8s linear infinite",
        }}
      />
      <p style={{ fontWeight: 600, fontSize: 16, margin: 0, color: "var(--text-secondary)" }}>{message}</p>
      {subtext && <p style={{ fontSize: 14, color: "var(--text-muted)", margin: 0, maxWidth: 360 }}>{subtext}</p>}
    </div>
  );
}

function buildFieldLookup(appStep: ApplyStepResponse): Map<string, FieldInfo> {
  const fieldsById = new Map<string, FieldInfo>();
  const addFields = (fields: FieldInfo[] | undefined) => {
    for (const field of fields ?? []) {
      if (field.id && !fieldsById.has(field.id)) {
        fieldsById.set(field.id, field);
      }
    }
  };

  addFields(appStep.step?.fields);
  for (const entry of appStep.step_history) {
    addFields(entry.step.fields);
  }
  return fieldsById;
}

function isSyntheticQuestionText(value: string, fieldId: string): boolean {
  const trimmed = value.trim();
  return (
    trimmed.length === 0 ||
    trimmed === fieldId ||
    /^question[-_]/i.test(trimmed) ||
    /^questionnaire\./i.test(trimmed)
  );
}

function fallbackQuestionLabel(fieldId: string): string {
  const match = fieldId.match(/qbg_(\d+)q/i);
  return match ? `Screening question ${match[1]}` : "Screening question";
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
  const fieldsById = buildFieldLookup(appStep);
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
        const field = fieldsById.get(fieldId);
        const rawLabel = field?.label ?? "";
        const label = isSyntheticQuestionText(rawLabel, fieldId) ? fallbackQuestionLabel(fieldId) : rawLabel;
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
                  <label key={opt} style={{ display: "flex", alignItems: "center", gap: "0.35rem", cursor: "pointer", fontSize: 14, color: "#1e3a5f" }}>
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
                  background: "#fff",
                  color: "#0c4a6e",
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
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Click a value to correct it</span>
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
            <div style={{ padding: "0.4rem 0.75rem", background: "var(--surface-muted)", borderRadius: "6px 6px 0 0", fontSize: 12, color: "var(--text-muted)", fontWeight: 500 }}>
              Step {si + 1}
            </div>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, border: "1px solid var(--border)", borderTop: "none" }}>
              <tbody>
                {rows.map((f, ri) => {
                  const original = filled[f.id];
                  const edited = edits[f.label];
                  const isEdited = edited !== undefined && edited !== original;
                  return (
                    <tr key={f.id} style={{ background: ri % 2 === 0 ? "var(--surface)" : "var(--surface-subtle)" }}>
                      <td style={{ padding: "0.5rem 0.75rem", color: "var(--text-secondary)", fontWeight: 500, width: "38%", borderBottom: "1px solid var(--border-subtle)", verticalAlign: "middle" }}>
                        {f.label}
                      </td>
                      <td style={{ padding: "0.35rem 0.75rem", borderBottom: "1px solid var(--border-subtle)", verticalAlign: "top" }}>
                        {f.field_type === "textarea" ? (
                          <textarea
                            defaultValue={original}
                            onChange={(e) => setEdit(f.label, e.target.value)}
                            rows={6}
                            style={{
                              width: "100%",
                              border: isEdited ? "1px solid #f59e0b" : "1px solid var(--border)",
                              background: isEdited ? "#fffbeb" : "var(--surface)",
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
          style={{ padding: "0.5rem 1.25rem", background: "var(--surface)", color: "var(--text-secondary)", border: "1px solid #d1d5db", borderRadius: 6, cursor: isPendingCancel ? "not-allowed" : "pointer", fontSize: 14 }}
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
  const [pendingRunId, setPendingRunId] = useState<string | null>(null);

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
  const fitSummary = useMemo(() => buildFitSummary(parsedEvidence), [parsedEvidence]);

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
    onSuccess: (data) => {
      setPendingRunId(data.workflow_run_id);
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["applicationDetail", selectedAppId] });
    },
  });

  const externalHarnessMutation = useMutation({
    mutationFn: ({ appId, targetUrl }: { appId: string; targetUrl?: string }) =>
      enqueueExternalHarness(appId, targetUrl),
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

  const resetMutation = useMutation({
    mutationFn: (appId: string) => resetApplication(appId),
    onSuccess: () => {
      setGateAnswers({});
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["applicationDetail", selectedAppId] });
    },
  });

  // ---------------------------------------------------------------------------
  // Right panel content
  // ---------------------------------------------------------------------------

  function renderRightPanel() {
    if (!selectedAppId) {
      return (
        <p style={{ color: "var(--text-muted)", marginTop: "2rem" }}>
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
    const fitGaps = parseJsonStringArray(detail.application.gaps_json);
    const canResetApply = [
      "approved",
      "applying",
      "needs_review",
      "awaiting_submit",
      "submitting",
      "paused",
      "failed",
      "applied",
    ].includes(state);

    // Header shared across most panels
    const header = (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1.25rem" }}>
        <div>
          <h2 style={{ margin: "0 0 0.2rem", fontSize: 18, color: "var(--text-primary)" }}>
            {jobTitle}
            {jobCompany && <span style={{ fontWeight: 400, color: "var(--text-muted)", fontSize: 15 }}> — {jobCompany}</span>}
          </h2>
          {selectedApp.job_location && (
            <div style={{ color: "var(--text-subtle)", fontSize: 13 }}>{selectedApp.job_location}</div>
          )}
        </div>
        {canResetApply && (
          <button
            onClick={() => resetMutation.mutate(appId)}
            disabled={resetMutation.isPending}
            title="Clear apply progress and return this application to Approved for testing"
            style={{
              padding: "0.35rem 0.75rem",
              background: "var(--surface)",
              color: "#92400e",
              border: "1px solid #f59e0b",
              borderRadius: 6,
              cursor: resetMutation.isPending ? "not-allowed" : "pointer",
              fontSize: 12,
              fontWeight: 600,
              marginLeft: "1rem",
              flexShrink: 0,
            }}
          >
            {resetMutation.isPending ? "Resetting..." : "Reset Apply"}
          </button>
        )}
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
              style={{ padding: "0.4rem 1rem", background: "var(--surface)", color: "var(--text-muted)", border: "1px solid #d1d5db", borderRadius: 6, cursor: cancelMutation.isPending ? "not-allowed" : "pointer", fontSize: 13 }}
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
              style={{ padding: "0.4rem 1rem", background: "var(--surface)", color: "var(--text-muted)", border: "1px solid #d1d5db", borderRadius: 6, cursor: cancelMutation.isPending ? "not-allowed" : "pointer", fontSize: 13 }}
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
              style={{ padding: "0.4rem 1rem", background: "var(--surface)", color: "var(--text-muted)", border: "1px solid #d1d5db", borderRadius: 6, cursor: cancelMutation.isPending ? "not-allowed" : "pointer", fontSize: 13 }}
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
            {parsedEvidence.length > 0 && (
              <p style={{ margin: "0 0 0.75rem", color: "#78350f", fontSize: 14 }}>
                The fit engine found {fitSummary.strong} strong, {fitSummary.moderate} moderate, and{" "}
                {fitSummary.weak} weak evidence matches.
              </p>
            )}
            {parsedEvidence.filter((e) => e.rating === "WEAK").length > 0 && (
              <ul style={{ margin: 0, paddingLeft: "1.25rem", color: "#78350f", fontSize: 14 }}>
                {parsedEvidence.filter((e) => e.rating === "WEAK").map((e, i) => (
                  <li key={i}>{e.requirement}</li>
                ))}
              </ul>
            )}
          </div>
          <FitAnalysisPanel evidence={parsedEvidence} gaps={fitGaps} fitScore={detail.application.fit_score} />
          <MatchEvidencePanel
            evidence={parsedEvidence}
            showEvidence={showEvidence}
            setShowEvidence={setShowEvidence}
          />
          <button
            onClick={() => discardMutation.mutate(appId)}
            disabled={discardMutation.isPending}
            style={{
              padding: "0.5rem 1.25rem",
              background: "var(--surface)",
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
                  background: "var(--surface)",
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
        const externalApply = parsedApplyStep?.external_apply ?? null;
        const pendingExternalQuestion = externalApply?.pending_user_question ?? null;
        const pendingExternalQuestions =
          externalApply?.pending_user_questions && externalApply.pending_user_questions.length > 0
            ? externalApply.pending_user_questions
            : pendingExternalQuestion
              ? [pendingExternalQuestion]
              : [];
        const runId = parsedApplyStep?.workflow_run_id;
        const pauseReason = parsedApplyStep?.pause_reason;
        const startTargetUrl = detail.application.target_application_url ?? pageUrl;
        return (
          <>
            {header}
            <CoverLetterSection text={coverLetterText} readOnly />
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
                {externalApply
                  ? "Envoy is using the external apply harness for this portal. Review the latest harness step, then continue when you are ready."
                  : "This job applies through an external portal. Open the link below, complete the application there, then click Mark as Submitted."}
              </p>
              {externalApply && (
                <div style={{ margin: "0 0 1rem", padding: "0.8rem", background: "#fff7ed", border: "1px solid #fed7aa", borderRadius: 6, color: "#7c2d12" }}>
                  <div style={{ fontSize: 13, marginBottom: 6 }}>
                    Harness status: <strong>{externalApply.status}</strong>
                    {pauseReason && <span> | {pauseReason}</span>}
                  </div>
                  {externalApply.proposed_action && (
                    <div style={{ fontSize: 13, marginBottom: 6 }}>
                      Last proposed action: <strong>{externalApply.proposed_action.action_type}</strong>
                      {externalApply.proposed_action.element_id && (
                        <span> on {externalApply.proposed_action.element_id}</span>
                      )}
                      <div style={{ marginTop: 4 }}>{externalApply.proposed_action.reason}</div>
                    </div>
                  )}
                  {pendingExternalQuestions.length === 1 && externalApply.pending_user_question && (
                    <div style={{ fontSize: 13, marginBottom: 6 }}>
                      Question: <strong>{externalApply.pending_user_question.question}</strong>
                      {externalApply.pending_user_question.context && (
                        <div style={{ marginTop: 4 }}>{externalApply.pending_user_question.context}</div>
                      )}
                    </div>
                  )}
                  {pendingExternalQuestions.length > 1 && (
                    <div style={{ fontSize: 13, marginBottom: 6 }}>
                      Questions needing your input: <strong>{pendingExternalQuestions.length}</strong>
                    </div>
                  )}
                  {externalApply.risk_flags.length > 0 && (
                    <div style={{ fontSize: 12 }}>
                      Risk flags: {externalApply.risk_flags.join(", ")}
                    </div>
                  )}
                  {runId && pendingExternalQuestions.length > 0 && (
                    <ExternalUserQuestionsPanel
                      isPending={gateMutation.isPending}
                      onSubmit={(answers) =>
                        gateMutation.mutate({
                          appId,
                          runId,
                          values: Object.fromEntries(
                            Object.entries(answers).map(([key, answer]) => [
                              pendingExternalQuestions.some((question) => question.target_element_id === key)
                                ? `${EXTERNAL_USER_ANSWER_PREFIX}${key}`
                                : `${EXTERNAL_USER_QUESTION_PREFIX}${key}`,
                              answer,
                            ]),
                          ),
                        })
                      }
                      questions={pendingExternalQuestions}
                    />
                  )}
                </div>
              )}
              {pageUrl && (
                <a href={pageUrl} target="_blank" rel="noreferrer" style={{ display: "inline-block", marginBottom: "1rem", fontSize: 13, color: "#1d4ed8", wordBreak: "break-all" }}>
                  {pageUrl} ↗
                </a>
              )}
              <div style={{ display: "flex", gap: "0.75rem" }}>
                {externalApply && !externalApply.submit_ready && runId && pendingExternalQuestions.length === 0 && (
                  <button
                    onClick={() => gateMutation.mutate({ appId, runId, values: {} })}
                    disabled={gateMutation.isPending}
                    style={{ padding: "0.5rem 1.25rem", background: "#2563eb", color: "#fff", border: "none", borderRadius: 6, cursor: gateMutation.isPending ? "not-allowed" : "pointer", fontSize: 14, fontWeight: 600 }}
                  >
                    {gateMutation.isPending ? "Continuing..." : "Continue Harness"}
                  </button>
                )}
                {!externalApply && (
                  <button
                    onClick={() => externalHarnessMutation.mutate({ appId, targetUrl: startTargetUrl })}
                    disabled={externalHarnessMutation.isPending}
                    style={{ padding: "0.5rem 1.25rem", background: "#2563eb", color: "#fff", border: "none", borderRadius: 6, cursor: externalHarnessMutation.isPending ? "not-allowed" : "pointer", fontSize: 14, fontWeight: 600 }}
                  >
                    {externalHarnessMutation.isPending ? "Starting..." : "Start Harness"}
                  </button>
                )}
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
                  style={{ padding: "0.5rem 1.25rem", background: "var(--surface)", color: "#dc2626", border: "1px solid #dc2626", borderRadius: 6, cursor: "pointer", fontSize: 14 }}
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
          <CoverLetterSection text={coverLetterText} onChange={setCoverLetterText} />
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
                style={{ padding: "0.5rem 1.25rem", background: "var(--surface)", color: "#dc2626", border: "1px solid #dc2626", borderRadius: 6, cursor: "pointer", fontSize: 14 }}
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
          <CoverLetterSection text={coverLetterText} readOnly />
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
          <CoverLetterSection text={coverLetterText} readOnly />
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
          <ReviewMaterialsSection
            evidence={parsedEvidence}
            gaps={fitGaps}
            fitScore={detail.application.fit_score}
            showEvidence={showEvidence}
            setShowEvidence={setShowEvidence}
            coverLetterText={coverLetterText}
            setCoverLetterText={setCoverLetterText}
            jobSummary={jobSummary}
          />
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
            <div style={{ display: "flex", gap: "0.75rem" }}>
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
              <button
                onClick={() => discardMutation.mutate(appId)}
                disabled={discardMutation.isPending}
                style={{ padding: "0.5rem 1.25rem", background: "var(--surface)", color: "#dc2626", border: "1px solid #dc2626", borderRadius: 6, cursor: discardMutation.isPending ? "not-allowed" : "pointer", fontSize: 14 }}
              >
                {discardMutation.isPending ? "Discarding…" : "Discard"}
              </button>
            </div>
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
          <ReviewMaterialsSection
            evidence={parsedEvidence}
            gaps={fitGaps}
            fitScore={detail.application.fit_score}
            showEvidence={showEvidence}
            setShowEvidence={setShowEvidence}
            coverLetterText={coverLetterText}
            setCoverLetterText={setCoverLetterText}
            jobSummary={jobSummary}
          />

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
        <p style={{ color: "var(--text-muted)", fontSize: 14 }}>
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
        <h2 style={{ marginTop: 0, marginBottom: "1rem", color: "var(--text-primary)" }}>Review Desk</h2>

        {appsQuery.isLoading && <p style={{ color: "var(--text-muted)", fontSize: 13 }}>Loading…</p>}
        {appsQuery.isError && (
          <p style={{ color: "#dc2626", fontSize: 13 }}>
            Failed to load applications.
          </p>
        )}
        {appsQuery.isSuccess && sortedApps.length === 0 && (
          <p style={{ color: "var(--text-muted)", fontSize: 13 }}>
            No applications yet. Queue some jobs from the Jobs page.
          </p>
        )}

        {sortedApps.map((app) => (
          <AppListItem
            key={app.id}
            app={app}
            selected={app.id === selectedAppId}
            onClick={() => { setSelectedAppId(app.id); setPendingRunId(null); }}
          />
        ))}
      </div>

      {/* Right panel */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {renderRightPanel()}
        <RunLog runId={parsedApplyStep?.workflow_run_id ?? pendingRunId ?? selectedAppId} />
      </div>
    </div>
  );
}
