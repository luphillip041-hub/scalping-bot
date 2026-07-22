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

## Backtest before you trade

```bash
python backtest.py            # last 20 trading days, default symbols
DAYS=40 SYMBOLS=SPY,QQQ python backtest.py
```

It walks forward bar-by-bar with the same signal, bracket, time-exit, and
circuit-breaker logic as the live bot, then reports per-symbol win rate,
profit factor, and P&L. Note: no commissions/slippage are modeled, so treat
marginal results (profit factor ≈ 1.0) as break-even at best. Tune the
strategy via `.env` (e.g. `VOLUME_SPIKE_MULT`, `TAKE_PROFIT_PCT`) and re-run.

## Discord alerts

Get entries, exits with P&L, circuit-breaker halts, and an end-of-day recap
pushed to your phone:

1. In Discord: **Server Settings → Integrations → Webhooks → New Webhook → Copy URL**
2. Set `DISCORD_WEBHOOK_URL=<your url>` in `.env` (or as a Railway/Claw variable)
3. Done — alerts arrive as color-coded embeds. Leave the var empty to disable.

## Deploy to Railway (run 24/7, manage from your phone)

The repo includes `railway.toml`, `Procfile`, and `runtime.txt` — zero extra
config needed:

1. Go to [railway.app](https://railway.app) → **New Project → Deploy from GitHub repo** → pick `scalping-bot`
2. In the project → **Variables**, add `ALPACA_API_KEY` and `ALPACA_API_SECRET`
   (plus any tuning vars from `.env.example`)
3. Deploy. Railway auto-detects Python, installs `requirements.txt`, and runs
   `python main.py`. It restarts automatically on failure.
4. Watch logs and stop/start anytime from the Railway mobile app or browser.

The bot sleeps outside market hours and only trades 09:35–15:50 ET on weekdays.

## Disclaimer

Educational project. Scalping is high-frequency, high-risk trading — test
thoroughly on paper before ever considering live funds. Past performance
guarantees nothing.
