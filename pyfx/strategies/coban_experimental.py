"""CobanExperimental — testbed for CobanReborn entry/exit variations.

This strategy extends CobanReborn with configurable entry and exit modes
to enable rapid A/B testing of different signal combinations and exit strategies.

Entry modes:
  "full"         — Original: H1 (SMA+MACD+RSI) + H2 RSI + M1 full
  "relaxed"      — H1 (SMA+MACD+RSI) + H2 RSI, no M1/double-confirm
  "2of3"         — Any 2 of 3 H1 signals + H2 RSI
  "no_h2"        — H1 (SMA+MACD+RSI) only, no H2 confirmation
  "sma_macd"     — H1 SMA cross + MACD histogram cross only
  "rsi_level"    — H1 SMA cross + MACD cross + RSI level filter (>0.5/<0.5)
  "trend_follow" — H1 SMA cross as trigger, MACD>0 + RSI>0.5 as filters

Exit modes:
  "fixed"        — Fixed TP/SL in pips (original)
  "trailing"     — Trailing stop (no fixed TP)
  "atr"          — ATR-based TP/SL
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
from pyfx.strategies.coban_reborn import (
    _ZERO_TS,
    _bar_hour_utc,
    _is_fresh,
    _signals_within_window,
    compute_ema_step,
    detect_rsi_trendline_break,
)


class CobanExperimentalConfig(PyfxStrategyConfig, frozen=True):
    """Configuration for the CobanExperimental strategy."""

    # Entry mode
    entry_mode: str = "relaxed"

    # Exit mode
    exit_mode: str = "fixed"

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

    # Session filter
    session_start_hour: int = 8
    session_end_hour: int = 17

    # Signal window
    max_signal_window_seconds: int = 3600

    # Filter staleness window — how long MACD/RSI filter values remain valid
    # in trend_follow mode (default: 2 H1 bars = 7200s)
    filter_staleness_seconds: int = 7200

    # RSI trendline params
    rsi_buffer_size: int = 100
    rsi_min_peak_diff: int = 2

    # RSI level filter threshold (for "rsi_level" mode)
    rsi_level_threshold: float = 0.50

    # Entry timing: defer entry to next bar open (more realistic)
    next_bar_entry: bool = False

    trade_size: Decimal = Decimal("100000")


_VALID_ENTRY_MODES = frozenset({
    "full", "relaxed", "2of3", "no_h2", "sma_macd", "rsi_level", "trend_follow",
})
_VALID_EXIT_MODES = frozenset({"fixed", "trailing", "atr"})


class CobanExperimentalStrategy(PyfxStrategy):
    """Experimental multi-timeframe strategy with configurable entry/exit modes."""

    def __init__(self, config: CobanExperimentalConfig) -> None:
        super().__init__(config)

        # Validate config
        if config.entry_mode not in _VALID_ENTRY_MODES:
            raise ValueError(
                f"Unknown entry_mode '{config.entry_mode}'. "
                f"Valid: {sorted(_VALID_ENTRY_MODES)}"
            )
        if config.exit_mode not in _VALID_EXIT_MODES:
            raise ValueError(
                f"Unknown exit_mode '{config.exit_mode}'. "
                f"Valid: {sorted(_VALID_EXIT_MODES)}"
            )
        if config.take_profit_pips <= 0:
            raise ValueError("take_profit_pips must be positive")
        if config.stop_loss_pips <= 0:
            raise ValueError("stop_loss_pips must be positive")
        if config.atr_tp_multiplier <= 0:
            raise ValueError("atr_tp_multiplier must be positive")
        if config.atr_sl_multiplier <= 0:
            raise ValueError("atr_sl_multiplier must be positive")
        if config.session_start_hour >= config.session_end_hour:
            raise ValueError(
                f"session_start_hour ({config.session_start_hour}) must be "
                f"< session_end_hour ({config.session_end_hour})"
            )

        self._m1_bt: BarType | None = None
        self._h1_bt: BarType | None = None
        self._h2_bt: BarType | None = None

        # H1 indicators
        self._h1_sma_fast = SimpleMovingAverage(config.sma_fast_period)
        self._h1_sma_slow = SimpleMovingAverage(config.sma_slow_period)
        self._h1_ema_fast = ExponentialMovingAverage(config.macd_fast_period)
        self._h1_ema_slow = ExponentialMovingAverage(config.macd_slow_period)
        self._h1_rsi = RelativeStrengthIndex(config.rsi_period)
        self._h1_atr = AverageTrueRange(config.atr_period)

        # H2 indicators
        self._h2_rsi = RelativeStrengthIndex(config.rsi_period)

        # MACD state
        self._macd_alpha: float = 2.0 / (config.macd_signal_period + 1)
        self._h1_macd_signal: float = 0.0
        self._h1_prev_hist: float = 0.0
        self._h1_macd_count: int = 0

        # RSI buffers
        self._h1_rsi_buf: list[float] = []
        self._h2_rsi_buf: list[float] = []

        # SMA crossover tracking
        self._h1_prev_sma_diff: float = 0.0

        # H1 signal timestamps and directions
        self._h1_sma_cross_ts: int = _ZERO_TS
        self._h1_sma_cross_dir: int = 0
        self._h1_macd_cross_ts: int = _ZERO_TS
        self._h1_macd_cross_dir: int = 0
        self._h1_rsi_cross_ts: int = _ZERO_TS
        self._h1_rsi_cross_dir: int = 0

        # H1 complete signal
        self._h1_complete_dir: int = 0
        self._h1_complete_ts: int = _ZERO_TS

        # H2 RSI
        self._h2_rsi_break_ts: int = _ZERO_TS
        self._h2_rsi_break_dir: int = 0

        # Trade state
        self._entry_price: float = 0.0
        self._trade_direction: int = 0
        self._pip_size: float = 0.0
        self._spread_cost: float = 0.0
        self._macd_reversal_count: int = 0

        # Trailing stop state
        self._best_price: float = 0.0

        # ATR state
        self._entry_atr: float = 0.0

        # Current H1 MACD histogram and RSI (for filter modes)
        self._h1_current_hist: float = 0.0
        self._h1_current_rsi: float = 0.0
        self._h1_hist_ts: int = _ZERO_TS
        self._h1_rsi_ts: int = _ZERO_TS

        # Deferred entry (next_bar_entry mode)
        self._deferred_direction: int = 0

    def on_start(self) -> None:
        cfg: CobanExperimentalConfig = self.config

        # Some entry modes don't need H2, but we still require the extra bar types
        # for compatibility with the runner
        if len(cfg.extra_bar_types) < 2:  # noqa: PLR2004
            raise ValueError(
                "CobanExperimental requires at least 2 extra_bar_types (H1 and H2). "
                f"Got {len(cfg.extra_bar_types)}."
            )

        self._m1_bt = cfg.bar_type
        self._h1_bt = cfg.extra_bar_types[0]
        self._h2_bt = cfg.extra_bar_types[1]

        instrument = self.cache.instrument(cfg.instrument_id)
        if instrument is not None:
            self._pip_size = float(instrument.price_increment) * 10
        else:
            self._pip_size = 0.0001  # pragma: no cover
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
        # Only set entry state when opening a new position, not on close fills
        if self._trade_direction == 0:
            return
        if not self.flat():
            # We just opened a position — set entry tracking state
            self._entry_price = float(event.last_px)
            self._best_price = self._entry_price
            if self._h1_atr.initialized:
                self._entry_atr = self._h1_atr.value

    # -- H1 handler ----------------------------------------------------------

    def _on_h1_bar(self, bar: Bar) -> None:
        cfg: CobanExperimentalConfig = self.config
        ts = bar.ts_init

        if not self._h1_sma_fast.initialized or not self._h1_rsi.initialized:
            self._h1_prev_sma_diff = 0.0
            return

        # Track current RSI for filter modes
        self._h1_current_rsi = self._h1_rsi.value
        self._h1_rsi_ts = ts

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
            self._h1_hist_ts = ts

            # MACD reversal exit
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

            # MACD zero-line crossover
            if self._h1_prev_hist != 0.0 and hist != 0.0:
                if self._h1_prev_hist < 0 < hist:
                    self._h1_macd_cross_dir = 1
                    self._h1_macd_cross_ts = ts
                elif self._h1_prev_hist > 0 > hist:
                    self._h1_macd_cross_dir = -1
                    self._h1_macd_cross_ts = ts
            self._h1_prev_hist = hist

        # 3. RSI trendline break
        self._h1_rsi_buf.append(self._h1_rsi.value)
        if len(self._h1_rsi_buf) > cfg.rsi_buffer_size:
            self._h1_rsi_buf = self._h1_rsi_buf[-cfg.rsi_buffer_size:]
        rsi_dir = detect_rsi_trendline_break(self._h1_rsi_buf, cfg.rsi_min_peak_diff)
        if rsi_dir != 0:
            self._h1_rsi_cross_dir = rsi_dir
            self._h1_rsi_cross_ts = ts

        # 4. Check H1 complete signal (mode-dependent)
        self._check_h1_complete(ts, cfg)

    # -- H2 handler ----------------------------------------------------------

    def _on_h2_bar(self, bar: Bar) -> None:
        cfg: CobanExperimentalConfig = self.config
        if not self._h2_rsi.initialized:
            return

        self._h2_rsi_buf.append(self._h2_rsi.value)
        if len(self._h2_rsi_buf) > cfg.rsi_buffer_size:
            self._h2_rsi_buf = self._h2_rsi_buf[-cfg.rsi_buffer_size:]

        rsi_dir = detect_rsi_trendline_break(self._h2_rsi_buf, cfg.rsi_min_peak_diff)
        if rsi_dir != 0:
            self._h2_rsi_break_dir = rsi_dir
            self._h2_rsi_break_ts = bar.ts_init

    # -- M1 handler ----------------------------------------------------------

    def _on_m1_bar(self, bar: Bar) -> None:
        cfg: CobanExperimentalConfig = self.config
        ts = bar.ts_init

        # Execute deferred entry from previous bar (next_bar_entry mode)
        if self._deferred_direction != 0 and self.flat():
            self._execute_entry(self._deferred_direction)
            self._deferred_direction = 0
            return

        self._deferred_direction = 0  # clear stale deferred if now in trade

        # Exit logic (if in a trade)
        if not self.flat():
            if self._check_exit(bar, cfg):
                return
            return  # don't enter while in a trade

        # Session filter
        bar_hour = _bar_hour_utc(ts)
        if bar_hour < cfg.session_start_hour or bar_hour >= cfg.session_end_hour:
            return

        # Entry check — depends on entry_mode
        direction = self._get_entry_signal(ts, cfg)
        if direction == 0:
            return

        if cfg.next_bar_entry:
            self._deferred_direction = direction
            self._reset_signals()
        else:
            self._execute_entry(direction)
            self._reset_signals()

    def _execute_entry(self, direction: int) -> None:
        """Submit a market order for the given direction."""
        self._trade_direction = direction
        if direction == 1:
            self.market_buy()
        else:
            self.market_sell()

    # -- Entry logic (mode-dependent) ----------------------------------------

    def _get_entry_signal(self, ts: int, cfg: CobanExperimentalConfig) -> int:
        """Return +1 (long), -1 (short), or 0 (no entry) based on entry_mode."""
        mode = cfg.entry_mode

        if mode == "full":
            return self._entry_full(ts, cfg)
        elif mode == "relaxed":
            return self._entry_relaxed(ts, cfg)
        elif mode == "2of3":
            return self._entry_2of3(ts, cfg)
        elif mode == "no_h2":
            return self._entry_no_h2(ts, cfg)
        elif mode == "sma_macd":
            return self._entry_sma_macd(ts, cfg)
        elif mode == "rsi_level":
            return self._entry_rsi_level(ts, cfg)
        elif mode == "trend_follow":
            return self._entry_trend_follow(ts, cfg)
        return 0

    def _entry_full(self, ts: int, cfg: CobanExperimentalConfig) -> int:
        """Alias for relaxed — experimental drops M1/double-confirm from CobanReborn."""
        return self._entry_relaxed(ts, cfg)

    def _entry_relaxed(self, ts: int, cfg: CobanExperimentalConfig) -> int:
        """H1 3-signal + H2 RSI, no M1 or double-confirm."""
        if self._h1_complete_ts == _ZERO_TS:
            return 0
        if not _is_fresh(self._h1_complete_ts, ts, cfg.max_signal_window_seconds):
            return 0
        if not _is_fresh(self._h2_rsi_break_ts, ts, cfg.max_signal_window_seconds):
            return 0
        if self._h2_rsi_break_dir != self._h1_complete_dir:
            return 0
        return self._h1_complete_dir

    def _entry_2of3(self, ts: int, cfg: CobanExperimentalConfig) -> int:
        """Any 2 of 3 H1 signals must agree + H2 RSI."""
        window = cfg.max_signal_window_seconds
        signals: list[tuple[int, int]] = []  # (direction, timestamp)

        if self._h1_sma_cross_dir != 0 and _is_fresh(self._h1_sma_cross_ts, ts, window):
            signals.append((self._h1_sma_cross_dir, self._h1_sma_cross_ts))
        if self._h1_macd_cross_dir != 0 and _is_fresh(self._h1_macd_cross_ts, ts, window):
            signals.append((self._h1_macd_cross_dir, self._h1_macd_cross_ts))
        if self._h1_rsi_cross_dir != 0 and _is_fresh(self._h1_rsi_cross_ts, ts, window):
            signals.append((self._h1_rsi_cross_dir, self._h1_rsi_cross_ts))

        if len(signals) < 2:  # noqa: PLR2004
            return 0

        # Check if at least 2 signals agree on direction
        for direction in (1, -1):
            matching = [s for s in signals if s[0] == direction]
            if len(matching) >= 2:  # noqa: PLR2004
                # Check H2
                if not _is_fresh(self._h2_rsi_break_ts, ts, window):
                    return 0
                if self._h2_rsi_break_dir != direction:
                    return 0
                return direction
        return 0

    def _entry_no_h2(self, ts: int, cfg: CobanExperimentalConfig) -> int:
        """H1 3-signal confluence only, no H2 confirmation."""
        if self._h1_complete_ts == _ZERO_TS:
            return 0
        if not _is_fresh(self._h1_complete_ts, ts, cfg.max_signal_window_seconds):
            return 0
        return self._h1_complete_dir

    def _entry_sma_macd(self, ts: int, cfg: CobanExperimentalConfig) -> int:
        """Only H1 SMA cross + MACD cross (no RSI, no H2)."""
        window = cfg.max_signal_window_seconds
        if self._h1_sma_cross_dir == 0 or self._h1_macd_cross_dir == 0:
            return 0
        if self._h1_sma_cross_dir != self._h1_macd_cross_dir:
            return 0
        if not _signals_within_window(
            [self._h1_sma_cross_ts, self._h1_macd_cross_ts], window,
        ):
            return 0
        return self._h1_sma_cross_dir

    def _entry_rsi_level(self, ts: int, cfg: CobanExperimentalConfig) -> int:
        """SMA cross + MACD cross + RSI level filter (not trendline)."""
        window = cfg.max_signal_window_seconds
        if self._h1_sma_cross_dir == 0 or self._h1_macd_cross_dir == 0:
            return 0
        if self._h1_sma_cross_dir != self._h1_macd_cross_dir:
            return 0
        if not _signals_within_window(
            [self._h1_sma_cross_ts, self._h1_macd_cross_ts], window,
        ):
            return 0

        # Reject stale RSI values
        if not _is_fresh(self._h1_rsi_ts, ts, cfg.filter_staleness_seconds):
            return 0

        direction = self._h1_sma_cross_dir
        threshold = cfg.rsi_level_threshold
        if direction == 1 and self._h1_current_rsi <= threshold:
            return 0
        if direction == -1 and self._h1_current_rsi >= threshold:
            return 0
        return direction

    def _entry_trend_follow(self, ts: int, cfg: CobanExperimentalConfig) -> int:
        """SMA cross as trigger, MACD histogram sign + RSI level as filters."""
        window = cfg.max_signal_window_seconds
        if self._h1_sma_cross_dir == 0:
            return 0
        if not _is_fresh(self._h1_sma_cross_ts, ts, window):
            return 0

        # Filter staleness — reject stale MACD/RSI values
        stale_window = cfg.filter_staleness_seconds
        if not _is_fresh(self._h1_hist_ts, ts, stale_window):
            return 0
        if not _is_fresh(self._h1_rsi_ts, ts, stale_window):
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

    def _check_exit(self, bar: Bar, cfg: CobanExperimentalConfig) -> bool:
        """Check exit conditions using bar high/low for realistic intra-bar fills."""
        if self._entry_price <= 0.0 or self._pip_size <= 0.0:
            return False

        high = float(bar.high)
        low = float(bar.low)

        mode = cfg.exit_mode
        if mode == "trailing":
            return self._exit_trailing(high, low, cfg)
        elif mode == "atr":
            return self._exit_atr(high, low, cfg)
        else:  # "fixed"
            return self._exit_fixed(high, low, cfg)

    def _exit_fixed(self, high: float, low: float, cfg: CobanExperimentalConfig) -> bool:
        """Fixed TP/SL exit using bar high/low."""
        spread = self._spread_cost
        tp_distance = cfg.take_profit_pips * self._pip_size - spread
        sl_distance = cfg.stop_loss_pips * self._pip_size + spread

        if self._trade_direction == 1:
            # Long: TP hit if high reaches target, SL hit if low drops to stop
            if (high - self._entry_price) >= tp_distance:
                self.close_all()
                self._reset_signals()
                return True
            if (self._entry_price - low) >= sl_distance:
                self.close_all()
                self._reset_signals()
                return True
        elif self._trade_direction == -1:
            # Short: TP hit if low drops to target, SL hit if high rises to stop
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
        self, high: float, low: float, cfg: CobanExperimentalConfig,
    ) -> bool:
        """Trailing stop exit using bar high/low."""
        trail_distance = cfg.trailing_stop_pips * self._pip_size
        sl_distance = cfg.stop_loss_pips * self._pip_size + self._spread_cost

        if self._trade_direction == 1:
            # Update best price from high
            if high > self._best_price:
                self._best_price = high
            # Trail from best price using low
            if self._best_price - low >= trail_distance:
                self.close_all()
                self._reset_signals()
                return True
            # Hard stop loss
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

    def _exit_atr(self, high: float, low: float, cfg: CobanExperimentalConfig) -> bool:
        """ATR-based TP/SL exit using bar high/low."""
        if self._entry_atr <= 0.0:
            return self._exit_fixed(high, low, cfg)  # fallback

        spread = self._spread_cost
        tp_distance = self._entry_atr * cfg.atr_tp_multiplier - spread
        sl_distance = self._entry_atr * cfg.atr_sl_multiplier + spread

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

    # -- H1 complete check (mode-aware) --------------------------------------

    def _check_h1_complete(self, ts: int, cfg: CobanExperimentalConfig) -> None:
        """Check if H1 signals are complete (for modes that use all 3)."""
        mode = cfg.entry_mode
        # For sma_macd, rsi_level, trend_follow — H1 complete not needed
        if mode in ("sma_macd", "rsi_level", "trend_follow"):
            return

        # For 2of3 mode, we handle it in the entry function directly
        if mode == "2of3":
            return

        # Standard 3-signal check
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
        self._h1_complete_dir = 0
        self._h1_complete_ts = _ZERO_TS
        self._h2_rsi_break_ts = _ZERO_TS
        self._h2_rsi_break_dir = 0
        self._macd_reversal_count = 0
        self._best_price = 0.0
        self._entry_atr = 0.0
        # Note: do NOT reset _deferred_direction here — it's set BEFORE
        # _reset_signals() in next_bar_entry mode and must survive the reset.
