from django.db import models


class Dataset(models.Model):
    """A tracked OHLCV dataset (Parquet file) with metadata."""

    SOURCE_DUKASCOPY = "dukascopy"
    SOURCE_MANUAL = "manual"
    SOURCE_GENERATED = "generated"
    SOURCE_CHOICES = [
        (SOURCE_DUKASCOPY, "Dukascopy"),
        (SOURCE_MANUAL, "Manual"),
        (SOURCE_GENERATED, "Generated"),
    ]

    STATUS_DOWNLOADING = "downloading"
    STATUS_INGESTING = "ingesting"
    STATUS_READY = "ready"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_DOWNLOADING, "Downloading"),
        (STATUS_INGESTING, "Ingesting"),
        (STATUS_READY, "Ready"),
        (STATUS_ERROR, "Error"),
    ]

    instrument = models.CharField(max_length=50)
    timeframe = models.CharField(max_length=20, default="M1")
    start_date = models.DateField()
    end_date = models.DateField()
    file_path = models.CharField(max_length=500, unique=True)
    file_size_bytes = models.BigIntegerField(default=0)
    row_count = models.BigIntegerField(default=0)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DOWNLOADING)
    progress_pct = models.IntegerField(default=0)
    progress_message = models.CharField(max_length=200, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["instrument", "timeframe", "start_date", "end_date"],
                name="unique_dataset_identity",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.instrument} {self.timeframe} {self.start_date} to {self.end_date}"

    @property
    def display_size(self) -> str:
        """Human-readable file size."""
        if self.file_size_bytes >= 1_000_000:
            return f"{self.file_size_bytes / 1_000_000:.1f} MB"
        if self.file_size_bytes >= 1_000:
            return f"{self.file_size_bytes / 1_000:.1f} KB"
        return f"{self.file_size_bytes} B"


class BacktestRun(models.Model):
    """A single backtest execution and its summary metrics."""

    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    created_at = models.DateTimeField(auto_now_add=True)

    # Config
    strategy = models.CharField(max_length=200)
    instrument = models.CharField(max_length=50)
    start = models.DateTimeField()
    end = models.DateTimeField()
    bar_type = models.CharField(max_length=100, default="1-MINUTE-LAST-EXTERNAL")
    extra_bar_types = models.JSONField(default=list, blank=True)
    trade_size = models.FloatField(default=100_000)
    balance = models.FloatField(default=100_000)
    leverage = models.FloatField(default=50)
    strategy_params = models.JSONField(default=dict, blank=True)

    # Execution state
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_COMPLETED
    )
    error_message = models.TextField(blank=True, default="")
    data_file = models.CharField(max_length=500, blank=True, default="")

    # Progress tracking
    progress_pct = models.IntegerField(default=0)
    progress_message = models.CharField(max_length=200, blank=True, default="")
    total_bars = models.IntegerField(default=0)

    # Results
    total_pnl = models.FloatField(default=0)
    total_return_pct = models.FloatField(default=0)
    num_trades = models.IntegerField(default=0)
    win_rate = models.FloatField(default=0)
    max_drawdown_pct = models.FloatField(default=0)
    avg_trade_pnl = models.FloatField(default=0)
    avg_win = models.FloatField(default=0)
    avg_loss = models.FloatField(default=0)
    profit_factor = models.FloatField(null=True, blank=True)
    duration_seconds = models.FloatField(default=0)

    @property
    def win_rate_pct(self) -> float:
        """Win rate as a percentage (0-100) for display."""
        return float(self.win_rate * 100)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.strategy} | {self.instrument} | {self.total_return_pct:+.1f}%"


class Trade(models.Model):
    """A single closed trade within a backtest run."""

    run = models.ForeignKey(BacktestRun, on_delete=models.CASCADE, related_name="trade_set")
    instrument = models.CharField(max_length=50)
    side = models.CharField(max_length=10)
    quantity = models.FloatField()
    open_price = models.FloatField()
    close_price = models.FloatField()
    realized_pnl = models.FloatField()
    realized_return_pct = models.FloatField(default=0)
    opened_at = models.DateTimeField()
    closed_at = models.DateTimeField()
    duration_seconds = models.FloatField(default=0)

    class Meta:
        ordering = ["opened_at"]

    def __str__(self) -> str:
        return f"{self.side} {self.instrument} {self.realized_pnl:+.2f}"


