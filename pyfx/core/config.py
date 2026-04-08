"""Application configuration via environment variables / .env file."""

from decimal import Decimal
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

_VALID_CURRENCIES = {"USD", "CHF", "GBP", "EUR"}
_VALID_TRADING_MODES = {"paper", "live"}
_VALID_SIZING_METHODS = {"fixed_fractional", "atr_based"}


class PyfxSettings(BaseSettings):
    model_config = {"env_prefix": "PYFX_", "env_file": ".env", "extra": "ignore"}

    @model_validator(mode="after")
    def _expand_user_paths(self) -> "PyfxSettings":
        """Expand ~ in all Path fields so consumers get absolute paths."""
        self.data_dir = self.data_dir.expanduser()
        self.catalog_dir = self.catalog_dir.expanduser()
        self.db_path = self.db_path.expanduser()
        if self.strategies_dir is not None:
            self.strategies_dir = self.strategies_dir.expanduser()
        if self.log_dir is not None:
            self.log_dir = self.log_dir.expanduser()
        return self

    # Data
    data_dir: Path = Field(default=Path("data"), description="Local data cache (project-relative)")
    catalog_dir: Path = Field(
        default=Path("data") / "catalog",
        description="NautilusTrader Parquet catalog",
    )

    # Strategy discovery
    strategies_dir: Path | None = Field(
        default=None,
        description="Extra directory to scan for strategy modules",
    )

    # Backtest defaults
    default_balance: float = Field(default=100_000.0, description="Starting balance in USD")
    default_leverage: float = Field(default=50.0, description="Default leverage ratio")

    # Django
    db_path: Path = Field(
        default=Path("data") / "db.sqlite3",
        description="SQLite database path (project-relative)",
    )
    secret_key: str = Field(
        default="pyfx-dev-secret-change-in-production",
        description="Django secret key",
    )
    debug: bool = Field(default=True, description="Django DEBUG mode")
    allowed_hosts: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Django ALLOWED_HOSTS",
    )

    # ---- Interactive Brokers ----
    ib_username: str | None = Field(
        default=None, description="IB Gateway / TWS username",
    )
    ib_password: str | None = Field(
        default=None, description="IB Gateway / TWS password",
    )
    ib_account_id: str | None = Field(
        default=None, description="IB account ID (e.g. DU1234567 for paper)",
    )
    ib_host: str = Field(default="127.0.0.1", description="IB Gateway host")
    ib_port: int = Field(
        default=4002,
        description="IB Gateway port (4002=paper IBG, 4001=live IBG, 7497=paper TWS)",
    )
    ib_client_id: int = Field(default=1, description="IB API client ID")
    ib_trading_mode: str = Field(default="paper", description="'paper' or 'live'")
    ib_read_only_api: bool = Field(
        default=False, description="If True, no order execution allowed",
    )
    ib_gateway_image: str = Field(
        default="ghcr.io/gnzsnz/ib-gateway:stable",
        description="Docker image for IB Gateway container",
    )

    # ---- Account ----
    account_currency: str = Field(default="USD", description="Account base currency")

    # ---- Risk Management ----
    risk_max_position_size: Decimal = Field(
        default=Decimal("100000"), description="Max units per position",
    )
    risk_max_positions: int = Field(
        default=3, description="Max concurrent open positions",
    )
    risk_daily_loss_limit: float = Field(
        default=2000.0, description="Daily loss circuit breaker (account currency)",
    )
    risk_max_drawdown_pct: float = Field(
        default=10.0, description="Max drawdown % before auto-stop",
    )
    risk_position_size_pct: float = Field(
        default=2.0, description="% of equity to risk per trade",
    )
    risk_max_notional_per_order: int = Field(
        default=500_000, description="NautilusTrader native max notional per order",
    )
    risk_sizing_method: str = Field(
        default="fixed_fractional",
        description="Position sizing method: fixed_fractional or atr_based",
    )

    # ---- Logging ----
    log_dir: Path | None = Field(
        default=None, description="Log file directory (default: data_dir/logs)",
    )

    # ---- Validators ----
    @field_validator("account_currency")
    @classmethod
    def _validate_currency(cls, v: str) -> str:
        v = v.upper()
        if v not in _VALID_CURRENCIES:
            msg = f"account_currency must be one of {_VALID_CURRENCIES}, got '{v}'"
            raise ValueError(msg)
        return v

    @field_validator("ib_trading_mode")
    @classmethod
    def _validate_trading_mode(cls, v: str) -> str:
        v = v.lower()
        if v not in _VALID_TRADING_MODES:
            msg = f"ib_trading_mode must be 'paper' or 'live', got '{v}'"
            raise ValueError(msg)
        return v

    @field_validator("risk_sizing_method")
    @classmethod
    def _validate_sizing_method(cls, v: str) -> str:
        v = v.lower()
        if v not in _VALID_SIZING_METHODS:
            msg = f"risk_sizing_method must be one of {_VALID_SIZING_METHODS}, got '{v}'"
            raise ValueError(msg)
        return v

    @field_validator("risk_daily_loss_limit")
    @classmethod
    def _validate_daily_loss(cls, v: float) -> float:
        if v <= 0:
            msg = "risk_daily_loss_limit must be positive"
            raise ValueError(msg)
        return v

    @field_validator("risk_max_drawdown_pct")
    @classmethod
    def _validate_max_dd(cls, v: float) -> float:
        if not 0 < v <= 100:
            msg = "risk_max_drawdown_pct must be between 0 and 100"
            raise ValueError(msg)
        return v

    @field_validator("risk_position_size_pct")
    @classmethod
    def _validate_position_pct(cls, v: float) -> float:
        if not 0 < v <= 100:
            msg = "risk_position_size_pct must be between 0 and 100"
            raise ValueError(msg)
        return v

    @field_validator("risk_max_positions")
    @classmethod
    def _validate_max_positions(cls, v: int) -> int:
        if v < 1:
            msg = "risk_max_positions must be at least 1"
            raise ValueError(msg)
        return v

    def get_log_dir(self) -> Path:
        """Return the effective log directory, creating it if needed."""
        d = self.log_dir if self.log_dir else self.data_dir / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def validate_ib_config(self) -> list[str]:
        """Validate IB config for live trading. Returns list of warnings."""
        warnings: list[str] = []
        if self.ib_account_id is None:
            warnings.append("PYFX_IB_ACCOUNT_ID is not set")
        elif (
            self.ib_trading_mode == "paper"
            and not self.ib_account_id.startswith("DU")
        ):
            warnings.append(
                f"Paper account IDs usually start with 'DU', got '{self.ib_account_id}'"
            )
        if self.ib_port in (4001, 7496) and self.ib_trading_mode == "paper":
            warnings.append(
                f"Port {self.ib_port} is typically used for LIVE trading, "
                f"but trading_mode='paper'"
            )
        if self.ib_trading_mode == "live":
            warnings.append("WARNING: trading_mode is 'live' -- use --confirm-live flag")
        if self.ib_read_only_api:
            warnings.append("read_only_api is True -- order execution will be blocked")
        return warnings


settings = PyfxSettings()
