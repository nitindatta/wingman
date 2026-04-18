import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { apiFetch } from "@/api/client";
import {
  profileInterviewSessionResponseSchema,
  profileTargetResponseSchema,
  profileUploadResponseSchema,
  rawProfileResponseSchema,
  setupStatusSchema,
  type CanonicalProfile,
  type ProfileInterviewSessionResponse,
  type ProfileTargetResponse,
  type ProfileUploadResponse,
  type RawProfile,
  type RawProfileResponse,
  type SetupStatus,
} from "@/api/schemas";
import { z } from "zod";

async function fetchSetupStatus(): Promise<SetupStatus> {
  const raw = await apiFetch<unknown>("/setup/status");
  return setupStatusSchema.parse(raw);
}

async function openProviderLogin(provider: string) {
  const raw = await apiFetch<unknown>(`/setup/login/${provider}`, { method: "POST" });
  return z.object({ ok: z.boolean(), error: z.string().optional() }).parse(raw);
}

async function fetchProfileTarget(): Promise<ProfileTargetResponse> {
  const raw = await apiFetch<unknown>("/setup/profile/target");
  return profileTargetResponseSchema.parse(raw);
}

async function fetchRawProfile(): Promise<RawProfileResponse> {
  const raw = await apiFetch<unknown>("/setup/profile/raw");
  return rawProfileResponseSchema.parse(raw);
}

async function saveProfileTarget(targetProfile: CanonicalProfile) {
  const raw = await apiFetch<unknown>("/setup/profile/target", {
    method: "POST",
    body: JSON.stringify({ target_profile: targetProfile }),
  });
  return z.object({ ok: z.boolean(), target_profile_path: z.string() }).parse(raw);
}

async function fetchActiveProfileInterview(): Promise<ProfileInterviewSessionResponse | null> {
  const raw = await apiFetch<unknown>("/profile-interview/active");
  if (raw === null) {
    return null;
  }
  return profileInterviewSessionResponseSchema.parse(raw);
}

async function startProfileInterview(itemId?: string): Promise<ProfileInterviewSessionResponse> {
  const raw = await apiFetch<unknown>("/profile-interview/start", {
    method: "POST",
    body: JSON.stringify(itemId ? { item_id: itemId } : {}),
  });
  return profileInterviewSessionResponseSchema.parse(raw);
}

async function selectProfileInterviewItem(
  sessionId: string,
  itemId: string,
): Promise<ProfileInterviewSessionResponse> {
  const raw = await apiFetch<unknown>(`/profile-interview/${sessionId}/select`, {
    method: "POST",
    body: JSON.stringify({ item_id: itemId }),
  });
  return profileInterviewSessionResponseSchema.parse(raw);
}

async function answerProfileInterview(
  sessionId: string,
  answer: string,
): Promise<ProfileInterviewSessionResponse> {
  const raw = await apiFetch<unknown>(`/profile-interview/${sessionId}/answer`, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });
  return profileInterviewSessionResponseSchema.parse(raw);
}

