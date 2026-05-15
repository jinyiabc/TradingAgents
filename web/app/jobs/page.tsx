"use client";

import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { getJob, JobDetail } from "@/lib/api";

const PIPELINE_STEPS = [
  "Analyst Team",
  "Research Debate",
  "Trader",
  "Risk Debate",
  "Portfolio Manager",
];

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
        <div className="muted" style={{ marginBottom: 8 }}>
          Pipeline
        </div>
        <ol style={{ margin: 0, paddingLeft: 20 }}>
          {PIPELINE_STEPS.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
        <p className="muted" style={{ marginTop: 12, marginBottom: 0 }}>
          Per-step progress will appear here in a later iteration. For now the
          job runs to completion as a single &ldquo;running&rdquo; phase.
        </p>
      </div>

      {job.status === "failed" && job.error && (
        <div style={{ marginTop: 16 }}>
          <div className="muted" style={{ marginBottom: 6 }}>
            Error
          </div>
          <div className="error-box">{job.error}</div>
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
