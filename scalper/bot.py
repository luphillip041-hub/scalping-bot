"""Main scalping bot loop (Alpaca, polling-based).

Runs during market hours: pulls recent 1-min bars per symbol, generates
signals, submits bracket orders (entry + take-profit + stop-loss), and
manages time-based exits and the daily-loss circuit breaker.
"""
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest

from .config import Config
from .risk import RiskManager
from .strategy import Side, generate_signal

log = logging.getLogger("scalper")
ET = ZoneInfo("America/New_York")


class ScalpingBot:
    def __init__(self, cfg: Config):
        cfg.validate()
        self.cfg = cfg
        self.trading = TradingClient(cfg.api_key, cfg.api_secret, paper=cfg.paper)
        self.data = StockHistoricalDataClient(cfg.api_key, cfg.api_secret)
        self.risk = RiskManager(
            max_daily_loss_usd=cfg.max_daily_loss_usd,
            max_trades_per_day=cfg.max_trades_per_day,
            max_positions=cfg.max_positions,
            position_size_usd=cfg.position_size_usd,
        )
        self.entry_times: dict[str, datetime] = {}
        self.entry_prices: dict[str, float] = {}

    # ---------- helpers ----------
    def _in_session(self) -> bool:
        now = datetime.now(ET)
        if now.weekday() >= 5:
            return False
        t = now.strftime("%H:%M")
        return self.cfg.trade_start <= t <= self.cfg.trade_end

    def _todays_bars(self, symbol: str):
        now = datetime.now(ET)
        start = now.replace(hour=9, minute=30, second=0, microsecond=0)
        tf_map = {"1Min": TimeFrame.Minute, "5Min": TimeFrame(5, TimeFrameUnit.Minute)}
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf_map.get(self.cfg.bar_timeframe, TimeFrame.Minute),
            start=start,
            end=now,
            feed=DataFeed.IEX,  # free plan: SIP blocks the last 15 min
        )
        bars = self.data.get_stock_bars(req).data.get(symbol, [])
        return list(bars)

    def _open_positions(self):
        return {p.symbol: p for p in self.trading.get_all_positions()}

    def _realized_today(self):
        """Approximate realized PnL from today's closed orders."""
        today = datetime.now(ET).date()
        pnl = 0.0
        for sym, entry_px in list(self.entry_prices.items()):
            if sym not in self._open_positions() and sym in self.entry_times:
                # position closed since last check — estimate via last fill
                pass
        return pnl

    # ---------- order handling ----------
    def _submit_bracket(self, symbol: str, side: Side):
        bars = self._todays_bars(symbol)
        price = bars[-1].close
        qty = self.risk.qty_for(price)
        tp = price * (1 + self.cfg.take_profit_pct / 100) if side == Side.LONG else price * (1 - self.cfg.take_profit_pct / 100)
        sl = price * (1 - self.cfg.stop_loss_pct / 100) if side == Side.LONG else price * (1 + self.cfg.stop_loss_pct / 100)
        order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == Side.LONG else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            take_profit=TakeProfitRequest(limit_price=round(tp, 2)),
            stop_loss=StopLossRequest(stop_price=round(sl, 2)),
        )
        self.trading.submit_order(order)
        self.entry_times[symbol] = datetime.now(ET)
        self.entry_prices[symbol] = price
        self.risk.register_trade(datetime.now(ET).date())
        log.info("ENTER %s %s x%d @ ~%.2f (tp %.2f / sl %.2f)", side.value, symbol, qty, price, tp, sl)
        send(
            f"{'📈' if side == Side.LONG else '📉'} Entered {side.value.upper()} {symbol}",
            f"Bracket order submitted",
            "entry_long" if side == Side.LONG else "entry_short",
            {"Qty": qty, "Entry ~": f"${price:.2f}", "TP": f"${tp:.2f}", "SL": f"${sl:.2f}"},
        )

    def _check_time_exits(self, positions):
        now = datetime.now(ET)
        for sym, pos in positions.items():
            entered = self.entry_times.get(sym)
            if entered and (now - entered) > timedelta(minutes=self.cfg.max_hold_minutes):
                log.info("TIME EXIT %s after %d min", sym, self.cfg.max_hold_minutes)
                self.trading.close_position(sym)
                self._record_result(sym, float(pos.current_price))

    def _record_result(self, symbol: str, exit_price: float):
        entry = self.entry_prices.pop(symbol, None)
        self.entry_times.pop(symbol, None)
        if entry:
            # qty no longer available here; approximate with configured size
            qty = self.risk.qty_for(entry)
            pnl = (exit_price - entry) * qty
            self.risk.record_close(pnl, datetime.now(ET).date())
            log.info("CLOSE %s pnl≈$%.2f (daily $%.2f)", symbol, pnl, self.risk.daily_pnl)

    def _sync_closed(self, positions):
        """Detect positions closed by bracket legs and record PnL."""
        for sym in list(self.entry_prices):
            if sym not in positions:
                try:
                    quote_bars = self._todays_bars(sym)
                    px = quote_bars[-1].close if quote_bars else self.entry_prices[sym]
                except Exception:
                    px = self.entry_prices[sym]
                self._record_result(sym, px)

    # ---------- main loop ----------
    def run(self):
        acct = self.trading.get_account()
        log.info("Connected. Account %s | equity $%s | paper=%s",
                 acct.account_number, acct.equity, self.cfg.paper)
        was_in_session = False
        while True:
            try:
                if not self._in_session():
                    if was_in_session:
                        # session just ended → daily recap
                        send("📋 Daily recap",
                             f"Session over. Realized P&L: ${self.risk.daily_pnl:+.2f}",
                             "exit_win" if self.risk.daily_pnl > 0 else "exit_loss",
                             {"Trades": self.risk._trade_count,
                              "Halted by loss limit": self.risk.halted})
                        was_in_session = False
                    log.info("Outside session %s–%s ET. Sleeping 60s.",
                             self.cfg.trade_start, self.cfg.trade_end)
                    time.sleep(60)
                    continue
                was_in_session = True

                positions = self._open_positions()
                self._sync_closed(positions)
                self._check_time_exits(positions)

                today = datetime.now(ET).date()
                ok, why = self.risk.can_trade(len(positions), today)
                if not ok:
                    log.warning("No new trades: %s", why)
                    time.sleep(self.cfg.poll_seconds)
                    continue

                for sym in self.cfg.symbols:
                    if sym in positions or sym in self.entry_prices:
                        continue
                    try:
                        bars = self._todays_bars(sym)
                    except Exception as e:
                        log.error("bars %s: %s", sym, e)
                        continue
                    sig = generate_signal(bars, self.cfg)
                    if sig:
                        log.info("SIGNAL %s %s — %s", sym, sig.side.value, sig.reason)
                        try:
                            self._submit_bracket(sym, sig.side)
                            ok, why = self.risk.can_trade(len(self._open_positions()), today)
                            if not ok:
                                break
                        except Exception as e:
                            log.error("order %s: %s", sym, e)

                time.sleep(self.cfg.poll_seconds)
            except KeyboardInterrupt:
                log.info("Stopped by user.")
                break
            except Exception as e:
                log.exception("loop error: %s", e)
                time.sleep(self.cfg.poll_seconds)
