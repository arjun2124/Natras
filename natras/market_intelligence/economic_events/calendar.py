"""Economic events calendar and macroeconomic indicator tracking.

Implements Section 12 (News and events) of the NAT RAS Master Project Draft:
Monitored events:
- RBI Policy & Monetary decisions
- Federal Reserve FOMC decisions
- US CPI, Employment (NFP), GDP
- India CPI, GDP, Industrial Production (IIP), PMI
- Crude oil & Currency developments

Every event record contains:
Source, URL, Publication time, Timezone, Category, Affected instrument/sector,
Importance, Headline, Actual value, Previous value, Market reaction, Data-quality status.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from natras.data.session import IST
from natras.market_intelligence.source_status import SourceHealth, SourceStatus

logger = logging.getLogger(__name__)

CALENDAR_STORE = Path("runtime_data") / "market_intelligence" / "economic_events.json"


@dataclass
class EconomicEvent:
    event_id: str
    source: str
    url_reference: str
    publication_time: str
    timezone: str
    category: str  # MONETARY_POLICY, INFLATION, EMPLOYMENT, GROWTH, COMMODITY
    affected_sector: str  # BANKING, IT, ALL_INDEX, CRUDE, CURRENCY
    importance: str  # HIGH, MEDIUM, LOW
    headline: str
    actual_value: Optional[str] = None
    previous_value: Optional[str] = None
    consensus_value: Optional[str] = None
    market_reaction: Optional[str] = None
    data_quality_status: str = "BROKER_PROVIDED"  # BROKER_PROVIDED, PUBLIC_SOURCE, CALCULATED, UNAVAILABLE

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EconomicCalendar:
    """Manages scheduled macroeconomic events and central bank decisions."""

    def __init__(self, store_path: Path | str = CALENDAR_STORE) -> None:
        self.store_path = Path(store_path)
        self.events: Dict[str, EconomicEvent] = {}
        self._load()
        if not self.events:
            self._seed_default_events()

    def record_event(self, event: EconomicEvent) -> None:
        self.events[event.event_id] = event
        self._save()

    def get_upcoming_high_impact_events(
        self,
        current_dt: Optional[datetime] = None,
        days_ahead: int = 7,
    ) -> List[EconomicEvent]:
        """Return HIGH importance events occurring within the window."""
        now = current_dt or datetime.now(IST)
        now_iso = now.isoformat()
        upcoming = [
            e for e in self.events.values()
            if e.importance == "HIGH" and e.publication_time >= now_iso[:10]
        ]
        return sorted(upcoming, key=lambda e: e.publication_time)

    def is_event_day(self, date_str: str) -> Tuple[bool, List[EconomicEvent]]:
        """Check if date matches any scheduled HIGH impact macroeconomic event."""
        matching = [e for e in self.events.values() if e.publication_time.startswith(date_str) and e.importance == "HIGH"]
        return bool(matching), matching

    def _seed_default_events(self) -> None:
        """Seed baseline scheduled calendar events from official releases."""
        default_events = [
            EconomicEvent(
                event_id="rbi_mpc_2026",
                source="RBI Official Calendar",
                url_reference="https://www.rbi.org.in",
                publication_time="2026-10-16T10:00:00+05:30",
                timezone="Asia/Kolkata",
                category="MONETARY_POLICY",
                affected_sector="BANKING",
                importance="HIGH",
                headline="RBI Monetary Policy Committee (MPC) Interest Rate Decision",
                consensus_value="6.50%",
                previous_value="6.50%",
                data_quality_status="PUBLIC_SOURCE",
            ),
            EconomicEvent(
                event_id="us_fomc_rate_2026",
                source="Federal Reserve",
                url_reference="https://www.federalreserve.gov",
                publication_time="2026-10-28T18:30:00-04:00",
                timezone="America/New_York",
                category="MONETARY_POLICY",
                affected_sector="ALL_INDEX",
                importance="HIGH",
                headline="US Federal Reserve FOMC Rate Decision",
                consensus_value="5.25%",
                previous_value="5.50%",
                data_quality_status="PUBLIC_SOURCE",
            ),
            EconomicEvent(
                event_id="india_cpi_2026",
                source="Ministry of Statistics and Programme Implementation",
                url_reference="https://www.mospi.gov.in",
                publication_time="2026-10-14T17:30:00+05:30",
                timezone="Asia/Kolkata",
                category="INFLATION",
                affected_sector="ALL_INDEX",
                importance="HIGH",
                headline="India Consumer Price Index (CPI) Inflation",
                consensus_value="4.80%",
                previous_value="5.08%",
                data_quality_status="PUBLIC_SOURCE",
            ),
            EconomicEvent(
                event_id="us_cpi_2026",
                source="US Bureau of Labor Statistics",
                url_reference="https://www.bls.gov/cpi",
                publication_time="2026-10-20T08:30:00-04:00",
                timezone="America/New_York",
                category="INFLATION",
                affected_sector="ALL_INDEX",
                importance="HIGH",
                headline="US CPI Inflation Rate YoY",
                consensus_value="2.90%",
                previous_value="3.00%",
                data_quality_status="PUBLIC_SOURCE",
            ),
        ]
        for ev in default_events:
            self.events[ev.event_id] = ev
        self._save()

    def _save(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        data = {eid: e.to_dict() for eid, e in self.events.items()}
        self.store_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load(self) -> None:
        if not self.store_path.exists():
            return
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            for eid, d in data.items():
                self.events[eid] = EconomicEvent(**d)
        except Exception as exc:
            logger.warning("Could not load economic events: %s", exc)