async function approveProfileInterview(sessionId: string): Promise<ProfileInterviewSessionResponse> {
  const raw = await apiFetch<unknown>(`/profile-interview/${sessionId}/approve`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  return profileInterviewSessionResponseSchema.parse(raw);
}

async function deferProfileInterview(sessionId: string): Promise<ProfileInterviewSessionResponse> {
  const raw = await apiFetch<unknown>(`/profile-interview/${sessionId}/defer`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  return profileInterviewSessionResponseSchema.parse(raw);
}

async function completeProfileInterview(sessionId: string): Promise<ProfileInterviewSessionResponse> {
  const raw = await apiFetch<unknown>(`/profile-interview/${sessionId}/complete`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  return profileInterviewSessionResponseSchema.parse(raw);
}

async function uploadProfileFile(file: File): Promise<ProfileUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const raw = await apiFetch<unknown>("/setup/profile/upload", {
    method: "POST",
    body: formData,
  });
  return profileUploadResponseSchema.parse(raw);
}

const PROVIDER_LABELS: Record<string, string> = {
  seek: "SEEK",
  linkedin: "LinkedIn",
};

function SectionHeader({ title }: { title: string }) {
  return (
    <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
      {title}
    </p>
  );
}

type EvidenceItem = CanonicalProfile["evidence_items"][number];

function getEvidenceItemGaps(item: EvidenceItem): string[] {
  const gaps: string[] = [];
  if (!item.situation.trim()) {
    gaps.push("situation");
  }
  if (!item.task.trim()) {
    gaps.push("task");
  }
  if (!item.outcome.trim()) {
    gaps.push("outcome");
  }
  if (item.metrics.length === 0) {
    gaps.push("metrics");
  }
  return gaps;
}

function buildProfileImprovementActions(
  profile: CanonicalProfile,
  selectedItemId: string,
): string[] {
  const actions: string[] = [];
  const selectedItem = profile.evidence_items.find((item) => item.id === selectedItemId);
  const selectedGaps = selectedItem ? getEvidenceItemGaps(selectedItem) : [];

  if (selectedItem && selectedGaps.length > 0) {
    actions.push(
      `Finish ${selectedItem.source}${selectedItem.role_title ? ` · ${selectedItem.role_title}` : ""} by filling ${selectedGaps.join(", ")}.`,
    );
  }

  if (profile.voice_samples.length < 8) {
    actions.push("Add a few more natural answers through the interview so Envoy captures your writing voice more reliably.");
  }

  const unapprovedWithGaps = profile.evidence_items.filter(
    (item) => item.confidence !== "approved" && getEvidenceItemGaps(item).length > 0,
  );
  if (unapprovedWithGaps.length > 0) {
    const nextItem = unapprovedWithGaps[0];
    const nextGaps = getEvidenceItemGaps(nextItem);
    actions.push(
      `Run the interview on ${nextItem.source}${nextItem.role_title ? ` · ${nextItem.role_title}` : ""} next to tighten ${nextGaps.join(", ")}.`,
    );
  }

  const approvedCount = profile.evidence_items.filter((item) => item.confidence === "approved").length;
  if (approvedCount < 3) {
    actions.push("Approve a few of your strongest evidence items so cover-letter generation has trusted material to write from.");
  }

  return actions.slice(0, 4);
}

function formatPercentScore(score: number | null | undefined): string {
  if (score === null || score === undefined) {
    return "Not scored yet";
  }
  return `${Math.round(score * 100)}%`;
}

function mergeInterviewDraftIntoProfile(
  profile: CanonicalProfile,
  session: ProfileInterviewSessionResponse | null,
): CanonicalProfile {
  if (!session?.draft_item) {
    return profile;
  }
  const draft = session.draft_item;
  return {
    ...profile,
    evidence_items: profile.evidence_items.map((item) => {
      if (item.id !== draft.id) {
        return item;
      }
      return {
        ...item,
        situation: draft.situation || item.situation,
        task: draft.task || item.task,
        action: draft.action || item.action,
        outcome: draft.outcome || item.outcome,
        metrics: draft.metrics.length > 0 ? draft.metrics : item.metrics,
        tone_sample: draft.tone_sample ?? item.tone_sample,
        confidence: draft.confidence || item.confidence,
      };
    }),
  };
}

function RawProfilePreview({ rawProfile }: { rawProfile: RawProfile }) {
  return (
    <div className="space-y-3">
      <div className="rounded border bg-slate-50 p-3">
        <p className="text-sm font-medium text-slate-800">
          {rawProfile.identity.name || "Unnamed candidate"}
        </p>
        <p className="text-xs text-slate-500 mt-0.5">
          {[rawProfile.identity.headline, rawProfile.identity.email, rawProfile.identity.location]
            .filter(Boolean)
            .join(" · ")}
        </p>
        {rawProfile.summary && (
          <p className="mt-2 text-xs text-slate-600 leading-5">{rawProfile.summary}</p>
        )}
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="rounded border bg-white p-3">
          <SectionHeader title="Experience" />
          <div className="mt-2 space-y-3">
            {rawProfile.experience.slice(0, 3).map((item) => (
              <div key={item.id}>
                <p className="text-sm font-medium text-slate-800">
                  {[item.title, item.company].filter(Boolean).join(" at ") || "Experience item"}
                </p>
                {item.period_raw && (
                  <p className="text-[11px] text-slate-400 mt-0.5">{item.period_raw}</p>
                )}
                {item.bullets.length > 0 && (
                  <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-slate-600">
                    {item.bullets.slice(0, 3).map((bullet, index) => (
                      <li key={`${item.id}-${index}`}>{bullet.text}</li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
            {rawProfile.experience.length === 0 && (
              <p className="text-xs text-slate-400">No experience entries extracted yet.</p>
            )}
          </div>
        </div>

        <div className="rounded border bg-white p-3 space-y-3">
          <div>
            <SectionHeader title="Skills" />
            <div className="mt-2 flex flex-wrap gap-1.5">
              {rawProfile.skills.slice(0, 12).map((skill) => (
                <span key={skill} className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">
                  {skill}
                </span>
              ))}
              {rawProfile.skills.length === 0 && (
                <p className="text-xs text-slate-400">No skills section extracted yet.</p>
              )}
            </div>
          </div>

          <div>
            <SectionHeader title="Projects" />
            <div className="mt-2 space-y-2">
              {rawProfile.projects.slice(0, 2).map((project) => (
                <div key={project.id}>
                  <p className="text-sm font-medium text-slate-800">{project.name || "Project"}</p>
                  {project.summary && (
                    <p className="mt-1 text-xs text-slate-600">{project.summary}</p>
                  )}
                </div>
              ))}
              {rawProfile.projects.length === 0 && (
                <p className="text-xs text-slate-400">No project entries extracted yet.</p>
              )}
            </div>
          </div>
        </div>
      </div>

      {rawProfile.parse_notes.length > 0 && (
        <div className="rounded border border-amber-200 bg-amber-50 p-3">
          <SectionHeader title="Parse Notes" />
          <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-amber-900">
            {rawProfile.parse_notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function SetupPage() {
  const queryClient = useQueryClient();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [interviewAnswer, setInterviewAnswer] = useState("");
  const [editableTargetProfile, setEditableTargetProfile] = useState<CanonicalProfile | null>(null);
  const [selectedEvidenceItemId, setSelectedEvidenceItemId] = useState("");

  const statusQuery = useQuery({
    queryKey: ["setup-status"],
    queryFn: fetchSetupStatus,
    refetchInterval: 5000,
  });

  const rawProfileQuery = useQuery({
    queryKey: ["setup-profile-raw"],
    queryFn: fetchRawProfile,
    refetchInterval: 5000,
  });

  const loginMutation = useMutation({
    mutationFn: openProviderLogin,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["setup-status"] }),
  });

  const uploadMutation = useMutation({
    mutationFn: uploadProfileFile,
    onSuccess: async () => {
      setSelectedFile(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["setup-status"] }),
        queryClient.invalidateQueries({ queryKey: ["setup-profile-raw"] }),
        queryClient.invalidateQueries({ queryKey: ["setup-profile-target"] }),
      ]);
    },
  });

  const targetQuery = useQuery({
    queryKey: ["setup-profile-target"],
    queryFn: fetchProfileTarget,
    refetchInterval: 5000,
  });

  const profileInterviewQuery = useQuery({
    queryKey: ["profile-interview-active"],
    queryFn: fetchActiveProfileInterview,
    refetchInterval: 5000,
  });

  const saveTargetMutation = useMutation({
    mutationFn: saveProfileTarget,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["setup-status"] });
      queryClient.invalidateQueries({ queryKey: ["setup-profile-target"] });
    },
  });

  const startInterviewMutation = useMutation({
    mutationFn: startProfileInterview,
    onSuccess: async (data) => {
      setInterviewAnswer("");
      queryClient.setQueryData(["profile-interview-active"], data);
      await queryClient.invalidateQueries({ queryKey: ["setup-profile-target"] });
    },
  });

  const selectInterviewMutation = useMutation({
    mutationFn: ({ sessionId, itemId }: { sessionId: string; itemId: string }) =>
      selectProfileInterviewItem(sessionId, itemId),
    onSuccess: async (data) => {
      setInterviewAnswer("");
      queryClient.setQueryData(["profile-interview-active"], data);
      await queryClient.invalidateQueries({ queryKey: ["setup-profile-target"] });
    },
  });

  const answerInterviewMutation = useMutation({
    mutationFn: ({ sessionId, answer }: { sessionId: string; answer: string }) =>
      answerProfileInterview(sessionId, answer),
    onSuccess: async (data) => {
      setInterviewAnswer("");
      queryClient.setQueryData(["profile-interview-active"], data);
      await queryClient.invalidateQueries({ queryKey: ["setup-profile-target"] });
    },
  });

  const approveInterviewMutation = useMutation({
    mutationFn: approveProfileInterview,
    onSuccess: async (data) => {
      queryClient.setQueryData(["profile-interview-active"], data);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["setup-profile-target"] }),
        queryClient.invalidateQueries({ queryKey: ["setup-status"] }),
      ]);
    },
  });

  const deferInterviewMutation = useMutation({
    mutationFn: deferProfileInterview,
    onSuccess: async (data) => {
      setInterviewAnswer("");
      queryClient.setQueryData(["profile-interview-active"], data);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["setup-profile-target"] }),
        queryClient.invalidateQueries({ queryKey: ["setup-status"] }),
      ]);
    },
  });

  const completeInterviewMutation = useMutation({
    mutationFn: completeProfileInterview,
    onSuccess: async (data) => {
      setInterviewAnswer("");
      queryClient.setQueryData(["profile-interview-active"], data);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["setup-profile-target"] }),
        queryClient.invalidateQueries({ queryKey: ["setup-status"] }),
      ]);
    },
  });

  const status = statusQuery.data;
  const rawProfile = rawProfileQuery.data;
  const target = targetQuery.data;
  const activeInterview = profileInterviewQuery.data;
  const hasProfileSource = Boolean(status?.raw_profile_exists || status?.profile_json_exists);
  const allDone = hasProfileSource && status?.target_profile_exists && status?.chrome_has_cookies;

  useEffect(() => {
    if (!activeInterview || activeInterview.status !== "waiting_for_user") {
      return;
    }
    setInterviewAnswer(activeInterview.current_prompt.suggested_answer ?? "");
  }, [
    activeInterview?.session_id,
    activeInterview?.status,
    activeInterview?.current_prompt.question_id,
    activeInterview?.current_prompt.suggested_answer,
  ]);

  useEffect(() => {
    if (!target?.target_profile) {
      setEditableTargetProfile(null);
      setSelectedEvidenceItemId("");
      return;
    }
    const nextProfile = mergeInterviewDraftIntoProfile(
      target.target_profile as CanonicalProfile,
      activeInterview ?? null,
    );
    setEditableTargetProfile(nextProfile);
    setSelectedEvidenceItemId((current) => {
      if (current && nextProfile.evidence_items.some((item) => item.id === current)) {
        return current;
      }
      return nextProfile.evidence_items[0]?.id ?? "";
    });
  }, [target?.target_profile, activeInterview?.draft_item, activeInterview?.session_id]);

  useEffect(() => {
    if (!activeInterview?.draft_item) {
      return;
    }
    setEditableTargetProfile((current) => {
      if (!current) {
        return current;
      }
      return mergeInterviewDraftIntoProfile(current, activeInterview ?? null);
    });
  }, [activeInterview?.draft_item, activeInterview?.session_id]);

  useEffect(() => {
    if (!activeInterview?.current_item_id || activeInterview.status === "completed") {
      return;
    }
    setSelectedEvidenceItemId(activeInterview.current_item_id);
  }, [activeInterview?.current_item_id, activeInterview?.status]);

  const updateEvidenceField = (
    itemId: string,
    field: "situation" | "task" | "action" | "outcome" | "metrics",
    value: string,
  ) => {
    setEditableTargetProfile((current) => {
      if (!current) {
        return current;
      }
      return {
        ...current,
        evidence_items: current.evidence_items.map((item) => {
          if (item.id !== itemId) {
            return item;
          }
          if (field === "metrics") {
            return {
              ...item,
              metrics: value
                .split("\n")
                .map((entry) => entry.trim())
                .filter(Boolean),
            };
          }
          return {
            ...item,
            [field]: value,
          };
        }),
      };
    });
  };

  const targetProfileChanged =
    editableTargetProfile !== null &&
    target?.target_profile !== undefined &&
    JSON.stringify(editableTargetProfile) !== JSON.stringify(target.target_profile);
  const profileForView = editableTargetProfile || target?.target_profile;
  const editableEvidenceItems = profileForView?.evidence_items ?? [];
  const selectedEvidenceItem =
    editableEvidenceItems.find((item) => item.id === selectedEvidenceItemId) ?? editableEvidenceItems[0];
  const canRestartInterview = !activeInterview || activeInterview.status === "completed";
  const approvedItemsCount = editableEvidenceItems.filter((item) => item.confidence === "approved").length;
  const fullyFilledItemsCount = editableEvidenceItems.filter((item) => getEvidenceItemGaps(item).length === 0).length;
  const itemsWithOpenGaps = editableEvidenceItems.filter((item) => getEvidenceItemGaps(item).length > 0);
  const profileCompletionPercent =
    editableEvidenceItems.length === 0 ? 0 : Math.round((fullyFilledItemsCount / editableEvidenceItems.length) * 100);
  const suggestedActions = profileForView
    ? buildProfileImprovementActions(profileForView, selectedEvidenceItemId)
    : [];
  const selectedItemGaps = selectedEvidenceItem ? getEvidenceItemGaps(selectedEvidenceItem) : [];
  const selectedItemQualityScore =
    selectedEvidenceItem ? activeInterview?.item_quality_scores?.[selectedEvidenceItem.id] : undefined;

  const persistEditsIfNeeded = async () => {
    if (!editableTargetProfile || !targetProfileChanged) {
      return;
    }
    await saveTargetMutation.mutateAsync(editableTargetProfile);
  };

  const handleSelectEvidenceItem = async (itemId: string) => {
    setSelectedEvidenceItemId(itemId);
    await persistEditsIfNeeded();
    if (!activeInterview || activeInterview.status === "completed") {
      return;
    }
    if (activeInterview.current_item_id === itemId) {
      return;
    }
    await selectInterviewMutation.mutateAsync({
      sessionId: activeInterview.session_id,
      itemId,
    });
  };

  const handleStartInterview = async () => {
    await persistEditsIfNeeded();
    await startInterviewMutation.mutateAsync(undefined);
  };

  const handleRunInterviewOnSelectedItem = async () => {
    if (!selectedEvidenceItem) {
      return;
    }
    await persistEditsIfNeeded();
    if (!activeInterview || activeInterview.status === "completed") {
      await startInterviewMutation.mutateAsync(selectedEvidenceItem.id);
      return;
    }
    await selectInterviewMutation.mutateAsync({
      sessionId: activeInterview.session_id,
      itemId: selectedEvidenceItem.id,
    });
  };

  const ensureSelectedItemIsActive = async (): Promise<ProfileInterviewSessionResponse | null> => {
    if (!selectedEvidenceItem) {
      return activeInterview ?? null;
    }
    if (!activeInterview || activeInterview.status === "completed") {
      return await startInterviewMutation.mutateAsync(selectedEvidenceItem.id);
    }
    if (activeInterview.current_item_id === selectedEvidenceItem.id) {
      return activeInterview;
    }
    return await selectInterviewMutation.mutateAsync({
      sessionId: activeInterview.session_id,
      itemId: selectedEvidenceItem.id,
    });
  };

  const handleAnswerInterview = async () => {
    if (!activeInterview || !interviewAnswer.trim()) {
      return;
    }
    await persistEditsIfNeeded();
    await answerInterviewMutation.mutateAsync({
      sessionId: activeInterview.session_id,
      answer: interviewAnswer,
    });
  };

  const handleApproveInterview = async () => {
    if (!activeInterview) {
      return;
    }
    await persistEditsIfNeeded();
    await approveInterviewMutation.mutateAsync(activeInterview.session_id);
  };

  const handleDeferInterview = async () => {
    if (!activeInterview) {
      return;
    }
    await persistEditsIfNeeded();
    let session = await ensureSelectedItemIsActive();
    if (!session) {
      return;
    }
    if (session.status === "waiting_for_user" && interviewAnswer.trim()) {
      session = await answerInterviewMutation.mutateAsync({
        sessionId: session.session_id,
        answer: interviewAnswer,
      });
    }
    if (session.status === "reviewing") {
      await deferInterviewMutation.mutateAsync(session.session_id);
    }
  };

  const handleCompleteInterview = async () => {
    if (!activeInterview) {
      return;
    }
    await persistEditsIfNeeded();
    await completeInterviewMutation.mutateAsync(activeInterview.session_id);
  };

  return (
    <section className="space-y-6 max-w-5xl">
      <header>
        <h1 className="text-2xl font-semibold">Setup</h1>
        <p className="text-slate-500 text-sm mt-1">
          Upload a source profile, review the parsed raw profile, and then shape the STAR-style target profile the agent should eventually write from.
        </p>
      </header>

      {statusQuery.isLoading && <p className="text-slate-400 text-sm">Checking…</p>}

      {status && (
        <>
          <div className="rounded-lg border bg-white divide-y">
            <div className="p-4 space-y-4">
              <div className="flex items-center gap-2">
                <span className={`text-base font-semibold ${hasProfileSource ? "text-green-600" : "text-slate-800"}`}>
                  1. Upload your source profile
                </span>
                {hasProfileSource && (
                  <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 font-medium">Done</span>
                )}
              </div>

              <p className="text-sm text-slate-500">
                Upload a resume or profile file in <strong>PDF</strong>, <strong>DOCX</strong>, or <strong>JSON</strong>. Envoy stores the original file, extracts a raw profile artifact, and uses that to build the canonical target profile.
              </p>

              <div className="rounded border bg-slate-50 p-3 space-y-3">
                <input
                  type="file"
                  accept=".pdf,.docx,.json,application/pdf,application/json,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
                  className="block w-full text-sm text-slate-700 file:mr-4 file:rounded file:border-0 file:bg-blue-600 file:px-3 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-blue-700"
                />
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => selectedFile && uploadMutation.mutate(selectedFile)}
                    disabled={!selectedFile || uploadMutation.isPending}
                    className="rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                  >
                    {uploadMutation.isPending ? "Uploading…" : "Upload and parse"}
                  </button>
                  {selectedFile && (
                    <span className="text-xs text-slate-500">{selectedFile.name}</span>
                  )}
                </div>
              </div>

              <div className="grid gap-2 text-xs text-slate-500 sm:grid-cols-2">
                <div className="rounded border bg-white px-3 py-2">
                  Raw profile path: <span className="font-mono text-slate-700">{status.raw_profile_path}</span>
                </div>
                <div className="rounded border bg-white px-3 py-2">
                  Latest upload: <span className="font-mono text-slate-700">{status.latest_uploaded_filename || "None yet"}</span>
                </div>
              </div>

              {status.profile_json_exists && (
                <p className="text-xs text-slate-400">
                  Legacy JSON profile still exists at <span className="font-mono">{status.profile_json_path}</span>. Until later phases switch generation to canonical profile only, the main application workflows still read that configured JSON profile.
                </p>
              )}

              {uploadMutation.isSuccess && (
                <div className="rounded border border-green-200 bg-green-50 p-3 text-sm text-green-800">
                  Uploaded <strong>{uploadMutation.data.source_document.filename}</strong> and saved raw profile to <span className="font-mono">{uploadMutation.data.raw_profile_path}</span>.
                </div>
              )}
              {uploadMutation.isError && (
                <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                  {(uploadMutation.error as Error).message}
                </div>
              )}
            </div>

            <div className="p-4 space-y-4">
              <div className="flex items-center gap-2">
                <span className={`text-base font-semibold ${status.raw_profile_exists ? "text-green-600" : "text-slate-800"}`}>
                  2. Review parsed raw profile
                </span>
                {status.raw_profile_exists && (
                  <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 font-medium">Ready</span>
                )}
              </div>

              <p className="text-sm text-slate-500">
                This is the non-canonical parsed profile derived from your upload. It stays close to the source material so we can preserve traceability before turning it into STAR-style evidence.
              </p>

              {rawProfileQuery.isLoading && <p className="text-xs text-slate-400">Loading raw profile…</p>}

              {rawProfile?.raw_profile ? (
                <>
                  <p className="text-xs text-slate-400 font-mono">{rawProfile.raw_profile_path}</p>
                  <RawProfilePreview rawProfile={rawProfile.raw_profile} />
                </>
              ) : (
                <p className="text-xs text-slate-400">
                  Upload a source profile to generate a raw profile artifact.
                </p>
              )}
            </div>

            <div className="p-4 space-y-4">
              <div className="flex items-center gap-2">
                <span className={`text-base font-semibold ${status.target_profile_exists ? "text-green-600" : "text-slate-800"}`}>
                  3. Build your target profile
                </span>
                {status.target_profile_exists && (
                  <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 font-medium">Done</span>
                )}
              </div>

              <p className="text-sm text-slate-500">
                This is the canonical STAR-style profile draft the agent will eventually write from. It is generated from the raw profile when available, otherwise from the existing JSON profile.
              </p>

              {!hasProfileSource && (
                <p className="text-xs text-slate-400">
                  Upload a source profile first, or keep using the existing JSON profile, then Envoy can generate a canonical target profile draft.
                </p>
              )}

              {hasProfileSource && targetQuery.isLoading && (
                <p className="text-xs text-slate-400">Generating target profile draft…</p>
              )}

              {hasProfileSource && target?.target_profile && (
                <div className="space-y-3">
                  <p className="text-xs text-slate-400 font-mono">
                    {status.target_profile_exists ? status.target_profile_path : `Draft path: ${target.target_profile_path}`}
                  </p>

                  <div className="rounded border bg-slate-50 p-3">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-medium text-slate-800">
                          {editableTargetProfile?.name || target.target_profile.name || "Unnamed profile"}
                        </p>
                        {(editableTargetProfile?.headline || target.target_profile.headline) && (
                          <p className="text-xs text-slate-500 mt-0.5">
                            {editableTargetProfile?.headline || target.target_profile.headline}
                          </p>
                        )}
                      </div>
                      {editableTargetProfile && (
                        <button
                          onClick={() => saveTargetMutation.mutate(editableTargetProfile)}
                          disabled={saveTargetMutation.isPending || !targetProfileChanged}
                          className="rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                        >
                          {saveTargetMutation.isPending
                            ? "Saving…"
                            : status.target_profile_exists
                              ? "Save changes"
                              : "Save draft"}
                        </button>
                      )}
                    </div>
                    <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-600">
                      <div className="rounded border bg-white px-2 py-1.5">
                        Evidence items: {(editableTargetProfile || target.target_profile).evidence_items.length}
                      </div>
                      <div className="rounded border bg-white px-2 py-1.5">
                        Voice samples: {(editableTargetProfile || target.target_profile).voice_samples.length}
                      </div>
                    </div>
                  </div>

                  <div className="rounded border border-indigo-200 bg-indigo-50 p-3 space-y-3">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <SectionHeader title="Overall Status" />
                        <p className="mt-1 text-sm text-indigo-950">
                          Canonical profile completeness is {profileCompletionPercent}%.
                        </p>
                      </div>
                      <div className="rounded border border-indigo-200 bg-white px-3 py-2 text-xs text-indigo-900">
                        {itemsWithOpenGaps.length === 0
                          ? "All STAR cards are filled"
                          : `${itemsWithOpenGaps.length} items still need work`}
                      </div>
                    </div>

                    <div className="grid gap-2 md:grid-cols-4 text-xs text-slate-700">
                      <div className="rounded border bg-white px-3 py-2">
                        Evidence items: <span className="font-medium">{editableEvidenceItems.length}</span>
                      </div>
                      <div className="rounded border bg-white px-3 py-2">
                        Fully filled STAR cards: <span className="font-medium">{fullyFilledItemsCount}</span>
                      </div>
                      <div className="rounded border bg-white px-3 py-2">
                        Approved items: <span className="font-medium">{approvedItemsCount}</span>
                      </div>
                      <div className="rounded border bg-white px-3 py-2">
                        Combined profile score: <span className="font-medium">{formatPercentScore(activeInterview?.overall_profile_score)}</span>
                      </div>
                    </div>

                    <div className="grid gap-2 md:grid-cols-3 text-xs text-slate-700">
                      <div className="rounded border bg-white px-3 py-2">
                        Field completeness: <span className="font-medium">{profileCompletionPercent}%</span>
                      </div>
                      <div className="rounded border bg-white px-3 py-2">
                        Answer quality: <span className="font-medium">{formatPercentScore(activeInterview?.overall_answer_quality_score)}</span>
                      </div>
                      <div className="rounded border bg-white px-3 py-2">
                        Voice profile: <span className="font-medium">{profileForView?.voice_profile.confidence || "draft"}</span>
                      </div>
                    </div>

                    {selectedEvidenceItem && (
                      <div className="rounded border bg-white p-3 text-sm text-slate-700">
                        <span className="font-medium text-slate-900">Selected item status:</span>{" "}
                        {selectedItemGaps.length === 0
                          ? `${selectedEvidenceItem.source}${selectedEvidenceItem.role_title ? ` · ${selectedEvidenceItem.role_title}` : ""} is filled and ready for approval or another interview pass.`
                          : `${selectedEvidenceItem.source}${selectedEvidenceItem.role_title ? ` · ${selectedEvidenceItem.role_title}` : ""} still needs ${selectedItemGaps.join(", ")}.`}
                        {" "}
                        {selectedItemQualityScore !== undefined && (
                          <span className="text-slate-500">
                            Current answer quality for this item: {formatPercentScore(selectedItemQualityScore)}.
                          </span>
                        )}
                      </div>
                    )}

                    <div className="rounded border bg-white p-3">
                      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                        Best Next Actions
                      </p>
                      {suggestedActions.length > 0 ? (
                        <ul className="mt-2 list-disc space-y-1 pl-4 text-sm text-slate-700">
                          {suggestedActions.map((action) => (
                            <li key={action}>{action}</li>
                          ))}
                        </ul>
                      ) : (
                        <p className="mt-2 text-sm text-slate-700">
                          The canonical profile is in a strong place. Focus on reviewing approved items and preparing the cover-letter flow to consume them.
                        </p>
                      )}
                    </div>
                  </div>

                  {target.questions.length > 0 && !activeInterview && (
                    <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                      Envoy has deterministic backup questions available for this profile, but the guided interview is now the primary way to refine STAR evidence.
                    </div>
                  )}

                  <div className="space-y-3 rounded border border-sky-200 bg-sky-50 p-3">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <SectionHeader title="Profile Interview" />
                        <p className="mt-1 text-sm text-sky-900">
                          Select an experience or project, review the current STAR draft, and keep refining it through the interview or direct edits.
                        </p>
                      </div>
                      {canRestartInterview && (
                        <button
                          onClick={() => void handleStartInterview()}
                          disabled={startInterviewMutation.isPending}
                          className="rounded bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-700 disabled:opacity-50"
                        >
                          {startInterviewMutation.isPending
                            ? "Starting…"
                            : (editableTargetProfile || target.target_profile).evidence_items.some(
                                  (item) => item.confidence === "approved",
                                )
                              ? "Run interview again"
                              : "Start interview"}
                        </button>
                      )}
                    </div>

                    <div className="space-y-3">
                      <label className="block">
                        <span className="text-[11px] font-semibold uppercase tracking-wide text-sky-700">
                          Select Experience Or Project
                        </span>
                        <select
                          value={selectedEvidenceItem?.id ?? ""}
                          onChange={(event) => void handleSelectEvidenceItem(event.target.value)}
                          className="mt-1 w-full rounded border border-sky-200 bg-white px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-200"
                        >
                          {editableEvidenceItems.map((item) => (
                            <option key={item.id} value={item.id}>
                              {item.source}
                              {item.role_title ? ` · ${item.role_title}` : ""}
                            </option>
                          ))}
                        </select>
                      </label>

                      {selectedEvidenceItem && (
                        <div className="rounded border border-sky-200 bg-white p-3 space-y-3">
                          <div className="flex items-center justify-between gap-3">
                            <p className="text-sm font-medium text-slate-800">
                              {selectedEvidenceItem.source}
                              {selectedEvidenceItem.role_title ? ` · ${selectedEvidenceItem.role_title}` : ""}
                            </p>
                            <div className="flex items-center gap-2">
                              <span className="text-[11px] uppercase tracking-wide text-sky-700">
                                {selectedEvidenceItem.confidence}
                              </span>
                              <button
                                onClick={() => void handleRunInterviewOnSelectedItem()}
                                disabled={
                                  startInterviewMutation.isPending ||
                                  selectInterviewMutation.isPending
                                }
                                className="rounded border border-sky-300 bg-white px-2.5 py-1 text-[11px] font-medium text-sky-700 hover:bg-sky-50 disabled:opacity-50"
                              >
                                {startInterviewMutation.isPending || selectInterviewMutation.isPending
                                  ? "Starting…"
                                  : activeInterview?.status !== "completed" &&
                                      activeInterview?.current_item_id === selectedEvidenceItem.id
                                    ? "Interview this item again"
                                    : "Run interview on this item"}
                              </button>
                            </div>
                          </div>
                          <p className="text-xs text-slate-500">
                            This uses the current STAR answers on this card as the starting point and asks only the next best follow-up.
                          </p>
                          <div className="flex flex-wrap gap-1.5">
                            {selectedEvidenceItem.skills.slice(0, 6).map((skill) => (
                              <span key={skill} className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">
                                {skill}
                              </span>
                            ))}
                          </div>
                          <div className="grid gap-3 md:grid-cols-2">
                            <label className="block">
                              <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                                Situation
                              </span>
                              <textarea
                                value={selectedEvidenceItem.situation}
                                onChange={(event) =>
                                  updateEvidenceField(selectedEvidenceItem.id, "situation", event.target.value)
                                }
                                rows={4}
                                className="mt-1 w-full rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-200"
                              />
                            </label>
                            <label className="block">
                              <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                                Task
                              </span>
                              <textarea
                                value={selectedEvidenceItem.task}
                                onChange={(event) =>
                                  updateEvidenceField(selectedEvidenceItem.id, "task", event.target.value)
                                }
                                rows={4}
                                className="mt-1 w-full rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-200"
                              />
                            </label>
                            <label className="block">
                              <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                                Action
                              </span>
                              <textarea
                                value={selectedEvidenceItem.action}
                                onChange={(event) =>
                                  updateEvidenceField(selectedEvidenceItem.id, "action", event.target.value)
                                }
                                rows={4}
                                className="mt-1 w-full rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-200"
                              />
                            </label>
                            <label className="block">
                              <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                                Outcome
                              </span>
                              <textarea
                                value={selectedEvidenceItem.outcome}
                                onChange={(event) =>
                                  updateEvidenceField(selectedEvidenceItem.id, "outcome", event.target.value)
                                }
                                rows={4}
                                className="mt-1 w-full rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-200"
                              />
                            </label>
                          </div>
                          <label className="block">
                            <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                              Metrics
                            </span>
                            <textarea
                              value={selectedEvidenceItem.metrics.join("\n")}
                              onChange={(event) =>
                                updateEvidenceField(selectedEvidenceItem.id, "metrics", event.target.value)
                              }
                              rows={3}
                              className="mt-1 w-full rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-200"
                              placeholder="One metric or proof point per line"
                            />
                          </label>
                        </div>
                      )}

                    {activeInterview && (
                      <div className="space-y-3">
                        <div className="grid gap-2 text-xs text-sky-800 sm:grid-cols-3">
                          <div className="rounded border border-sky-200 bg-white px-3 py-2">
                            Status: <span className="font-medium">{activeInterview.status}</span>
                          </div>
                          <div className="rounded border border-sky-200 bg-white px-3 py-2">
                            Approved items: <span className="font-medium">{activeInterview.approved_items}</span>
                          </div>
                          <div className="rounded border border-sky-200 bg-white px-3 py-2">
                            Total items: <span className="font-medium">{activeInterview.total_items}</span>
                          </div>
                        </div>

                        {activeInterview.current_item_id === selectedEvidenceItem?.id && (
                          <div className="rounded border border-sky-200 bg-white px-3 py-2 text-xs text-sky-800">
                            Completeness {Math.round(activeInterview.completeness_score * 100)}%
                            {" · "}
                            Open gaps: {activeInterview.open_gaps.length ? activeInterview.open_gaps.join(", ") : "none"}
                            {" · "}
                            Combined score: {formatPercentScore(activeInterview.overall_profile_score)}
                          </div>
                        )}

                        {activeInterview.status === "waiting_for_user" && (
                          <div className="rounded border border-sky-200 bg-white p-3">
                            <p className="text-sm font-medium text-sky-900">
                              {activeInterview.current_prompt.question || activeInterview.current_question}
                            </p>
                            {activeInterview.current_prompt.source_basis.length > 0 && (
                              <div className="mt-3 rounded border border-slate-200 bg-slate-50 p-3">
                                <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                                  Why Envoy suggested this
                                </p>
                                <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-slate-600">
                                  {activeInterview.current_prompt.source_basis.map((basis) => (
                                    <li key={basis}>{basis}</li>
                                  ))}
                                </ul>
                              </div>
                            )}
                            {activeInterview.current_prompt.improvement_hint && (
                              <div className="mt-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                                <span className="font-medium">How to make it stronger:</span>{" "}
                                {activeInterview.current_prompt.improvement_hint}
                              </div>
                            )}
                            {(activeInterview.last_answer_assessment.strengths.length > 0 ||
                              activeInterview.last_answer_assessment.weaknesses.length > 0 ||
                              activeInterview.last_answer_assessment.next_focus) && (
                              <div className="mt-3 rounded border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-950 space-y-2">
                                <p className="font-medium">
                                  Latest answer quality: {formatPercentScore(activeInterview.last_answer_assessment.score)}
                                </p>
                                {activeInterview.last_answer_assessment.strengths.length > 0 && (
                                  <p>
                                    Strongest signals: {activeInterview.last_answer_assessment.strengths.join(", ")}.
                                  </p>
                                )}
                                {activeInterview.last_answer_assessment.weaknesses.length > 0 && (
                                  <p>
                                    Still weak: {activeInterview.last_answer_assessment.weaknesses.join(", ")}.
                                  </p>
                                )}
                                {activeInterview.last_answer_assessment.next_focus && (
                                  <p>
                                    Next focus: {activeInterview.last_answer_assessment.next_focus}
                                  </p>
                                )}
                              </div>
                            )}
                            <textarea
                              value={interviewAnswer}
                              onChange={(event) => setInterviewAnswer(event.target.value)}
                              rows={4}
                              className="mt-3 w-full rounded border border-sky-200 bg-white px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-200"
                              placeholder="Envoy will prefill a draft answer here when it has enough context"
                            />
                            <div className="mt-3 flex items-center gap-3">
                              <button
                                onClick={() => void handleAnswerInterview()}
                                disabled={!interviewAnswer.trim() || answerInterviewMutation.isPending}
                                className="rounded bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-700 disabled:opacity-50"
                              >
                                {answerInterviewMutation.isPending ? "Saving answer…" : "Use this answer"}
                              </button>
                              <button
                                onClick={() => void handleDeferInterview()}
                                disabled={deferInterviewMutation.isPending}
                                className="rounded border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                              >
                                {deferInterviewMutation.isPending ? "Continuing…" : "Use this and continue"}
                              </button>
                              <button
                                onClick={() => void handleCompleteInterview()}
                                disabled={completeInterviewMutation.isPending}
                                className="rounded border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                              >
                                {completeInterviewMutation.isPending ? "Ending…" : "End interview"}
                              </button>
                              <span className="text-xs text-sky-700">
                                Edit the draft however you like. Envoy will rewrite the evidence item and then ask the next best follow-up.
                              </span>
                            </div>
                          </div>
                        )}

                        {activeInterview.status === "reviewing" && (
                          <div className="rounded border border-sky-200 bg-white p-3">
                            <p className="text-sm text-sky-900">
                              This evidence item is ready for approval. Approve it to save it into the canonical profile and move to the next item.
                            </p>
                            <button
                              onClick={() => void handleApproveInterview()}
                              disabled={approveInterviewMutation.isPending}
                              className="mt-3 rounded bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-700 disabled:opacity-50"
                            >
                              {approveInterviewMutation.isPending ? "Approving…" : "Approve item"}
                            </button>
                            <button
                              onClick={() => void handleDeferInterview()}
                              disabled={deferInterviewMutation.isPending}
                              className="mt-3 ml-3 rounded border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                            >
                              {deferInterviewMutation.isPending ? "Continuing…" : "Use this and continue"}
                            </button>
                            <button
                              onClick={() => void handleCompleteInterview()}
                              disabled={completeInterviewMutation.isPending}
                              className="mt-3 ml-3 rounded border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                            >
                              {completeInterviewMutation.isPending ? "Ending…" : "End interview"}
                            </button>
                          </div>
                        )}

                        {activeInterview.status === "completed" && (
                          <p className="text-sm text-green-700">
                            Profile interview complete. Envoy has stepped through the available evidence items for this phase.
                          </p>
                        )}

                        {activeInterview.error && (
                          <p className="text-sm text-red-600">{activeInterview.error}</p>
                        )}
                      </div>
                    )}

                    {startInterviewMutation.isError && (
                      <p className="text-sm text-red-600">
                        {(startInterviewMutation.error as Error).message}
                      </p>
                    )}
                    {answerInterviewMutation.isError && (
                      <p className="text-sm text-red-600">
                        {(answerInterviewMutation.error as Error).message}
                      </p>
                    )}
                    {approveInterviewMutation.isError && (
                      <p className="text-sm text-red-600">
                        {(approveInterviewMutation.error as Error).message}
                      </p>
                    )}
                    {deferInterviewMutation.isError && (
                      <p className="text-sm text-red-600">
                        {(deferInterviewMutation.error as Error).message}
                      </p>
                    )}
                    {completeInterviewMutation.isError && (
                      <p className="text-sm text-red-600">
                        {(completeInterviewMutation.error as Error).message}
                      </p>
                    )}
                    {selectInterviewMutation.isError && (
                      <p className="text-sm text-red-600">
                        {(selectInterviewMutation.error as Error).message}
                      </p>
                    )}
                    </div>
                  </div>

                  {saveTargetMutation.isSuccess && (
                    <p className="text-sm text-green-700">
                      Target profile saved. You can now refine this file or use it as the basis for the next onboarding step.
                    </p>
                  )}
                  {saveTargetMutation.isError && (
                    <p className="text-sm text-red-600">
                      {(saveTargetMutation.error as Error).message}
                    </p>
                  )}
                </div>
              )}
            </div>

            <div className="p-4">
              <div className="flex items-center gap-2 mb-1">
                <span className={`text-base font-semibold ${status.chrome_has_cookies ? "text-green-600" : "text-slate-800"}`}>
                  4. Log in to job providers
                </span>
                {status.chrome_has_cookies && (
                  <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 font-medium">Done</span>
                )}
              </div>
              <p className="text-sm text-slate-500 mb-3">
                Envoy uses a <strong>dedicated browser profile</strong>, separate from your personal Chrome, so it only has access to the accounts you log in to here.
              </p>

              {status.chrome_profile_dir && (
                <p className="text-xs text-slate-400 font-mono mb-3">
                  Profile: {status.chrome_profile_dir}
                </p>
              )}

              <div className="space-y-2">
                {status.providers.map((provider) => (
                  <div key={provider} className="flex items-center justify-between gap-3 p-3 rounded border bg-slate-50">
                    <span className="text-sm font-medium text-slate-700">
                      {PROVIDER_LABELS[provider] ?? provider}
                    </span>
                    <button
                      onClick={() => loginMutation.mutate(provider)}
                      disabled={loginMutation.isPending && loginMutation.variables === provider}
                      className="rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                    >
                      {loginMutation.isPending && loginMutation.variables === provider
                        ? "Opening…"
                        : "Open login page"}
                    </button>
                  </div>
                ))}
              </div>

              {loginMutation.isSuccess && (
                <p className="mt-3 text-sm text-slate-500">
                  Chrome opened. Log in, then come back here. This page refreshes automatically.
                </p>
              )}
              {loginMutation.isError && (
                <p className="mt-2 text-sm text-red-600">{(loginMutation.error as Error).message}</p>
              )}
            </div>
          </div>

          {allDone && (
            <div className="rounded-lg border border-green-200 bg-green-50 p-4 text-sm text-green-800">
              <strong>You're all set.</strong> Head to <strong>Jobs</strong> to run your first search.
            </div>
          )}
        </>
      )}
    </section>
  );
}
