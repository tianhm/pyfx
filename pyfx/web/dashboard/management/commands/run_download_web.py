"""Management command to download data for a pre-created Dataset row.

Downloads in monthly chunks with delays between requests to avoid
Dukascopy rate-limiting.  Checks for other active downloads and waits
for them to finish before starting (sequential queue).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import traceback
from datetime import date, timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandParser  # type: ignore[import-untyped]

# Seconds to wait between monthly chunk downloads
CHUNK_DELAY_SECONDS = 5

# Seconds to pause before starting if another download is active
QUEUE_POLL_SECONDS = 10

# Maximum time to wait in queue before giving up (10 minutes)
QUEUE_TIMEOUT_SECONDS = 600

# Per-chunk subprocess timeout (5 minutes)
CHUNK_TIMEOUT_SECONDS = 300

# Maximum retries per chunk on failure
MAX_RETRIES = 2

# Seconds to wait before retrying a failed chunk
RETRY_DELAY_SECONDS = 30


def _month_ranges(start: date, end: date) -> list[tuple[date, date]]:
    """Split a date range into per-month chunks."""
    chunks: list[tuple[date, date]] = []
    cursor = start.replace(day=1)
    while cursor <= end:
        chunk_start = max(cursor, start)
        # Last day of month
        if cursor.month == 12:
            next_month = cursor.replace(year=cursor.year + 1, month=1)
        else:
            next_month = cursor.replace(month=cursor.month + 1)
        chunk_end = min(next_month - timedelta(days=1), end)
        chunks.append((chunk_start, chunk_end))
        cursor = next_month
    return chunks


class Command(BaseCommand):  # type: ignore[misc]
    help = "Download data for a pre-created Dataset row (used by the web UI)"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--dataset-id", required=True, type=int)

    def _wait_for_queue(self, dataset_id: int) -> None:
        """Wait until no other downloads are active (simple sequential queue)."""
        from pyfx.web.dashboard.models import Dataset

        waited = 0
        while waited < QUEUE_TIMEOUT_SECONDS:
            active = (
                Dataset.objects.filter(
                    status__in=[
                        Dataset.STATUS_DOWNLOADING,
                        Dataset.STATUS_INGESTING,
                    ],
                )
                .exclude(pk=dataset_id)
                .exists()
            )
            if not active:
                return
            time.sleep(QUEUE_POLL_SECONDS)
            waited += QUEUE_POLL_SECONDS

    def handle(self, **options: object) -> None:  # noqa: C901
        import pandas as pd

        from pyfx.data.dukascopy import DUKASCOPY_INSTRUMENTS
        from pyfx.web.dashboard.models import Dataset

        dataset_id: int = options["dataset_id"]  # type: ignore[assignment]
        try:
            dataset = Dataset.objects.get(pk=dataset_id)
        except Dataset.DoesNotExist:
            self.stderr.write(f"Dataset {dataset_id} not found")
            return

        def _update_progress(pct: int, msg: str) -> None:
            dataset.progress_pct = pct
            dataset.progress_message = msg
            dataset.save(update_fields=["progress_pct", "progress_message"])

        try:
            _update_progress(2, "Waiting in queue...")

            self._wait_for_queue(dataset_id)

            _update_progress(5, "Starting download...")

            duka_id = DUKASCOPY_INSTRUMENTS.get(dataset.instrument)
            if duka_id is None:
                raise ValueError(f"Unknown instrument: {dataset.instrument}")

            npx_path = shutil.which("npx")
            if npx_path is None:
                raise FileNotFoundError(
                    "npx not found. Install Node.js to download data."
                )

            # Use a unique temp directory per download to avoid file collisions
            data_dir = Path(dataset.file_path).parent
            duka_dir = Path(
                tempfile.mkdtemp(prefix=f"pyfx_dl_{duka_id}_", dir=data_dir),
            )

            tf_map = {
                "M1": "m1", "M5": "m5", "M15": "m15", "M30": "m30", "H1": "h1",
            }
            tf_flag = tf_map.get(dataset.timeframe, "m1")

            # Split into monthly chunks
            chunks = _month_ranges(dataset.start_date, dataset.end_date)
            total_chunks = len(chunks)
            all_csvs: list[Path] = []

            for i, (chunk_start, chunk_end) in enumerate(chunks):
                chunk_num = i + 1
                pct = 5 + int(60 * chunk_num / total_chunks)
                _update_progress(
                    pct,
                    f"Downloading chunk {chunk_num}/{total_chunks} "
                    f"({chunk_start.strftime('%b %Y')})...",
                )

                chunk_dir = duka_dir / f"chunk_{chunk_num:03d}"
                chunk_dir.mkdir(parents=True, exist_ok=True)

                start_str = chunk_start.strftime("%Y/%m/%d")
                end_str = chunk_end.strftime("%Y/%m/%d")

                cmd = [
                    npx_path, "dukascopy-node",
                    "-i", duka_id,
                    "-from", start_str,
                    "-to", end_str,
                    "-t", tf_flag,
                    "-v",
                    "-f", "csv",
                    "-dir", str(chunk_dir),
                ]

                # Retry logic per chunk
                for attempt in range(1, MAX_RETRIES + 1):
                    try:
                        subprocess.run(  # noqa: S603
                            cmd,
                            check=True,
                            capture_output=True,
                            text=True,
                            timeout=CHUNK_TIMEOUT_SECONDS,
                        )
                        break
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                        if attempt < MAX_RETRIES:
                            _update_progress(
                                pct,
                                f"Chunk {chunk_num}/{total_chunks} "
                                f"failed, retrying in {RETRY_DELAY_SECONDS}s "
                                f"(attempt {attempt}/{MAX_RETRIES})...",
                            )
                            time.sleep(RETRY_DELAY_SECONDS)
                        else:
                            raise

                # Collect CSVs from this chunk
                chunk_csvs = list(chunk_dir.rglob("*.csv"))
                all_csvs.extend(chunk_csvs)

                # Rate-limit delay between chunks (skip after last)
                if chunk_num < total_chunks:
                    time.sleep(CHUNK_DELAY_SECONDS)

            _update_progress(68, "Download complete, merging CSVs...")

            if not all_csvs:
                raise FileNotFoundError(
                    f"No CSV files found in {duka_dir} after download"
                )

            # Concatenate all chunk CSVs into one DataFrame
            from pyfx.data.dukascopy import read_dukascopy_csv

            frames: list[pd.DataFrame] = []
            for csv_path in sorted(all_csvs):
                df = read_dukascopy_csv(csv_path)
                if not df.empty:
                    frames.append(df)

            if not frames:
                raise ValueError("All downloaded CSVs were empty")

            combined = pd.concat(frames)
            combined = combined.sort_index()
            combined = combined[~combined.index.duplicated(keep="first")]

            _update_progress(75, "Writing Parquet...")

            dataset.status = Dataset.STATUS_INGESTING
            dataset.save(update_fields=["status"])

            output_path = Path(dataset.file_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            combined.to_parquet(output_path)

            _update_progress(90, "Validating...")

            # Verify the written file
            verify = pd.read_parquet(output_path, columns=[])
            row_count = len(verify)

            dataset.row_count = row_count
            dataset.file_size_bytes = output_path.stat().st_size
            if row_count > 0:
                dataset.start_date = verify.index.min().date()
                dataset.end_date = verify.index.max().date()

            dataset.status = Dataset.STATUS_READY
            dataset.progress_pct = 100
            dataset.progress_message = "Complete"
            dataset.save()

            # Clean up temp download dir
            shutil.rmtree(duka_dir, ignore_errors=True)

        except Exception:
            dataset.status = Dataset.STATUS_ERROR
            dataset.error_message = traceback.format_exc()
            dataset.progress_message = "Failed"
            dataset.save()
