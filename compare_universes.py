"""Compare performance across different symbol universes.

Test different universe subsets to find the optimal mix.

Usage:
    # Test all named universes
    python compare_universes.py

    # Test custom universes
    DAYS=20 python compare_universes.py
"""
import os
import sys
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo
from dataclasses import dataclass

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from scalper.config import Config
from scalper.risk import RiskManager
from scalper.strategy import Side, generate_signal

ET = ZoneInfo("America/New_York")


@dataclass
class UniverseResult:
    name: str
    symbols: list
    trades: int = 0
    wins: int = 0
    pnl: float = 0.0
    gross_win: float = 0.0
    gross_loss: float = 0.0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades else 0

    @property
    def pf(self) -> float:
        return self.gross_win / self.gross_loss if self.gross_loss > 0 else (
            float('inf') if self.gross_win > 0 else 0
        )

    @property
    def avg_trade(self) -> float:
        return self.pnl / self.trades if self.trades else 0


def fetch_bars(data, symbol: str, days: int):
    """Fetch bars for last N trading days."""
    end = datetime.now(ET)
    start = end - timedelta(days=int(days * 1.6) + 5)
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
        feed=DataFeed.IEX,
    )
    bars = list(data.get_stock_bars(req).data.get(symbol, []))
    by_day = {}
    for b in bars:
        by_day.setdefault(b.timestamp.date(), []).append(b)
    days_sorted = sorted(by_day)[-days:]
    return {d: by_day[d] for d in days_sorted}


def in_window(ts: datetime, cfg: Config) -> bool:
    """Check if timestamp is in trading window."""
    t = ts.astimezone(ET).time()
    h1, m1 = map(int, cfg.trade_start.split(":"))
    h2, m2 = map(int, cfg.trade_end.split(":"))
    return dtime(h1, m1) <= t <= dtime(h2, m2)


def simulate_day(symbol: str, bars, cfg: Config, risk: RiskManager, result: UniverseResult):
    """Simulate one symbol for one day."""
    day = bars[0].timestamp.date()
    open_trade = None
    
    for i in range(1, len(bars)):
        bar = bars[i]
        
        # Manage open trade
        if open_trade is not None:
            px, qty, entry, tp, sl, side = open_trade
            exit_px = None
            
            if side == Side.LONG:
                if bar.low <= sl:
                    exit_px = sl
                elif bar.high >= tp:
                    exit_px = tp
            else:
                if bar.high >= sl:
                    exit_px = sl
                elif bar.low <= tp:
                    exit_px = tp
            
            held_min = (bar.timestamp - open_trade[5]).total_seconds() / 60  # Entry time
            if exit_px is None and held_min >= cfg.max_hold_minutes:
                exit_px = bar.close
            
            if exit_px is not None:
                pnl = (exit_px - entry) * qty if side == Side.LONG else (entry - exit_px) * qty
                result.trades += 1
                if pnl > 0:
                    result.wins += 1
                    result.gross_win += pnl
                else:
                    result.gross_loss += abs(pnl)
                result.pnl += pnl
                risk.record_close(pnl, day)
                open_trade = None
        
        # Entry
        if open_trade is None and i >= 30 and in_window(bar.timestamp, cfg):
            ok, _ = risk.can_trade(0, day)
            if not ok:
                continue
            
            sig = generate_signal(bars[:i+1], cfg)
            if sig:
                price = bar.close
                qty = risk.qty_for(price)
                
                if sig.side == Side.LONG:
                    tp = price * (1 + cfg.take_profit_pct / 100)
                    sl = price * (1 - cfg.stop_loss_pct / 100)
                else:
                    tp = price * (1 - cfg.take_profit_pct / 100)
                    sl = price * (1 + cfg.stop_loss_pct / 100)
                
                # Store: (price, qty, entry_price, tp, sl, side, entry_time)
                open_trade = (price, qty, price, tp, sl, sig.side, bar.timestamp)
                risk.register_trade(day)
    
    # Flatten at close
    if open_trade is not None:
        px, qty, entry, tp, sl, side, _ = open_trade
        price = bars[-1].close
        pnl = (price - entry) * qty if side == Side.LONG else (entry - price) * qty
        result.trades += 1
        if pnl > 0:
            result.wins += 1
            result.gross_win += pnl
        else:
            result.gross_loss += abs(pnl)
        result.pnl += pnl
        risk.record_close(pnl, day)


