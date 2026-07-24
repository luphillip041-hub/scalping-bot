"""Research symbol liquidity and characteristics for scalping.

Checks spread, volume, and volatility for each symbol to ensure
they meet scalping requirements.

Usage:
    python research_symbols.py AAPL MSFT TSLA AMZN ...
    python research_symbols.py --all  # Check default universe
"""
import sys
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import statistics

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

from scalper.config import Config

ET = ZoneInfo("America/New_York")


def analyze_symbol(client, symbol: str, days: int = 5):
    """Analyze symbol liquidity and intraday volatility."""
    try:
        end = datetime.now(ET)
        start = end - timedelta(days=days)
        
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )
        bars = list(client.get_stock_bars(req).data.get(symbol, []))
        
        if not bars:
            return None
        
        # Compute metrics
        spreads = []
        volumes = []
        intraday_ranges = []
        
        for bar in bars[-1000:]:  # Last 1000 bars (recent trading)
            spread = (bar.high - bar.low) / bar.close * 100
            spreads.append(spread)
            volumes.append(bar.volume)
            intraday_ranges.append((bar.high - bar.low) / bar.open * 100)
        
        avg_spread_bps = statistics.mean(spreads) * 100  # in basis points
        med_spread = statistics.median(spreads)
        avg_vol = statistics.mean(volumes)
        avg_range = statistics.mean(intraday_ranges)
        
        return {
            "symbol": symbol,
            "avg_spread_bps": avg_spread_bps,
            "med_spread_pct": med_spread,
            "avg_volume": int(avg_vol),
            "avg_range_pct": avg_range,
            "scalp_score": compute_score(avg_spread_bps, avg_vol, avg_range),
            "bars_analyzed": len(bars),
        }
    except Exception as e:
        print(f"{symbol}: ERROR - {e}", file=sys.stderr)
        return None


def compute_score(spread_bps: float, avg_vol: float, intraday_range: float) -> float:
    """Score symbol on scalp-friendliness (0.0 to 100.0)."""
    # Tight spread (< 5 bps) = good
    spread_score = max(0, 100 - (spread_bps * 5))
    
    # High volume (> 1M) = good
    vol_score = min(100, (avg_vol / 1_000_000) * 100)
    
    # Medium volatility (0.5–2%) = good; too low or too high is bad
    if intraday_range < 0.3:
        range_score = 0
    elif intraday_range < 0.5:
        range_score = 30
    elif intraday_range < 1.0:
        range_score = 70
    elif intraday_range < 2.5:
        range_score = 100
    else:
        range_score = max(50, 150 - (intraday_range * 20))
    
    return (spread_score + vol_score + range_score) / 3


def main():
    cfg = Config()
    cfg.validate()
    
    if "--all" in sys.argv:
        symbols = cfg.symbols
    else:
        symbols = sys.argv[1:] if len(sys.argv) > 1 else cfg.symbols
    
    print(f"Analyzing {len(symbols)} symbols (last 5 trading days)...\n")
    
    client = StockHistoricalDataClient(cfg.api_key, cfg.api_secret)
    results = []
    
    for sym in symbols:
        print(f"  {sym}...", end=" ", flush=True)
        result = analyze_symbol(client, sym)
        if result:
            results.append(result)
            print("✓")
        else:
            print("✗")
    
    # Sort by scalp score
    results.sort(key=lambda x: x["scalp_score"], reverse=True)
    
    print("\n" + "=" * 100)
    print(f"{'Symbol':<8} {'Spread':<10} {'Avg Vol':<15} {'Range':<10} {'Score':<8} {'Scalp-Fit':<20}")
    print("=" * 100)
    
    for r in results:
        spread_str = f"{r['med_spread_pct']:.3f}%"
        vol_str = f"{r['avg_volume']:,.0f}"
        range_str = f"{r['avg_range_pct']:.2f}%"
        score = r['scalp_score']
        
        if score >= 80:
            fit = "🟢 Excellent"
        elif score >= 60:
            fit = "🟡 Good"
        elif score >= 40:
            fit = "🟠 Fair"
        else:
            fit = "🔴 Poor"
        
        print(
            f"{r['symbol']:<8} {spread_str:<10} {vol_str:<15} "
            f"{range_str:<10} {score:>6.1f}  {fit:<20}"
        )
    
    print("\n" + "=" * 100)
    print("Criteria:")
    print("  Spread: < 5 bps (tight) ideal for scalping")
    print("  Avg Vol: > 1M shares/min ideal for liquidity")
    print("  Range: 0.5–2.5% intraday is good for scalping signals")
    print("  Score: 80+ excellent, 60+ good, 40+ fair, <40 poor")
    print("\nRecommendations:")
    
    excellent = [r for r in results if r['scalp_score'] >= 80]
    good = [r for r in results if 60 <= r['scalp_score'] < 80]
    fair = [r for r in results if 40 <= r['scalp_score'] < 60]
    poor = [r for r in results if r['scalp_score'] < 40]
    
    if excellent:
        print(f"  ✓ Use: {', '.join(r['symbol'] for r in excellent)}")
    if good:
        print(f"  ✓ Good: {', '.join(r['symbol'] for r in good)}")
    if fair:
        print(f"  ⚠ Monitor: {', '.join(r['symbol'] for r in fair)}")
    if poor:
        print(f"  ✗ Avoid: {', '.join(r['symbol'] for r in poor)}")


if __name__ == "__main__":
    main()

