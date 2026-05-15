"use client";

import Link from "next/link";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { reportUrl } from "@/lib/api";

function ReportInner() {
  const searchParams = useSearchParams();
  const id = searchParams.get("id") ?? "";

  if (!id) {
    return (
      <>
        <h1>Report</h1>
        <p className="muted">
          Missing <code>id</code> query parameter.{" "}
          <Link href="/history">See history</Link>.
        </p>
      </>
    );
  }

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
          <Link href={`/jobs?id=${id}`}>
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

export default function ReportPage() {
  return (
    <Suspense fallback={<p className="muted">Loading…</p>}>
      <ReportInner />
    </Suspense>
  );
}
