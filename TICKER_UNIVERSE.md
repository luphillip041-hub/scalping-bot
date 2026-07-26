# Scalping Bot Ticker Universe

## Overview

The bot trades a diversified universe of **20 mega-cap liquid stocks** across 5 sectors. This provides a much larger opportunity surface than the original 3-ticker setup while maintaining tight spreads and high volume needed for scalping.

## Ticker Selection Criteria

All tickers meet these strict requirements:
- **Market cap**: ≥$100B (most ≥$1T)
- **Average daily volume**: ≥20M shares
- **Bid-ask spread**: Typically <$0.01 (tight for scalping)
- **Tradable intraday moves**: ±0.5–2% range common
- **Overnight holding**: None (day trades only, brackets close EOD)

## The 20-Stock Universe

### Technology & Cloud (6 stocks)
**Ultra-liquid mega-caps with tight spreads. Highest volume, most scalping signals.**

| Ticker | Sector | Market Cap | Characteristics |
|--------|--------|-----------|-----------------|
| **AAPL** | Tech (Hardware) | ~$3.3T | Highest volume, smallest spreads, consistent momentum |
| **MSFT** | Cloud/Software | ~$3.0T | Enterprise software, stable, good momentum |
| **NVDA** | Semiconductors | ~$2.5T | AI darling, volatile, strong moves |
| **TSLA** | Auto/Energy | ~$800B | High volatility, good scalping range |
| **META** | Social/Advertising | ~$1.3T | Strong momentum swings, tight spreads |
| **GOOG** | Search/Cloud | ~$2.0T | Stable volume, good technical setups |

### E-Commerce & Growth (2 stocks)
**Large-cap growth with good intraday ranges.**

| Ticker | Sector | Market Cap | Characteristics |
|--------|--------|-----------|-----------------|
| **AMZN** | E-Commerce | ~$2.0T | High volume, decent spreads, range-bound |
| **NFLX** | Streaming | ~$300B | Smaller size, more volatile, good setups |

### Financial Services (5 stocks)
**Diverse financial sector exposure. Volume spikes on Fed news, earnings.**

| Ticker | Sector | Market Cap | Characteristics |
|--------|--------|-----------|-----------------|
| **JPM** | Investment Banking | ~$700B | Highest volume in financials, tight spreads |
| **BAC** | Consumer Banking | ~$300B | Large volume, good momentum |
| **GS** | Investment Banking | ~$150B | Smaller, more volatile |
| **V** | Payment Networks | ~$700B | Stable, consistent volume |
| **MA** | Payment Networks | ~$500B | Similar to V, good correlation |
| **BLK** | Asset Management | ~$200B | Institutional flows, trending setups |

### Semiconductors (3 stocks)
**Chip sector leaders. Higher volatility = good scalping range.**

| Ticker | Sector | Market Cap | Characteristics |
|--------|--------|-----------|-----------------|
| **AMD** | Semiconductors | ~$200B | Volatile, good intraday moves |
| **QCOM** | Semiconductors | ~$200B | Mobile/wireless, trending |
| **MU** | Memory Chips | ~$150B | Smaller, choppy, volume spikes |
| **AVGO** | Semiconductor Equipment | ~$200B | Infrastructure play, stable volume |

### Industrials & Discretionary (2 stocks)
**Broader market exposure, different correlation profile.**

| Ticker | Sector | Market Cap | Characteristics |
|--------|--------|-----------|-----------------|
| **BA** | Aerospace/Defense | ~$200B | Larger, stable, institutional flows |
| **F** | Automotive | ~$50B | Smaller, higher beta, more volatility |

---

## Strategy by Sector

### Tech (AAPL, MSFT, NVDA, TSLA, META, GOOG)
- **Characteristics**: Tightest spreads, highest volume, momentum-driven
- **Best setup**: VWAP breakouts with volume spike
- **Expected hold time**: 3–10 minutes (quick fills, tight scalp)
- **Win rate**: Higher (better liquidity)

### Growth (AMZN, NFLX)
- **Characteristics**: Slightly wider spreads, trending
- **Best setup**: Momentum + RSI confirmation
- **Expected hold time**: 5–15 minutes
- **Win rate**: Moderate (good setups but less frequent)

### Financials (JPM, BAC, GS, V, MA, BLK)
- **Characteristics**: Correlated to interest rates, Fed moves
- **Best setup**: Fed announcement breakouts, sector rotation
- **Expected hold time**: 5–20 minutes
- **Win rate**: Moderate (stable but fewer signals)

### Semiconductors (AMD, QCOM, MU, AVGO)
- **Characteristics**: High volatility, sector trend-following
- **Best setup**: Tech sector moves + chip-specific catalysts
- **Expected hold time**: 3–15 minutes
- **Win rate**: Moderate-to-high (volatile = large moves)

### Industrials (BA, F)
- **Characteristics**: Lower volume, broader market correlation
- **Best setup**: Market opens, sector rotation
- **Expected hold time**: 10–20 minutes
- **Win rate**: Lower (less liquid, fewer signals)

---

## Running with the Expanded Universe

### Default (20 stocks)
```bash
python main.py
```
Trades all 20 symbols. Expect ~3–5 signals per hour across the universe.

### Custom Subset
Trade only your preferred sectors:

```bash
# Tech only (highest Sharpe ratio expected)
SYMBOLS=AAPL,MSFT,NVDA,TSLA,META,GOOG python main.py

# Tech + Semiconductors (higher volatility)
SYMBOLS=AAPL,MSFT,NVDA,TSLA,META,GOOG,AMD,QCOM,MU,AVGO python main.py

# Financials only
SYMBOLS=JPM,BAC,GS,V,MA,BLK python main.py

# Safe list (proven momentum setups)
SYMBOLS=AAPL,MSFT,META,JPM,AMD python main.py
```

