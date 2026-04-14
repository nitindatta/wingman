import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";

type SetupStatus = {
  profile_json_exists: boolean;
  profile_json_path: string;
  chrome_profile_exists: boolean;
  chrome_has_cookies: boolean;
  chrome_profile_dir: string;
  providers: string[];
};

async function fetchSetupStatus(): Promise<SetupStatus> {
  const res = await apiFetch("/api/setup/status");
  if (!res.ok) throw new Error("Failed to fetch setup status");
  return res.json();
}

async function openProviderLogin(provider: string) {
  const res = await apiFetch(`/api/setup/login/${provider}`, { method: "POST" });
  if (!res.ok) throw new Error("Failed to open login");
  return res.json();
}

function CheckRow({ label, ok, detail }: { label: string; ok: boolean; detail?: string }) {
  return (
    <div className="flex items-start gap-3 py-3 border-b last:border-0">
      <span className={`mt-0.5 text-lg ${ok ? "text-green-500" : "text-slate-300"}`}>
        {ok ? "✓" : "○"}
      </span>
      <div className="flex-1 min-w-0">
        <p className={`text-sm font-medium ${ok ? "text-slate-800" : "text-slate-500"}`}>{label}</p>
        {detail && <p className="text-xs text-slate-400 mt-0.5 font-mono truncate">{detail}</p>}
      </div>
    </div>
  );
}

const PROVIDER_LABELS: Record<string, string> = {
  seek: "SEEK",
  linkedin: "LinkedIn",
};

export default function SetupPage() {
  const queryClient = useQueryClient();

  const statusQuery = useQuery({
    queryKey: ["setup-status"],
    queryFn: fetchSetupStatus,
    refetchInterval: 5000,
  });

  const loginMutation = useMutation({
    mutationFn: openProviderLogin,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["setup-status"] }),
  });

  const status = statusQuery.data;
  const allDone = status?.profile_json_exists && status?.chrome_has_cookies;

  return (
    <section className="space-y-6 max-w-xl">
      <header>
        <h1 className="text-2xl font-semibold">Setup</h1>
        <p className="text-slate-500 text-sm mt-1">
          Complete these steps before running your first search.
        </p>
      </header>

      {statusQuery.isLoading && <p className="text-slate-400 text-sm">Checking…</p>}

      {status && (
        <>
          {/* Checklist */}
          <div className="rounded-lg border bg-white divide-y">
            {/* Step 1 — Profile */}
            <div className="p-4">
              <div className="flex items-center gap-2 mb-1">
                <span className={`text-base font-semibold ${status.profile_json_exists ? "text-green-600" : "text-slate-800"}`}>
                  1. Create your profile
                </span>
                {status.profile_json_exists && (
                  <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 font-medium">Done</span>
                )}
              </div>
              <p className="text-sm text-slate-500 mb-2">
                Your profile tells the AI about your skills, experience, and preferences so it can personalise cover letters and answer screening questions.
              </p>
              {status.profile_json_exists ? (
                <p className="text-xs text-slate-400 font-mono">{status.profile_json_path}</p>
              ) : (
                <div className="bg-slate-50 border rounded p-3 text-xs font-mono text-slate-600 space-y-1">
                  <p>Copy the example and fill it in:</p>
                  <p className="text-slate-800">cp profile/example_profile.json profile/my_profile.json</p>
                  <p className="text-slate-500 mt-1">Then set in agent/.env:</p>
                  <p className="text-slate-800">PROFILE_PATH=../profile/my_profile.json</p>
                </div>
              )}
            </div>

            {/* Step 2 — Provider logins */}
            <div className="p-4">
              <div className="flex items-center gap-2 mb-1">
                <span className={`text-base font-semibold ${status.chrome_has_cookies ? "text-green-600" : "text-slate-800"}`}>
                  2. Log in to job providers
                </span>
                {status.chrome_has_cookies && (
                  <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 font-medium">Done</span>
                )}
              </div>
              <p className="text-sm text-slate-500 mb-3">
                Envoy uses a <strong>dedicated browser profile</strong> — separate from your personal Chrome — so it only has access to the accounts you log in to here.
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
                  Chrome opened — log in, then come back here. This page refreshes automatically.
                </p>
              )}
              {loginMutation.isError && (
                <p className="mt-2 text-sm text-red-600">{(loginMutation.error as Error).message}</p>
              )}
            </div>
          </div>

          {/* Ready banner */}
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
