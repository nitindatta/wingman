import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { EXTERNAL_USER_ANSWER_PREFIX, EXTERNAL_USER_QUESTION_PREFIX, startApply, resumeApply } from "../api/applications";
import type { ApplyStepResponse, FieldInfo } from "../api/schemas";
import { ExternalUserQuestionsPanel } from "../components/ExternalUserQuestionsPanel";
import RunLog from "../components/RunLog";

type Phase = "idle" | "starting" | "gate" | "done" | "error";

export default function ApplyPage() {
  const { applicationId } = useParams<{ applicationId: string }>();
  const navigate = useNavigate();

  const [phase, setPhase] = useState<Phase>("idle");
  const [response, setResponse] = useState<ApplyStepResponse | null>(null);
  const [editedValues, setEditedValues] = useState<Record<string, string>>({});
  const [errorMsg, setErrorMsg] = useState<string>("");

  // Start the apply workflow
  const startMutation = useMutation({
    mutationFn: () => startApply(applicationId!),
    onMutate: () => setPhase("starting"),
    onSuccess: (data) => {
      setResponse(data);
      setEditedValues({ ...data.proposed_values });
      if (data.status === "paused" || data.status === "running") {
        setPhase("gate");
      } else {
        setPhase("done");
      }
    },
    onError: (err: Error) => {
      setErrorMsg(err.message);
      setPhase("error");
    },
  });

  // Resume after user approves a step
  const resumeMutation = useMutation({
    mutationFn: ({
      actionLabel,
      approvedValues,
    }: {
      actionLabel: string;
      approvedValues?: Record<string, string>;
    }) =>
      resumeApply(response!.workflow_run_id, approvedValues ?? editedValues, actionLabel),
    onSuccess: (data) => {
      setResponse(data);
      setEditedValues({ ...data.proposed_values });
      if (data.status === "paused" || data.status === "running") {
        setPhase("gate");
      } else {
        setPhase("done");
      }
    },
    onError: (err: Error) => {
      setErrorMsg(err.message);
      setPhase("error");
    },
  });

  // Abort
  const abortMutation = useMutation({
    mutationFn: () =>
      resumeApply(response!.workflow_run_id, {}, "Continue", "abort"),
    onSuccess: () => {
      navigate("/queue");
    },
  });

  const step = response?.step;
  const isExternal = step?.is_external_portal;
  const externalApply = response?.external_apply;
  const pendingExternalQuestion = externalApply?.pending_user_question ?? null;
  const pendingExternalQuestions =
    externalApply?.pending_user_questions && externalApply.pending_user_questions.length > 0
      ? externalApply.pending_user_questions
      : pendingExternalQuestion
        ? [pendingExternalQuestion]
        : [];
  const isAuthRequired = step?.page_type === "auth_required";
  const isConfirmed = response?.status === "completed";
  const isFailed = response?.status === "failed";

  return (
    <div style={{ maxWidth: 720, margin: "0 auto" }}>
      <h1 style={{ marginBottom: "0.5rem" }}>Apply</h1>
      <p style={{ color: "var(--text-muted)", fontSize: 14, marginBottom: "1.5rem" }}>
        Application ID: {applicationId}
      </p>

      {/* Idle — start button */}
      {phase === "idle" && (
        <button
          onClick={() => startMutation.mutate()}
          style={btnStyle("#2563eb")}
        >
          Start Apply
        </button>
      )}

      {/* Starting spinner */}
      {phase === "starting" && (
        <p style={{ color: "var(--text-muted)" }}>Opening browser and navigating to application…</p>
      )}

      {/* Done */}
      {phase === "done" && isConfirmed && (
        <div style={alertStyle("#dcfce7", "#16a34a")}>
          Application submitted successfully!{" "}
          <button onClick={() => navigate("/queue")} style={{ background: "none", border: "none", color: "#16a34a", cursor: "pointer", fontWeight: 600 }}>
            Back to Queue
          </button>
        </div>
      )}

      {/* Failed */}
      {(phase === "done" && isFailed) || phase === "error" ? (
        <div style={alertStyle("#fee2e2", "#dc2626")}>
          {isFailed ? `Workflow failed: ${response?.step?.page_url ?? "unknown step"}` : errorMsg}
          <br />
          <button onClick={() => navigate("/queue")} style={{ background: "none", border: "none", color: "#dc2626", cursor: "pointer", marginTop: 8 }}>
            Back to Queue
          </button>
        </div>
      ) : null}

      {/* Auth required — not logged in to SEEK */}
      {phase === "gate" && isAuthRequired && (
        <div style={alertStyle("#fef3c7", "#d97706")}>
          <strong>SEEK login required.</strong> The browser window opened but SEEK requires you to
          be logged in. Please log in to SEEK in the Chrome window that just opened, then click
          Retry below.
          <div style={{ marginTop: 12, display: "flex", gap: "0.75rem" }}>
            <button
              onClick={() => startMutation.mutate()}
              style={btnStyle("#2563eb")}
            >
              Retry Apply
            </button>
            <button onClick={() => navigate("/queue")} style={btnStyle("#6b7280")}>
              Back to Queue
            </button>
          </div>
        </div>
      )}

      {/* External portal */}
      {phase === "gate" && isExternal && !isAuthRequired && (
        <div style={alertStyle("#fef3c7", "#d97706")}>
          <strong>
            {externalApply ? "External apply harness paused." : "External portal detected."}
          </strong>{" "}
          {step?.portal_type && <span>Portal type: {step.portal_type}. </span>}
          {externalApply
            ? "Envoy inspected or acted on the employer portal and is waiting before continuing."
            : "This job requires manual application on the employer's own site."}

          {externalApply && (
            <div style={{ marginTop: 12, padding: "0.75rem", background: "#fff7ed", border: "1px solid #fed7aa", borderRadius: 6, color: "#7c2d12" }}>
              <div style={{ fontSize: 13, marginBottom: 6 }}>
                Harness status: <strong>{externalApply.status}</strong>
                {response?.pause_reason && <span> | {response.pause_reason}</span>}
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

              {pendingExternalQuestions.length > 0 && (
                <ExternalUserQuestionsPanel
                  isPending={resumeMutation.isPending}
                  onSubmit={(answers) =>
                    resumeMutation.mutate({
                      actionLabel: "Continue",
                      approvedValues: Object.fromEntries(
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
          <div style={{ marginTop: 12 }}>
            {step?.page_url && (
              <a
                href={step.page_url}
                target="_blank"
                rel="noreferrer"
                style={{ marginRight: 12, color: "#d97706", fontWeight: 600 }}
              >
                Open Portal ↗
              </a>
            )}
            {externalApply && !externalApply.submit_ready && pendingExternalQuestions.length === 0 && (
              <button
                onClick={() => resumeMutation.mutate({ actionLabel: "Continue" })}
                disabled={resumeMutation.isPending}
                style={{ ...btnStyle("#2563eb"), marginRight: 12 }}
              >
                {resumeMutation.isPending ? "Continuing..." : "Continue Harness"}
              </button>
            )}
            <button onClick={() => navigate("/queue")} style={btnStyle("#6b7280")}>
              Back to Queue
            </button>
          </div>
        </div>
      )}

      {/* HITL gate — normal form step */}
      {phase === "gate" && !isExternal && !isAuthRequired && step && (
        <div>
          {/* Step progress */}
          {step.step_index != null && (
            <p style={{ color: "var(--text-muted)", fontSize: 13, marginBottom: "0.75rem" }}>
              Step {step.step_index}{step.total_steps_estimate ? ` of ${step.total_steps_estimate}` : ""}
            </p>
          )}

          {/* Fields */}
          {step.fields.length === 0 ? (
            <p style={{ color: "var(--text-muted)", fontSize: 14 }}>No fields detected on this step.</p>
          ) : (
            <>
              {response?.low_confidence_ids && response.low_confidence_ids.length > 0 && (
                <p style={{ fontSize: 13, color: "#d97706", marginBottom: "0.5rem" }}>
                  Highlighted fields need your review — the AI wasn't confident about the answer.
                </p>
              )}
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem", marginBottom: "1.5rem" }}>
                {step.fields.map((field) => {
                  const needsReview = response?.low_confidence_ids?.includes(field.id);
                  return (
                    <FieldEditor
                      key={field.id}
                      field={field}
                      value={editedValues[field.id] ?? ""}
                      onChange={(val) =>
                        setEditedValues((prev) => ({ ...prev, [field.id]: val }))
                      }
                      highlight={needsReview}
                    />
                  );
                })}
              </div>
            </>
          )}

          {/* Action buttons */}
          <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
            {step.visible_actions.length > 0 ? (
              step.visible_actions.map((action) => (
                <button
                  key={action}
                  onClick={() => resumeMutation.mutate({ actionLabel: action })}
                  disabled={resumeMutation.isPending}
                  style={btnStyle(action.toLowerCase().includes("submit") ? "#16a34a" : "#2563eb")}
                >
                  {resumeMutation.isPending ? "Filling…" : action}
                </button>
              ))
            ) : (
              <button
                onClick={() => resumeMutation.mutate({ actionLabel: "Continue" })}
                disabled={resumeMutation.isPending}
                style={btnStyle("#2563eb")}
              >
                {resumeMutation.isPending ? "Filling…" : "Continue"}
              </button>
            )}

            <button
              onClick={() => abortMutation.mutate()}
              disabled={abortMutation.isPending}
              style={btnStyle("#dc2626")}
            >
              Abort
            </button>
          </div>

          {resumeMutation.isError && (
            <p style={{ color: "#dc2626", marginTop: 8, fontSize: 13 }}>
              {(resumeMutation.error as Error).message}
            </p>
          )}
        </div>
      )}
      <RunLog runId={response?.workflow_run_id} />
    </div>
  );
}

// ── Field editor ───────────────────────────────────────────────────────────

function FieldEditor({
  field,
  value,
  onChange,
  highlight,
}: {
  field: FieldInfo;
  value: string;
  onChange: (val: string) => void;
  highlight?: boolean;
}) {
  const borderColor = highlight ? "#d97706" : "#d1d5db";
  const labelEl = (
    <label style={{ fontSize: 13, fontWeight: 600, color: highlight ? "#d97706" : "#374151", display: "block", marginBottom: 4 }}>
      {field.label}
      {field.required && <span style={{ color: "#dc2626", marginLeft: 4 }}>*</span>}
    </label>
  );

  if (field.field_type === "select" && field.options?.length) {
    return (
      <div>
        {labelEl}
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          style={{ ...inputStyle, border: `1px solid ${borderColor}` }}
        >
          <option value="">— select —</option>
          {field.options.map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      </div>
    );
  }

  if (field.field_type === "radio" && field.options?.length) {
    return (
      <div>
        {labelEl}
        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
          {field.options.map((opt) => (
            <label key={opt} style={{ fontSize: 14, display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
              <input
                type="radio"
                name={field.id}
                value={opt}
                checked={value === opt}
                onChange={() => onChange(opt)}
              />
              {opt}
            </label>
          ))}
        </div>
      </div>
    );
  }

  if (field.field_type === "textarea") {
    return (
      <div>
        {labelEl}
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={6}
          maxLength={field.max_length ?? undefined}
          style={{ ...inputStyle, border: `1px solid ${borderColor}`, resize: "vertical", height: "auto" }}
        />
        {field.max_length && (
          <div style={{ fontSize: 11, color: "#9ca3af", textAlign: "right" }}>
            {value.length} / {field.max_length}
          </div>
        )}
      </div>
    );
  }

  // Default: text input
  return (
    <div>
      {labelEl}
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        maxLength={field.max_length ?? undefined}
        style={{ ...inputStyle, border: `1px solid ${borderColor}` }}
      />
    </div>
  );
}

// ── Style helpers ──────────────────────────────────────────────────────────

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "0.5rem 0.75rem",
  border: "1px solid #d1d5db",  // overridden per-field via borderColor
  borderRadius: 6,
  fontSize: 14,
  fontFamily: "inherit",
  boxSizing: "border-box",
};

function btnStyle(bg: string): React.CSSProperties {
  return {
    padding: "0.5rem 1.25rem",
    background: bg,
    color: "#fff",
    border: "none",
    borderRadius: 6,
    cursor: "pointer",
    fontSize: 14,
    fontWeight: 600,
  };
}

function alertStyle(bg: string, border: string): React.CSSProperties {
  return {
    padding: "1rem",
    background: bg,
    border: `1px solid ${border}`,
    borderRadius: 6,
    color: border,
    marginBottom: "1rem",
  };
}
