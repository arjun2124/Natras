"""Shadow trading candidate execution runner stub.

STATUS: NOT_IMPLEMENTED
Awaiting shadow execution lifecycle and candidate evaluation framework.
"""

from typing import Any, Dict
from natras.config.constants import ModuleStatus


class ShadowNotImplementedError(NotImplementedError):
    """Raised on any attempt to execute unimplemented shadow runner operations."""
    pass


class ShadowRunner:
    """Explicit NOT_IMPLEMENTED stub for shadow strategy candidate execution."""

    status: ModuleStatus = ModuleStatus.NOT_IMPLEMENTED

    def is_implemented(self) -> bool:
        return False

    def get_status(self) -> Dict[str, Any]:
        return {
            "module": "shadow",
            "status": self.status.value,
            "implemented": False,
            "message": "Shadow candidate runner is NOT_IMPLEMENTED.",
        }

    def register_candidate(self, *args, **kwargs) -> Any:
        raise ShadowNotImplementedError(
            "Shadow candidate registration is NOT_IMPLEMENTED."
        )

    def execute_tick(self, *args, **kwargs) -> Any:
        raise ShadowNotImplementedError(
            "Shadow candidate execution is NOT_IMPLEMENTED."
        )
