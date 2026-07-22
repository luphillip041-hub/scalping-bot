"""Risk management: position sizing, daily loss circuit breaker, trade limits."""
from dataclasses import dataclass, field
from datetime import date


@dataclass
class RiskManager:
    max_daily_loss_usd: float
    max_trades_per_day: int
    max_positions: int
    position_size_usd: float
    _day: date | None = None
    _realized_pnl: float = 0.0
    _trade_count: int = 0
    _halted: bool = False

    def _roll_day(self, today: date):
        if self._day != today:
            self._day = today
            self._realized_pnl = 0.0
            self._trade_count = 0
            self._halted = False

    def record_close(self, pnl: float, today: date):
        self._roll_day(today)
        self._realized_pnl += pnl
        if self._realized_pnl <= -abs(self.max_daily_loss_usd):
            self._halted = True

    def can_trade(self, open_positions: int, today: date) -> tuple[bool, str]:
        self._roll_day(today)
        if self._halted:
            return False, f"halted: daily loss ${self._realized_pnl:.2f}"
        if self._trade_count >= self.max_trades_per_day:
            return False, "halted: max trades/day reached"
        if open_positions >= self.max_positions:
            return False, "halted: max positions reached"
        return True, "ok"

    def register_trade(self, today: date):
        self._roll_day(today)
        self._trade_count += 1

    def qty_for(self, price: float) -> int:
        return max(1, int(self.position_size_usd // price))

    @property
    def daily_pnl(self) -> float:
        return self._realized_pnl

    @property
    def halted(self) -> bool:
        return self._halted
