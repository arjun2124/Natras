"""Public and official news feeds collector for research context.

Implements Section 12 (Public/free sources & News) of the NAT RAS Master Project Draft:
- Collects public RSS and official press releases (e.g. RBI, PIB, financial news feeds).
- Normalizes headlines, timestamps, and affects.
- Tags news items strictly as research context (does NOT trigger trades independently).
"""

from __future__ import annotations

import json
import logging
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from natras.data.session import IST
from natras.market_intelligence.source_status import SourceHealth, SourceStatus

logger = logging.getLogger(__name__)

NEWS_STORE = Path("runtime_data") / "market_intelligence" / "news_feed.json"

PUBLIC_RSS_FEEDS = [
    {
        "name": "RBI_Press_Releases",
        "url": "https://www.rbi.org.in/pressreleases_rss.xml",
        "category": "CENTRAL_BANK",
        "sector": "BANKING",
    },
    {
        "name": "Economic_Times_Markets",
        "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "category": "MARKET_NEWS",
        "sector": "ALL_INDEX",
    },
]


@dataclass
class NewsItem:
    news_id: str
    source: str
    url: str
    published_at: str
    category: str
    sector: str
    headline: str
    summary: str
    importance: str  # HIGH, MEDIUM, LOW
    data_quality_status: str = "PUBLIC_SOURCE"


class NewsFeedCollector:
    """Collects and stores financial news items from public RSS feeds."""

    def __init__(self, store_path: Path | str = NEWS_STORE, timeout: float = 10.0) -> None:
        self.store_path = Path(store_path)
        self.timeout = timeout
        self.items: Dict[str, NewsItem] = {}
        self._load()

    def fetch_rss_feed(self, feed_name: str, feed_url: str, category: str, sector: str) -> List[NewsItem]:
        """Fetch and parse an RSS feed."""
        headers = {"User-Agent": "Mozilla/5.0"}
        new_items: List[NewsItem] = []
        try:
            resp = requests.get(feed_url, headers=headers, timeout=self.timeout)
            if resp.status_code != 200:
                logger.warning("Failed to fetch RSS %s: HTTP %s", feed_name, resp.status_code)
                return []

            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:15]:
                title = item.findtext("title") or "No title"
                link = item.findtext("link") or ""
                pub_date = item.findtext("pubDate") or datetime.now(IST).isoformat()
                desc = item.findtext("description") or ""

                news_id = f"news_{uuid.uuid4().hex[:8]}"
                importance = "HIGH" if any(w in title.lower() for w in ["rate", "inflation", "rbi", "fed", "cpi", "war"]) else "MEDIUM"

                news_item = NewsItem(
                    news_id=news_id,
                    source=feed_name,
                    url=link,
                    published_at=pub_date,
                    category=category,
                    sector=sector,
                    headline=title.strip(),
                    summary=desc.strip()[:300],
                    importance=importance,
                )
                new_items.append(news_item)
                self.items[news_id] = news_item

            self._save()
        except Exception as exc:
            logger.warning("RSS parsing error for %s: %s", feed_name, exc)

        return new_items

    def collect_all(self) -> Dict[str, Any]:
        """Poll all configured RSS feeds."""
        now_str = datetime.now(IST).isoformat()
        collected_count = 0
        feed_healths: List[Dict[str, Any]] = []

        for f in PUBLIC_RSS_FEEDS:
            items = self.fetch_rss_feed(f["name"], f["url"], f["category"], f["sector"])
            collected_count += len(items)
            status = SourceStatus.ACTIVE if items else SourceStatus.DEGRADED
            feed_healths.append({
                "feed": f["name"],
                "status": status.value,
                "items_retrieved": len(items),
            })

        return {
            "timestamp": now_str,
            "total_items": len(self.items),
            "new_items_collected": collected_count,
            "feeds": feed_healths,
        }

    def _save(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        data = {nid: asdict(i) for nid, i in self.items.items()}
        self.store_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load(self) -> None:
        if not self.store_path.exists():
            return
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            for nid, d in data.items():
                self.items[nid] = NewsItem(**d)
        except Exception as exc:
            logger.warning("Could not load news feed: %s", exc)
