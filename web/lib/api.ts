// Typed client for the FastAPI backend (see tradingagents/server/app.py).

export type AnalystKind = "market" | "social" | "news" | "fundamentals";

export interface CreateAnalysisRequest {
  ticker: string;
  analysis_date: string; // YYYY-MM-DD
  analysts: AnalystKind[];
  llm_provider: string;
  deep_thinking_model?: string;
  quick_thinking_model?: string;
  max_debate_rounds: number;
  max_risk_discuss_rounds: number;
  output_language: string;
}

export interface JobSummary {
  job_id: string;
  ticker: string;
  analysis_date: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  created_at: string;
  finished_at?: string | null;
}

export interface JobDetail extends JobSummary {
  current_step?: string | null;
  progress_pct?: number | null;
  error?: string | null;
  report_url?: string | null;
  started_at?: string | null;
  config: Record<string, unknown>;
}

export interface ModelOption {
  label: string;
  value: string;
}

export interface OptionsResponse {
  providers: string[];
  models: Record<string, Record<"quick" | "deep", ModelOption[]>>;
  analysts: AnalystKind[];
}

export const apiBase = (): string =>
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export interface MeResponse {
  authenticated: boolean;
  name?: string;
  email?: string;
  provider?: string;
}

export const loginUrl = (returnTo?: string): string => {
  const target = returnTo ?? (typeof window !== "undefined" ? window.location.href : "/");
  return `${apiBase()}/.auth/login/aad?post_login_redirect_uri=${encodeURIComponent(target)}`;
};

export const logoutUrl = (returnTo?: string): string => {
  const target = returnTo ?? (typeof window !== "undefined" ? window.location.origin : "/");
  return `${apiBase()}/.auth/logout?post_logout_redirect_uri=${encodeURIComponent(target)}`;
};

const redirectToLogin = (): never => {
  if (typeof window !== "undefined") {
    window.location.href = loginUrl();
  }
  throw new Error("401 Unauthorized — redirecting to login");
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
    ...init,
    // Send Easy Auth cookies cross-origin. The backend only honours
    // credentials when TRADINGAGENTS_CORS_ORIGINS is set to a specific
    // origin (not "*"); otherwise the browser drops the cookie.
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (res.status === 401) {
    redirectToLogin();
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* body wasn't JSON */
    }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const getMe = (): Promise<MeResponse> => request<MeResponse>("/me");

export const getOptions = (): Promise<OptionsResponse> =>
  request<OptionsResponse>("/config/options");

export const createAnalysis = (
  body: CreateAnalysisRequest,
): Promise<{ job_id: string }> =>
  request<{ job_id: string }>("/analyses", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const getJob = (jobId: string): Promise<JobDetail> =>
  request<JobDetail>(`/analyses/${jobId}`);

export const listJobs = (limit = 50): Promise<JobSummary[]> =>
  request<JobSummary[]>(`/analyses?limit=${limit}`);

export const reportUrl = (jobId: string): string =>
  `${apiBase()}/analyses/${jobId}/report`;
