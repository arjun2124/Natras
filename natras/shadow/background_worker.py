"""Autonomous Background Shadow Research Worker.

Operates silently in the background:
- Senses Wi-Fi and internet connectivity.
- Senses live market hours (09:15 - 15:30 IST) vs idle / post-market hours.
- Live-Data First Strategy Formulation:
    1. Formulates/calibrates strategy variants directly from live market dynamics
       (intraday ATR, volatility regimes, options PCR, Max Pain, sector breadth).
    2. Deploys candidate into live shadow mode to test forward on real-time candles directly first.
    3. Runs historical walk-forward backtest (2023-2026, 70k+ candles) in the background
       using the exact same parameters.
    4. Cross-validates live forward results against historical backtest results.
- Runs silently without blocking live trading loops, GUI, or consuming high CPU during market hours.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from natras.config.constants import MARKET_TIMEZONE_STR
from natras.data.session import IST
from natras.research.walk_forward import WalkForwardBatchValidator
from natras.shadow.shadow_engine import ShadowCandidate, ShadowEngine

logger = logging.getLogger("natras.shadow.research")
LOG_PATH = Path("data") / "logs" / "shadow_research.log"


def is_wifi_or_internet_connected(host: str = "8.8.8.8", port: int = 53, timeout: float = 2.0) -> bool:
    """Fast, silent check to determine if laptop is connected to Wi-Fi/Internet."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        return True
    except (socket.timeout, OSError):
        return False


def is_market_hours(now: Optional[datetime] = None) -> bool:
    """Return True if currently within active market hours (09:15 - 15:30 IST on weekdays)."""
    tz = ZoneInfo(MARKET_TIMEZONE_STR)
    dt = now or datetime.now(tz)
    if dt.weekday() >= 5:  # Saturday or Sunday
        return False
    t = dt.astimezone(tz).time()
    return dt_time(9, 15) <= t <= dt_time(15, 30)


