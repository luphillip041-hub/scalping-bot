# VWAP Scalping Bot (Alpaca)

A signals-driven **1-minute scalping bot** for US equities, running on Alpaca
paper trading by default.

## Strategy

**VWAP + momentum with volume confirmation**

- Computes session VWAP from 1-min bars starting at the 9:30 AM ET open
- **Long entry**: price crosses above VWAP (or holds > 0.05% above it) with
  positive 3-bar momentum and volume ≥ 1.5× the 20-bar average
- **Short entry**: mirror image below VWAP
- **Exits**: bracket orders with +0.4% take-profit / −0.25% stop-loss, plus a
  15-minute time-based exit — scalps shouldn't linger

## Risk controls

| Control | Default |
|---|---|
| Position size | $2,000 per trade |
| Max open positions | 3 |
| Max daily loss (circuit breaker) | $150 — bot halts for the day |
| Max trades per day | 25 |
| Session window | 09:35–15:50 ET (skips open/close chaos) |

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your Alpaca keys
python main.py
```

Get paper-trading keys at <https://app.alpaca.markets> → Paper Trading → API Keys.

> Note: the free Alpaca data plan uses the IEX feed (SIP blocks the last
> 15 minutes). The bot is already configured for IEX. Bars are slightly
> thinner than SIP but fine for liquid large-caps and ETFs.

## Configuration

Everything is tuned via `.env` — see `.env.example`. Symbols default to
liquid names: `SPY,QQQ,AAPL,MSFT,NVDA,AMD,TSLA,META`.

## Run 24/7

Deploy to Railway/Render/Fly as a worker process with `python main.py` as the
start command and the env vars set in the dashboard. It sleeps outside market
hours and trades only during the session window.

## Disclaimer

Educational project. Scalping is high-frequency, high-risk trading — test
thoroughly on paper before ever considering live funds. Past performance
guarantees nothing.
