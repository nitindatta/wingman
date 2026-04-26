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
  posted_at: z.string().nullable().optional(),
  discovered_at: z.string(),
  last_seen_at: z.string(),
  search_tags: z.array(z.string()).optional().default([]),
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
  target_portal: z.string().nullable().optional(),
  target_application_url: z.string().nullable().optional(),
  state: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  last_apply_step_json: z.string().nullable().optional(),
  is_suitable: z.boolean().optional(),
  gaps_json: z.string().optional().default("[]"),
  fit_score: z.number().nullable().optional(),
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
  fit_score: z.number().nullable().optional(),
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

export const externalObservedFieldSchema = z.object({
  element_id: z.string(),
  label: z.string(),
  field_type: z.string(),
  required: z.boolean().optional().default(false),
  current_value: z.string().nullable().optional(),
  options: z.array(z.string()).optional().default([]),
  nearby_text: z.string().optional().default(""),
  disabled: z.boolean().optional().default(false),
  visible: z.boolean().optional().default(true),
});

export const externalObservedActionSchema = z.object({
  element_id: z.string(),
  label: z.string(),
  kind: z.string().optional().default("unknown"),
  href: z.string().nullable().optional(),
  disabled: z.boolean().optional().default(false),
  nearby_text: z.string().optional().default(""),
});

export const externalPageObservationSchema = z.object({
  url: z.string(),
  title: z.string().optional().default(""),
  page_type: z.string().optional().default("unknown"),
  visible_text: z.string().optional().default(""),
  fields: z.array(externalObservedFieldSchema).optional().default([]),
  buttons: z.array(externalObservedActionSchema).optional().default([]),
  links: z.array(externalObservedActionSchema).optional().default([]),
  uploads: z.array(externalObservedFieldSchema).optional().default([]),
  errors: z.array(z.string()).optional().default([]),
  screenshot_ref: z.string().nullable().optional(),
});

export const externalProposedActionSchema = z.object({
  action_type: z.string(),
  element_id: z.string().nullable().optional(),
  value: z.string().nullable().optional(),
  question: z.string().nullable().optional(),
  confidence: z.number(),
  risk: z.string(),
  reason: z.string(),
  source: z.string().optional().default("none"),
});

export const externalActionResultSchema = z.object({
  ok: z.boolean(),
  action_type: z.string(),
  element_id: z.string().nullable().optional(),
  message: z.string().optional().default(""),
  value_after: z.string().nullable().optional(),
  navigated: z.boolean().optional().default(false),
  new_url: z.string().nullable().optional(),
  errors: z.array(z.string()).optional().default([]),
});

export const externalActionTraceSchema = z.object({
  observation: externalPageObservationSchema,
  proposed_action: externalProposedActionSchema,
  policy_decision: z.string(),
  result: externalActionResultSchema.nullable().optional(),
});

export const externalUserQuestionSchema = z.object({
  question: z.string(),
  context: z.string().optional().default(""),
  suggested_answers: z.array(z.string()).optional().default([]),
  target_element_id: z.string().nullable().optional(),
  question_key: z.string().nullable().optional(),
});

export const externalApplyStateSchema = z.object({
  application_id: z.string(),
  current_url: z.string().optional().default(""),
  page_type: z.string().optional().default("unknown"),
  observation: externalPageObservationSchema.nullable().optional(),
  proposed_action: externalProposedActionSchema.nullable().optional(),
  last_action_result: externalActionResultSchema.nullable().optional(),
  completed_actions: z.array(externalActionTraceSchema).optional().default([]),
  pending_user_question: externalUserQuestionSchema.nullable().optional(),
  pending_user_questions: z.array(externalUserQuestionSchema).optional().default([]),
  risk_flags: z.array(z.string()).optional().default([]),
  submit_ready: z.boolean().optional().default(false),
  status: z.string().optional().default("running"),
  error: z.string().nullable().optional(),
});
export type ExternalApplyState = z.infer<typeof externalApplyStateSchema>;

