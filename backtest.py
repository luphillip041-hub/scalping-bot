"""Backtest the VWAP scalping strategy on historical 1-min bars.

Walks forward bar-by-bar per symbol per day, exactly like the live bot:
signals from scalper.strategy, bracket exits (TP/SL), time-based exits,
position limits and the daily-loss circuit breaker from scalper.risk.

Usage:
    python backtest.py                 # last 20 trading days, default symbols
    DAYS=40 SYMBOLS=SPY,QQQ python backtest.py
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from scalper.config import Config
from scalper.risk import RiskManager
from scalper.strategy import Side, generate_signal

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("backtest")
ET = ZoneInfo("America/New_York")


@dataclass
class OpenTrade:
    symbol: str
    side: Side
    entry_price: float
    qty: int
    entry_time: datetime
    tp: float
    sl: float


@dataclass
class Result:
    pnl: float = 0.0
    trades: int = 0
    wins: int = 0
    gross_win: float = 0.0
    gross_loss: float = 0.0
    daily_pnls: dict = None

    def __post_init__(self):
        self.daily_pnls = {}

    def record(self, pnl: float, day):
        self.pnl += pnl
        self.trades += 1
        self.daily_pnls[day] = self.daily_pnls.get(day, 0) + pnl
        if pnl > 0:
            self.wins += 1
            self.gross_win += pnl
        else:
            self.gross_loss += abs(pnl)


def fetch_bars(data, symbol: str, days: int):
    end = datetime.now(ET)
    start = end - timedelta(days=int(days * 1.6) + 5)  # pad for weekends/holidays
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
        feed=DataFeed.IEX,
    )
    bars = list(data.get_stock_bars(req).data.get(symbol, []))
    by_day: dict = {}
    for b in bars:
        by_day.setdefault(b.timestamp.date(), []).append(b)
    # keep the most recent `days` trading days
    days_sorted = sorted(by_day)[-days:]
    return {d: by_day[d] for d in days_sorted}


def in_window(ts: datetime, cfg: Config) -> bool:
    t = ts.astimezone(ET).time()
    h1, m1 = map(int, cfg.trade_start.split(":"))
    h2, m2 = map(int, cfg.trade_end.split(":"))
    return dtime(h1, m1) <= t <= dtime(h2, m2)


def simulate_day(symbol: str, bars, cfg: Config, risk: RiskManager, res: Result,
                 open_trade: OpenTrade | None) -> OpenTrade | None:
    """Simulate one symbol for one day. Returns open trade left at day end."""
    day = bars[0].timestamp.date()
    for i in range(1, len(bars)):
        bar = bars[i]

        # --- manage open trade: SL/TP on bar low/high, then time exit ---
        if open_trade is not None:
            t, e, q = open_trade, open_trade.entry_price, open_trade.qty
            exit_px = None
            if t.side == Side.LONG:
                if bar.low <= t.sl:
                    exit_px = t.sl
                elif bar.high >= t.tp:
                    exit_px = t.tp
            else:
                if bar.high >= t.sl:
                    exit_px = t.sl
                elif bar.low <= t.tp:
                    exit_px = t.tp
            held_min = (bar.timestamp - t.entry_time).total_seconds() / 60
            if exit_px is None and held_min >= cfg.max_hold_minutes:
                exit_px = bar.close
            if exit_px is not None:
                pnl = (exit_px - e) * q if t.side == Side.LONG else (e - exit_px) * q
                res.record(pnl, day)
                risk.record_close(pnl, day)
                open_trade = None

        # --- entries (one position per symbol at a time) ---
        if open_trade is None and i >= 30 and in_window(bar.timestamp, cfg):
            ok, _ = risk.can_trade(0 if open_trade is None else 1, day)
            if not ok:
                continue
            sig = generate_signal(bars[: i + 1], cfg)
            if sig:
                px = bar.close
                qty = risk.qty_for(px)
                if sig.side == Side.LONG:
                    tp = px * (1 + cfg.take_profit_pct / 100)
                    sl = px * (1 - cfg.stop_loss_pct / 100)
                else:
                    tp = px * (1 - cfg.take_profit_pct / 100)
                    sl = px * (1 + cfg.stop_loss_pct / 100)
                open_trade = OpenTrade(symbol, sig.side, px, qty, bar.timestamp, tp, sl)
                risk.register_trade(day)

    # end of day: flatten any leftover at the close (live bot's bracket is DAY)
    if open_trade is not None:
        t, e, q = open_trade, open_trade.entry_price, open_trade.qty
        px = bars[-1].close
        pnl = (px - e) * q if t.side == Side.LONG else (e - px) * q
        res.record(pnl, day)
        risk.record_close(pnl, day)
        open_trade = None
    return None


def main():
    cfg = Config()
    cfg.validate()
    days = int(os.getenv("DAYS", "20"))
    data = StockHistoricalDataClient(cfg.api_key, cfg.api_secret)

    total = Result()
    print(f"\n=== Backtest: last {days} trading days | {', '.join(cfg.symbols)} ===")
    print(f"TP +{cfg.take_profit_pct}% / SL -{cfg.stop_loss_pct}% | "
          f"size ${cfg.position_size_usd:.0f} | max hold {cfg.max_hold_minutes}m\n")

    for sym in cfg.symbols:
        try:
            by_day = fetch_bars(data, sym, days)
        except Exception as e:
            log.error("%s: fetch failed: %s", sym, e)
            continue
        res = Result()
        risk = RiskManager(cfg.max_daily_loss_usd, cfg.max_trades_per_day,
                           cfg.max_positions, cfg.position_size_usd)
        open_trade = None
        for day in sorted(by_day):
            open_trade = simulate_day(sym, by_day[day], cfg, risk, res, open_trade)
        wr = res.wins / res.trades * 100 if res.trades else 0
        pf = res.gross_win / res.gross_loss if res.gross_loss else float("inf")
        print(f"{sym:5s}  trades {res.trades:4d}  win {wr:5.1f}%  "
              f"PF {pf:4.2f}  PnL ${res.pnl:+9.2f}")
        total.pnl += res.pnl
        total.trades += res.trades
        total.wins += res.wins
        total.gross_win += res.gross_win
        total.gross_loss += res.gross_loss
        for d, p in res.daily_pnls.items():
            total.daily_pnls[d] = total.daily_pnls.get(d, 0) + p

    if total.trades:
        wr = total.wins / total.trades * 100
        pf = total.gross_win / total.gross_loss if total.gross_loss else float("inf")
        daily = sorted(total.daily_pnls.items())
        best = max(daily, key=lambda x: x[1])
        worst = min(daily, key=lambda x: x[1])
        print(f"\nTOTAL    trades {total.trades:4d}  win {wr:5.1f}%  "
              f"PF {pf:4.2f}  PnL ${total.pnl:+9.2f}")
        print(f"Best day  {best[0]} ${best[1]:+.2f} | Worst day {worst[0]} ${worst[1]:+.2f}")
        losing_days = sum(1 for _, p in daily if p < 0)
        print(f"Days traded: {len(daily)}, losing days: {losing_days}")


if __name__ == "__main__":
    main()
