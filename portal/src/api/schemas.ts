import { z } from "zod";

export const healthSchema = z.object({
  service: z.string(),
  status: z.string(),
});
export type Health = z.infer<typeof healthSchema>;

// Generic listing metadata carried in payload — any provider can populate these fields.
// Fields are optional: render whatever is available, skip what isn't.
export const listingMetaSchema = z.object({
  provider_job_id: z.string().optional(),
  posted_at: z.string().nullable().optional(),
  salary: z.string().nullable().optional(),
  work_type: z.string().nullable().optional(),
  work_arrangement: z.string().nullable().optional(),
  tags: z.array(z.string()).optional().default([]),
  logo_url: z.string().nullable().optional(),
  bullet_points: z.array(z.string()).optional().default([]),
}).passthrough();

export const jobSchema = z.object({
  id: z.string(),
  provider: z.string(),
  source_url: z.string(),
  canonical_key: z.string(),
  title: z.string(),
  company: z.string(),
  location: z.string().nullable(),
  summary: z.string().nullable(),
  payload: listingMetaSchema,
  state: z.string().default("discovered"),
  discovered_at: z.string(),
  last_seen_at: z.string(),
});
export type Job = z.infer<typeof jobSchema>;

export const jobListSchema = z.object({
  jobs: z.array(jobSchema),
});

export const searchResponseSchema = z.object({
  discovered: z.number(),
  blocked: z.number(),
  persisted: z.number(),
  job_ids: z.array(z.string()),
});
export type SearchResponse = z.infer<typeof searchResponseSchema>;

export const draftSchema = z.object({
  id: z.string(),
  application_id: z.string(),
  draft_type: z.string(),
  question_fingerprint: z.string().nullable(),
  generator: z.string(),
  content: z.string(),
  version: z.number(),
  created_at: z.string(),
});
export type Draft = z.infer<typeof draftSchema>;

export const applicationSchema = z.object({
  id: z.string(),
  job_id: z.string(),
  source_provider: z.string().optional(),
  source_url: z.string(),
  state: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  last_apply_step_json: z.string().nullable().optional(),
  // joined job fields (from list endpoint)
  job_title: z.string().nullable().optional(),
  job_company: z.string().nullable().optional(),
  job_location: z.string().nullable().optional(),
  job_source_url: z.string().nullable().optional(),
  job_summary: z.string().nullable().optional(),
  job_payload: listingMetaSchema.optional(),
});
export type Application = z.infer<typeof applicationSchema>;

export const applicationDetailSchema = z.object({
  application: applicationSchema,
  cover_letter: z.string().default(""),
  match_evidence: z.string().default(""),
  last_apply_step: z.string().nullable().optional(),
  job: z.object({
    title: z.string().nullable(),
    company: z.string().nullable(),
    location: z.string().nullable(),
    source_url: z.string().nullable(),
    summary: z.string().nullable(),
    payload: listingMetaSchema.optional(),
  }).nullable().optional(),
});
export type ApplicationDetail = z.infer<typeof applicationDetailSchema>;

export const prepareResponseSchema = z.object({
  application_id: z.string(),
  cover_letter: z.string(),
  job_description: z.string().default(""),
  questions: z.array(z.object({ question: z.string(), answer: z.string() })),
  is_suitable: z.boolean().optional(),
  gaps: z.array(z.string()).optional(),
  match_evidence: z.string().default(""),
});
export type PrepareResponse = z.infer<typeof prepareResponseSchema>;

// Apply workflow schemas
export const fieldInfoSchema = z.object({
  id: z.string(),
  label: z.string(),
  field_type: z.string(),
  required: z.boolean(),
  current_value: z.string().nullable().optional(),
  options: z.array(z.string()).nullable().optional(),
  max_length: z.number().nullable().optional(),
});
export type FieldInfo = z.infer<typeof fieldInfoSchema>;

export const stepInfoSchema = z.object({
  page_url: z.string(),
  page_type: z.string(),
  step_index: z.number().nullable().optional(),
  total_steps_estimate: z.number().nullable().optional(),
  is_external_portal: z.boolean().optional(),
  portal_type: z.string().nullable().optional(),
  fields: z.array(fieldInfoSchema),
  visible_actions: z.array(z.string()),
});
export type StepInfo = z.infer<typeof stepInfoSchema>;

const stepHistoryEntrySchema = z.object({
  step: z.object({
    page_url: z.string(),
    fields: z.array(fieldInfoSchema).optional().default([]),
    visible_actions: z.array(z.string()).optional().default([]),
  }).passthrough(),
  filled_values: z.record(z.string()),
});
export type StepHistoryEntry = z.infer<typeof stepHistoryEntrySchema>;

export const applyStepResponseSchema = z.object({
  workflow_run_id: z.string(),
  status: z.string(),
  step: stepInfoSchema.nullable().optional(),
  proposed_values: z.record(z.string()),
  low_confidence_ids: z.array(z.string()).optional(),
  submit_action_label: z.string().optional().default("Continue"),
  step_history: z.array(stepHistoryEntrySchema).optional().default([]),
  error: z.string().nullable().optional(),
  pause_reason: z.string().nullable().optional(),
});
export type ApplyStepResponse = z.infer<typeof applyStepResponseSchema>;

export const queueJobResponseSchema = z.object({
  job_id: z.string(),
  application_id: z.string(),
  state: z.string(),
});
export type QueueJobResponse = z.infer<typeof queueJobResponseSchema>;
