"""Application configuration via environment variables / .env file."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class PyfxSettings(BaseSettings):
    model_config = {"env_prefix": "PYFX_", "env_file": ".env", "extra": "ignore"}

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


settings = PyfxSettings()
