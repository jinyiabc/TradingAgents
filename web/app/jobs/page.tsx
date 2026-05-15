"use client";

import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { createAnalysis, CreateAnalysisRequest, getJob, JobDetail } from "@/lib/api";

// Coarse-grained pipeline categories shown to the user, with the underlying
// LangGraph node names that map onto each. The server writes the raw node
// name into job.current_step (see tradingagents/server/_worker.py); we use
// these maps to highlight the matching coarse step.
const PIPELINE_STEPS: { label: string; nodes: string[] }[] = [
  {
    label: "Analyst Team",
    nodes: [
      "Market Analyst",
      "Social Analyst",
      "News Analyst",
      "Fundamentals Analyst",
    ],
  },
  {
    label: "Research Debate",
    nodes: ["Bull Researcher", "Bear Researcher", "Research Manager"],
  },
  { label: "Trader", nodes: ["Trader"] },
  {
    label: "Risk Debate",
    nodes: [
      "Risky Analyst",
      "Safe Analyst",
      "Neutral Analyst",
      "Aggressive Debator",
      "Conservative Debator",
      "Neutral Debator",
    ],
  },
  { label: "Portfolio Manager", nodes: ["Portfolio Manager", "Judge"] },
];

function pipelineIndex(currentStep: string | null | undefined): number {
  if (!currentStep) return -1;
  return PIPELINE_STEPS.findIndex((s) =>
    s.nodes.some(
      (n) => n.toLowerCase() === currentStep.toLowerCase().trim(),
    ),
  );
}

const POLL_INTERVAL_MS = 3000;

function StatusPill({ status }: { status: string }) {
  return <span className={`status-pill status-${status}`}>{status}</span>;
}

function fmt(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function elapsed(startIso?: string | null, endIso?: string | null): string {
  if (!startIso) return "—";
  const start = new Date(startIso).getTime();
  const end = endIso ? new Date(endIso).getTime() : Date.now();
  const sec = Math.max(0, Math.round((end - start) / 1000));
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m ${s}s`;
}

function ErrorDisplay({ error }: { error: string }) {
  // First non-empty line is usually the exception-type + message — surface it
  // prominently; the traceback goes behind a toggle so the failure is
  // legible at a glance.
  const lines = error.split("\n").map((l) => l.trimEnd());
  const summary = lines.find((l) => l.trim().length > 0) ?? "Unknown error";
  const rest = error.slice(error.indexOf(summary) + summary.length).trimStart();
  return (
    <div>
      <div className="error-box" style={{ fontFamily: "inherit", fontSize: 14 }}>
        <strong>{summary}</strong>
      </div>
      {rest && (
        <details style={{ marginTop: 6 }}>
          <summary
            style={{ cursor: "pointer", fontSize: 13, color: "var(--muted)" }}
          >
            Show traceback
          </summary>
          <pre
            style={{
              marginTop: 6,
              padding: 12,
              background: "var(--bg-soft)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              fontSize: 12,
              overflowX: "auto",
              whiteSpace: "pre-wrap",
            }}
          >
            {rest}
          </pre>
        </details>
      )}
    </div>
  );
}

function RetryButton({ config }: { config: Record<string, unknown> }) {
  const router = useRouter();
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onRetry = async () => {
    setRetrying(true);
    setError(null);
    try {
      // The saved config field is exactly what was submitted, just stored
      // loosely-typed in the DB; cast back to the request shape and re-POST.
      const body = config as unknown as CreateAnalysisRequest;
      const { job_id } = await createAnalysis(body);
      router.push(`/jobs?id=${job_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setRetrying(false);
    }
  };

  return (
    <>
      <button className="primary" onClick={onRetry} disabled={retrying}>
        {retrying ? "Retrying…" : "Retry"}
      </button>
      {error && (
        <span className="muted" style={{ fontSize: 13, color: "var(--danger)" }}>
          {error}
        </span>
      )}
    </>
  );
}

