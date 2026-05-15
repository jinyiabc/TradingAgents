"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { JobSummary, listJobs } from "@/lib/api";

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

export default function HistoryPage() {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    listJobs(200)
      .then(setJobs)
      .catch((e: Error) => setError(e.message));
  }, []);

  const filtered = (jobs ?? []).filter((j) =>
    filter ? j.ticker.toUpperCase().includes(filter.toUpperCase()) : true,
  );

  return (
    <>
      <div
        style={{
          display: "flex",
          gap: 12,
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <h1 style={{ margin: 0 }}>History</h1>
        <input
          placeholder="Filter by ticker"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ marginLeft: "auto", width: 200 }}
        />
      </div>

      {error && <div className="error-box">{error}</div>}
      {!jobs && !error && <p className="muted">Loading…</p>}
      {jobs && jobs.length === 0 && (
        <p className="muted">
          No analyses yet. <Link href="/">Run your first one</Link>.
        </p>
      )}

      {jobs && jobs.length > 0 && (
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table>
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Date</th>
                <th>Status</th>
                <th>Created</th>
                <th>Finished</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((j) => (
                <tr key={j.job_id}>
                  <td>
                    <Link href={`/jobs?id=${j.job_id}`}>{j.ticker}</Link>
                  </td>
                  <td>{j.analysis_date}</td>
                  <td>
                    <StatusPill status={j.status} />
                  </td>
                  <td>{fmt(j.created_at)}</td>
                  <td>{fmt(j.finished_at)}</td>
                  <td>
                    {j.status === "done" && (
                      <Link href={`/jobs/report?id=${j.job_id}`}>Report</Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
