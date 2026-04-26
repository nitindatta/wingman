import { useEffect, useMemo, useState } from "react";
import type { ExternalApplyState } from "../api/schemas";
import { detectInputMode } from "./ExternalUserQuestionPrompt";

type ExternalQuestion = NonNullable<ExternalApplyState["pending_user_question"]>;

function placeholderForMode(mode: ReturnType<typeof detectInputMode>): string {
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

function hintForMode(mode: ReturnType<typeof detectInputMode>): string {
  if (mode === "consent") {
    return "This will be answered as consent/approval.";
  }
  if (mode === "review") {
    return "Continue after reviewing the current page.";
  }
  return "";
}

function submitLabelForQuestions(
  keyedQuestions: Array<{ mode: ReturnType<typeof detectInputMode> }>,
  count: number,
): string {
  if (count === 1 && keyedQuestions[0]?.mode === "review") {
    return "Continue After Review";
  }
  return count === 1 ? "Submit Answer" : "Submit Answers";
}

function questionKey(question: ExternalQuestion, index: number): string {
  return question.target_element_id || question.question_key || `${index}:${question.question}`;
}

export function ExternalUserQuestionsPanel({
  questions,
  isPending,
  onSubmit,
}: {
  questions: ExternalQuestion[];
  isPending: boolean;
  onSubmit: (answers: Record<string, string>) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});

  const keyedQuestions = useMemo(
    () =>
      questions.map((question, index) => ({
        key: questionKey(question, index),
        mode: detectInputMode(question.question, question.context ?? ""),
        question,
      })),
    [questions],
  );

  useEffect(() => {
    setValues({});
  }, [questions]);

  const canSubmit = keyedQuestions.every(({ key, mode }) => mode === "consent" || mode === "review" || Boolean(values[key]?.trim()));

  return (
    <div style={{ marginTop: 12, padding: "0.85rem", background: "#ffffff", border: "1px solid #fed7aa", borderRadius: 6 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: "#7c2d12", marginBottom: "0.75rem" }}>
        {questions.length === 1 ? "Question" : "Questions"}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "0.9rem" }}>
        {keyedQuestions.map(({ key, mode, question }) => (
          <div key={key}>
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
                onChange={(e) => setValues((prev) => ({ ...prev, [key]: e.target.value }))}
                placeholder={placeholderForMode(mode)}
                style={{
                  width: "100%",
                  boxSizing: "border-box",
                  padding: "0.7rem 0.75rem",
                  border: "1px solid #fdba74",
                  borderRadius: 6,
                  fontSize: 14,
                  marginBottom: question.suggested_answers.length > 0 ? "0.75rem" : 0,
                }}
                type={mode === "password" ? "password" : mode === "email" ? "email" : "text"}
                value={values[key] ?? ""}
              />
            )}
            {question.suggested_answers.length > 0 && mode !== "consent" && mode !== "review" && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", marginTop: "0.75rem" }}>
                {question.suggested_answers.map((answer) => (
                  <button
                    key={answer}
                    onClick={() => setValues((prev) => ({ ...prev, [key]: answer }))}
                    style={{
                      padding: "0.35rem 0.65rem",
                      borderRadius: 6,
                      border: "1px solid #fdba74",
                      background: values[key] === answer ? "#ffedd5" : "#fff7ed",
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
            {(mode === "consent" || mode === "review") && (
              <div style={{ fontSize: 12, color: "#9a3412" }}>
                {hintForMode(mode)}
              </div>
            )}
          </div>
        ))}
      </div>
      <button
        disabled={isPending || !canSubmit}
        onClick={() =>
          onSubmit(
            keyedQuestions.reduce<Record<string, string>>((acc, item) => {
              const answer = item.mode === "consent" || item.mode === "review" ? "true" : (values[item.key] ?? "").trim();
              if (item.question.target_element_id) {
                acc[item.question.target_element_id] = answer;
                return acc;
              }
              if (item.question.question_key) {
                acc[item.question.question_key] = answer;
              }
              return acc;
            }, {}),
          )
        }
        style={{
          marginTop: "0.9rem",
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
        {isPending ? "Continuing..." : submitLabelForQuestions(keyedQuestions, questions.length)}
      </button>
    </div>
  );
}