function JobStatusInner() {
  const searchParams = useSearchParams();
  const id = searchParams.get("id") ?? "";
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const data = await getJob(id);
        if (cancelled) return;
        setJob(data);
        setError(null);
        if (data.status === "queued" || data.status === "running") {
          timer = setTimeout(poll, POLL_INTERVAL_MS);
        }
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        timer = setTimeout(poll, POLL_INTERVAL_MS);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [id]);

  useEffect(() => {
    if (!job || (job.status !== "queued" && job.status !== "running")) return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [job]);

  if (!id) {
    return (
      <>
        <h1>Job</h1>
        <p className="muted">
          Missing <code>id</code> query parameter. Go back to{" "}
          <Link href="/history">history</Link>.
        </p>
      </>
    );
  }

  if (!job && !error) {
    return <p className="muted">Loading…</p>;
  }

  if (!job) {
    return (
      <>
        <h1>Job {id.slice(0, 8)}</h1>
        <div className="error-box">{error}</div>
      </>
    );
  }

  const isTerminal = job.status === "done" || job.status === "failed";

  return (
    <>
      <h1 style={{ display: "flex", alignItems: "center", gap: 12 }}>
        {job.ticker} <span className="muted">·</span> {job.analysis_date}
        <StatusPill status={job.status} />
      </h1>

      <div className="card" style={{ marginTop: 12 }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "max-content 1fr",
            rowGap: 6,
            columnGap: 16,
          }}
        >
          <span className="muted">Job ID</span>
          <span style={{ fontFamily: "ui-monospace, monospace" }}>{id}</span>
          <span className="muted">Created</span>
          <span>{fmt(job.created_at)}</span>
          <span className="muted">Started</span>
          <span>{fmt(job.started_at)}</span>
          <span className="muted">Finished</span>
          <span>{fmt(job.finished_at)}</span>
          <span className="muted">Elapsed</span>
          <span>{elapsed(job.started_at, job.finished_at)}</span>
          {job.current_step && (
            <>
              <span className="muted">Current step</span>
              <span>{job.current_step}</span>
            </>
          )}
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            marginBottom: 8,
          }}
        >
          <span className="muted">Pipeline</span>
          {job.current_step && (
            <span style={{ fontSize: 13 }}>
              <span className="muted">at </span>
              <strong>{job.current_step}</strong>
            </span>
          )}
        </div>
        <ol style={{ margin: 0, paddingLeft: 20 }}>
          {PIPELINE_STEPS.map((step, i) => {
            const activeIdx = pipelineIndex(job.current_step);
            const isActive = i === activeIdx;
            const isPast = activeIdx >= 0 && i < activeIdx;
            return (
              <li
                key={step.label}
                style={{
                  fontWeight: isActive ? 600 : 400,
                  color: isActive
                    ? "var(--accent)"
                    : isPast
                      ? "var(--muted)"
                      : "var(--fg)",
                }}
              >
                {step.label}
              </li>
            );
          })}
        </ol>
      </div>

      {job.status === "failed" && (
        <div style={{ marginTop: 16 }}>
          <div className="muted" style={{ marginBottom: 6 }}>
            Error
          </div>
          {job.error ? <ErrorDisplay error={job.error} /> : (
            <div className="muted">No error details captured.</div>
          )}
          <div
            style={{
              marginTop: 12,
              display: "flex",
              gap: 12,
              alignItems: "center",
            }}
          >
            <RetryButton config={job.config} />
            <Link href="/">
              <button>New analysis</button>
            </Link>
          </div>
        </div>
      )}

      {job.status === "done" && (
        <div style={{ marginTop: 16, display: "flex", gap: 12 }}>
          <Link href={`/jobs/report?id=${id}`}>
            <button className="primary">View report</button>
          </Link>
          <Link href="/">
            <button>New analysis</button>
          </Link>
        </div>
      )}

      {isTerminal && (
        <p className="muted" style={{ marginTop: 12 }}>
          Polling stopped. <Link href="/history">See history</Link> for past
          runs.
        </p>
      )}
    </>
  );
}

export default function JobStatusPage() {
  // useSearchParams() requires a Suspense boundary for static export.
  return (
    <Suspense fallback={<p className="muted">Loading…</p>}>
      <JobStatusInner />
    </Suspense>
  );
}
