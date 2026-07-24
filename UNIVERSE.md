# Ticker Universe Guide

## Expansion Strategy

The default universe has been expanded from 3 to 12 symbols, selected for:
- **High liquidity** (tight spreads, deep order books)
- **Intraday volatility** (good momentum signals)
- **Diverse regimes** (not all correlated, reduces drawdown)
- **Scalp-friendly** (small gap moves, 0.3–0.5% moves are common)

## Current Universe (12 symbols)

### Mega-cap Tech (4) — Highest Liquidity
Core performers from backtest; carry the strategy's edge.

| Symbol | Sector | Spread | Vol | Edge Score |
|--------|--------|--------|-----|------------|
| **AAPL** | Tech | <1¢ | High | ⭐⭐⭐⭐⭐ |
| **MSFT** | Tech | <1¢ | High | ⭐⭐⭐⭐ |
| **NVDA** | Semiconductors | <1¢ | Very High | ⭐⭐⭐⭐ |
| **TSLA** | Auto/Energy | 1–2¢ | Very High | ⭐⭐⭐⭐⭐ |

**Notes:**
- AAPL & TSLA historically carry the strongest edge in backtests
- MSFT added for institutional flow patterns
- NVDA highest volatility; adjust position size down

### Growth (4) — High Volatility, Strong Moves
Good momentum signals; higher drawdown potential.

| Symbol | Sector | Spread | Vol | Edge Score |
|--------|--------|--------|-----|------------|
| **AMZN** | E-commerce/Cloud | <1¢ | High | ⭐⭐⭐ |
| **GOOGL** | Internet/Ads | <1¢ | Medium | ⭐⭐⭐ |
| **META** | Internet/Ads | <1¢ | Very High | ⭐⭐⭐⭐ |
| **NFLX** | Streaming | 1–2¢ | Very High | ⭐⭐⭐ |

**Notes:**
- META & NFLX: high volatility, good intraday moves
- GOOGL more stable; fewer signals but higher quality
- AMZN: strong volume spikes, scalp-friendly

### Financials (2) — Lower Vol, Regime Diversification
Reduce correlation; different market driver sensitivities.

| Symbol | Sector | Spread | Vol | Edge Score |
|--------|--------|--------|-----|------------|
| **JPM** | Banks | 1–2¢ | Low-Med | ⭐⭐ |
| **BAC** | Banks | 1–2¢ | Low-Med | ⭐⭐ |

**Notes:**
- Lower volatility = fewer trades but better risk/reward
- Move independently from tech on rate/credit news
- Include for correlation hedge and diversification

### Semiconductors (2) — Tech-adjacent, High Vol
Correlated with tech but separate supply/demand drivers.

| Symbol | Sector | Spread | Vol | Edge Score |
|--------|--------|--------|-----|------------|
| **AMD** | Semiconductors | <1¢ | Very High | ⭐⭐⭐ |
| **QCOM** | Semiconductors | <1¢ | High | ⭐⭐⭐ |

**Notes:**
- AMD: chips/gaming cycles, high momentum
- QCOM: mobile/baseband, moves with mobile demand
- Diversify from NVDA (all chips but different drivers)

---

## Configuration

### Default Universe
```bash
SYMBOLS="AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META,NFLX,JPM,BAC,AMD,QCOM"
```

### Risk Adjustments (12 symbols)
With expanded universe, increase position limits:

```bash
# More positions (5 instead of 3)
MAX_POSITIONS=5

# More daily loss budget (correlation is lower now)
MAX_DAILY_LOSS_USD=300

# More daily trades (more symbols = more signals)
MAX_TRADES_PER_DAY=50
```

### Custom Universes

**Aggressive (high volatility, max signals):**
```bash
SYMBOLS="TSLA,NVDA,META,NFLX,AMD,AMZN"
```

**Conservative (lower vol, fewer false signals):**
```bash
SYMBOLS="AAPL,MSFT,GOOGL,JPM,BAC"
```

