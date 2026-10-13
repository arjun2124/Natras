"""Upstox historical and audit provider integration stub.

STATUS: NOT_IMPLEMENTED
Awaiting data reconciliation and audit design before implementation.
"""

from typing import Any, Dict, List
from natras.config.constants import ModuleStatus


class UpstoxNotImplementedError(NotImplementedError):
    """Raised on any attempt to execute unimplemented Upstox methods."""
    pass


class UpstoxAdapter:
    """Explicit NOT_IMPLEMENTED stub for Upstox historical/audit provider.

    Do not simulate or fabricate external market data.
    """

    status: ModuleStatus = ModuleStatus.NOT_IMPLEMENTED

    def __init__(self, api_key: str = "", access_token: str = "") -> None:
        self.api_key = api_key
        self.access_token = access_token

    def is_implemented(self) -> bool:
        """Honest implementation indicator."""
        return False

    def get_status(self) -> Dict[str, Any]:
        """Return module status metadata."""
        return {
            "adapter": "Upstox",
            "status": self.status.value,
            "implemented": False,
            "message": "Upstox adapter is NOT_IMPLEMENTED. Awaiting audit provider implementation.",
        }

    def authenticate(self) -> None:
        """Authenticate with Upstox API."""
        raise UpstoxNotImplementedError(
            "Upstox adapter authentication is NOT_IMPLEMENTED."
        )

    def fetch_historical_candles(
        self,
        instrument_key: str,
        interval: str,
        to_date: str,
        from_date: str,
    ) -> List[Any]:
        """Fetch historical candles for audit."""
        raise UpstoxNotImplementedError(
            "Upstox historical candle fetch is NOT_IMPLEMENTED. Refusing to fabricate candles."
        )
