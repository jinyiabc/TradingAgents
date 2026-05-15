"""Write a consolidated analysis report (Markdown + HTML) to disk.

Shared by the CLI ([cli/main.py](../cli/main.py)) and the web server
([tradingagents/server/jobs.py](tradingagents/server/jobs.py)). The output
layout is:

    <save_path>/
        1_analysts/        # one file per analyst
        2_research/        # bull / bear / research-manager
        3_trading/         # trader plan
        4_risk/            # aggressive / conservative / neutral
        5_portfolio/       # final portfolio-manager decision
        complete_report.md
        complete_report.html
"""

from __future__ import annotations

import datetime
from html import escape
from pathlib import Path
from typing import Any

_HTML_STYLE = """
:root { --fg:#1f2328; --muted:#57606a; --bg:#fff; --bg-soft:#f6f8fa;
        --border:#d0d7de; --accent:#0969da; }
* { box-sizing: border-box; }
body { margin: 0 auto; max-width: 960px; padding: 32px 24px;
       font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
                    "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC",
                    Arial, sans-serif;
       font-size: 16px; line-height: 1.7; color: var(--fg); background: var(--bg); }
h1, h2, h3, h4 { line-height: 1.25; margin-top: 1.6em; margin-bottom: 0.6em; }
h1 { font-size: 2em; border-bottom: 1px solid var(--border); padding-bottom: 0.3em; }
h2 { font-size: 1.5em; border-bottom: 1px solid var(--border); padding-bottom: 0.3em; }
h3 { font-size: 1.25em; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code { background: var(--bg-soft); padding: 0.15em 0.4em; border-radius: 4px;
       font-size: 0.9em; }
pre { background: var(--bg-soft); padding: 12px 16px; border-radius: 6px;
      overflow-x: auto; }
pre code { background: transparent; padding: 0; }
blockquote { color: var(--muted); border-left: 4px solid var(--border);
             padding: 0 1em; margin: 0 0 1em 0; }
table { border-collapse: collapse; margin: 1em 0; display: block; overflow-x: auto; }
th, td { border: 1px solid var(--border); padding: 6px 12px; }
th { background: var(--bg-soft); }
hr { border: 0; border-top: 1px solid var(--border); margin: 2em 0; }
.report-meta { color: var(--muted); font-size: 0.95em; margin-top: -0.5em; }
"""


def render_report_html(md_text: str, title: str) -> str:
    """Render the consolidated markdown report to a standalone HTML page."""
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": False, "linkify": True, "breaks": False}).enable("table")
    body = md.render(md_text)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)}</title>\n"
        f"<style>{_HTML_STYLE}</style>\n"
        "</head>\n<body>\n"
        f"{body}\n"
        "</body>\n</html>\n"
    )


def save_report_to_disk(final_state: dict[str, Any], ticker: str, save_path: Path) -> Path:
    """Save complete analysis report to disk with organized subfolders.

    Returns the path to ``complete_report.md``.
    """
    save_path.mkdir(parents=True, exist_ok=True)
    analysts_dir = save_path / "1_analysts"

    sections: list[str] = []

    # 1. Analyst Team Reports
    analyst_parts: list[tuple[str, str]] = []
    if final_state.get("market_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "market.md").write_text(final_state["market_report"], encoding="utf-8")
        analyst_parts.append(("Market Analyst", final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "sentiment.md").write_text(final_state["sentiment_report"], encoding="utf-8")
        analyst_parts.append(("Social Analyst", final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "news.md").write_text(final_state["news_report"], encoding="utf-8")
        analyst_parts.append(("News Analyst", final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "fundamentals.md").write_text(final_state["fundamentals_report"], encoding="utf-8")
        analyst_parts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))
    if analyst_parts:
        content = "\n\n".join(f"### {name}\n{text}" for name, text in analyst_parts)
        sections.append(f"## I. Analyst Team Reports\n\n{content}")

    # 2. Research
    if final_state.get("investment_debate_state"):
        research_dir = save_path / "2_research"
        debate = final_state["investment_debate_state"]
        research_parts: list[tuple[str, str]] = []
        if debate.get("bull_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bull.md").write_text(debate["bull_history"], encoding="utf-8")
            research_parts.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bear.md").write_text(debate["bear_history"], encoding="utf-8")
            research_parts.append(("Bear Researcher", debate["bear_history"]))
        if debate.get("judge_decision"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "manager.md").write_text(debate["judge_decision"], encoding="utf-8")
            research_parts.append(("Research Manager", debate["judge_decision"]))
        if research_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in research_parts)
            sections.append(f"## II. Research Team Decision\n\n{content}")

    # 3. Trading
    if final_state.get("trader_investment_plan"):
        trading_dir = save_path / "3_trading"
        trading_dir.mkdir(exist_ok=True)
        (trading_dir / "trader.md").write_text(final_state["trader_investment_plan"], encoding="utf-8")
        sections.append(f"## III. Trading Team Plan\n\n### Trader\n{final_state['trader_investment_plan']}")

    # 4. Risk Management
    if final_state.get("risk_debate_state"):
        risk_dir = save_path / "4_risk"
        risk = final_state["risk_debate_state"]
        risk_parts: list[tuple[str, str]] = []
        if risk.get("aggressive_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "aggressive.md").write_text(risk["aggressive_history"], encoding="utf-8")
            risk_parts.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "conservative.md").write_text(risk["conservative_history"], encoding="utf-8")
            risk_parts.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "neutral.md").write_text(risk["neutral_history"], encoding="utf-8")
            risk_parts.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in risk_parts)
            sections.append(f"## IV. Risk Management Team Decision\n\n{content}")

        if risk.get("judge_decision"):
            portfolio_dir = save_path / "5_portfolio"
            portfolio_dir.mkdir(exist_ok=True)
            (portfolio_dir / "decision.md").write_text(risk["judge_decision"], encoding="utf-8")
            sections.append(f"## V. Portfolio Manager Decision\n\n### Portfolio Manager\n{risk['judge_decision']}")

    title = f"Trading Analysis Report: {ticker}"
    header = f"# {title}\n\nGenerated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    md_text = header + "\n\n".join(sections)
    (save_path / "complete_report.md").write_text(md_text, encoding="utf-8")
    (save_path / "complete_report.html").write_text(render_report_html(md_text, title), encoding="utf-8")
    return save_path / "complete_report.md"
