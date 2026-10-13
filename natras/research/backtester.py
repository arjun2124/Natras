"""Historical backtesting and replay engine for strategy research.

Implements Section 13 and Phase 5 of the NAT RAS Master Project Draft:
- Event-driven replay of completed 5m candles from SQLite or dataset manifests
- Evaluates baseline or parameterized strategy variants
- Applies realistic slippage (₹0.50) and brokerage (₹20 per order)
- Tracks SteppedTrailingStop, MFE, MAE, Drawdown, Profit Factor, Expectancy
- Persists reproducible experiment reports to runtime_data/reports/research/
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from natras.data.candle_engine import Candle, validate_candle
from natras.data.session import IST
from natras.risk.strategy_profiles import StrategyRiskProfile, get_default_risk_profile
from natras.strategies.base import Signal
from natras.strategies.momentum_wick import MomentumWickStrategy, SteppedTrailingStop

REPORTS_DIR = Path("runtime_data") / "reports" / "research"


@dataclass
class BacktestTrade:
    trade_id: str
    symbol: str
    direction: str  # CE or PE
    entry_ts: str
    entry_quoted: float
    entry_fill: float
    exit_ts: str
    exit_quoted: float
    exit_fill: float
    quantity: int
    gross_pnl: float
    brokerage: float
    net_pnl: float
    pnl_points: float
    mfe_points: float
    mae_points: float
    exit_reason: str


@dataclass
class BacktestResult:
    experiment_id: str
    parent_strategy: str
    strategy_params: Dict[str, Any]
    date_range: Tuple[str, str]
    total_candles: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    gross_pnl: float
    total_brokerage: float
    net_pnl: float
    profit_factor: float
    max_drawdown: float
    max_consecutive_losses: int
    expectancy_per_trade: float
    trades: List[BacktestTrade] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["date_range"] = list(self.date_range)
        return d


class Backtester:
    """Event-driven strategy backtester on 5m historical candles."""

    def __init__(
        self,
        db_path: Path | str = "data/market.db",
        risk_profile: Optional[StrategyRiskProfile] = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.risk_profile = risk_profile or get_default_risk_profile()

    def run(
        self,
        strategy: Optional[MomentumWickStrategy] = None,
        symbol: str = "NSE:NIFTY50-INDEX",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        candles_list: Optional[List[Candle]] = None,
    ) -> BacktestResult:
        """Run backtest over candle list or database."""
        strat = strategy or MomentumWickStrategy()
        candles = candles_list or self._load_candles(symbol, start_date, end_date)

        experiment_id = f"exp_{uuid.uuid4().hex[:10]}"
        trades: List[BacktestTrade] = []

        active_trade: Optional[Dict[str, Any]] = None
        trailing_stop: Optional[SteppedTrailingStop] = None

        equity_curve: List[float] = [self.risk_profile.initial_paper_equity]
        peak_equity = self.risk_profile.initial_paper_equity
        max_drawdown = 0.0

        for candle in candles:
            c_time = candle.timestamp.astimezone(strat.tz).time()

            # 1. If in active trade, update trailing stop using candle spot or simulated option move
            if active_trade is not None and trailing_stop is not None:
                # Option delta is approx ~0.50 of spot move
                spot_entry = active_trade["spot_entry"]
                direction = active_trade["direction"]
                delta = 0.50

                if direction == "CE":
                    best_option_move = (candle.high - spot_entry) * delta
                    worst_option_move = (candle.low - spot_entry) * delta
                    close_option_move = (candle.close - spot_entry) * delta
                else:
                    best_option_move = (spot_entry - candle.low) * delta
                    worst_option_move = (spot_entry - candle.high) * delta
                    close_option_move = (spot_entry - candle.close) * delta

                # Track MFE and MAE
                if best_option_move > active_trade["mfe_points"]:
                    active_trade["mfe_points"] = best_option_move
                if worst_option_move < active_trade["mae_points"]:
                    active_trade["mae_points"] = worst_option_move

                # Update trailing stop with best favorable move in this candle
                trailing_stop.update(best_option_move)

                # Check stop-out breach during candle
                is_stopped = trailing_stop.is_stopped_out(worst_option_move)
                is_eod = strat.is_eod_square_off_time(candle.timestamp)

                if is_stopped or is_eod:
                    exit_reason = "EOD_SQUARE_OFF" if is_eod and not is_stopped else "TRAILING_STOP_BREACH"
                    exit_points = trailing_stop.current_stop_pp if is_stopped else close_option_move
                    quoted_exit = active_trade["quoted_entry"] + exit_points
                    fill_exit = quoted_exit - self.risk_profile.exit_slippage

                    pnl_pts = fill_exit - active_trade["fill_entry"]
                    gross = pnl_pts * active_trade["quantity"]
                    brokerage = self.risk_profile.brokerage_per_order * 2.0
                    net = round(gross - brokerage, 2)

                    trade = BacktestTrade(
                        trade_id=active_trade["trade_id"],
                        symbol=symbol,
                        direction=direction,
                        entry_ts=active_trade["entry_ts"],
                        entry_quoted=active_trade["quoted_entry"],
                        entry_fill=active_trade["fill_entry"],
                        exit_ts=candle.timestamp.isoformat(),
                        exit_quoted=round(quoted_exit, 2),
                        exit_fill=round(fill_exit, 2),
                        quantity=active_trade["quantity"],
                        gross_pnl=round(gross, 2),
                        brokerage=brokerage,
                        net_pnl=net,
                        pnl_points=round(pnl_pts, 2),
                        mfe_points=round(active_trade["mfe_points"], 2),
                        mae_points=round(active_trade["mae_points"], 2),
                        exit_reason=exit_reason,
                    )
                    trades.append(trade)

                    current_equity = equity_curve[-1] + net
                    equity_curve.append(current_equity)
                    if current_equity > peak_equity:
                        peak_equity = current_equity
                    dd = peak_equity - current_equity
                    if dd > max_drawdown:
                        max_drawdown = dd

                    active_trade = None
                    trailing_stop = None
                    continue

            # 2. Check for new signals if no position is open
            if active_trade is None:
                signals = strat.on_candle(candle)
                if signals:
                    sig = signals[0]
                    direction = sig.metadata.get("instrument_type", "CE")
                    quoted_entry = 100.0  # Normalized ATM premium baseline
                    fill_entry = quoted_entry + self.risk_profile.entry_slippage

                    trailing_stop = strat.create_trailing_stop()
                    active_trade = {
                        "trade_id": f"t_{uuid.uuid4().hex[:8]}",
                        "direction": direction,
                        "spot_entry": candle.close,
                        "quoted_entry": quoted_entry,
                        "fill_entry": fill_entry,
                        "entry_ts": candle.timestamp.isoformat(),
                        "quantity": self.risk_profile.lot_size_units * self.risk_profile.min_lots,
                        "mfe_points": 0.0,
                        "mae_points": 0.0,
                    }

        # Calculate metrics
        total_trades = len(trades)
        wins = sum(1 for t in trades if t.net_pnl > 0)
        losses = sum(1 for t in trades if t.net_pnl <= 0)
        win_rate = round(wins / total_trades, 4) if total_trades > 0 else 0.0
        gross_pnl = sum(t.gross_pnl for t in trades)
        total_brokerage = sum(t.brokerage for t in trades)
        net_pnl = sum(t.net_pnl for t in trades)

        win_sum = sum(t.net_pnl for t in trades if t.net_pnl > 0)
        loss_sum = abs(sum(t.net_pnl for t in trades if t.net_pnl < 0))
        profit_factor = round(win_sum / loss_sum, 2) if loss_sum > 0 else (99.0 if win_sum > 0 else 0.0)

        expectancy = round(net_pnl / total_trades, 2) if total_trades > 0 else 0.0

        # Max loss streak
        max_streak = curr_streak = 0
        for t in trades:
            if t.net_pnl <= 0:
                curr_streak += 1
                if curr_streak > max_streak:
                    max_streak = curr_streak
            else:
                curr_streak = 0

        date_range = (
            candles[0].timestamp.date().isoformat() if candles else "N/A",
            candles[-1].timestamp.date().isoformat() if candles else "N/A",
        )

        result = BacktestResult(
            experiment_id=experiment_id,
            parent_strategy=strat.strategy_id,
            strategy_params=strat.get_status().get("parameter_defaults", {}),
            date_range=date_range,
            total_candles=len(candles),
            total_trades=total_trades,
            winning_trades=wins,
            losing_trades=losses,
            win_rate=win_rate,
            gross_pnl=round(gross_pnl, 2),
            total_brokerage=round(total_brokerage, 2),
            net_pnl=round(net_pnl, 2),
            profit_factor=profit_factor,
            max_drawdown=round(max_drawdown, 2),
            max_consecutive_losses=max_streak,
            expectancy_per_trade=expectancy,
            trades=trades,
        )

        self._save_report(result)
        return result

    def _load_candles(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Candle]:
        if not self.db_path.exists():
            return []
        conn = sqlite3.connect(self.db_path)
        query = "SELECT symbol, ts, open, high, low, close, volume FROM candles WHERE symbol = ?"
        params: List[Any] = [symbol]
        if start_date:
            query += " AND ts >= ?"
            params.append(start_date)
        if end_date:
            query += " AND ts <= ?"
            params.append(end_date)
        query += " ORDER BY ts ASC"

        rows = conn.execute(query, params).fetchall()
        conn.close()

        candles: List[Candle] = []
        for r in rows:
            ts = datetime.fromisoformat(r[1])
            c = validate_candle(r[0], ts, float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6]))
            candles.append(c)
        return candles

    def _save_report(self, res: BacktestResult) -> None:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = REPORTS_DIR / f"{res.experiment_id}.json"
        path.write_text(json.dumps(res.to_dict(), indent=2), encoding="utf-8")
