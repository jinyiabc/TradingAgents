"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { JobSummary, listJobs } from "@/lib/api";

const STATUS_VALUES = ["all", "queued", "running", "done", "failed", "cancelled"] as const;
type StatusFilter = (typeof STATUS_VALUES)[number];

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
  const [tickerFilter, setTickerFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  useEffect(() => {
    listJobs(200)
      .then(setJobs)
      .catch((e: Error) => setError(e.message));
  }, []);

  const filtered = useMemo(() => {
    return (jobs ?? []).filter((j) => {
      if (tickerFilter && !j.ticker.toUpperCase().includes(tickerFilter.toUpperCase())) {
        return false;
      }
      if (statusFilter !== "all" && j.status !== statusFilter) {
        return false;
      }
      if (dateFrom && j.analysis_date < dateFrom) return false;
      if (dateTo && j.analysis_date > dateTo) return false;
      return true;
    });
  }, [jobs, tickerFilter, statusFilter, dateFrom, dateTo]);

  const total = jobs?.length ?? 0;
  const showing = filtered.length;
  const anyFilter = tickerFilter || statusFilter !== "all" || dateFrom || dateTo;

  const clearFilters = () => {
    setTickerFilter("");
    setStatusFilter("all");
    setDateFrom("");
    setDateTo("");
  };

  return (
    <>
      <div
        style={{
          display: "flex",
          gap: 12,
          alignItems: "center",
          marginBottom: 12,
          flexWrap: "wrap",
        }}
      >
        <h1 style={{ margin: 0 }}>History</h1>
        {jobs && (
          <span className="muted" style={{ fontSize: 13 }}>
            {anyFilter ? `${showing} of ${total}` : `${total} runs`}
          </span>
        )}
      </div>

      <div
        className="card"
        style={{
          marginBottom: 16,
          padding: "12px 16px",
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
          gap: 12,
          alignItems: "end",
        }}
      >
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="hist-ticker">Ticker</label>
          <input
            id="hist-ticker"
            placeholder="e.g. NVDA"
            value={tickerFilter}
            onChange={(e) => setTickerFilter(e.target.value)}
          />
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="hist-status">Status</label>
          <select
            id="hist-status"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
          >
            {STATUS_VALUES.map((s) => (
              <option key={s} value={s}>
                {s === "all" ? "all statuses" : s}
              </option>
            ))}
          </select>
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="hist-from">Date from</label>
          <input
            id="hist-from"
            type="date"
            value={dateFrom}
            max={dateTo || undefined}
            onChange={(e) => setDateFrom(e.target.value)}
          />
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="hist-to">Date to</label>
          <input
            id="hist-to"
            type="date"
            value={dateTo}
            min={dateFrom || undefined}
            onChange={(e) => setDateTo(e.target.value)}
          />
        </div>
        <div>
          <button onClick={clearFilters} disabled={!anyFilter}>
            Clear
          </button>
        </div>
      </div>

      {error && <div className="error-box">{error}</div>}
      {!jobs && !error && <p className="muted">Loading…</p>}
      {jobs && jobs.length === 0 && (
        <p className="muted">
          No analyses yet. <Link href="/">Run your first one</Link>.
        </p>
      )}
      {jobs && jobs.length > 0 && filtered.length === 0 && (
        <p className="muted">No runs match the current filters.</p>
      )}

      {filtered.length > 0 && (
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