def backtest_universe(data, symbols: list, cfg: Config, days: int) -> UniverseResult:
    """Backtest a symbol universe."""
    result = UniverseResult("", symbols)
    result.name = ",".join(symbols)
    
    all_bars = {}
    for sym in symbols:
        try:
            all_bars[sym] = fetch_bars(data, sym, days)
        except Exception as e:
            print(f"  {sym}: fetch error {e}", file=sys.stderr)
    
    risk = RiskManager(
        cfg.max_daily_loss_usd,
        cfg.max_trades_per_day,
        cfg.max_positions,
        cfg.position_size_usd,
    )
    
    # Walk through all days
    all_days = set()
    for sym_bars in all_bars.values():
        all_days.update(sym_bars.keys())
    
    for day in sorted(all_days):
        for sym in symbols:
            if day in all_bars.get(sym, {}):
                simulate_day(sym, all_bars[sym][day], cfg, risk, result)
    
    return result


def main():
    cfg = Config()
    cfg.validate()
    days = int(os.getenv("DAYS", "20"))
    data = StockHistoricalDataClient(cfg.api_key, cfg.api_secret)
    
    # Define universes to test
    universes = {
        "Original (3)": ["AAPL", "TSLA", "META"],
        "Expanded (12)": [
            "AAPL", "MSFT", "NVDA", "TSLA",
            "AMZN", "GOOGL", "META", "NFLX",
            "JPM", "BAC", "AMD", "QCOM"
        ],
        "Core Edge (3)": ["AAPL", "TSLA", "META"],
        "Tech Only (10)": [
            "AAPL", "MSFT", "NVDA", "TSLA",
            "AMZN", "GOOGL", "META", "NFLX", "AMD", "QCOM"
        ],
        "Large Cap (7)": [
            "AAPL", "MSFT", "NVDA", "TSLA",
            "AMZN", "GOOGL", "META"
        ],
        "Aggressive (6)": [
            "TSLA", "NVDA", "META", "NFLX", "AMD", "AMZN"
        ],
        "Conservative (5)": [
            "AAPL", "MSFT", "GOOGL", "JPM", "BAC"
        ],
        "Finance (2)": ["JPM", "BAC"],
        "Semiconductors (2)": ["AMD", "QCOM"],
    }
    
    print(f"\n{'='*120}")
    print(f"Universe Comparison | Last {days} trading days")
    print(f"{'='*120}\n")
    
    results = []
    for name, symbols in universes.items():
        print(f"{name:30s}...", end=" ", flush=True)
        result = backtest_universe(data, symbols, cfg, days)
        results.append(result)
        print(f"✓ ({result.trades} trades)")
    
    # Sort by Profit Factor
    results.sort(key=lambda r: r.pf, reverse=True)
    
    print(f"\n{'='*120}")
    print(
        f"{'Universe':<30} {'Trades':<8} {'Wins':<8} {'Win %':<8} "
        f"{'PF':<8} {'PnL':<12} {'Avg/Trade':<12}"
    )
    print(f"{'='*120}")
    
    for r in results:
        pf_str = f"{r.pf:.2f}" if r.pf != float('inf') else "∞"
        print(
            f"{r.name:<30} {r.trades:<8} {r.wins:<8} {r.win_rate:>6.1f}%  "
            f"{pf_str:<8} ${r.pnl:>+10.2f}  ${r.avg_trade:>+10.2f}"
        )
    
    print(f"{'='*120}\n")
    
    # Recommendations
    best = results[0]
    print(f"🏆 Best universe: {best.name}")
    print(f"   {best.trades} trades | {best.win_rate:.1f}% win | PF {best.pf:.2f} | ${best.pnl:+.2f} PnL")


if __name__ == "__main__":
    main()

