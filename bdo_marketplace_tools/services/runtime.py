"""Immutable process-level runtime configuration.

The launch mode is selected once at the composition root.  Services receive the
resulting values instead of mutating a shared "test mode" flag after startup.
"""

from dataclasses import dataclass
from pathlib import Path

from bdo_marketplace_tools.storage.paths import LOCAL_STATS_PATH, STATS_DB_PATH, TEST_STATS_DB_PATH


@dataclass(frozen=True)
class RuntimeConfig:
    """Mode-dependent policy selected before application services are built."""

    developer_tools_enabled: bool
    stats_db_path: Path
    legacy_stats_path: Path | None
    run_startup_session_check: bool
    automatic_update_checks: bool


def runtime_for_test_mode(test_mode: bool) -> RuntimeConfig:
    """Return the live or developer runtime policy for one application run."""

    return developer_runtime() if test_mode else live_runtime()


def live_runtime() -> RuntimeConfig:
    """Return the normal application runtime policy."""

    return RuntimeConfig(
        developer_tools_enabled=False,
        stats_db_path=STATS_DB_PATH,
        legacy_stats_path=LOCAL_STATS_PATH,
        run_startup_session_check=True,
        automatic_update_checks=True,
    )


def developer_runtime() -> RuntimeConfig:
    """Return the opt-in local diagnostics runtime policy."""

    return RuntimeConfig(
        developer_tools_enabled=True,
        stats_db_path=TEST_STATS_DB_PATH,
        legacy_stats_path=None,
        run_startup_session_check=False,
        automatic_update_checks=False,
    )
