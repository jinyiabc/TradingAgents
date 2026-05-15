"use client";

import { useEffect, useState } from "react";
import { getMe, logoutUrl, MeResponse } from "@/lib/api";

export default function UserBadge() {
  const [me, setMe] = useState<MeResponse | null>(null);

  useEffect(() => {
    // Suppress 401 redirect from request() here — we want a quiet
    // "not authenticated" state, not a redirect on every page load.
    fetch(`${process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"}/me`, {
      credentials: "include",
    })
      .then(async (r) => (r.ok ? ((await r.json()) as MeResponse) : { authenticated: false }))
      .then(setMe)
      .catch(() => setMe({ authenticated: false }));
  }, []);

  if (!me) return null;
  if (!me.authenticated) {
    // No sign-in link in the nav by default — the API call will redirect
    // through /.auth/login/aad whenever an authenticated endpoint is hit.
    return null;
  }
  const label = me.name ?? me.email ?? "signed in";
  return (
    <span
      style={{
        display: "inline-flex",
        gap: 8,
        alignItems: "center",
        fontSize: 13,
        color: "var(--muted)",
      }}
    >
      {label}
      <a href={logoutUrl()}>Sign out</a>
    </span>
  );
}
