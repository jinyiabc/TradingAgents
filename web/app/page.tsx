"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AnalystKind,
  createAnalysis,
  CreateAnalysisRequest,
  getOptions,
  OptionsResponse,
} from "@/lib/api";

const ANALYST_LABELS: Record<AnalystKind, string> = {
  market: "Market",
  social: "Social",
  news: "News",
  fundamentals: "Fundamentals",
};

const todayIso = (): string => new Date().toISOString().slice(0, 10);

export default function NewAnalysisPage() {
  const router = useRouter();
  const [options, setOptions] = useState<OptionsResponse | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [ticker, setTicker] = useState("NVDA");
  const [analysisDate, setAnalysisDate] = useState(todayIso());
  const [analysts, setAnalysts] = useState<AnalystKind[]>([
    "market",
    "social",
    "news",
    "fundamentals",
  ]);
  const [provider, setProvider] = useState("openai");
  const [quickModel, setQuickModel] = useState("");
  const [deepModel, setDeepModel] = useState("");
  const [maxDebate, setMaxDebate] = useState(1);
  const [maxRisk, setMaxRisk] = useState(1);
  const [language, setLanguage] = useState("English");

  useEffect(() => {
    getOptions()
      .then((opts) => {
        setOptions(opts);
        if (!opts.providers.includes(provider)) {
          setProvider(opts.providers[0] ?? "openai");
        }
      })
      .catch((e: Error) => setOptionsError(e.message));
    // Intentionally run once on mount; provider state is initialised before fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reset model selections when provider changes (the previous models may not exist).
  useEffect(() => {
    setQuickModel("");
    setDeepModel("");
  }, [provider]);

  const quickModels = options?.models[provider]?.quick ?? [];
  const deepModels = options?.models[provider]?.deep ?? [];

  const toggleAnalyst = (a: AnalystKind) => {
    setAnalysts((prev) =>
      prev.includes(a) ? prev.filter((x) => x !== a) : [...prev, a],
    );
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError(null);
    setSubmitting(true);
    try {
      const body: CreateAnalysisRequest = {
        ticker: ticker.trim().toUpperCase(),
        analysis_date: analysisDate,
        analysts,
        llm_provider: provider,
        deep_thinking_model: deepModel || undefined,
        quick_thinking_model: quickModel || undefined,
        max_debate_rounds: maxDebate,
        max_risk_discuss_rounds: maxRisk,
        output_language: language,
      };
      const { job_id } = await createAnalysis(body);
      router.push(`/jobs?id=${job_id}`);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  };

  return (
    <>
      <h1>New analysis</h1>
      {optionsError && (
        <div className="error-box">
          Couldn&apos;t load options from API: {optionsError}
        </div>
      )}
      <form onSubmit={onSubmit} className="card" style={{ marginTop: 16 }}>
        <div className="row">
          <div className="field">
            <label htmlFor="ticker">Ticker</label>
            <input
              id="ticker"
              required
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="date">Analysis date</label>
            <input
              id="date"
              type="date"
              required
              max={todayIso()}
              value={analysisDate}
              onChange={(e) => setAnalysisDate(e.target.value)}
            />
          </div>
        </div>

        <div className="field">
          <label>Analysts</label>
          <div className="analyst-grid">
            {(Object.keys(ANALYST_LABELS) as AnalystKind[]).map((a) => (
              <label key={a}>
                <input
                  type="checkbox"
                  checked={analysts.includes(a)}
                  onChange={() => toggleAnalyst(a)}
                />
                {ANALYST_LABELS[a]}
              </label>
            ))}
          </div>
        </div>

        <div className="row">
          <div className="field">
            <label htmlFor="provider">LLM provider</label>
            <select
              id="provider"
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
            >
              {(options?.providers ?? [provider]).map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="lang">Output language</label>
            <input
              id="lang"
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
            />
          </div>
        </div>

        <div className="row">
          <div className="field">
            <label htmlFor="quick">Quick-thinking model</label>
            <select
              id="quick"
              value={quickModel}
              onChange={(e) => setQuickModel(e.target.value)}
            >
              <option value="">(provider default)</option>
              {quickModels.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="deep">Deep-thinking model</label>
            <select
              id="deep"
              value={deepModel}
              onChange={(e) => setDeepModel(e.target.value)}
            >
              <option value="">(provider default)</option>
              {deepModels.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="row">
          <div className="field">
            <label htmlFor="debate">Debate rounds</label>
            <input
              id="debate"
              type="number"
              min={1}
              max={5}
              value={maxDebate}
              onChange={(e) => setMaxDebate(Number(e.target.value))}
            />
          </div>
          <div className="field">
            <label htmlFor="risk">Risk discussion rounds</label>
            <input
              id="risk"
              type="number"
              min={1}
              max={5}
              value={maxRisk}
              onChange={(e) => setMaxRisk(Number(e.target.value))}
            />
          </div>
        </div>

        {submitError && <div className="error-box">{submitError}</div>}

        <button
          type="submit"
          className="primary"
          disabled={submitting || analysts.length === 0}
          style={{ marginTop: 8 }}
        >
          {submitting ? "Submitting..." : "Start analysis"}
        </button>
      </form>
    </>
  );
}
