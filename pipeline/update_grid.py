"""Compatibility entry point for the weekly, source-backed pipeline.

Run python -m pipeline.update_grid (or pipeline.weekly_snapshot).
The old local-file/PMTiles publishing route is no longer the application updater.
"""
from pipeline.weekly_snapshot import main, validate_national_source

__all__ = ["main", "validate_national_source"]

if __name__ == "__main__":
    main()
