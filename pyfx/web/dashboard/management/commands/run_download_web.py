"""Management command to download data for a pre-created Dataset row."""

from __future__ import annotations

import shutil
import subprocess
import traceback
from pathlib import Path

from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    help = "Download data for a pre-created Dataset row (used by the web UI)"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--dataset-id", required=True, type=int)

    def handle(self, **options: object) -> None:
        from pyfx.data.dukascopy import (
            DUKASCOPY_INSTRUMENTS,
            ingest_to_parquet,
        )
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
            _update_progress(5, "Starting download...")

            duka_id = DUKASCOPY_INSTRUMENTS.get(dataset.instrument)
            if duka_id is None:
                raise ValueError(f"Unknown instrument: {dataset.instrument}")

            # Check that npx is available
            npx_path = shutil.which("npx")
            if npx_path is None:
                raise FileNotFoundError(
                    "npx not found. Install Node.js to download data."
                )

            # Download directory for raw CSVs
            data_dir = Path(dataset.file_path).parent
            duka_dir = data_dir / "dukascopy"
            duka_dir.mkdir(parents=True, exist_ok=True)

            start_str = dataset.start_date.strftime("%Y/%m/%d")
            end_str = dataset.end_date.strftime("%Y/%m/%d")

            # Map timeframe to dukascopy-node flag
            tf_map = {"M1": "m1", "M5": "m5", "M15": "m15", "M30": "m30", "H1": "h1"}
            tf_flag = tf_map.get(dataset.timeframe, "m1")

            cmd = [
                npx_path, "dukascopy-node",
                "-i", duka_id,
                "-from", start_str,
                "-to", end_str,
                "-t", tf_flag,
                "-v",
                "-f", "csv",
                "-dir", str(duka_dir),
            ]

            _update_progress(10, "Downloading from Dukascopy...")

            subprocess.run(  # noqa: S603
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=600,
            )

            _update_progress(70, "Download complete, locating CSV...")

            # Find the CSV output (dukascopy-node creates nested dirs)
            csv_files = sorted(duka_dir.rglob("*.csv"), key=lambda p: p.stat().st_mtime)
            if not csv_files:
                raise FileNotFoundError(
                    f"No CSV files found in {duka_dir} after download"
                )

            csv_path = csv_files[-1]  # Most recently modified

            _update_progress(75, "Ingesting CSV to Parquet...")

            dataset.status = Dataset.STATUS_INGESTING
            dataset.save(update_fields=["status"])

            output_path = Path(dataset.file_path)
            ingest_to_parquet(csv_path, output_path)

            _update_progress(90, "Reading metadata...")

            import pandas as pd

            df = pd.read_parquet(output_path, columns=[])
            dataset.row_count = len(df)
            dataset.file_size_bytes = output_path.stat().st_size
            if not df.empty:  # pragma: no cover — defensive guard
                dataset.start_date = df.index.min().date()
                dataset.end_date = df.index.max().date()

            dataset.status = Dataset.STATUS_READY
            dataset.progress_pct = 100
            dataset.progress_message = "Complete"
            dataset.save()

        except Exception:
            dataset.status = Dataset.STATUS_ERROR
            dataset.error_message = traceback.format_exc()
            dataset.progress_message = "Failed"
            dataset.save()
