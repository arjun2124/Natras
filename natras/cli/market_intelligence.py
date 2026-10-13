"""Market intelligence CLI tool.

Run:
    python -m natras.cli.market_intelligence --status
    python -m natras.cli.market_intelligence --report
    python -m natras.cli.market_intelligence --collect-news
"""

from __future__ import annotations

import argparse
import json

from natras.cli._env import load_env
from natras.market_intelligence.orchestrator import MarketIntelligenceOrchestrator


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="NAT RAS Market Intelligence System")
    parser.add_argument("--status", action="store_true", help="Print current market regime and global sentiment")
    parser.add_argument("--report", action="store_true", help="Generate and print full market intelligence report")
    parser.add_argument("--collect-news", action="store_true", help="Poll RSS feeds and collect news items")

    args = parser.parse_args()
    orchestrator = MarketIntelligenceOrchestrator()

    if args.collect_news:
        print("Polling public news and economic RSS feeds...")
        res = orchestrator.news_collector.collect_all()
        print(f"Collected {res['new_items_collected']} new items. Total items in storage: {res['total_items']}")
        for f in res["feeds"]:
            print(f"  - {f['feed']}: {f['status']} ({f['items_retrieved']} items)")
        return 0

    # Default to status or report
    report = orchestrator.generate_report()
    regime = report.regime

    print("================================================================")
    print("                NAT RAS MARKET INTELLIGENCE REPORT              ")
    print("================================================================")
    print(f"Timestamp (IST):       {report.timestamp}")
    print(f"Trend Regime:          {regime['trend_regime']}")
    print(f"Volatility Regime:     {regime['volatility_regime']}")
    print(f"Global Sentiment:      {regime['global_sentiment']}")
    print(f"Regime Confidence:     {regime['confidence_score']:.0%}")
    print(f"Event Risk:            {'EVENT DAY' if regime['is_event_day'] else 'NORMAL DAY'}")
    print(f"Summary:               {regime['summary_text']}")
    print("----------------------------------------------------------------")

    if report.options_intel:
        opt = report.options_intel
        print("OPTIONS INTELLIGENCE & INSTITUTIONAL FLOWS:")
        print(f"  - Put-Call Ratio (PCR): {opt.get('pcr', 'N/A')}")
        print(f"  - Max Pain Strike:      {opt.get('max_pain', 'N/A')}")
        print(f"  - FII Net Flow (Index): Rs {opt.get('fii_net_flow', 0.0):+,.2f} Cr")
        print(f"  - DII Net Cash Flow:    Rs {opt.get('dii_net_flow', 0.0):+,.2f} Cr")
        print("----------------------------------------------------------------")

    if report.global_markets and "assets" in report.global_markets:
        print("GLOBAL ASSETS:")
        for k, a in list(report.global_markets["assets"].items())[:8]:
            chg = a.get("change_pct", 0.0)
            sign = "+" if chg > 0 else ""
            print(f"  - {a['name']:<25} {a['price']:>10.2f} ({sign}{chg:.2f}%)")
        print("----------------------------------------------------------------")

    if report.upcoming_events:
        print(f"UPCOMING HIGH-IMPACT MACRO EVENTS ({len(report.upcoming_events)}):")
        for e in report.upcoming_events[:3]:
            print(f"  - [{e['publication_time'][:10]}] {e['headline']}")
        print("----------------------------------------------------------------")

    if args.report:
        print("\nFull JSON Report:\n")
        print(json.dumps(report.to_dict(), indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
