"""FastAPI server exposing TradingAgentsGraph runs over HTTP.

See docs/azure-web-deployment.md for the full design. The CLI in ``cli/``
is independent of this package; importing from one does not import the other.
"""

from tradingagents.server.app import create_app

__all__ = ["create_app"]
