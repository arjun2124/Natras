"""Research scaffolding stub.

STATUS: NOT_IMPLEMENTED
Awaiting verified dataset ingestion pipeline and feature engineering framework.
"""

from typing import Any, Dict
from natras.config.constants import ModuleStatus


class ResearchNotImplementedError(NotImplementedError):
    """Raised on any attempt to execute unimplemented research pipeline operations."""
    pass


class ResearchScaffolding:
    """Explicit NOT_IMPLEMENTED stub for strategy research and offline backtesting."""

    status: ModuleStatus = ModuleStatus.NOT_IMPLEMENTED

    def is_implemented(self) -> bool:
        return False

    def get_status(self) -> Dict[str, Any]:
        return {
            "module": "research",
            "status": self.status.value,
            "implemented": False,
            "message": "Research scaffolding is NOT_IMPLEMENTED.",
        }

    def run_backtest(self, *args, **kwargs) -> Any:
        raise ResearchNotImplementedError(
            "Research backtesting pipeline is NOT_IMPLEMENTED."
        )

    def evaluate_hypothesis(self, *args, **kwargs) -> Any:
        raise ResearchNotImplementedError(
            "Hypothesis evaluation engine is NOT_IMPLEMENTED."
        )
