"""Shared bar resampling and loading utilities."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

_AGGREGATION_FREQ: dict[str, str] = {
    "SECOND": "s",
    "MINUTE": "min",
    "HOUR": "h",
    "DAY": "D",
}


def resample_bars(bars_df: pd.DataFrame, bar_type_str: str) -> pd.DataFrame:
    """Resample OHLCV bars to a higher timeframe.

    Args:
        bars_df: Source DataFrame with OHLCV columns and DatetimeIndex.
        bar_type_str: Bar type spec like ``"60-MINUTE-LAST-EXTERNAL"``.

    Returns:
        Resampled DataFrame with the same column structure.
    """
    parts = bar_type_str.split("-")
    step = int(parts[0])
    aggregation = parts[1]
    suffix = _AGGREGATION_FREQ.get(aggregation)
    if suffix is None:
        raise ValueError(f"Unsupported aggregation '{aggregation}' in '{bar_type_str}'")
    rule = f"{step}{suffix}"

    agg: dict[str, str] = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    if "volume" in bars_df.columns:
        agg["volume"] = "sum"

    resampled: pd.DataFrame = bars_df.resample(rule).agg(agg).dropna()  # type: ignore[arg-type]
    return resampled


def load_bars(
    data_file: str | Path,
    timeframe: str | None = None,
) -> pd.DataFrame:
    """Load OHLCV bars from a Parquet or CSV file.

    Args:
        data_file: Path to data file (Parquet or CSV).
        timeframe: Optional bar type string to resample to (e.g. "5-MINUTE-LAST-EXTERNAL").
                   If ``None``, returns raw bars.

    Returns:
        DataFrame with OHLCV columns and a UTC DatetimeIndex.

    Raises:
        FileNotFoundError: If the data file does not exist.
    """
    path = Path(data_file)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {data_file}")

    if path.suffix == ".parquet":
        df: pd.DataFrame = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, index_col=0, parse_dates=True)

    # Ensure UTC DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    if timeframe is not None:
        df = resample_bars(df, timeframe)

    return df


def compute_indicator(
    df: pd.DataFrame,
    name: str,
    period: int = 14,
) -> pd.Series | dict[str, pd.Series]:
    """Compute a technical indicator from OHLCV data.

    Args:
        df: DataFrame with OHLCV columns.
        name: Indicator name (sma, ema, rsi, macd, atr).
        period: Indicator period.

    Returns:
        A Series of indicator values, or a dict of Series for MACD.

    Raises:
        ValueError: If the indicator name is unknown.
    """
    if name == "sma":
        result: pd.Series = df["close"].rolling(period).mean()
        return result
    elif name == "ema":
        result = df["close"].ewm(span=period, adjust=False).mean()
        return result
    elif name == "rsi":
        delta = df["close"].diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
        rs = gain / loss
        result = 100 - (100 / (1 + rs))
        return result
    elif name == "macd":
        ema12 = df["close"].ewm(span=12, adjust=False).mean()
        ema26 = df["close"].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal = macd_line.ewm(span=9, adjust=False).mean()
        histogram = macd_line - signal
        return {"macd": macd_line, "signal": signal, "histogram": histogram}
    elif name == "atr":
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        result = tr.ewm(alpha=1 / period, adjust=False).mean()
        return result
    else:
        raise ValueError(f"Unknown indicator: {name}")
