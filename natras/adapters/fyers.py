"""FYERS live data and broker integration stub.

STATUS: NOT_IMPLEMENTED
Awaiting authentication flow design and explicit user approval before implementation.
"""

from typing import Any, Dict, List
from natras.config.constants import ModuleStatus


class FyersNotImplementedError(NotImplementedError):
    """Raised on any attempt to execute unimplemented FYERS methods."""
    pass


class FyersAdapter:
    """Explicit NOT_IMPLEMENTED stub for FYERS live data and broker adapter.

    Do not simulate or fabricate broker connectivity.
    """

    status: ModuleStatus = ModuleStatus.NOT_IMPLEMENTED

    def __init__(self, app_id: str = "", access_token: str = "") -> None:
        self.app_id = app_id
        self.access_token = access_token

    def is_implemented(self) -> bool:
        """Honest implementation indicator."""
        return False

    def get_status(self) -> Dict[str, Any]:
        """Return module status metadata."""
        return {
            "adapter": "FYERS",
            "status": self.status.value,
            "implemented": False,
            "message": "FYERS adapter is NOT_IMPLEMENTED. Awaiting auth flow implementation.",
        }

    def authenticate(self) -> None:
        """Authenticate with FYERS API v3."""
        raise FyersNotImplementedError(
            "FYERS adapter authentication is NOT_IMPLEMENTED. Awaiting auth approval."
        )

    def fetch_quotes(self, symbols: List[str]) -> Dict[str, Any]:
        """Fetch real-time quotes."""
        raise FyersNotImplementedError(
            "FYERS quote fetching is NOT_IMPLEMENTED. Refusing to fabricate market quotes."
        )

    def fetch_candles(self, symbol: str, resolution: str, range_from: str, range_to: str) -> List[Any]:
        """Fetch historical candle data."""
        raise FyersNotImplementedError(
            "FYERS historical candle fetch is NOT_IMPLEMENTED. Refusing to fabricate candles."
        )

    def subscribe_quotes(self, symbols: List[str]) -> None:
        """Subscribe to WebSocket quote feed."""
        raise FyersNotImplementedError(
            "FYERS WebSocket streaming is NOT_IMPLEMENTED."
        )
