import { useEffect, useMemo, useState } from "react";
import type { ExternalApplyState } from "../api/schemas";

type InputMode = "consent" | "email" | "password" | "text" | "review";

function detectInputMode(question: string, context: string): InputMode {
  const questionText = question.toLowerCase();
  const combined = `${question} ${context}`.toLowerCase();
  if (/\b(e-?mail)\b/.test(questionText)) {
    return "email";
  }
  if (/\b(pass(word|code|phrase)?)\b/.test(questionText)) {
    return "password";
  }
  if (/\b(e-?mail)\b/.test(combined)) {
    return "email";
  }
  if (/\b(pass(word|code|phrase)?)\b/.test(combined)) {
    return "password";
  }
  if (
    /page did not advance after clicking|review the page and continue|continue when the page is ready|highlighted errors or missing fields/.test(combined)
  ) {
    return "review";
  }
  if (/\b(consent|agree|approval|approve|authorize|authorise|permission)\b/.test(combined)) {
    return "consent";
  }
  return "text";
}

function placeholderForMode(mode: InputMode): string {
  switch (mode) {
    case "email":
      return "Enter the exact email address";
    case "password":
      return "Enter the password";
    case "text":
      return "Type your answer";
    default:
      return "";
  }
}

function buttonLabelForMode(mode: InputMode): string {
  if (mode === "consent") {
    return "I consent, continue";
  }
  if (mode === "review") {
    return "Continue After Review";
  }
  return "Submit Answer";
}

export function ExternalUserQuestionPrompt({
  question,
  isPending,
  onSubmit,
}: {
  question: NonNullable<ExternalApplyState["pending_user_question"]>;
  isPending: boolean;
  onSubmit: (answer: string) => void;
}) {
  const [value, setValue] = useState("");
  const mode = useMemo(
    () => detectInputMode(question.question, question.context ?? ""),
    [question.context, question.question],
  );

  useEffect(() => {
    setValue("");
  }, [question.question, question.context, question.target_element_id]);

  const trimmed = value.trim();
  const canSubmit = mode === "consent" || mode === "review" ? true : trimmed.length > 0;

  return (
    <div style={{ marginTop: 12, padding: "0.85rem", background: "#ffffff", border: "1px solid #fed7aa", borderRadius: 6 }}>
      <label style={{ display: "block", fontSize: 13, fontWeight: 600, color: "#7c2d12", marginBottom: "0.45rem" }}>
        {question.question}
      </label>
      {question.context && (
        <div style={{ fontSize: 12, color: "#9a3412", marginBottom: "0.6rem", whiteSpace: "pre-wrap" }}>
          {question.context}
        </div>
      )}
      {mode !== "consent" && mode !== "review" && (
        <input
          aria-label={question.question}
          autoComplete={mode === "email" ? "email" : mode === "password" ? "current-password" : "off"}
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholderForMode(mode)}
          style={{
            width: "100%",
            boxSizing: "border-box",
            padding: "0.7rem 0.75rem",
            border: "1px solid #fdba74",
            borderRadius: 6,
            fontSize: 14,
            marginBottom: "0.75rem",
          }}
          type={mode === "password" ? "password" : mode === "email" ? "email" : "text"}
          value={value}
        />
      )}
      {question.suggested_answers && question.suggested_answers.length > 0 && mode !== "consent" && mode !== "review" && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", marginBottom: "0.75rem" }}>
          {question.suggested_answers.map((answer) => (
            <button
              key={answer}
              onClick={() => setValue(answer)}
              style={{
                padding: "0.35rem 0.65rem",
                borderRadius: 6,
                border: "1px solid #fdba74",
                background: value === answer ? "#ffedd5" : "#fff7ed",
                color: "#9a3412",
                cursor: "pointer",
                fontSize: 12,
              }}
              type="button"
            >
              {answer}
            </button>
          ))}
        </div>
      )}
      {mode === "review" && (
        <div style={{ fontSize: 12, color: "#9a3412", marginBottom: "0.75rem" }}>
          Continue after reviewing the current page.
        </div>
      )}
      <button
        disabled={isPending || !canSubmit}
        onClick={() => onSubmit(mode === "consent" || mode === "review" ? "true" : trimmed)}
        style={{
          padding: "0.5rem 1.25rem",
          background: isPending || !canSubmit ? "#9ca3af" : "#16a34a",
          color: "#fff",
          border: "none",
          borderRadius: 6,
          cursor: isPending || !canSubmit ? "not-allowed" : "pointer",
          fontSize: 14,
          fontWeight: 600,
        }}
        type="button"
      >
        {isPending ? "Continuing..." : buttonLabelForMode(mode)}
      </button>
    </div>
  );
}

export { detectInputMode };
