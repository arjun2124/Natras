"""Data source status and availability health tracking.

Implements Section 12 requirement:
Every data source must report one of:
- ACTIVE
- DEGRADED
- STALE
- BLOCKED
- UNAVAILABLE
- NOT_IMPLEMENTED

A source is marked ACTIVE only after validation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Optional


class SourceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


@dataclass
class SourceHealth:
    source_name: str
    status: SourceStatus
    category: str
    last_checked: str
    message: str = ""
    error_detail: Optional[str] = None
    response_time_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d
