"use client";

import Link from "next/link";
import { use } from "react";
import { reportUrl } from "@/lib/api";

export default function ReportPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          marginBottom: 12,
        }}
      >
        <h1 style={{ margin: 0 }}>Report</h1>
        <span className="muted" style={{ fontFamily: "ui-monospace, monospace" }}>
          {id.slice(0, 12)}…
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <Link href={`/jobs/${id}`}>
            <button>Back to job</button>
          </Link>
          <a href={reportUrl(id)} target="_blank" rel="noopener noreferrer">
            <button>Open standalone</button>
          </a>
        </div>
      </div>
      <iframe
        src={reportUrl(id)}
        style={{
          width: "100%",
          height: "calc(100vh - 160px)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          background: "#fff",
        }}
        title="Analysis report"
      />
    </>
  );
}
