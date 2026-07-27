"""Main scalping bot loop (Alpaca, polling-based).

Runs during market hours: pulls recent 1-min bars per symbol, generates
signals, submits bracket orders for equities AND calls/puts options,
and manages time-based exits and the daily-loss circuit breaker.
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
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus
from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest

from .config import Config
from .notify import send
from .risk import RiskManager
from .strategy import Side, generate_signal
from .options import OptionSide, OptionSelector, OptionsFetcher

log = logging.getLogger("scalper")
ET = ZoneInfo("America/New_York")

# Retry config for transient failures
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds


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
        # Track positions by symbol -> (entry_price, entry_time, order_id, asset_type)
        # asset_type is 'equity' or 'option'
        self.tracked_positions: dict[str, tuple[float, datetime, str, str]] = {}
        # Cache for last synced position state
        self._last_synced_positions = set()
        # Track last sync time to avoid duplicate notifications
        self._last_position_check = datetime.now(ET)
        # Options support
        self.option_fetcher = OptionsFetcher(self.trading) if cfg.enable_options else None
        self.option_selector = OptionSelector(
            days_to_expiry=cfg.options_expiry_days,
            strike_offset_pct=cfg.options_strike_offset_pct,
            max_delta=cfg.options_max_delta,
            min_delta=cfg.options_min_delta,
            max_bid_ask_spread_pct=cfg.options_max_spread_pct,
            min_volume=cfg.options_min_volume,
        ) if cfg.enable_options else None

    # ---------- helpers ----------
    def _in_session(self) -> bool:
        now = datetime.now(ET)
        if now.weekday() >= 5:
            return False
        t = now.strftime("%H:%M")
        return self.cfg.trade_start <= t <= self.cfg.trade_end

    def _todays_bars(self, symbol: str):
        """Fetch today's bars with retry logic."""
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
        for attempt in range(MAX_RETRIES):
            try:
                bars = self.data.get_stock_bars(req).data.get(symbol, [])
                return list(bars)
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                log.warning("bars %s (attempt %d/%d): %s, retrying...", symbol, attempt + 1, MAX_RETRIES, e)
                time.sleep(RETRY_DELAY)
        return []

    def _open_positions(self):
        """Fetch all open positions from Alpaca (equities only)."""
        for attempt in range(MAX_RETRIES):
            try:
                return {p.symbol: p for p in self.trading.get_all_positions()}
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                log.warning("open_positions (attempt %d/%d): %s, retrying...", attempt + 1, MAX_RETRIES, e)
                time.sleep(RETRY_DELAY)
        return {}

    def _get_filled_orders(self, after: datetime = None) -> list:
        """Fetch filled orders from Alpaca for PnL tracking."""
        try:
            limit = 100
            orders = self.trading.get_orders(limit=limit, status=OrderStatus.FILLED)
            if after:
                orders = [o for o in orders if o.filled_at and o.filled_at > after]
            return orders
        except Exception as e:
            log.warning("failed to fetch filled orders: %s", e)
            return []

    def _calculate_realized_pnl(self, after: datetime = None) -> float:
        """Calculate realized PnL from filled orders (sell - buy pairs)."""
        orders = self._get_filled_orders(after=after)
        pnl = 0.0
        
        symbol_fills = {}
        for o in orders:
            if not o.filled_at:
                continue
            sym = o.symbol
            if sym not in symbol_fills:
                symbol_fills[sym] = []
            symbol_fills[sym].append(o)
        
        for sym, fills in symbol_fills.items():
            fills.sort(key=lambda x: x.filled_at)
            buy_qty = 0
            buy_cost = 0.0
            for fill in fills:
                if fill.side == OrderSide.BUY:
                    buy_qty += fill.qty
                    buy_cost += fill.qty * fill.filled_avg_price
                elif fill.side == OrderSide.SELL:
                    if buy_qty > 0:
                        pnl += fill.qty * (fill.filled_avg_price - buy_cost / buy_qty)
                        buy_qty -= fill.qty
                        if buy_qty <= 0:
                            buy_qty = 0
                            buy_cost = 0.0
        
        return pnl

    # ---------- order handling: EQUITIES ----------
    def _submit_bracket(self, symbol: str, side: Side):
        """Submit bracket order with proper TP/SL for equity long/short."""
        bars = self._todays_bars(symbol)
        if not bars:
            log.error("No bars available for %s", symbol)
            return
        
        price = bars[-1].close
        qty = self.risk.qty_for(price)
        
        if side == Side.LONG:
            tp = price * (1 + self.cfg.take_profit_pct / 100)
            sl = price * (1 - self.cfg.stop_loss_pct / 100)
        else:  # SHORT
            tp = price * (1 - self.cfg.take_profit_pct / 100)
            sl = price * (1 + self.cfg.stop_loss_pct / 100)
        
        order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == Side.LONG else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            take_profit=TakeProfitRequest(limit_price=round(tp, 2)),
            stop_loss=StopLossRequest(stop_price=round(sl, 2)),
        )
        
        try:
            placed_order = self.trading.submit_order(order)
            self.tracked_positions[symbol] = (price, datetime.now(ET), placed_order.id, "equity")
            self.risk.register_trade(datetime.now(ET).date())
            log.info("ENTER EQUITY %s %s x%d @ %.2f (tp %.2f / sl %.2f)", 
                     side.value, symbol, qty, price, tp, sl)
            send(
                f"{'📈' if side == Side.LONG else '📉'} Equity {side.value.upper()} {symbol}",
                f"Bracket order submitted",
                "entry_long" if side == Side.LONG else "entry_short",
                {"Qty": qty, "Entry": f"${price:.2f}", "TP": f"${tp:.2f}", "SL": f"${sl:.2f}"},
            )
        except Exception as e:
            log.error("Failed to submit bracket for %s: %s", symbol, e)
            send(
                "⚠️ Equity order failed",
                f"Could not enter {symbol}: {str(e)}",
                "info",
                {"Symbol": symbol},
            )

    # ---------- order handling: OPTIONS ----------
    def _submit_option_order(self, symbol: str, side: Side, underlying_price: float):
        """Submit market order for a call (LONG) or put (SHORT) option.
        
        Fetches options chain, selects contract based on Greeks and liquidity,
        and submits market order.
        """
        if not self.cfg.enable_options or not self.option_fetcher or not self.option_selector:
            return
        
        try:
            # Determine expiration date to fetch
            expiry_date = self.option_selector.next_market_expiry()
            log.debug("Fetching options for %s expiry %s", symbol, expiry_date)
            
            # Fetch options chain from Alpaca
            contracts = self.option_fetcher.fetch_chain(symbol, expiry_date)
            if not contracts:
                log.warning("No options contracts found for %s", symbol)
                return
            
            # Select appropriate contract
            option_contract = None
            if side == Side.LONG:
                # Buy a call for LONG signal
                option_contract = self.option_selector.select_call(underlying_price, contracts)
            else:
                # Buy a put for SHORT signal
                option_contract = self.option_selector.select_put(underlying_price, contracts)
            
            if not option_contract:
                log.warning("No suitable option contract found for %s %s", symbol, side.value)
                return
            
            # Calculate contracts needed (100 shares per contract)
            contract_notional = option_contract.mid_price * 100
            if contract_notional <= 0:
                log.warning("Invalid contract notional for %s", option_contract.contract_symbol)
                return
            
            contracts_qty = max(1, int(self.cfg.options_position_size_usd / contract_notional))
            
            # Submit market buy order for the contract
            order = MarketOrderRequest(
                symbol=option_contract.contract_symbol,
                qty=contracts_qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
            
            placed_order = self.trading.submit_order(order)
            
            # Track this option position
            position_key = f"OPT_{symbol}_{option_contract.contract_symbol}"
            self.tracked_positions[position_key] = (
                option_contract.mid_price,
                datetime.now(ET),
                placed_order.id,
                "option"
            )
            self.risk.register_trade(datetime.now(ET).date())
            
            log.info("ENTER OPTION %s %s x%d @ %.3f (delta=%.2f, spread=%.2f%%)",
                     side.value.upper(), option_contract.contract_symbol, contracts_qty,
                     option_contract.mid_price, option_contract.delta, option_contract.spread_pct)
            
            send(
                f"📊 Option {side.value.upper()} {symbol}",
                f"{option_contract.side.value.upper()} contract",
                "entry_long" if side == Side.LONG else "entry_short",
                {
                    "Contract": option_contract.contract_symbol,
                    "Underlying": f"${underlying_price:.2f}",
                    "Strike": f"${option_contract.strike:.2f}",
                    "Delta": f"{option_contract.delta:.2f}",
                    "Theta": f"{option_contract.theta:.3f}",
                    "IV": f"{option_contract.iv:.1%}",
                    "Price": f"${option_contract.mid_price:.3f}",
                    "Spread": f"{option_contract.spread_pct:.2f}%",
                    "Qty": contracts_qty,
                },
            )
        
        except Exception as e:
            log.error("Failed to submit option order for %s: %s", symbol, e)
            send(
                "⚠️ Option order failed",
                f"Could not enter option for {symbol}: {str(e)}",
                "info",
                {"Symbol": symbol},
            )

    def _check_time_exits(self, positions):
        """Close positions that have exceeded max hold time."""
        now = datetime.now(ET)
        for sym in list(self.tracked_positions.keys()):
            entry_price, entered_at, order_id, asset_type = self.tracked_positions[sym]
            if (now - entered_at) > timedelta(minutes=self.cfg.max_hold_minutes):
                log.info("TIME EXIT %s (%s) after %d min", sym, asset_type, self.cfg.max_hold_minutes)
                try:
                    self.trading.close_position(sym)
                    
                    # Get exit price from current position or last fill
                    exit_price = entry_price
                    if asset_type == "equity" and sym in positions:
                        exit_price = float(positions[sym].current_price)
                    
                    qty = 1
                    if asset_type == "equity":
                        qty = self.risk.qty_for(entry_price)
                    
                    pnl = (exit_price - entry_price) * qty
                    self.risk.record_close(pnl, datetime.now(ET).date())
                    send(
                        "⏱️ Time-based exit",
                        f"Closed {sym} ({asset_type}) after {self.cfg.max_hold_minutes} minutes",
                        "exit_win" if pnl > 0 else "exit_loss",
                        {
                            "Exit Price": f"${exit_price:.3f}", 
                            "PnL": f"${pnl:+.2f}", 
                            "Hold Time": f"{self.cfg.max_hold_minutes}m",
                            "Type": asset_type,
                        },
                    )
                except Exception as e:
                    log.error("Failed to close position %s: %s", sym, e)
                
                self.tracked_positions.pop(sym, None)

    def _sync_positions(self, positions):
        """Detect and record closed positions; clean up stale tracking."""
        current_syms = set(positions.keys())
        
        for sym in list(self.tracked_positions.keys()):
            entry_price, entered_at, order_id, asset_type = self.tracked_positions[sym]
            
            # Only equity symbols tracked here; options use dedicated tracking
            if asset_type == "equity":
                if sym not in current_syms and sym not in self._last_synced_positions:
                    # Position just closed
                    qty = self.risk.qty_for(entry_price)
                    
                    exit_price = entry_price
                    exit_reason = "bracket exit"
                    try:
                        orders = self.trading.get_orders(limit=50, status=OrderStatus.FILLED)
                        for o in orders:
                            if o.symbol == sym and o.id != order_id and o.filled_at:
                                exit_price = o.filled_avg_price
                                if o.order_class == "bracket":
                                    exit_reason = "take profit" if (
                                        (o.side == OrderSide.SELL and o.filled_avg_price > entry_price) or
                                        (o.side == OrderSide.BUY and o.filled_avg_price < entry_price)
                                    ) else "stop loss"
                                break
                    except Exception:
                        pass
                    
                    pnl = (exit_price - entry_price) * qty
                    self.risk.record_close(pnl, datetime.now(ET).date())
                    log.info("CLOSE %s via %s pnl=$%.2f (daily $%.2f)", 
                             sym, exit_reason, pnl, self.risk.daily_pnl)
                    
                    send(
                        f"{'✅' if pnl > 0 else '❌'} Exit {sym.upper()} ({exit_reason})",
                        f"Equity position closed",
                        "exit_win" if pnl > 0 else "exit_loss",
                        {"Exit": f"${exit_price:.2f}", "PnL": f"${pnl:+.2f}", "Reason": exit_reason},
                    )
                    
                    self.tracked_positions.pop(sym, None)
                elif sym not in current_syms:
                    self.tracked_positions.pop(sym, None)
        
        self._last_synced_positions = current_syms

    # ---------- main loop ----------
    def run(self):
        acct = self.trading.get_account()
        log.info("Connected. Account %s | equity $%s | paper=%s | options=%s",
                 acct.account_number, acct.equity, self.cfg.paper, self.cfg.enable_options)
        was_in_session = False
        session_start_time = None
        
        while True:
            try:
                if not self._in_session():
                    if was_in_session:
                        realized_pnl = self._calculate_realized_pnl(after=session_start_time)
                        send("📋 Daily recap",
                             f"Session over. Realized P&L: ${realized_pnl:+.2f} | Risk tracked: ${self.risk.daily_pnl:+.2f}",
                             "exit_win" if realized_pnl > 0 else "exit_loss",
                             {"Trades": self.risk._trade_count,
                              "Realized PnL": f"${realized_pnl:+.2f}",
                              "Halted": "Yes" if self.risk.halted else "No"})
                        was_in_session = False
                    log.info("Outside session %s–%s ET. Sleeping 60s.",
                             self.cfg.trade_start, self.cfg.trade_end)
                    time.sleep(60)
                    continue
                
                if not was_in_session:
                    was_in_session = True
                    session_start_time = datetime.now(ET)
                    send("🟢 Trading session started", 
                         f"Session active {self.cfg.trade_start}–{self.cfg.trade_end} ET",
                         "info",
                         {})

                try:
                    positions = self._open_positions()
                except Exception as e:
                    log.error("Failed to fetch positions: %s", e)
                    time.sleep(self.cfg.poll_seconds)
                    continue

                self._sync_positions(positions)
                self._check_time_exits(positions)

                today = datetime.now(ET).date()
                ok, why = self.risk.can_trade(len(self.tracked_positions), today)
                if not ok:
                    log.warning("No new trades: %s", why)
                    time.sleep(self.cfg.poll_seconds)
                    continue

                for sym in self.cfg.symbols:
                    if sym in positions or sym in self.tracked_positions:
                        continue
                    
                    try:
                        bars = self._todays_bars(sym)
                    except Exception as e:
                        log.error("Failed to fetch bars for %s: %s", sym, e)
                        continue
                    
                    if len(bars) < 10:
                        continue
                    
                    sig = generate_signal(bars, self.cfg)
                    if sig:
                        log.info("SIGNAL %s %s — %s", sym, sig.side.value, sig.reason)
                        try:
                            # Submit equity bracket order
                            self._submit_bracket(sym, sig.side)
                            
                            # Submit options order (call or put) on the same signal
                            if self.cfg.enable_options:
                                underlying_price = bars[-1].close
                                self._submit_option_order(sym, sig.side, underlying_price)
                            
                            # Re-check if we can trade after orders
                            ok, why = self.risk.can_trade(len(self.tracked_positions), today)
                            if not ok:
                                break
                        except Exception as e:
                            log.error("Failed to submit orders for %s: %s", sym, e)

                time.sleep(self.cfg.poll_seconds)
            except KeyboardInterrupt:
                log.info("Stopped by user.")
                break
            except Exception as e:
                log.exception("Loop error: %s", e)
                time.sleep(self.cfg.poll_seconds)

