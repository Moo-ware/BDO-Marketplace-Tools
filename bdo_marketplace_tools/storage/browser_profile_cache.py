import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from bdo_marketplace_tools.storage.paths import (
    PA_MARKET_PROFILE_PATH,
    STEAM_MARKET_PROFILE_PATH,
)


MIB = 1024 * 1024
DEFAULT_DISPOSABLE_CACHE_CLEANUP_THRESHOLD_BYTES = 150 * MIB
APP_BROWSER_PROFILE_PATHS = (
    STEAM_MARKET_PROFILE_PATH,
    PA_MARKET_PROFILE_PATH,
)

DISPOSABLE_BROWSER_PROFILE_PATHS = (
    "BrowserMetrics",
    "BrowserMetrics-spare.pma",
    "Default/Cache",
    "Default/Code Cache",
    "Default/GPUCache",
    "Default/DawnGraphiteCache",
    "Default/DawnWebGPUCache",
    "Default/Shared Dictionary",
    "Default/Service Worker/CacheStorage",
    "GrShaderCache",
    "ShaderCache",
    "GraphiteDawnCache",
    "GPUPersistentCache",
    "component_crx_cache",
    "extensions_crx_cache",
)


@dataclass(frozen=True)
class BrowserProfileStorageSummary:
    total_bytes: int = 0
    disposable_bytes: int = 0
    scanned_at: float = 0.0


@dataclass(frozen=True)
class BrowserProfileCleanupResult:
    profile_path: Path
    total_bytes_before: int
    disposable_bytes_before: int
    removed_bytes: int
    removed_paths: tuple[str, ...] = ()
    failed_paths: tuple[str, ...] = ()
    skipped: bool = False

    @property
    def had_failures(self):
        return bool(self.failed_paths)

    @property
    def removed_anything(self):
        return self.removed_bytes > 0


def measure_browser_profile_storage(profile_path):
    profile_path = Path(profile_path)
    return BrowserProfileStorageSummary(
        total_bytes=_path_size(profile_path),
        disposable_bytes=_disposable_path_size(profile_path),
        scanned_at=time.time(),
    )


def measure_all_browser_profile_storage(profile_paths=APP_BROWSER_PROFILE_PATHS):
    total = 0
    disposable = 0
    for profile_path in profile_paths:
        summary = measure_browser_profile_storage(profile_path)
        total += summary.total_bytes
        disposable += summary.disposable_bytes
    return BrowserProfileStorageSummary(total_bytes=total, disposable_bytes=disposable, scanned_at=time.time())


def clean_disposable_browser_profile_cache(
    profile_path,
    *,
    threshold_bytes=DEFAULT_DISPOSABLE_CACHE_CLEANUP_THRESHOLD_BYTES,
):
    profile_path = Path(profile_path)
    disposable_before = _disposable_path_size(profile_path)
    if disposable_before < int(threshold_bytes):
        return BrowserProfileCleanupResult(
            profile_path=profile_path,
            total_bytes_before=0,
            disposable_bytes_before=disposable_before,
            removed_bytes=0,
            skipped=True,
        )

    total_before = _path_size(profile_path)
    removed_bytes = 0
    removed_paths = []
    failed_paths = []
    for relative_path in DISPOSABLE_BROWSER_PROFILE_PATHS:
        target = profile_path / Path(relative_path)
        if not target.exists():
            continue
        if not _is_safe_profile_child(profile_path, target) or target.is_symlink():
            failed_paths.append(relative_path)
            continue

        before = _path_size(target)
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError:
            failed_paths.append(relative_path)
        after = _path_size(target) if target.exists() else 0
        removed = max(0, before - after)
        if removed:
            removed_bytes += removed
            removed_paths.append(relative_path)

    return BrowserProfileCleanupResult(
        profile_path=profile_path,
        total_bytes_before=total_before,
        disposable_bytes_before=disposable_before,
        removed_bytes=removed_bytes,
        removed_paths=tuple(removed_paths),
        failed_paths=tuple(failed_paths),
        skipped=False,
    )


def clean_all_disposable_browser_profile_caches(
    profile_paths=APP_BROWSER_PROFILE_PATHS,
    *,
    threshold_bytes=1,
):
    return tuple(
        clean_disposable_browser_profile_cache(profile_path, threshold_bytes=threshold_bytes)
        for profile_path in profile_paths
    )


def format_storage_size(num_bytes):
    num_bytes = int(num_bytes or 0)
    for suffix, size in (("GiB", 1024**3), ("MiB", 1024**2), ("KiB", 1024)):
        if abs(num_bytes) >= size:
            return f"{num_bytes / size:.1f} {suffix}"
    return f"{num_bytes} B"


def _disposable_path_size(profile_path):
    total = 0
    for relative_path in DISPOSABLE_BROWSER_PROFILE_PATHS:
        target = Path(profile_path) / Path(relative_path)
        total += _path_size(target)
    return total


def _path_size(path):
    path = Path(path)
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0

    total = 0
    try:
        iterator = path.rglob("*")
        for child in iterator:
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def _is_safe_profile_child(profile_path, target):
    try:
        root = Path(profile_path).resolve()
        resolved_target = Path(target).resolve()
        resolved_target.relative_to(root)
        return True
    except (OSError, ValueError):
        return False