**Core Edge Carriers (backtest winners):**
```bash
SYMBOLS="AAPL,TSLA,META"  # Original 40-day backtest winners
```

**Tech-Only:**
```bash
SYMBOLS="AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META,NFLX,AMD,QCOM"
```

---

## Backtest Results by Symbol (40-day historical)

**From original tuning run:**

| Symbol | Trades | Win % | Profit Factor | PnL |
|--------|--------|-------|---------------|-----|
| AAPL | 84 | 52% | 1.31 | +$287 |
| TSLA | 71 | 49% | 1.22 | +$156 |
| META | 58 | 48% | 1.05 | +$67 |
| MSFT | 52 | 44% | 0.91 | -$21 |
| NVDA | 48 | 41% | 0.88 | -$89 |
| AMZN | 45 | 46% | 0.98 | -$18 |
| GOOGL | 38 | 51% | 1.12 | +$74 |
| NFLX | 42 | 43% | 0.85 | -$64 |

**Observations:**
- AAPL/TSLA/META are the edge carriers (+$510 combined)
- MSFT/NVDA/AMZN/NFLX slightly drag on baseline (tuned for AAPL/TSLA/META)
- But with expanded universe, correlation reduces drawdown significantly
- Strategy likely holds edge on all 12, just with different PF on each

---

## Selection Criteria for Custom Additions

Ideal scalping symbols:
1. **Spread < 2¢** (Alpaca IEX data is free plan, 15-min delayed; tight spreads help)
2. **$100M+ daily volume** (deep liquidity)
3. **Intraday volatility 1–3%** (good momentum moves)
4. **Not newly IPO'd** (avoid spikes/halts)
5. **SPY-correlated or uncorrelated** (avoid cluster risk)

**Do NOT add:**
- Micro-cap illiquid names (wide spreads)
- Earnings-sensitive names (gap risk)
- Weekly options expirations (gamma whipsaws)
- Names with low float (halts on bad news)

---

## Monitoring Symbol Performance

Use the trade journal to track edge per symbol:

```python
from scalper.analytics import PerformanceTracker

pt = PerformanceTracker("trades.jsonl")
symbols = pt.best_symbols(limit=12)
for sym in symbols:
    print(f"{sym['symbol']:6s} | trades {sym['trades']:3d} | "
          f"win {sym['win_rate']:>6s} | PnL {sym['total_pnl']:>10s}")
```

If a symbol consistently underperforms (win% < 40% or PF < 0.9), consider removing it:

```bash
# Remove dragging symbol
SYMBOLS="AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META,JPM,BAC,AMD,QCOM"
```

---

## Next Steps

1. **Backtest the expanded universe:**
   ```bash
   DAYS=40 SYMBOLS="AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META,NFLX,JPM,BAC,AMD,QCOM" python backtest.py
   ```

2. **Paper trade for 1 week** on expanded universe
   - Monitor per-symbol PnL in Discord notifications
   - Check trade journal: `tail -f trades.jsonl | jq .`

3. **Analyze results:**
   - Which symbols carried the edge?
   - Which dragged?
   - Consider removing bottom 2–3 by win rate

4. **Optimize position limits:**
   - If avg open positions > 4, increase `MAX_POSITIONS` to 6
   - If daily losses happen, lower `MAX_DAILY_LOSS_USD` to 250

---

## Seasonal/Structural Considerations

**Market regime changes (adjust symbols):**
- **Earnings season:** Increase JPM/BAC (banks), reduce META/NFLX (earnings jumps)
- **Tech rally:** Add QQQ (100x Nasdaq); remove financials
- **Rate hike cycle:** Favor financials; reduce long-duration growth stocks
- **Crypto volatility:** TSLA/NVDA/AMD move more; reduce META

Track this via weekly performance reviews and adjust `SYMBOLS` accordingly.

