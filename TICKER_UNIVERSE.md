# Scalping Bot Ticker Universe

## Current Default Universe (12 tickers)

Curated for high liquidity, tight spreads, and consistent intraday volatility.

### Mega-Cap Tech (4 tickers)
- **AAPL** - Apple: highest volume, liquid options
- **MSFT** - Microsoft: consistent intraday ranges
- **NVDA** - NVIDIA: tech momentum, AI-driven volatility
- **TSLA** - Tesla: high beta, strong intraday swings

### Growth/Mega-Cap (4 tickers)
- **AMZN** - Amazon: reliable volume, low spreads
- **GOOGL** - Alphabet: tech sector leader
- **META** - Meta/Facebook: social media volatility
- **NFLX** - Netflix: entertainment, momentum plays

### Financials (2 tickers)
- **JPM** - JP Morgan: banking sector anchor
- **BAC** - Bank of America: financial stability

### Semiconductors (2 tickers)
- **AMD** - Advanced Micro Devices: chip sector
- **QCOM** - Qualcomm: mobile/comms chips

---

## Why These Tickers?

1. **Liquidity**: Average daily volume > 50M shares
2. **Spread**: Bid-ask typically < 1 penny for most of the day
3. **Volatility**: Consistent intraday swings ($0.25-2.00 per bar)
4. **Correlation**: Some sector diversity to avoid simultaneous signals
5. **Market Hours**: All actively traded 9:30-16:00 ET

---

## Other Candidates to Test

### Expansion Candidates (Lower volume, higher spreads—backtest first)
- **ORCL** - Oracle: enterprise software
- **INTC** - Intel: semiconductor defensive
- **CSCO** - Cisco: networking
- **ADBE** - Adobe: SaaS stalwart
- **CRM** - Salesforce: cloud computing
- **PYPL** - PayPal: fintech
- **SQ** - Square: payments/fintech
- **SHOP** - Shopify: e-commerce platform
- **COIN** - Coinbase: crypto exposure
- **UBER** - Uber: mobility/delivery

### Avoid
- **SPY, QQQ, IWM** - ETFs have lower intraday volatility, less scalp-friendly
- **Penny stocks** - Wide spreads, low volume, slippage
- **Illiquid names** - Order fill delays, adverse slippage
- **Pre-market only** - No IEX data for extended hours (yet)

---

## Using Custom Tickers

Override the default universe:

```bash
# Single symbol
export SYMBOLS="AAPL"

# Custom list
export SYMBOLS="AAPL,TSLA,META,AMZN"

# Run with env var
SYMBOLS="NVDA,AMD,QCOM" python main.py
```

Or edit `.env`:
```
SYMBOLS=AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META,NFLX,JPM,BAC,AMD,QCOM
```

---

## Backtesting a Custom Universe

Test profitability before deploying:

```bash
# Test new symbol
SYMBOLS="ORCL" DAYS=20 python backtest.py

# Compare two universes
SYMBOLS="AAPL,MSFT" DAYS=40 python backtest.py
SYMBOLS="AAPL,MSFT,AMZN,NFLX" DAYS=40 python backtest.py
```

If profit factor < 1.2 or win rate < 45%, the symbol may not be scalp-friendly.

---

## Performance Tracking by Ticker

Query your trade journal to find best performers:

```python
from scalper.analytics import PerformanceTracker

pt = PerformanceTracker("trades.jsonl")
print(pt.best_symbols(limit=10))
```

Output:
```
[
    {"symbol": "TSLA", "trades": 48, "win_rate": "54.2%", "total_pnl": "$487.50"},
    {"symbol": "AAPL", "trades": 42, "win_rate": "52.4%", "total_pnl": "$342.10"},
    ...
]
```

### Optimization Strategy

1. **Backtest** each candidate for 20–40 days
2. **Deploy** profitable symbols (PF > 1.2)
3. **Monitor** live performance in trade journal
4. **Rotate** underperformers out quarterly
5. **Diversify** across sectors to avoid correlation

---

## Risk Scaling by Universe Size

As you add more tickers, adjust risk limits:

| Universe Size | Max Positions | Daily Loss Limit | Trades/Day |
|---|---|---|---|
| 1–3 tickers | 2 | $150 | 15 |
| 4–6 tickers | 4 | $250 | 30 |
| 7–12 tickers | 5–6 | $300–400 | 40–50 |
| 13+ tickers | 8–10 | $500+ | 60+ |

Tighter loss limits and position limits prevent catastrophic drawdowns in a wider universe.

---

## Sector Distribution (Current Universe)

- **Technology**: 7 tickers (58%) — correlated, use position limits
- **Financials**: 2 tickers (17%)
- **Semiconductors**: 2 tickers (17%)
- **Energy**: 1 ticker (8%) — not included by default

To balance sector exposure:
```bash
export SYMBOLS="AAPL,MSFT,NVDA,TSLA,JPM,BAC,AMD,QCOM,XOM,CVX,PG,JNJ"
```

---

## Monitoring Alerts

The Discord bot now alerts on:
- **Entry signals** per symbol
- **Win/loss streaks** per symbol
- **Sector divergence** (if one sector overheating)
- **Correlation breaks** (when tight-correlated symbols diverge)

Review `trades.jsonl` weekly to audit symbol-level performance.

