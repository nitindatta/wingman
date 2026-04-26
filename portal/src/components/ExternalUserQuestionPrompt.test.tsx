// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ExternalUserQuestionPrompt, detectInputMode } from "./ExternalUserQuestionPrompt";

describe("detectInputMode", () => {
  it("detects email questions before generic approval wording", () => {
    expect(
      detectInputMode(
        "What exact email address should be used for this Workday account?",
        "Email and password are required to create the account.",
      ),
    ).toBe("email");
  });

  it("detects password questions", () => {
    expect(detectInputMode("What password should be used?", "")).toBe("password");
  });

  it("keeps consent questions as consent mode", () => {
    expect(detectInputMode("Do you consent to the privacy policy?", "")).toBe("consent");
  });

  it("treats stale-click review prompts as review mode instead of consent", () => {
    expect(
      detectInputMode(
        "The page did not advance after clicking Create Account. Review the page and continue when it is ready.",
        "The page stayed on the same step after clicking Create Account. Please review any highlighted errors or missing fields, then continue when the page is ready.",
      ),
    ).toBe("review");
  });
});

describe("ExternalUserQuestionPrompt", () => {
  it("requires a typed answer for email-style prompts", () => {
    const onSubmit = vi.fn();
    render(
      <ExternalUserQuestionPrompt
        isPending={false}
        onSubmit={onSubmit}
        question={{
          question: "What exact email address should be used for this Workday account?",
          context: "Email and password are required to create the account.",
          suggested_answers: [],
          target_element_id: "field_email",
        }}
      />,
    );

    const submit = screen.getByRole("button", { name: "Submit Answer" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText(/what exact email address/i), {
      target: { value: "candidate@example.com" },
    });
    expect(submit.disabled).toBe(false);

    fireEvent.click(submit);
    expect(onSubmit).toHaveBeenCalledWith("candidate@example.com");
  });

  it("uses the consent shortcut for approval prompts", () => {
    const onSubmit = vi.fn();
    render(
      <ExternalUserQuestionPrompt
        isPending={false}
        onSubmit={onSubmit}
        question={{
          question: "Do you consent to proceed?",
          context: "Privacy consent is required.",
          suggested_answers: [],
          target_element_id: "field_privacy",
        }}
      />,
    );

    const submit = screen.getByRole("button", { name: "I consent, continue" });
    expect(screen.queryByRole("textbox")).toBeNull();

    fireEvent.click(submit);
    expect(onSubmit).toHaveBeenCalledWith("true");
  });

  it("uses the review shortcut for stale-click prompts", () => {
    const onSubmit = vi.fn();
    render(
      <ExternalUserQuestionPrompt
        isPending={false}
        onSubmit={onSubmit}
        question={{
          question: "The page did not advance after clicking Create Account. Review the page and continue when it is ready.",
          context: "The page stayed on the same step after clicking Create Account. Please review any highlighted errors or missing fields, then continue when the page is ready.",
          suggested_answers: [],
        }}
      />,
    );

    const submit = screen.getByRole("button", { name: "Continue After Review" });
    expect(screen.queryByRole("textbox")).toBeNull();

    fireEvent.click(submit);
    expect(onSubmit).toHaveBeenCalledWith("true");
  });
});
