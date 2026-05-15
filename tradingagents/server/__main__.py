"""CLI entry point for the web server.

Usage:
    tradingagents-server [--host 0.0.0.0] [--port 8000] [--reload]

Equivalent to:
    uvicorn tradingagents.server.app:create_app --factory --host ... --port ...
"""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="TradingAgents web server")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="enable auto-reload (dev only)")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "tradingagents.server.app:create_app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=True,
    )


if __name__ == "__main__":
    main()
