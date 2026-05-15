import type { Metadata } from "next";
import Link from "next/link";
import UserBadge from "@/components/UserBadge";
import "./globals.css";

export const metadata: Metadata = {
  title: "TradingAgents",
  description: "Multi-agent LLM trading analysis",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <nav className="nav">
          <Link href="/" className="brand">
            TradingAgents
          </Link>
          <Link href="/">New analysis</Link>
          <Link href="/history">History</Link>
          <span style={{ marginLeft: "auto" }}>
            <UserBadge />
          </span>
        </nav>
        <main className="layout">{children}</main>
      </body>
    </html>
  );
}
