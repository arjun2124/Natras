"""Telegram notification client stub.

STATUS: NOT_IMPLEMENTED
Awaiting notification dispatch and alert policy design before implementation.
"""

from typing import Any, Dict
from natras.config.constants import ModuleStatus


class TelegramNotImplementedError(NotImplementedError):
    """Raised on any attempt to execute unimplemented Telegram client methods."""
    pass


class TelegramNotifier:
    """Explicit NOT_IMPLEMENTED stub for Telegram alerts client."""

    status: ModuleStatus = ModuleStatus.NOT_IMPLEMENTED

    def __init__(self, bot_token: str = "", chat_id: str = "") -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def is_implemented(self) -> bool:
        """Honest implementation indicator."""
        return False

    def get_status(self) -> Dict[str, Any]:
        """Return module status metadata."""
        return {
            "adapter": "Telegram",
            "status": self.status.value,
            "implemented": False,
            "message": "Telegram notifier is NOT_IMPLEMENTED.",
        }

    def send_message(self, message: str) -> bool:
        """Dispatch a message to Telegram channel/chat."""
        raise TelegramNotImplementedError(
            "Telegram client is NOT_IMPLEMENTED. Alerts cannot be dispatched yet."
        )