export const applyStepResponseSchema = z.object({
  workflow_run_id: z.string(),
  status: z.string(),
  step: stepInfoSchema.nullable().optional(),
  proposed_values: z.record(z.string()),
  low_confidence_ids: z.array(z.string()).optional(),
  external_apply: externalApplyStateSchema.nullable().optional(),
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

export const setupStatusSchema = z.object({
  profile_json_exists: z.boolean(),
  profile_json_path: z.string(),
  raw_profile_exists: z.boolean(),
  raw_profile_path: z.string(),
  target_profile_exists: z.boolean(),
  target_profile_path: z.string(),
  latest_uploaded_filename: z.string(),
  chrome_profile_exists: z.boolean(),
  chrome_has_cookies: z.boolean(),
  chrome_profile_dir: z.string(),
  providers: z.array(z.string()),
});
export type SetupStatus = z.infer<typeof setupStatusSchema>;

export const canonicalEvidenceItemSchema = z.object({
  id: z.string(),
  source: z.string(),
  role_title: z.string().nullable().optional(),
  skills: z.array(z.string()).default([]),
  domain: z.array(z.string()).default([]),
  situation: z.string().default(""),
  task: z.string().default(""),
  action: z.string().default(""),
  outcome: z.string().default(""),
  metrics: z.array(z.string()).default([]),
  proof_points: z.array(z.string()).default([]),
  tone_sample: z.string().nullable().optional(),
  confidence: z.string().default("draft"),
});
export type CanonicalEvidenceItem = z.infer<typeof canonicalEvidenceItemSchema>;

export const voiceProfileSchema = z.object({
  tone_labels: z.array(z.string()).default([]),
  formality: z.string().default(""),
  sentence_style: z.string().default(""),
  uses_contractions: z.boolean().nullable().optional(),
  prefers_first_person: z.boolean().nullable().optional(),
  opening_style: z.string().default(""),
  strengths: z.array(z.string()).default([]),
  avoid: z.array(z.string()).default([]),
  confidence: z.string().default("draft"),
});
export type VoiceProfile = z.infer<typeof voiceProfileSchema>;

export const canonicalProfileSchema = z.object({
  name: z.string().default(""),
  headline: z.string().default(""),
  summary: z.string().default(""),
  location: z.string().nullable().optional(),
  work_rights: z.string().nullable().optional(),
  salary_expectation: z.string().nullable().optional(),
  core_strengths: z.array(z.string()).default([]),
  voice_samples: z.array(z.string()).default([]),
  voice_profile: voiceProfileSchema.default({
    tone_labels: [],
    formality: "",
    sentence_style: "",
    uses_contractions: null,
    prefers_first_person: null,
    opening_style: "",
    strengths: [],
    avoid: [],
    confidence: "draft",
  }),
  evidence_items: z.array(canonicalEvidenceItemSchema).default([]),
});
export type CanonicalProfile = z.infer<typeof canonicalProfileSchema>;

export const profileEnrichmentQuestionSchema = z.object({
  id: z.string(),
  evidence_item_id: z.string().nullable().optional(),
  target_field: z.string(),
  prompt: z.string(),
  help_text: z.string().default(""),
  priority: z.string().default("medium"),
  input_type: z.string().default("text"),
  current_value: z.string().nullable().optional(),
});
export type ProfileEnrichmentQuestion = z.infer<typeof profileEnrichmentQuestionSchema>;

export const profileAnswerSchema = z.object({
  question_id: z.string().nullable().optional(),
  target_field: z.string(),
  value: z.string().default(""),
});
export type ProfileAnswer = z.infer<typeof profileAnswerSchema>;

export const profileInterviewPromptSchema = z.object({
  question_id: z.string().default(""),
  question: z.string().default(""),
  suggested_answer: z.string().default(""),
  source_basis: z.array(z.string()).default([]),
  improvement_hint: z.string().default(""),
  mode: z.string().default("question"),
  assistant_message: z.string().default(""),
});
export type ProfileInterviewPrompt = z.infer<typeof profileInterviewPromptSchema>;

export const profileInterviewAnswerAssessmentSchema = z.object({
  score: z.number().default(0),
  dimension_scores: z.record(z.number()).default({}),
  strengths: z.array(z.string()).default([]),
  weaknesses: z.array(z.string()).default([]),
  next_focus: z.string().default(""),
  confidence: z.string().default("draft"),
});
export type ProfileInterviewAnswerAssessment = z.infer<typeof profileInterviewAnswerAssessmentSchema>;

export const profileInterviewSessionResponseSchema = z.object({
  session_id: z.string(),
  status: z.string(),
  source_profile_path: z.string(),
  target_profile_path: z.string(),
  current_item_id: z.string().default(""),
  draft_item: canonicalEvidenceItemSchema.nullable().optional(),
  open_gaps: z.array(z.string()).default([]),
  current_gap: z.string().default(""),
  current_question_id: z.string().default(""),
  current_question: z.string().default(""),
  current_prompt: profileInterviewPromptSchema.default({
    question_id: "",
    question: "",
    suggested_answer: "",
    source_basis: [],
    improvement_hint: "",
    mode: "question",
    assistant_message: "",
  }),
  pending_item: canonicalEvidenceItemSchema.nullable().optional(),
  last_answer_assessment: profileInterviewAnswerAssessmentSchema.default({
    score: 0,
    dimension_scores: {},
    strengths: [],
    weaknesses: [],
    next_focus: "",
    confidence: "draft",
  }),
  item_quality_scores: z.record(z.number()).default({}),
  completeness_score: z.number().default(0),
  overall_answer_quality_score: z.number().nullable().optional(),
  overall_profile_score: z.number().nullable().optional(),
  approved_items: z.number().default(0),
  total_items: z.number().default(0),
  error: z.string().nullable().optional(),
});
export type ProfileInterviewSessionResponse = z.infer<typeof profileInterviewSessionResponseSchema>;

export const profileTargetResponseSchema = z.object({
  profile_exists: z.boolean(),
  source_profile_path: z.string(),
  target_profile_path: z.string(),
  target_profile_exists: z.boolean(),
  target_profile: canonicalProfileSchema.nullable().optional(),
  questions: z.array(profileEnrichmentQuestionSchema).default([]),
});
export type ProfileTargetResponse = z.infer<typeof profileTargetResponseSchema>;

export const sourceDocumentSchema = z.object({
  id: z.string(),
  filename: z.string(),
  mime_type: z.string(),
  saved_path: z.string(),
  sha256: z.string(),
  extracted_text_path: z.string().nullable().optional(),
  extracted_markdown_path: z.string().nullable().optional(),
  parse_status: z.string(),
  parse_error: z.string().nullable().optional(),
});
export type SourceDocument = z.infer<typeof sourceDocumentSchema>;

export const rawProfileBulletSchema = z.object({
  text: z.string(),
  source_excerpt: z.string().default(""),
  confidence: z.string().default("medium"),
});
export type RawProfileBullet = z.infer<typeof rawProfileBulletSchema>;

export const rawProfileExperienceSchema = z.object({
  id: z.string(),
  title: z.string().default(""),
  company: z.string().default(""),
  period_raw: z.string().default(""),
  bullets: z.array(rawProfileBulletSchema).default([]),
  metrics: z.array(z.string()).default([]),
  technologies: z.array(z.string()).default([]),
});
export type RawProfileExperience = z.infer<typeof rawProfileExperienceSchema>;

export const rawProfileProjectSchema = z.object({
  id: z.string(),
  name: z.string().default(""),
  summary: z.string().default(""),
  bullets: z.array(rawProfileBulletSchema).default([]),
  technologies: z.array(z.string()).default([]),
});
export type RawProfileProject = z.infer<typeof rawProfileProjectSchema>;

export const rawProfileSchema = z.object({
  version: z.number().default(1),
  source_documents: z.array(sourceDocumentSchema).default([]),
  identity: z.object({
    name: z.string().default(""),
    headline: z.string().default(""),
    email: z.string().default(""),
    phone: z.string().default(""),
    location: z.string().default(""),
  }).default({ name: "", headline: "", email: "", phone: "", location: "" }),
  summary: z.string().default(""),
  experience: z.array(rawProfileExperienceSchema).default([]),
  projects: z.array(rawProfileProjectSchema).default([]),
  skills: z.array(z.string()).default([]),
  education: z.array(z.string()).default([]),
  certifications: z.array(z.string()).default([]),
  writing_samples: z.array(z.string()).default([]),
  parse_notes: z.array(z.string()).default([]),
});
export type RawProfile = z.infer<typeof rawProfileSchema>;

export const rawProfileResponseSchema = z.object({
  raw_profile_exists: z.boolean(),
  raw_profile_path: z.string(),
  raw_profile: rawProfileSchema.nullable().optional(),
});
export type RawProfileResponse = z.infer<typeof rawProfileResponseSchema>;

export const profileUploadResponseSchema = z.object({
  ok: z.boolean(),
  source_document: sourceDocumentSchema,
  raw_profile_path: z.string(),
  raw_profile: rawProfileSchema,
});
export type ProfileUploadResponse = z.infer<typeof profileUploadResponseSchema>;
