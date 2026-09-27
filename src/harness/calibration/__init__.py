"""Calibrate fleet-state quantile mocks from Base's public ADER telemetry.

    python -m harness.calibration --store-dir data/market --start 2026-07-01 --end 2026-07-20 --out data/calibration/base-actual
"""

from harness.calibration.command import main

__all__ = ["main"]
