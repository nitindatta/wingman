import { apiFetch } from "./client";
import {
  applicationSchema,
  applicationDetailSchema,
  applyStepResponseSchema,
  prepareResponseSchema,
  type Application,
  type ApplyStepResponse,
  type PrepareResponse,
} from "./schemas";
import { z } from "zod";

export async function fetchApplications(state?: string): Promise<Application[]> {
  const url = state ? `/applications?state=${state}` : "/applications";
  const raw = await apiFetch<unknown>(url);
  const parsed = z.object({ applications: z.array(applicationSchema) }).parse(raw);
  return parsed.applications;
}

export async function fetchApplicationDetail(appId: string) {
  const raw = await apiFetch<unknown>(`/applications/${appId}`);
  return applicationDetailSchema.parse(raw);
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
