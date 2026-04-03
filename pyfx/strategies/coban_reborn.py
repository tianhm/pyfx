"""CobanReborn — multi-timeframe confluence strategy.

Supports two entry modes:
  "full" (default): Requires three independent signals to align across timeframes:
    H1: SMA 4/9 crossover + MACD histogram zero-cross + RSI trendline break
    H2: RSI trendline break confirmation
    M1: Entry trigger (optionally with full M1 confluence)
  "trend_follow": Simplified trend-following approach:
    SMA 4/9 crossover as trigger, MACD histogram sign + RSI level as filters

Supports three exit modes:
  "fixed" (default): Fixed TP/SL in pips (spread-adjusted)
  "trailing": Trailing stop from best price with hard SL
  "atr": ATR-based dynamic TP/SL (adapts to volatility)

All modes include MACD reversal exit and session filtering.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.indicators import (
    AverageTrueRange,
    ExponentialMovingAverage,
    RelativeStrengthIndex,
    SimpleMovingAverage,
)
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.events import OrderFilled

from pyfx.strategies.base import PyfxStrategy, PyfxStrategyConfig


class CobanRebornConfig(PyfxStrategyConfig, frozen=True):
    """Configuration for the CobanReborn strategy."""

    # Entry/exit mode selection
    entry_mode: str = "trend_follow"  # "full" (original) | "trend_follow"
    exit_mode: str = "atr"            # "fixed" (original) | "trailing" | "atr"

    # Indicator periods
    sma_fast_period: int = 4
    sma_slow_period: int = 9
    macd_fast_period: int = 12
    macd_slow_period: int = 26
    macd_signal_period: int = 9
    rsi_period: int = 14

    # Fixed exit params
    take_profit_pips: int = 10
    stop_loss_pips: int = 30
    spread_pips: float = 1.5

    # MACD reversal exit
    macd_reversal_exit: bool = True
    macd_reversal_bars: int = 2

    # Trailing stop params
    trailing_stop_pips: int = 15

    # ATR exit params
    atr_period: int = 14
    atr_tp_multiplier: float = 2.0
    atr_sl_multiplier: float = 1.5

    # Session filter (0/24 = 24h trading; use 8/17 for EUR/GBP)
    session_start_hour: int = 0
    session_end_hour: int = 24

    # Signal window
    max_signal_window_seconds: int = 3600

    # Full-mode specific: double-confirm and M1
    double_confirm_enabled: bool = True
    double_confirm_window_seconds: int = 600
    double_confirm_min_gap_seconds: int = 300
    m1_confirm_enabled: bool = True

    # RSI params
    rsi_buffer_size: int = 100
    rsi_min_peak_diff: int = 2
    rsi_level_threshold: float = 0.50  # for trend_follow mode

    trade_size: Decimal = Decimal("100000")


# ---------------------------------------------------------------------------
# Pure helper: RSI trendline break detection
# ---------------------------------------------------------------------------

def _find_local_extrema(
    values: list[float],
    find_maxima: bool,
) -> list[int]:
    """Return indices of local maxima (or minima) in *values*.

    An extremum at index *i* means values[i] is strictly greater (or less)
    than both its neighbours.  The first and last indices are never returned.
    """
    extrema: list[int] = []
    for i in range(1, len(values) - 1):
        if find_maxima:
            if values[i] > values[i - 1] and values[i] > values[i + 1]:
                extrema.append(i)
        else:
            if values[i] < values[i - 1] and values[i] < values[i + 1]:
                extrema.append(i)
    return extrema


def detect_rsi_trendline_break(
    rsi_values: list[float],
    min_peak_diff: int,
) -> int:
    """Detect an RSI trendline break on the most recent value.

    Returns:
        +1  bullish break (RSI breaks *above* descending resistance)
        -1  bearish break (RSI breaks *below* ascending support)
         0  no break detected
    """
    if len(rsi_values) < 4:
        return 0

    buf = rsi_values[:-1]  # everything except the latest value
    latest = rsi_values[-1]

    # --- bullish: break above descending resistance (peaks) ---
    result = _check_break(buf, latest, min_peak_diff, bullish=True)
    if result != 0:
        return result

    # --- bearish: break below ascending support (troughs) ---
    return _check_break(buf, latest, min_peak_diff, bullish=False)


def _check_break(
    buf: list[float],
    latest: float,
    min_peak_diff: int,
    *,
    bullish: bool,
) -> int:
    """Check for a single direction of RSI trendline break."""
    extrema = _find_local_extrema(buf, find_maxima=bullish)
    if len(extrema) < 2:
        return 0

    # Sort by RSI value: highest first for peaks, lowest first for troughs
    sorted_extrema = sorted(extrema, key=lambda i: buf[i], reverse=bullish)

    for a_pos, idx_a in enumerate(sorted_extrema):
        for idx_b in sorted_extrema[a_pos + 1 :]:
            # Ensure idx_a is earlier than idx_b
            first, second = (idx_a, idx_b) if idx_a < idx_b else (idx_b, idx_a)

            if (second - first) < min_peak_diff:
                continue

            slope = (buf[second] - buf[first]) / (second - first)

            # Bullish: need descending resistance (negative slope)
            if bullish and slope >= 0:
                continue
            # Bearish: need ascending support (positive slope)
            if not bullish and slope <= 0:
                continue

            # Verify no intermediate values violate the trendline
            violated = False
            for k in range(first + 1, second):
                tl_val = buf[first] + slope * (k - first)
                if bullish and buf[k] > tl_val:
                    violated = True
                    break
                if not bullish and buf[k] < tl_val:
                    violated = True
                    break
            if violated:
                continue

            # Check the latest value against the trendline extended to the end
            tl_at_latest = buf[first] + slope * (len(buf) - first)
            if bullish and latest > tl_at_latest:
                return 1
            if not bullish and latest < tl_at_latest:
                return -1

    return 0


# ---------------------------------------------------------------------------
# Pure helper: MACD signal-line EMA
# ---------------------------------------------------------------------------

def compute_ema_step(prev_ema: float, value: float, alpha: float) -> float:
    """Single step of an exponential moving average."""
    return alpha * value + (1.0 - alpha) * prev_ema


# ---------------------------------------------------------------------------
# Signal timestamp helpers
# ---------------------------------------------------------------------------

_ZERO_TS: int = 0


def _ts_seconds(ts_ns: int) -> float:
    return ts_ns / 1_000_000_000


def _bar_hour_utc(ts_ns: int) -> int:
    """Extract the UTC hour (0-23) from a nanosecond timestamp."""
    seconds = ts_ns // 1_000_000_000
    return (seconds % 86400) // 3600


def _signals_within_window(
    timestamps: list[int],
    window_seconds: int,
) -> bool:
    """Check all timestamps are non-zero and within *window_seconds* of each other."""
    if any(t == _ZERO_TS for t in timestamps):
        return False
    lo = min(timestamps)
    hi = max(timestamps)
    return (_ts_seconds(hi) - _ts_seconds(lo)) <= window_seconds


def _is_fresh(ts_ns: int, now_ns: int, window_seconds: int) -> bool:
    return ts_ns != _ZERO_TS and (_ts_seconds(now_ns) - _ts_seconds(ts_ns)) <= window_seconds


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class CobanRebornStrategy(PyfxStrategy):
    """Multi-timeframe confluence strategy (CobanReborn).

    Entry modes (``entry_mode`` config):
      ``"full"``:  Requires SMA cross + MACD histogram cross + RSI trendline
          break on H1, RSI trendline break on H2, and optionally full M1
          confluence.
      ``"trend_follow"``:  SMA 4/9 cross as trigger with MACD histogram sign
          and RSI level (>/<0.50) as directional filters.  No H2 or M1 needed.

    Exit modes (``exit_mode`` config):
      ``"fixed"``:  Spread-adjusted fixed TP/SL in pips using bar high/low.
      ``"trailing"``:  Trailing stop from best price with a hard SL backstop.
      ``"atr"``:  ATR-based TP/SL captured at entry, adapts to volatility.

    All modes share MACD reversal exit (configurable) and session-hour filter.
    """

    def __init__(self, config: CobanRebornConfig) -> None:
        super().__init__(config)

        # Timeframe bar types — set in on_start once validated
        self._m1_bt: BarType | None = None
        self._h1_bt: BarType | None = None
        self._h2_bt: BarType | None = None

        # -- Indicators (created here, registered in on_start) ---------------
        # H1
        self._h1_sma_fast = SimpleMovingAverage(config.sma_fast_period)
        self._h1_sma_slow = SimpleMovingAverage(config.sma_slow_period)
        self._h1_ema_fast = ExponentialMovingAverage(config.macd_fast_period)
        self._h1_ema_slow = ExponentialMovingAverage(config.macd_slow_period)
        self._h1_rsi = RelativeStrengthIndex(config.rsi_period)
        self._h1_atr = AverageTrueRange(config.atr_period)

        # H2
        self._h2_rsi = RelativeStrengthIndex(config.rsi_period)

        # M1 (optional)
        self._m1_sma_fast = SimpleMovingAverage(config.sma_fast_period)
        self._m1_sma_slow = SimpleMovingAverage(config.sma_slow_period)
        self._m1_ema_fast = ExponentialMovingAverage(config.macd_fast_period)
        self._m1_ema_slow = ExponentialMovingAverage(config.macd_slow_period)
        self._m1_rsi = RelativeStrengthIndex(config.rsi_period)

        # -- MACD histogram state (manual signal-line EMA) -------------------
        self._macd_alpha: float = 2.0 / (config.macd_signal_period + 1)

        self._h1_macd_signal: float = 0.0
        self._h1_prev_hist: float = 0.0
        self._h1_macd_count: int = 0

        self._m1_macd_signal: float = 0.0
        self._m1_prev_hist: float = 0.0
        self._m1_macd_count: int = 0

        # -- RSI buffers -----------------------------------------------------
        self._h1_rsi_buf: list[float] = []
        self._h2_rsi_buf: list[float] = []
        self._m1_rsi_buf: list[float] = []

        # -- Previous SMA diff for crossover detection -----------------------
        self._h1_prev_sma_diff: float = 0.0
        self._m1_prev_sma_diff: float = 0.0

        # -- Signal timestamps (nanoseconds) ---------------------------------
        self._h1_sma_cross_ts: int = _ZERO_TS
        self._h1_sma_cross_dir: int = 0
        self._h1_macd_cross_ts: int = _ZERO_TS
        self._h1_macd_cross_dir: int = 0
        self._h1_rsi_cross_ts: int = _ZERO_TS
        self._h1_rsi_cross_dir: int = 0

        # Double-confirm tracking
        self._h1_macd_dc_first_ts: int = _ZERO_TS
        self._h1_macd_dc_dir: int = 0
        self._h1_rsi_dc_first_ts: int = _ZERO_TS
        self._h1_rsi_dc_dir: int = 0

        # H1 complete signal
        self._h1_complete_dir: int = 0
        self._h1_complete_ts: int = _ZERO_TS

        # H2 RSI
        self._h2_rsi_break_ts: int = _ZERO_TS
        self._h2_rsi_break_dir: int = 0

        # M1 signal state
        self._m1_sma_dir: int = 0
        self._m1_macd_dir: int = 0
        self._m1_rsi_dir: int = 0

        # Trade state
        self._entry_price: float = 0.0
        self._trade_direction: int = 0  # +1 long, -1 short
        self._pending_entry: bool = False
        self._pip_size: float = 0.0
        self._spread_cost: float = 0.0  # half-spread in price units
        self._macd_reversal_count: int = 0  # consecutive bars of hist decline

        # Trailing stop / ATR exit state
        self._best_price: float = 0.0
        self._entry_atr: float = 0.0

        # Current H1 indicator values (for trend_follow filters)
        self._h1_current_hist: float = 0.0
        self._h1_current_rsi: float = 0.0

    # -- Lifecycle -----------------------------------------------------------

    def on_start(self) -> None:
        cfg: CobanRebornConfig = self.config

        if len(cfg.extra_bar_types) != 2:  # noqa: PLR2004
            raise ValueError(
                "CobanReborn requires exactly 2 extra_bar_types (H1 and H2). "
                f"Got {len(cfg.extra_bar_types)}."
            )

        self._m1_bt = cfg.bar_type
        self._h1_bt = cfg.extra_bar_types[0]
        self._h2_bt = cfg.extra_bar_types[1]

        instrument = self.cache.instrument(cfg.instrument_id)
        if instrument is not None:
            self._pip_size = float(instrument.price_increment) * 10
        else:
            self._pip_size = 0.0001  # fallback for FX  # pragma: no cover
        self._spread_cost = cfg.spread_pips * self._pip_size

        # Register H1 indicators
        for ind in (
            self._h1_sma_fast, self._h1_sma_slow,
            self._h1_ema_fast, self._h1_ema_slow,
            self._h1_rsi, self._h1_atr,
        ):
            self.register_indicator_for_bars(self._h1_bt, ind)

        # Register H2 indicators
        self.register_indicator_for_bars(self._h2_bt, self._h2_rsi)

        # Register M1 indicators
        if cfg.m1_confirm_enabled:
            for ind in (
                self._m1_sma_fast, self._m1_sma_slow,
                self._m1_ema_fast, self._m1_ema_slow,
                self._m1_rsi,
            ):
                self.register_indicator_for_bars(self._m1_bt, ind)

        # Subscribe
        self.subscribe_bars(self._m1_bt)
        self.subscribe_bars(self._h1_bt)
        self.subscribe_bars(self._h2_bt)

    def on_bar(self, bar: Bar) -> None:
        if bar.bar_type == self._h1_bt:
            self._on_h1_bar(bar)
        elif bar.bar_type == self._h2_bt:
            self._on_h2_bar(bar)
        elif bar.bar_type == self._m1_bt:
            self._on_m1_bar(bar)

    def on_order_filled(self, event: OrderFilled) -> None:
        if not self._pending_entry:
            return  # Only set entry state on opening fills
        self._pending_entry = False
        self._entry_price = float(event.last_px)
        self._best_price = self._entry_price
        if self._h1_atr.initialized:
            self._entry_atr = self._h1_atr.value

    # -- H1 handler ----------------------------------------------------------

    def _on_h1_bar(self, bar: Bar) -> None:
        cfg: CobanRebornConfig = self.config
        ts = bar.ts_init

        # Wait for indicators to warm up
        if not self._h1_sma_fast.initialized or not self._h1_rsi.initialized:
            self._h1_prev_sma_diff = 0.0
            return

        # Track current RSI for trend_follow filter
        self._h1_current_rsi = self._h1_rsi.value

        # 1. SMA crossover
        sma_diff = self._h1_sma_fast.value - self._h1_sma_slow.value
        if self._h1_prev_sma_diff != 0.0 and sma_diff != 0.0:
            if self._h1_prev_sma_diff < 0 < sma_diff:
                self._h1_sma_cross_dir = 1
                self._h1_sma_cross_ts = ts
            elif self._h1_prev_sma_diff > 0 > sma_diff:
                self._h1_sma_cross_dir = -1
                self._h1_sma_cross_ts = ts
        self._h1_prev_sma_diff = sma_diff

        # 2. MACD histogram
        if self._h1_ema_fast.initialized and self._h1_ema_slow.initialized:
            macd_line = self._h1_ema_fast.value - self._h1_ema_slow.value
            self._h1_macd_count += 1
            if self._h1_macd_count == 1:
                self._h1_macd_signal = macd_line
            else:
                self._h1_macd_signal = compute_ema_step(
                    self._h1_macd_signal, macd_line, self._macd_alpha,
                )
            hist = macd_line - self._h1_macd_signal
            self._h1_current_hist = hist

            # MACD reversal exit — close after sustained histogram decline
            if (
                cfg.macd_reversal_exit
                and not self.flat()
                and self._h1_prev_hist != 0.0
            ):
                fading = (
                    (self._trade_direction == 1 and hist < self._h1_prev_hist)
                    or (self._trade_direction == -1 and hist > self._h1_prev_hist)
                )
                if fading:
                    self._macd_reversal_count += 1
                    if self._macd_reversal_count >= cfg.macd_reversal_bars:
                        self._h1_prev_hist = hist
                        self.close_all()
                        self._reset_signals()
                        return
                else:
                    self._macd_reversal_count = 0

            # MACD zero-line crossover for entry signals
            if self._h1_prev_hist != 0.0 and hist != 0.0:
                if self._h1_prev_hist < 0 < hist:
                    self._apply_signal("h1_macd", 1, ts, cfg)
                elif self._h1_prev_hist > 0 > hist:
                    self._apply_signal("h1_macd", -1, ts, cfg)
            self._h1_prev_hist = hist

        # 3. RSI trendline break
        self._h1_rsi_buf.append(self._h1_rsi.value)
        if len(self._h1_rsi_buf) > cfg.rsi_buffer_size:
            self._h1_rsi_buf = self._h1_rsi_buf[-cfg.rsi_buffer_size :]
        rsi_dir = detect_rsi_trendline_break(self._h1_rsi_buf, cfg.rsi_min_peak_diff)
        if rsi_dir != 0:
            self._apply_signal("h1_rsi", rsi_dir, ts, cfg)

        # 4. Check H1 complete signal
        self._check_h1_complete(ts, cfg)

    # -- H2 handler ----------------------------------------------------------

    def _on_h2_bar(self, bar: Bar) -> None:
        cfg: CobanRebornConfig = self.config
        if not self._h2_rsi.initialized:
            return

        self._h2_rsi_buf.append(self._h2_rsi.value)
        if len(self._h2_rsi_buf) > cfg.rsi_buffer_size:
            self._h2_rsi_buf = self._h2_rsi_buf[-cfg.rsi_buffer_size :]

        rsi_dir = detect_rsi_trendline_break(self._h2_rsi_buf, cfg.rsi_min_peak_diff)
        if rsi_dir != 0:
            self._h2_rsi_break_dir = rsi_dir
            self._h2_rsi_break_ts = bar.ts_init

    # -- M1 handler ----------------------------------------------------------

    def _on_m1_bar(self, bar: Bar) -> None:
        cfg: CobanRebornConfig = self.config
        ts = bar.ts_init

        # Update M1 indicators state (full mode only)
        if cfg.m1_confirm_enabled and self._m1_sma_fast.initialized:
            self._update_m1_signals(ts, cfg)

        # Exit logic (if in a trade)
        if not self.flat():
            if self._entry_price > 0.0 and self._pip_size > 0.0:
                if self._check_exit(bar, cfg):
                    return
            return  # already in a trade, don't enter again

        # Session filter — only enter during configurable trading hours (UTC)
        bar_hour = _bar_hour_utc(ts)
        if bar_hour < cfg.session_start_hour or bar_hour >= cfg.session_end_hour:
            return

        # Entry check — mode-dependent
        direction = self._get_entry_signal(ts, cfg)
        if direction == 0:
            return

        self._trade_direction = direction
        self._pending_entry = True
        if direction == 1:
            self.market_buy()
        else:
            self.market_sell()
        self._reset_signals()

    # -- Entry logic (mode-dependent) ----------------------------------------

    def _get_entry_signal(self, ts: int, cfg: CobanRebornConfig) -> int:
        """Return +1 (long), -1 (short), or 0 (no entry) based on entry_mode."""
        if cfg.entry_mode == "trend_follow":
            return self._entry_trend_follow(ts, cfg)
        return self._entry_full(ts, cfg)

    def _entry_full(self, ts: int, cfg: CobanRebornConfig) -> int:
        """Original: H1 3-signal + H2 RSI + optional M1 confirmation."""
        if self._h1_complete_ts == _ZERO_TS:
            return 0
        if not _is_fresh(self._h1_complete_ts, ts, cfg.max_signal_window_seconds):
            return 0
        if not _is_fresh(self._h2_rsi_break_ts, ts, cfg.max_signal_window_seconds):
            return 0
        if self._h2_rsi_break_dir != self._h1_complete_dir:
            return 0

        direction = self._h1_complete_dir

        # M1 confirmation
        if cfg.m1_confirm_enabled:
            if not self._m1_sma_fast.initialized:
                return 0
            if not (
                self._m1_sma_dir == direction
                and self._m1_macd_dir == direction
                and self._m1_rsi_dir == direction
            ):
                return 0

        return direction

    def _entry_trend_follow(self, ts: int, cfg: CobanRebornConfig) -> int:
        """SMA cross as trigger, MACD histogram sign + RSI level as filters."""
        if self._h1_sma_cross_dir == 0:
            return 0
        if not _is_fresh(self._h1_sma_cross_ts, ts, cfg.max_signal_window_seconds):
            return 0

        direction = self._h1_sma_cross_dir
        threshold = cfg.rsi_level_threshold

        # MACD histogram must be positive for longs, negative for shorts
        if direction == 1 and self._h1_current_hist <= 0:
            return 0
        if direction == -1 and self._h1_current_hist >= 0:
            return 0

        # RSI filter
        if direction == 1 and self._h1_current_rsi <= threshold:
            return 0
        if direction == -1 and self._h1_current_rsi >= threshold:
            return 0

        return direction

    # -- Exit logic (mode-dependent) -----------------------------------------

    def _check_exit(self, bar: Bar, cfg: CobanRebornConfig) -> bool:
        """Check exit conditions using bar high/low for realistic fills."""
        high = float(bar.high)
        low = float(bar.low)

        mode = cfg.exit_mode
        if mode == "trailing":
            return self._exit_trailing(high, low, cfg)
        elif mode == "atr":
            return self._exit_atr(high, low, cfg)
        return self._exit_fixed(high, low, cfg)

    def _exit_fixed(
        self, high: float, low: float, cfg: CobanRebornConfig,
    ) -> bool:
        """Fixed TP/SL exit using bar high/low."""
        spread = self._spread_cost
        tp_distance = cfg.take_profit_pips * self._pip_size - spread
        sl_distance = cfg.stop_loss_pips * self._pip_size - spread

        if self._trade_direction == 1:
            if (high - self._entry_price) >= tp_distance:
                self.close_all()
                self._reset_signals()
                return True
            if (self._entry_price - low) >= sl_distance:
                self.close_all()
                self._reset_signals()
                return True
        elif self._trade_direction == -1:
            if (self._entry_price - low) >= tp_distance:
                self.close_all()
                self._reset_signals()
                return True
            if (high - self._entry_price) >= sl_distance:
                self.close_all()
                self._reset_signals()
                return True
        return False

    def _exit_trailing(
        self, high: float, low: float, cfg: CobanRebornConfig,
    ) -> bool:
        """Trailing stop exit using bar high/low."""
        trail_distance = cfg.trailing_stop_pips * self._pip_size
        sl_distance = cfg.stop_loss_pips * self._pip_size - self._spread_cost

        if self._trade_direction == 1:
            if high > self._best_price:
                self._best_price = high
            if self._best_price - low >= trail_distance:
                self.close_all()
                self._reset_signals()
                return True
            if self._entry_price - low >= sl_distance:
                self.close_all()
                self._reset_signals()
                return True
        elif self._trade_direction == -1:
            if low < self._best_price:
                self._best_price = low
            if high - self._best_price >= trail_distance:
                self.close_all()
                self._reset_signals()
                return True
            if high - self._entry_price >= sl_distance:
                self.close_all()
                self._reset_signals()
                return True
        return False

    def _exit_atr(
        self, high: float, low: float, cfg: CobanRebornConfig,
    ) -> bool:
        """ATR-based TP/SL exit using bar high/low."""
        if self._entry_atr <= 0.0:
            return self._exit_fixed(high, low, cfg)  # pragma: no cover

        spread = self._spread_cost
        tp_distance = self._entry_atr * cfg.atr_tp_multiplier - spread
        sl_distance = self._entry_atr * cfg.atr_sl_multiplier - spread

        if self._trade_direction == 1:
            if (high - self._entry_price) >= tp_distance:
                self.close_all()
                self._reset_signals()
                return True
            if (self._entry_price - low) >= sl_distance:
                self.close_all()
                self._reset_signals()
                return True
        elif self._trade_direction == -1:
            if (self._entry_price - low) >= tp_distance:
                self.close_all()
                self._reset_signals()
                return True
            if (high - self._entry_price) >= sl_distance:
                self.close_all()
                self._reset_signals()
                return True
        return False

    # -- M1 indicator state --------------------------------------------------

    def _update_m1_signals(self, ts: int, cfg: CobanRebornConfig) -> None:
        # SMA direction
        sma_diff = self._m1_sma_fast.value - self._m1_sma_slow.value
        if self._m1_prev_sma_diff != 0.0 and sma_diff != 0.0:
            if self._m1_prev_sma_diff < 0 < sma_diff:
                self._m1_sma_dir = 1
            elif self._m1_prev_sma_diff > 0 > sma_diff:
                self._m1_sma_dir = -1
        self._m1_prev_sma_diff = sma_diff

        # MACD histogram direction
        if self._m1_ema_fast.initialized and self._m1_ema_slow.initialized:
            macd_line = self._m1_ema_fast.value - self._m1_ema_slow.value
            self._m1_macd_count += 1
            if self._m1_macd_count == 1:
                self._m1_macd_signal = macd_line
            else:
                self._m1_macd_signal = compute_ema_step(
                    self._m1_macd_signal, macd_line, self._macd_alpha,
                )
            hist = macd_line - self._m1_macd_signal
            if self._m1_prev_hist != 0.0 and hist != 0.0:
                if self._m1_prev_hist < 0 < hist:
                    self._m1_macd_dir = 1
                elif self._m1_prev_hist > 0 > hist:
                    self._m1_macd_dir = -1
            self._m1_prev_hist = hist

        # RSI trendline break
        if self._m1_rsi.initialized:
            self._m1_rsi_buf.append(self._m1_rsi.value)
            if len(self._m1_rsi_buf) > cfg.rsi_buffer_size:
                self._m1_rsi_buf = self._m1_rsi_buf[-cfg.rsi_buffer_size :]
            rsi_dir = detect_rsi_trendline_break(self._m1_rsi_buf, cfg.rsi_min_peak_diff)
            if rsi_dir != 0:
                self._m1_rsi_dir = rsi_dir

    # -- Signal helpers ------------------------------------------------------

    def _apply_signal(
        self,
        signal_name: str,
        direction: int,
        ts: int,
        cfg: CobanRebornConfig,
    ) -> None:
        """Apply a signal, handling double-confirm if enabled."""
        if cfg.double_confirm_enabled:
            dc_first_attr = f"_{signal_name}_dc_first_ts"
            dc_dir_attr = f"_{signal_name}_dc_dir"
            first_ts = getattr(self, dc_first_attr)
            first_dir = getattr(self, dc_dir_attr)

            if first_dir == direction and first_ts != _ZERO_TS:
                gap = _ts_seconds(ts) - _ts_seconds(first_ts)
                if (
                    gap >= cfg.double_confirm_min_gap_seconds
                    and gap <= cfg.double_confirm_window_seconds
                ):
                    # Double confirmed!
                    setattr(self, f"_{signal_name}_cross_dir", direction)
                    setattr(self, f"_{signal_name}_cross_ts", ts)
                    setattr(self, dc_first_attr, _ZERO_TS)
                    setattr(self, dc_dir_attr, 0)
                    return
                if gap > cfg.double_confirm_window_seconds:
                    # Expired, start fresh
                    setattr(self, dc_first_attr, ts)
                    setattr(self, dc_dir_attr, direction)
                    return
                # Gap too small, keep waiting
                return
            # New signal or direction changed
            setattr(self, dc_first_attr, ts)
            setattr(self, dc_dir_attr, direction)
        else:
            setattr(self, f"_{signal_name}_cross_dir", direction)
            setattr(self, f"_{signal_name}_cross_ts", ts)

    def _check_h1_complete(self, ts: int, cfg: CobanRebornConfig) -> None:
        """Check if all 3 H1 signals agree and are within the time window."""
        sma_dir = self._h1_sma_cross_dir
        macd_dir = self._h1_macd_cross_dir
        rsi_dir = self._h1_rsi_cross_dir

        if sma_dir == 0 or macd_dir == 0 or rsi_dir == 0:
            return
        if not (sma_dir == macd_dir == rsi_dir):
            return

        if _signals_within_window(
            [self._h1_sma_cross_ts, self._h1_macd_cross_ts, self._h1_rsi_cross_ts],
            cfg.max_signal_window_seconds,
        ):
            self._h1_complete_dir = sma_dir
            self._h1_complete_ts = ts

    def _reset_signals(self) -> None:
        """Reset all signal state after entry or exit."""
        self._h1_sma_cross_ts = _ZERO_TS
        self._h1_sma_cross_dir = 0
        self._h1_macd_cross_ts = _ZERO_TS
        self._h1_macd_cross_dir = 0
        self._h1_rsi_cross_ts = _ZERO_TS
        self._h1_rsi_cross_dir = 0
        self._h1_macd_dc_first_ts = _ZERO_TS
        self._h1_macd_dc_dir = 0
        self._h1_rsi_dc_first_ts = _ZERO_TS
        self._h1_rsi_dc_dir = 0
        self._h1_complete_dir = 0
        self._h1_complete_ts = _ZERO_TS
        self._h2_rsi_break_ts = _ZERO_TS
        self._h2_rsi_break_dir = 0
        self._m1_sma_dir = 0
        self._m1_macd_dir = 0
        self._m1_rsi_dir = 0
        self._macd_reversal_count = 0
        self._best_price = 0.0
        self._entry_atr = 0.0
