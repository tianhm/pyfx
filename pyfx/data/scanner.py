"""Scan the data directory and register Parquet files as Dataset records."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from pyfx.data.dukascopy import FILENAME_TO_INSTRUMENT


def _parse_instrument_from_filename(name: str) -> str | None:
    """Extract instrument from filename prefix (e.g. ``EURUSD_...`` -> ``EUR/USD``)."""
    for prefix, instrument in FILENAME_TO_INSTRUMENT.items():
        if name.upper().startswith(prefix):
            return instrument
    return None


def _parse_timeframe_from_filename(name: str) -> str:
    """Extract timeframe from filename (e.g. ``..._M1.parquet`` -> ``M1``)."""
    match = re.search(r"_(M\d+)\.parquet$", name, re.IGNORECASE)
    return match.group(1).upper() if match else "M1"


def _is_generated(name: str) -> bool:
    """Check if filename indicates generated/sample data (e.g. ``EURUSD_90d_M1``)."""
    return bool(re.search(r"_\d+d_", name))


def scan_data_directory(
    data_dir: Path | None = None,
    quiet: bool = False,
) -> tuple[int, int]:
    """Scan data_dir for Parquet files and register new ones as Dataset records.

    Returns:
        Tuple of (newly_registered, already_tracked) counts.
    """
    # Lazy Django import — only available when Django is set up
    from pyfx.web.dashboard.models import Dataset

    if data_dir is None:
        from pyfx.core.config import settings

        data_dir = settings.data_dir

    data_dir.mkdir(parents=True, exist_ok=True)

    registered = 0
    already_tracked = 0

    for parquet_path in sorted(data_dir.glob("*.parquet")):
        file_path_str = str(parquet_path.resolve())

        if Dataset.objects.filter(file_path=file_path_str).exists():
            already_tracked += 1
            continue

        instrument = _parse_instrument_from_filename(parquet_path.name)
        if instrument is None:
            continue

        timeframe = _parse_timeframe_from_filename(parquet_path.name)

        # Read Parquet index for date range and row count
        df = pd.read_parquet(parquet_path, columns=[])
        row_count = len(df)
        if row_count == 0:
            continue
        start_date = df.index.min().date()
        end_date = df.index.max().date()
        file_size_bytes = parquet_path.stat().st_size

        source = (
            Dataset.SOURCE_GENERATED
            if _is_generated(parquet_path.name)
            else Dataset.SOURCE_MANUAL
        )

        Dataset.objects.create(
            instrument=instrument,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            file_path=file_path_str,
            file_size_bytes=file_size_bytes,
            row_count=row_count,
            source=source,
            status=Dataset.STATUS_READY,
            progress_pct=100,
        )
        registered += 1

    return registered, already_tracked
