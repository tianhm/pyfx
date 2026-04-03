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

    def __str__(self):
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

    def __str__(self):
        return f"{self.side} {self.instrument} {self.realized_pnl:+.2f}"


class EquitySnapshot(models.Model):
    """A point on the equity curve."""

    run = models.ForeignKey(BacktestRun, on_delete=models.CASCADE, related_name="equity_set")
    timestamp = models.DateTimeField()
    balance = models.FloatField()

    class Meta:
        ordering = ["timestamp"]
