import { apiFetch } from "./client";
import {
  applicationSchema,
  applicationDetailSchema,
  applyStepResponseSchema,
  prepareResponseSchema,
  type Application,
  type ApplicationDetail,
  type ApplyStepResponse,
  type PrepareResponse,
} from "./schemas";
import { z } from "zod";

export const EXTERNAL_USER_ANSWER_KEY = "__external_apply_user_answer";
export const EXTERNAL_USER_ANSWER_PREFIX = "__external_apply_user_answer__";
export const EXTERNAL_USER_QUESTION_PREFIX = "__external_apply_user_question__";

export async function fetchApplications(params?: { state?: string }): Promise<Application[]> {
  const url = params?.state ? `/applications?state=${params.state}` : "/applications";
  const raw = await apiFetch<unknown>(url);
  const parsed = z.object({ applications: z.array(applicationSchema) }).parse(raw);
  return parsed.applications;
}

export async function fetchApplicationDetail(appId: string): Promise<ApplicationDetail> {
  const raw = await apiFetch<unknown>(`/applications/${appId}`);
  return applicationDetailSchema.parse(raw);
}

export async function enqueueApply(appId: string): Promise<{ workflow_run_id: string }> {
  return apiFetch<{ workflow_run_id: string }>(`/applications/${appId}/apply`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function enqueueExternalHarness(appId: string, targetUrl?: string): Promise<void> {
  await apiFetch<unknown>(`/applications/${appId}/external_harness`, {
    method: "POST",
    body: JSON.stringify({ target_url: targetUrl ?? null }),
  });
}

export async function enqueueGate(
  appId: string,
  runId: string,
  approvedValues: Record<string, string>,
): Promise<void> {
  await apiFetch<unknown>(`/applications/${appId}/gate`, {
    method: "POST",
    body: JSON.stringify({ run_id: runId, approved_values: approvedValues }),
  });
}

export async function enqueueSubmit(
  appId: string,
  runId: string,
  label: string,
  correctedValues?: Record<string, string>,
): Promise<void> {
  await apiFetch<unknown>(`/applications/${appId}/submit`, {
    method: "POST",
    body: JSON.stringify({ run_id: runId, label, corrected_values: correctedValues ?? {} }),
  });
}

export async function triggerPrepare(jobId: string): Promise<PrepareResponse> {
  const raw = await apiFetch<unknown>("/workflows/prepare", {
    method: "POST",
    body: JSON.stringify({ job_id: jobId }),
  });
  return prepareResponseSchema.parse(raw);
}

export async function approveApplication(appId: string, coverLetter?: string): Promise<void> {
  await apiFetch<unknown>(`/applications/${appId}/approve`, {
    method: "POST",
    body: JSON.stringify({ cover_letter: coverLetter ?? null }),
  });
}

export async function discardApplication(appId: string): Promise<void> {
  await apiFetch<unknown>(`/applications/${appId}/discard`, { method: "POST" });
}

export async function startApply(applicationId: string): Promise<ApplyStepResponse> {
  const raw = await apiFetch<unknown>("/workflows/apply", {
    method: "POST",
    body: JSON.stringify({ application_id: applicationId }),
  });
  return applyStepResponseSchema.parse(raw);
}

export async function resumeApply(
  runId: string,
  approvedValues: Record<string, string>,
  actionLabel: string = "Continue",
  action: "continue" | "abort" = "continue"
): Promise<ApplyStepResponse> {
  const raw = await apiFetch<unknown>(`/workflows/apply/${runId}/resume`, {
    method: "POST",
    body: JSON.stringify({ approved_values: approvedValues, action_label: actionLabel, action }),
  });
  return applyStepResponseSchema.parse(raw);
}

export async function generateQuestions(
  applicationId: string,
): Promise<Array<{ question: string; answer: string }>> {
  const raw = await apiFetch<unknown>("/workflows/questions", {
    method: "POST",
    body: JSON.stringify({ application_id: applicationId }),
  });
  const parsed = (raw as { questions: Array<{ question: string; answer: string }> }).questions;
  return parsed;
}

export async function markSubmitted(appId: string): Promise<void> {
  await apiFetch<unknown>(`/applications/${appId}/mark_submitted`, { method: "POST" });
}

export async function cancelApplication(appId: string): Promise<void> {
  await apiFetch<unknown>(`/applications/${appId}/cancel`, { method: "POST" });
}

export async function resetApplication(appId: string): Promise<void> {
  await apiFetch<unknown>(`/applications/${appId}/reset`, { method: "POST" });
}

export async function submitApply(
  runId: string,
  submitActionLabel: string,
): Promise<ApplyStepResponse> {
  const raw = await apiFetch<unknown>(`/workflows/apply/${runId}/resume`, {
    method: "POST",
    body: JSON.stringify({ approved_values: {}, action_label: submitActionLabel, action: "continue" }),
  });
  return applyStepResponseSchema.parse(raw);
}