class EquitySnapshot(models.Model):
    """A point on the equity curve."""

    run = models.ForeignKey(BacktestRun, on_delete=models.CASCADE, related_name="equity_set")
    timestamp = models.DateTimeField()
    balance = models.FloatField()

    class Meta:
        ordering = ["timestamp"]


# ---------------------------------------------------------------------------
# Paper / Live Trading Models
# ---------------------------------------------------------------------------


class PaperTradingSession(models.Model):
    """A paper trading session (parallel to BacktestRun for live trading)."""

    STATUS_RUNNING = "running"
    STATUS_STOPPED = "stopped"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_RUNNING, "Running"),
        (STATUS_STOPPED, "Stopped"),
        (STATUS_ERROR, "Error"),
    ]

    created_at = models.DateTimeField(auto_now_add=True)

    # Config
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    strategy = models.CharField(max_length=200)
    instrument = models.CharField(max_length=200)
    bar_type = models.CharField(max_length=100, default="1-MINUTE-LAST-EXTERNAL")
    started_at = models.DateTimeField()
    stopped_at = models.DateTimeField(null=True, blank=True)
    account_currency = models.CharField(max_length=10, default="USD")
    account_id = models.CharField(max_length=50, blank=True, default="")
    config_json = models.JSONField(default=dict, blank=True)

    # Multi-session support
    client_id = models.IntegerField(null=True, blank=True)
    process_pid = models.IntegerField(null=True, blank=True)

    # Aggregate metrics (updated as trades close)
    total_pnl = models.FloatField(null=True, blank=True)
    total_return_pct = models.FloatField(null=True, blank=True)
    num_trades = models.IntegerField(default=0)
    win_rate = models.FloatField(null=True, blank=True)
    max_drawdown_pct = models.FloatField(null=True, blank=True)
    profit_factor = models.FloatField(null=True, blank=True)
    avg_trade_pnl = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        pnl = f"${self.total_pnl:+,.2f}" if self.total_pnl is not None else "n/a"
        return f"{self.strategy} | {self.instrument} | {pnl}"

    @property
    def win_rate_pct(self) -> float:
        """Win rate as a percentage (0-100) for display."""
        return float((self.win_rate or 0) * 100)

    @property
    def is_running(self) -> bool:
        return self.status == self.STATUS_RUNNING

    @property
    def instrument_list(self) -> list[str]:
        """Split comma-separated instrument string into a list."""
        return [i.strip() for i in self.instrument.split(",") if i.strip()]


class PaperTrade(models.Model):
    """An individual trade from a paper trading session."""

    session = models.ForeignKey(
        PaperTradingSession, on_delete=models.CASCADE, related_name="trades",
    )
    instrument = models.CharField(max_length=50)
    side = models.CharField(max_length=10)
    quantity = models.FloatField()
    open_price = models.FloatField()
    close_price = models.FloatField(null=True, blank=True)
    realized_pnl = models.FloatField(null=True, blank=True)
    realized_return_pct = models.FloatField(null=True, blank=True)
    opened_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)

    # Live-specific metrics
    fill_latency_ms = models.FloatField(null=True, blank=True)
    slippage_ticks = models.FloatField(null=True, blank=True)
    spread_at_entry = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["opened_at"]

    def __str__(self) -> str:
        pnl = f"{self.realized_pnl:+.2f}" if self.realized_pnl is not None else "open"
        return f"{self.side} {self.instrument} {pnl}"

    @property
    def is_open(self) -> bool:
        return self.closed_at is None


class SessionEvent(models.Model):
    """A timestamped event from a paper trading session."""

    session = models.ForeignKey(
        PaperTradingSession, on_delete=models.CASCADE, related_name="events",
    )
    timestamp = models.DateTimeField()
    event_type = models.CharField(max_length=50)
    message = models.TextField()
    details_json = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self) -> str:
        return f"[{self.event_type}] {self.message[:80]}"


class RiskSnapshot(models.Model):
    """Periodic risk state snapshot for monitoring."""

    session = models.ForeignKey(
        PaperTradingSession, on_delete=models.CASCADE, related_name="risk_snapshots",
    )
    timestamp = models.DateTimeField()
    equity = models.FloatField()
    daily_pnl = models.FloatField()
    open_positions = models.IntegerField()
    drawdown_pct = models.FloatField()
    utilization_pct = models.FloatField()

    class Meta:
        ordering = ["-timestamp"]