class ShadowResearchWorker:
    """Autonomous background worker executing silent strategy calibration and backtesting."""

    def __init__(
        self,
        shadow_engine: Optional[ShadowEngine] = None,
        db_path: Path | str = Path("data") / "market.db",
        poll_interval_seconds: float = 300.0,
        telegram_notifier: Optional[Any] = None,
    ) -> None:
        self.shadow_engine = shadow_engine or ShadowEngine(auto_seed=True)
        self.db_path = Path(db_path)
        self.poll_interval = poll_interval_seconds
        self.telegram_notifier = telegram_notifier
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._audit_lock = threading.Lock()
        self._setup_logging()

    def _setup_logging(self) -> None:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.setLevel(logging.INFO)
        if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
            logger.addHandler(handler)

    def start(self) -> None:
        """Start background worker thread."""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, name="ShadowResearchWorker", daemon=True)
        self._thread.start()
        logger.info("Autonomous Shadow Research Worker started (poll_interval=%ss).", self.poll_interval)

    def stop(self) -> None:
        """Stop background worker thread."""
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("Autonomous Shadow Research Worker stopped.")

    def _run_loop(self) -> None:
        """Main background loop."""
        while self.running:
            try:
                self.run_cycle()
            except Exception as exc:
                logger.error("Error during shadow research background cycle: %s", exc)

            # Sleep in 5-second increments to allow responsive shutdown
            slept = 0.0
            while slept < self.poll_interval and self.running:
                time.sleep(5)
                slept += 5.0

    def run_cycle(self) -> Dict[str, Any]:
        """Execute one autonomous cycle: connectivity check, live calibration, and backtests."""
        results: Dict[str, Any] = {
            "connected": False,
            "market_hours": False,
            "calibrated_candidates": 0,
            "audited_candidates": 0,
        }

        # 1. Connectivity check: only proceed if connected to Wi-Fi / Internet
        if not is_wifi_or_internet_connected():
            logger.debug("Laptop offline / no Wi-Fi connectivity. Skipping cycle.")
            return results
        results["connected"] = True

        in_mkt = is_market_hours()
        results["market_hours"] = in_mkt

        # 2. Live-first strategy formulation & calibration from freshest market intelligence
        latest_intel = self.shadow_engine.get_latest_market_intelligence()
        if latest_intel:
            new_cand = self.calibrate_candidate_from_live_intelligence(latest_intel)
            if new_cand:
                results["calibrated_candidates"] += 1

        # 3. Process pending historical walk-forward audits silently in background
        # During market hours, run audits gently to preserve CPU for live market feeds
        audited_count = self.process_pending_audits(throttled=in_mkt)
        results["audited_candidates"] = audited_count

        return results

    def calibrate_candidate_from_live_intelligence(
        self,
        intel: Dict[str, Any],
    ) -> Optional[ShadowCandidate]:
        """Formulate or tune candidate variants directly from live market dynamics.
        
        The candidate is immediately registered for live forward testing first.
        Its exact parameters are marked for subsequent background historical backtesting.
        """
        regime = intel.get("regime") or {}
        vol_regime = regime.get("volatility_regime", "NORMAL_VOLATILITY")
        trend_regime = regime.get("trend_regime", "NEUTRAL")
        vix = intel.get("vix") or (intel.get("global_markets") or {}).get("vix", 14.0)

        opt = intel.get("options_intel") or intel.get("options") or {}
        pcr = opt.get("pcr")
        max_pain = opt.get("max_pain")

        breadth = intel.get("breadth") or intel.get("market_breadth") or {}
        ad_ratio = breadth.get("advance_decline_ratio", 1.0)

        # Example Adaptive Variant: High Volatility regime with expanding ATR
        if vol_regime == "HIGH_VOLATILITY" or (isinstance(vix, (int, float)) and vix >= 17.0):
            cand_name = "LiveAdaptive_HighVol_Momentum_Wick"
            existing = [c for c in self.shadow_engine.candidates.values() if c.name == cand_name]
            if not existing:
                cand = self.shadow_engine.register_variant(
                    name=cand_name,
                    param_overrides={
                        "min_body_ratio": 0.50,
                        "min_range_atr_ratio": 1.25,
                        "high_vol_trail_trigger_pp": 5.0,
                        "high_vol_trail_gap_pp": 2.0,
                        "vix_high_threshold": 16.0,
                    },
                    strategy_class_name="RegimeAdaptiveMomentumWickStrategy",
                )
                cand.origin = "LIVE_MARKET_DATA"
                cand.backtest_status = "PENDING_AUDIT"
                self.shadow_engine._save()
                logger.info(
                    "Formulated new candidate '%s' from live market dynamics (VIX=%.1f, Vol=%s). Forward testing started.",
                    cand_name, vix, vol_regime
                )
                return cand

        # Example Adaptive Variant: Extreme PCR Pinning Reversion
        if isinstance(pcr, (int, float)) and (pcr <= 0.70 or pcr >= 1.35) and max_pain:
            cand_name = "LiveAdaptive_ExtremePCR_Gravity_Reversion"
            existing = [c for c in self.shadow_engine.candidates.values() if c.name == cand_name]
            if not existing:
                cand = self.shadow_engine.register_variant(
                    name=cand_name,
                    param_overrides={
                        "rsi_oversold": 30.0,
                        "rsi_overbought": 70.0,
                        "pcr_oversold_threshold": 0.75,
                        "pcr_overbought_threshold": 1.30,
                        "min_max_pain_distance_pts": 50.0,
                        "initial_stop_pp": 10.0,
                    },
                    strategy_class_name="OptionsMaxPainPCRReversionStrategy",
                )
                cand.origin = "LIVE_MARKET_DATA"
                cand.backtest_status = "PENDING_AUDIT"
                self.shadow_engine._save()
                logger.info(
                    "Formulated new candidate '%s' from live options extremes (PCR=%.2f, MaxPain=%.1f). Forward testing started.",
                    cand_name, pcr, max_pain
                )
                return cand

        return None

    def process_pending_audits(self, throttled: bool = False) -> int:
        """Silently audit pending candidates against the 2023-2026 historical dataset."""
        if not self.db_path.exists():
            return 0

        pending_ids = [
            cid for cid, c in self.shadow_engine.candidates.items()
            if getattr(c, "backtest_status", "PENDING_AUDIT") == "PENDING_AUDIT"
        ]
        if not pending_ids:
            return 0

        audited_count = 0
        with self._audit_lock:
            validator = WalkForwardBatchValidator(db_path=self.db_path)
            for cid in pending_ids:
                if self._thread and not self.running:
                    break
                candidate = self.shadow_engine.candidates.get(cid)
                if not candidate:
                    continue

                logger.info(
                    "Background audit: Running 2023-2026 walk-forward backtest for '%s' (%s) using same live parameters.",
                    candidate.name, cid
                )

                if throttled:
                    # Yield slightly so live tick processing is never starved
                    time.sleep(0.5)

                try:
                    report = self.shadow_engine.run_walkforward_audit(cid, db_path=self.db_path)
                    candidate.backtest_metrics = {
                        "total_trades": report.total_trades,
                        "win_rate": report.total_win_rate,
                        "total_net_pnl": report.total_net_pnl,
                        "profit_factor": report.overall_profit_factor,
                        "consistency_pct": report.consistency_percentage,
                        "profitable_batches": report.profitable_batches,
                        "total_batches": report.batches_evaluated,
                        "certification_status": report.certification_status,
                    }
                    candidate.backtest_status = "AUDITED"
                    self.shadow_engine._evaluate_lifecycle(candidate)
                    self.shadow_engine._save()
                    audited_count += 1

                    logger.info(
                        "Background audit completed for '%s': 2023-2026 Net PnL=Rs %.2f, PF=%.2f, Status=%s",
                        candidate.name,
                        report.total_net_pnl,
                        report.overall_profit_factor,
                        report.certification_status,
                    )

                    # Notify Telegram if candidate is eligible for promotion
                    if candidate.status == "PROMOTION_CANDIDATE" and self.telegram_notifier:
                        msg = (
                            f"🌟 *SHADOW STRATEGY PROMOTION READY*\n"
                            f"• Candidate: `{candidate.candidate_id}` ({candidate.name})\n"
                            f"• Live Forward PnL: Rs {candidate.net_pnl:,.2f} (PF: {candidate.profit_factor:.2f})\n"
                            f"• Historical Backtest PnL: Rs {report.total_net_pnl:,.2f} (PF: {report.overall_profit_factor:.2f})\n"
                            f"• Use `/approve {candidate.candidate_id}` to schedule for live paper trading."
                        )
                        try:
                            self.telegram_notifier(msg)
                        except Exception:
                            pass

                except Exception as exc:
                    logger.warning("Background audit failed for %s: %s", cid, exc)
                    candidate.backtest_status = "AUDIT_FAILED"
                    self.shadow_engine._save()

        return audited_count