### Backtesting the Universe

Test on full 20-ticker universe:
```bash
DAYS=40 python backtest.py
```

Test sector subsets:
```bash
# 40-day backtest, tech only
DAYS=40 SYMBOLS=AAPL,MSFT,NVDA,TSLA,META,GOOG python backtest.py

# 20-day backtest, financials
DAYS=20 SYMBOLS=JPM,BAC,GS,V,MA,BLK python backtest.py
```

---

## Risk Limits (Adjusted for Expanded Universe)

With 20 tickers and expanded opportunity surface, we've adjusted risk parameters:

```bash
MAX_POSITIONS=5              # was 3 (more symbols = more positions)
MAX_DAILY_LOSS_USD=300       # was 150 (higher loss limit for larger universe)
MAX_TRADES_PER_DAY=50        # was 25 (more signals from 20 tickers)
POSITION_SIZE_USD=2000       # unchanged (per-position sizing)
```

**Daily loss circuit breaker still fires at -$300**, but you have more upside opportunity from 20 stocks vs. 3.

---

## Expected Signal Frequency

Based on 40-day backtest (3-ticker setup: AAPL/TSLA/META):
- **Original**: ~1 signal per hour across 3 symbols
- **Expanded**: ~2–3 signals per hour across 20 symbols (7× more opportunities)

This assumes similar signal quality (RSI filter, confidence threshold, volume spike).

---

## Monitoring Sector Performance

Use the analytics module to track best-performing sectors:

```python
from scalper.analytics import PerformanceTracker
from datetime import date

pt = PerformanceTracker("trades.jsonl")

# Best symbols this month
print(pt.best_symbols(limit=10))

# Monthly breakdown
print(pt.monthly_summary())

# Today's stats
today_trades = pt.log.load_day(date.today())
print(f"Today: {len(today_trades)} trades, PnL ${sum(t['pnl'] for t in today_trades):+.2f}")
```

---

## Recommended Starting Strategy

### Phase 1: Test Tech-Only (Days 1–5)
Trade highest-liquidity subset: `AAPL,MSFT,NVDA,TSLA,META`
- Verify signal quality and execution
- Expected: ~3–5 trades/hour, win rate >50%

### Phase 2: Add Financials (Days 6–10)
Add banking sector: `JPM,BAC,GS,V,MA,BLK`
- Test how banking signals correlate
- Monitor spread widening on Fed news

### Phase 3: Full Universe (Days 11+)
Trade all 20 tickers
- Monitor per-sector performance
- Adjust risk limits based on realized volatility

---

## Spreads & Execution Quality

Expected bid-ask spreads by ticker (as of latest market):

| Tier | Tickers | Typical Spread |
|------|---------|---|
| Tightest (<$0.01) | AAPL, MSFT, GOOG, AMZN | $0.003–0.005 |
| Tight ($0.01) | NVDA, TSLA, META, NFLX, JPM, V, MA | $0.005–0.01 |
| Normal ($0.01–0.02) | BAC, AMD, QCOM, GS, BLK, MU, AVGO | $0.01–0.02 |
| Wider ($0.02+) | BA, F | $0.02–0.05 |

**Tighter spreads = higher profitability** due to lower slippage. Tech / Financials outperform larger-cap industrials.

---

## Notes for Live Trading

1. **Correlation risk**: Tech + NVDA + semiconductors are all correlated. A sector-wide move triggers multiple signals.
   - Consider position limits by correlation cluster
   - Or embrace it: sector moves provide consistent signal flow

2. **Fed news**: Financials spike on rate decisions. Monitor economic calendar.
   - Wider spreads on FOMC days
   - Higher signal quality on bank earnings

3. **Off-hours**: Overnight gaps reset all positions (DAY orders auto-close). No overnight risk.

4. **Scaling in/out**: With 5 max concurrent positions and 50 max trades/day, you have room for both small quick scalps and slightly longer holds.

---

## Customization & Future Improvements

### Add more symbols
Simply extend the default list in `config.py`:
```python
"AAPL,MSFT,NVDA,..."  # add your own
```

### Filter by minimum spread
Add a real-time spread check before entry:
```python
def _acceptable_spread(symbol) -> bool:
    # fetch bid/ask, check if spread < threshold
    pass
```

### Sector-specific parameters
Use different TP/SL/lookback for different sectors:
```python
TECH_SYMBOLS = ["AAPL", "MSFT", "NVDA", ...]
FINANCE_SYMBOLS = ["JPM", "BAC", ...]

if symbol in TECH_SYMBOLS:
    tp_pct = 0.4  # tighter for tech
else:
    tp_pct = 0.6  # wider for financials
```

---

## Summary

| Metric | 3-Ticker | 20-Ticker |
|--------|----------|-----------|
| Symbols | AAPL, TSLA, META | Tech (6) + Growth (2) + Financials (5) + Semis (4) + Industrial (2) |
| Opportunity Surface | ~1 signal/hour | ~2–3 signals/hour |
| Max Concurrent Positions | 3 | 5 |
| Max Daily Loss | $150 | $300 |
| Max Trades/Day | 25 | 50 |
| Expected Daily Trades | ~8–12 | ~20–30 |
| Liquidity Risk | Very Low | Low |
| Correlation Risk | Moderate | Moderate-High (tech cluster) |

Start with Phase 1 (tech-only) to validate, then expand to full 20-ticker universe as you gain confidence.

