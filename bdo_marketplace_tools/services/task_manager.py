import asyncio
import random
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from bdo_marketplace_tools.market.api_handler import (
    MarketplaceAPIError,
)
from bdo_marketplace_tools.market.browser_auth import (
    BDO_SITE_BOOTSTRAP_URL,
    BrowserAuthError,
    acquire_market_cookies,
    clear_market_cookies_keep_steam_login,
    clear_steam_browser_profile_cookies,
    prepare_steam_browser_profile,
)
from bdo_marketplace_tools.market.browser_worker import PersistentPABrowserWorker
from bdo_marketplace_tools.market.pricing import apply_price_rules, purchase_record_count, purchase_record_spend
from bdo_marketplace_tools.market.test_mode import (
    LIVE_BUY_ERROR_TEST_TARGET,
    SINGLE_ITEM_TEST_TARGET,
    check_single_item_stock,
    live_buy_error_test_listing,
)
from bdo_marketplace_tools.services.update_checker import RELEASES_URL, check_for_update as run_update_check
from bdo_marketplace_tools.storage.app_settings import (
    PA_CREDENTIALS_MODE,
    STEAM_BROWSER_MODE,
    account_mode_detail,
    account_mode_label,
    default_app_settings,
    load_browser_cache_cleanup_threshold_mb,
    load_account_mode,
    load_pa_browser_profile_prepared,
    load_pa_browser_keep_open,
    load_saved_session_last_known_valid,
    load_steam_browser_profile_prepared,
    load_steam_pa_cookie_consent_prepared,
    load_ui_settings,
    load_update_check_on_startup,
    normalize_account_mode,
    normalize_browser_cache_cleanup_threshold_mb,
    save_account_mode,
    save_browser_cache_cleanup_threshold_mb,
    save_buy_mode,
    save_scan_scope,
    save_pa_browser_profile_prepared,
    save_pa_browser_keep_open,
    save_polling_settings,
    save_purchase_delay_bounds,
    save_saved_session_last_known_valid,
    save_spend_cap,
    save_steam_browser_profile_prepared,
    save_steam_pa_cookie_consent_prepared,
    save_update_check_on_startup,
)
from bdo_marketplace_tools.storage.browser_profile_cache import (
    MIB,
    clean_all_disposable_browser_profile_caches,
    clean_disposable_browser_profile_cache,
    format_storage_size,
    measure_all_browser_profile_storage,
)
from bdo_marketplace_tools.storage.credentials import CredentialStoreError, load_credentials
from bdo_marketplace_tools.storage import stats_db
from bdo_marketplace_tools.storage.paths import (
    LOCAL_STATS_PATH,
    PA_MARKET_PROFILE_PATH,
    STATS_DB_PATH,
    STEAM_MARKET_PROFILE_PATH,
    TEST_STATS_DB_PATH,
)
from bdo_marketplace_tools.services.event_log import EventLog
from bdo_marketplace_tools.ui.display import (
    APP_TITLE,
    format_duration,
    highlight,
    highlight_brand,
)
from bdo_marketplace_tools.version import APP_VERSION

LOCAL_DATA_PATH = LOCAL_STATS_PATH
DEFAULT_LOCAL_DATA = stats_db.DEFAULT_LIFETIME_STATS
_DEFAULT_STATS_PATH = object()
_DEFAULT_LEGACY_STATS_PATH = object()
DEBUG_OUTFIT_LISTING = [["debug-premium-outfit", "1", "2020000000"]]
DEBUG_MULTI_OUTFIT_INITIAL_LISTING = [["debug-premium-outfit-a", "2", "2020000000"]]
DEBUG_MULTI_OUTFIT_JOINED_LISTING = [
    ["debug-premium-outfit-a", "2", "2020000000"],
    ["debug-premium-outfit-b", "1", "2020000000"],
]
DEBUG_BUNDLED_OUTFIT_LISTING = [["debug-premium-outfit", "8", "2020000000"]]
DEBUG_BUNDLED_PURCHASE_TICK_SECONDS = 5.0
MAX_ERROR_BACKOFF_MULTIPLIER = 6
# Scan coverage flushes are batched so watching does not turn into a disk write per cycle.
SCAN_COVERAGE_FLUSH_THRESHOLD = 12
STATS_WRITE_RETRY_LIMIT = 2
STATS_WRITE_RETRY_DELAY_SECONDS = 0.1
# A listing is considered the same availability episode until it has been absent from a
# couple of successful scans. This avoids counting one visible bundle again on every poll,
# while still splitting real disappear/reappear cycles.
DETECTION_EPISODE_MISSED_SCAN_LIMIT = 2
SIMULATED_SESSION_EMAIL = "test-session@example.local"
BROWSER_VERIFICATION_MARKERS = (
    "browser verification",
    "manual browser verification",
    "requires browser",
)


def _same_file_path(left, right):
    return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()


async def _run_profile_io_in_thread(function, *args, **kwargs):
    """Run profile filesystem work to completion even if its caller is cancelled.

    Cancelling ``asyncio.to_thread`` only cancels the awaiter; the underlying thread keeps
    running. Profile cleanup must therefore be joined before the caller can release its browser
    locks, otherwise a replacement Chrome context could open while that thread is still deleting
    files from the same profile.
    """
    operation = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        await asyncio.wait((operation,))
    except asyncio.CancelledError as cancellation:
        while not operation.done():
            try:
                await asyncio.wait((operation,))
            except asyncio.CancelledError:
                # Repeated cancellation requests must not let the profile lock escape early.
                continue
        if not operation.cancelled():
            operation.exception()
        raise cancellation
    return operation.result()


def _load_local_data(path=_DEFAULT_STATS_PATH, legacy_json_path=_DEFAULT_LEGACY_STATS_PATH):
    if path is _DEFAULT_STATS_PATH:
        path = STATS_DB_PATH
    if legacy_json_path is _DEFAULT_LEGACY_STATS_PATH:
        legacy_json_path = LOCAL_DATA_PATH
    return stats_db.load_lifetime_stats(path=path, legacy_json_path=legacy_json_path)


def _save_local_data(data, path=_DEFAULT_STATS_PATH):
    if path is _DEFAULT_STATS_PATH:
        path = STATS_DB_PATH
    stats_db.save_lifetime_stats(
        data["successful_purchases"],
        data["silver_spent"],
        path=path,
    )


class BackgroundTasks:
    def __init__(self, api_handler, test_mode_enabled=False, persist_ui_settings=True):
        self.api_handler = api_handler
        self.test_mode_enabled = bool(test_mode_enabled)
        self.persist_ui_settings = bool(persist_ui_settings)
        self.stats_db_path = TEST_STATS_DB_PATH if self.test_mode_enabled else STATS_DB_PATH
        self._assert_stats_db_path_allowed(self.stats_db_path)
        self.legacy_stats_path = None if self.test_mode_enabled else LOCAL_DATA_PATH
        local_data = _load_local_data(path=self.stats_db_path, legacy_json_path=self.legacy_stats_path)
        self.account_mode = load_account_mode()
        self.api_handler.account_mode = self.account_mode
        self.steam_browser_profile_prepared = load_steam_browser_profile_prepared()
        self.pa_browser_profile_prepared = load_pa_browser_profile_prepared()
        self.pa_browser_keep_open = (
            load_pa_browser_keep_open() if self.persist_ui_settings else False
        )
        self._pa_browser_worker = None
        self.saved_session_last_known_valid = (
            load_saved_session_last_known_valid() if self.persist_ui_settings else False
        )
        self.steam_pa_cookie_consent_prepared = (
            load_steam_pa_cookie_consent_prepared() if self.persist_ui_settings else False
        )
        self.steam_auto_reauth_enabled = False
        self.checker_task = None
        self.single_item_test_checker_task = None
        self.login_checker_task = None
        self.checker_enabled = False
        self.checker_stop_requested = False
        self._checker_purchase_stop_event = asyncio.Event()
        self.single_item_test_checker_enabled = False
        self.single_item_test_purchase_enabled = False
        self._single_item_test_purchase_stop_event = asyncio.Event()
        self._purchase_owner_task = None
        self.delay_choices = {
            "1": ("Fast", (3, 5)),
            "2": ("Balanced", (5, 10)),
            "3": ("Slow", (15, 30)),
        }
        ui_settings = load_ui_settings() if self.persist_ui_settings else default_app_settings()["ui"]
        polling_settings = ui_settings["polling"]
        self.include_outfit_boxes = bool(ui_settings["scan_scope"]["include_outfit_boxes"])
        self.include_outfit_pieces = bool(ui_settings["scan_scope"]["include_outfit_pieces"])
        self.delay = polling_settings["selected"]
        self.custom_delay_range = tuple(polling_settings["custom_range"])
        if self.delay == "custom":
            self.delay = self.matching_delay_choice(self.custom_delay_range) or "custom"
        elif self.delay not in self.delay_choices:
            self.delay = "3"
        self.purchase_delay_bounds = tuple(ui_settings["buy_delay"]["range"])
        self.browser_cache_cleanup_threshold_mb = (
            load_browser_cache_cleanup_threshold_mb()
            if self.persist_ui_settings
            else default_app_settings()["maintenance"]["browser_cache_cleanup_threshold_mb"]
        )
        self.event_log = EventLog()
        self.purchase_submission_enabled = bool(ui_settings["buy_mode"])
        self.buy_mode_resume_pending = False
        self.max_spend = ui_settings["spend_cap"]
        self.checker_started_at = None
        self.single_item_test_checker_started_at = None
        self.session_detected_outfits = 0
        self.session_successful_purchases = 0
        self.session_silver_spent = 0
        # Live per-item purchase ticks for the UI celebration: bumped the moment each item
        # in a buy list is secured (api_handler on_purchase observer), while the official
        # session totals above still commit once per batch in record_purchase_summary.
        # Cosmetic mirror only — never used for stats, history, or spend-cap math.
        self.purchase_progress_count = 0
        self.purchase_progress_silver = 0
        self.simulated_session_enabled = False
        self.debug_force_purchase_session_expired = False
        self.purchase_in_progress = False
        self.pending_auth_reset_reason = None
        self.browser_auth_lock = asyncio.Lock()
        self._auth_context_generation = 0
        self._pa_browser_lifecycle_generation = 0
        self._pa_browser_stop_requests = 0
        self._pa_browser_start_task = None
        self.consecutive_cycle_errors = 0
        self.single_item_test_cycle_errors = 0
        self.lifetime_successful_purchases = local_data["successful_purchases"]
        self.lifetime_silver_spent = local_data["silver_spent"]
        # Bumped whenever stats.db gains new history rows; the Stats view uses it to
        # re-render trend charts only when there is actually something new to draw.
        self.stats_history_revision = 0
        self._pending_scan_counts = {}
        self._pending_scan_count_total = 0
        self._pending_stats_writes = deque()
        self._stats_writer_task = None
        self._active_detection_episodes = {}
        self.update_check_on_startup = (
            load_update_check_on_startup() if self.persist_ui_settings else True
        )
        self.available_update_version = None
        self.update_check_completed = False
        self.update_check_in_progress = False
        self._browser_storage_summary = None
        self._browser_storage_generation = 0
        self._browser_storage_refresh_task = None
        self._browser_storage_refresh_generation = None

    def reload_lifetime_stats(self):
        self._assert_stats_db_path_allowed(self.stats_db_path)
        # While lifetime/history writes are still queued, the in-memory counters are
        # ahead of disk; reloading now would clobber them with stale totals.
        if self._pending_stats_writes or (
            self._stats_writer_task is not None and not self._stats_writer_task.done()
        ):
            return
        local_data = _load_local_data(path=self.stats_db_path, legacy_json_path=self.legacy_stats_path)
        self.lifetime_successful_purchases = local_data["successful_purchases"]
        self.lifetime_silver_spent = local_data["silver_spent"]

    def set_test_mode_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled == self.test_mode_enabled:
            return self.test_mode_enabled
        self.test_mode_enabled = enabled
        self.stats_db_path = TEST_STATS_DB_PATH if self.test_mode_enabled else STATS_DB_PATH
        self._assert_stats_db_path_allowed(self.stats_db_path)
        self.legacy_stats_path = None if self.test_mode_enabled else LOCAL_DATA_PATH
        self.reload_lifetime_stats()
        return self.test_mode_enabled

    def _assert_stats_db_path_allowed(self, path, *, require_explicit=False):
        if not self.test_mode_enabled:
            return
        if path is None:
            if require_explicit:
                raise RuntimeError("Test mode stats writes require an explicit stats DB path.")
            return
        if _same_file_path(path, STATS_DB_PATH):
            raise RuntimeError("Test mode cannot use the production stats DB.")

    async def save_local_data(self):
        # Lifetime totals go through the same serialized writer as history rows: one
        # write path, no concurrent connections, and a failure degrades to a logged
        # warning instead of an exception on the purchase path. The totals snapshot
        # is captured here at enqueue time and self-heals on the next save because
        # the upsert writes absolute values.
        self._enqueue_stats_write(
            "lifetime totals",
            _save_local_data,
            {
                "successful_purchases": self.lifetime_successful_purchases,
                "silver_spent": self.lifetime_silver_spent,
            },
            path=self.stats_db_path,
            bump_revision=False,
        )

    def _start_stats_writer(self):
        if self._stats_writer_task is not None and not self._stats_writer_task.done():
            return True
        try:
            self._stats_writer_task = asyncio.create_task(self._drain_stats_writes())
        except RuntimeError:
            self._stats_writer_task = None
            return False
        return True

    def _enqueue_stats_write(self, label, func, *args, bump_revision=True, **kwargs):
        self._assert_stats_db_path_allowed(kwargs.get("path"), require_explicit=True)
        self._pending_stats_writes.append(
            {
                "label": label,
                "func": func,
                "args": args,
                "kwargs": kwargs,
                "bump_revision": bump_revision,
            }
        )
        return self._start_stats_writer()

    async def _drain_stats_writes(self):
        try:
            while self._pending_stats_writes:
                job = self._pending_stats_writes.popleft()
                written = await self._run_stats_write(job)
                if written and job["bump_revision"]:
                    self.stats_history_revision += 1
        finally:
            self._stats_writer_task = None
            if self._pending_stats_writes:
                self._start_stats_writer()

    async def _run_stats_write(self, job):
        last_error = None
        for attempt in range(STATS_WRITE_RETRY_LIMIT + 1):
            try:
                await asyncio.to_thread(job["func"], *job["args"], **job["kwargs"])
                return True
            except Exception as exc:
                last_error = exc
                if attempt < STATS_WRITE_RETRY_LIMIT:
                    await asyncio.sleep(STATS_WRITE_RETRY_DELAY_SECONDS * (attempt + 1))
        detail = str(last_error).strip() or last_error.__class__.__name__
        self.add_event(f"Stats write failed for {job['label']}: {detail}", "warning")
        return False

    async def flush_stats_writes(self):
        flushed = False
        while True:
            if self._pending_stats_writes:
                self._start_stats_writer()
            task = self._stats_writer_task
            if task is None or task.done() or task is asyncio.current_task():
                return flushed
            flushed = True
            await task

    def _episode_start_rows(self, buy_list):
        current_rows = {}
        for item in buy_list or []:
            try:
                item_id, stock, price = item[0], item[1], item[2]
                stock_count = int(stock)
            except (IndexError, TypeError, ValueError):
                continue

            if stock_count <= 0:
                continue

            item_key = str(item_id)
            if item_key in current_rows:
                current_rows[item_key][1] = str(int(current_rows[item_key][1]) + stock_count)
            else:
                current_rows[item_key] = [item_key, str(stock_count), str(price)]

        episode_start_rows = []
        for item_key, row in current_rows.items():
            episode = self._active_detection_episodes.get(item_key)
            if episode is None:
                episode_start_rows.append(row)
                self._active_detection_episodes[item_key] = {"missed_scans": 0}
            else:
                episode["missed_scans"] = 0

        for item_key in list(self._active_detection_episodes):
            if item_key in current_rows:
                continue
            missed_scans = int(self._active_detection_episodes[item_key].get("missed_scans", 0)) + 1
            if missed_scans >= DETECTION_EPISODE_MISSED_SCAN_LIMIT:
                del self._active_detection_episodes[item_key]
            else:
                self._active_detection_episodes[item_key]["missed_scans"] = missed_scans

        return episode_start_rows

    async def _record_detection_history(self, buy_list):
        # Keep episode state on the monitor path so repeated polls cannot enqueue
        # duplicate detections, but leave SQLite work to the background writer.
        # Returns the newly started episode quantity so the session counter can
        # stay on the same episode-based scale as stats.db.
        episode_start_rows = self._episode_start_rows(buy_list)
        if not episode_start_rows:
            return 0
        new_episode_count = sum(int(row[1]) for row in episode_start_rows)
        detected_at = datetime.now().timestamp()
        rows = [list(row) for row in episode_start_rows]
        self._enqueue_stats_write(
            "detection history",
            stats_db.record_detection_events,
            rows,
            at=detected_at,
            path=self.stats_db_path,
        )
        return new_episode_count

    async def _record_purchase_history(self, purchase_records):
        records = [dict(record) for record in purchase_records or []]
        if not records:
            return False
        purchased_at = datetime.now().timestamp()
        return self._enqueue_stats_write(
            "purchase history",
            stats_db.record_purchase_events,
            records,
            at=purchased_at,
            path=self.stats_db_path,
        )

    async def _record_scan_coverage(self):
        day = datetime.now().date().isoformat()
        self._pending_scan_counts[day] = self._pending_scan_counts.get(day, 0) + 1
        self._pending_scan_count_total += 1
        if (
            self._pending_scan_count_total >= SCAN_COVERAGE_FLUSH_THRESHOLD
            or len(self._pending_scan_counts) > 1
        ):
            return await self.flush_scan_coverage(wait=False)
        return False

    async def flush_scan_coverage(self, wait=True):
        pending, self._pending_scan_counts = self._pending_scan_counts, {}
        self._pending_scan_count_total = 0
        if not pending:
            return False
        enqueued = self._enqueue_stats_write(
            "scan coverage",
            stats_db.add_daily_coverage,
            dict(pending),
            path=self.stats_db_path,
        )
        if wait:
            await self.flush_stats_writes()
        return enqueued

    def _schedule_scan_coverage_flush(self):
        if not self._pending_scan_counts:
            return
        try:
            asyncio.create_task(self.flush_scan_coverage(wait=False))
        except RuntimeError:
            pass

    def browser_storage_summary(self):
        return self._browser_storage_summary

    @property
    def browser_storage_generation(self):
        return self._browser_storage_generation

    async def refresh_browser_storage_summary(self, *, force=False):
        if self._browser_storage_summary is not None and not force:
            return self._browser_storage_summary

        generation = self._browser_storage_generation
        existing_task = self._browser_storage_refresh_task
        if (
            existing_task is not None
            and not existing_task.done()
            and self._browser_storage_refresh_generation == generation
        ):
            return await asyncio.shield(existing_task)

        async def measure_and_cache():
            summary = await asyncio.to_thread(measure_all_browser_profile_storage)
            if generation == self._browser_storage_generation:
                self._browser_storage_summary = summary
            return self._browser_storage_summary

        refresh_task = asyncio.create_task(measure_and_cache())
        self._browser_storage_refresh_task = refresh_task
        self._browser_storage_refresh_generation = generation
        try:
            return await asyncio.shield(refresh_task)
        finally:
            if self._browser_storage_refresh_task is refresh_task and refresh_task.done():
                self._browser_storage_refresh_task = None
                self._browser_storage_refresh_generation = None

    def invalidate_browser_storage_summary(self):
        self._browser_storage_summary = None
        self._browser_storage_generation += 1

    def browser_cache_cleanup_threshold_bytes(self):
        return int(self.browser_cache_cleanup_threshold_mb) * MIB

    def browser_cache_cleanup_threshold_label(self):
        if int(self.browser_cache_cleanup_threshold_mb) <= 0:
            return "Every manual auth open"
        return format_storage_size(self.browser_cache_cleanup_threshold_bytes())

    def set_browser_cache_cleanup_threshold_mb(self, threshold_mb):
        threshold = normalize_browser_cache_cleanup_threshold_mb(threshold_mb)
        self.browser_cache_cleanup_threshold_mb = threshold
        if self.persist_ui_settings:
            self.browser_cache_cleanup_threshold_mb = save_browser_cache_cleanup_threshold_mb(threshold)
        return self.browser_cache_cleanup_threshold_mb

    async def _clean_browser_cache_before_auth(self, profile_path, account_label):
        try:
            result = await _run_profile_io_in_thread(
                clean_disposable_browser_profile_cache,
                profile_path,
                threshold_bytes=self.browser_cache_cleanup_threshold_bytes(),
            )
        except asyncio.CancelledError:
            self.invalidate_browser_storage_summary()
            raise
        except Exception as exc:
            self.invalidate_browser_storage_summary()
            self.add_event(
                f"Browser cache cleanup skipped for {account_label}: {exc}. Opening Chrome anyway.",
                "warning",
            )
            return False

        self.invalidate_browser_storage_summary()
        if result.removed_anything:
            self.add_event(
                f"Cleaned {highlight(format_storage_size(result.removed_bytes))} of disposable "
                f"{account_label} browser cache before opening Chrome.",
                "info",
            )
        if result.had_failures:
            self.add_event(
                f"Browser cache cleanup partially completed for {account_label}; "
                f"{len(result.failed_paths)} cache path(s) could not be removed. Opening Chrome anyway.",
                "warning",
            )
        return result.removed_anything

    async def clean_browser_cache_now(self):
        async with self.browser_auth_lock:
            restart_pa_worker = self.pa_browser_worker_running
            restart_generation = (
                self._pa_browser_lifecycle_generation if restart_pa_worker else None
            )
            if self._pa_browser_worker is not None:
                try:
                    await self._close_pa_browser_worker()
                except BrowserAuthError as exc:
                    self.invalidate_browser_storage_summary()
                    self.add_event(
                        f"Manual browser cache cleanup skipped because the PA Chrome worker could not close: {exc}",
                        "error",
                    )
                    return None
            try:
                results = await _run_profile_io_in_thread(
                    clean_all_disposable_browser_profile_caches,
                    threshold_bytes=1,
                )
            except asyncio.CancelledError:
                restart_pa_worker = False
                self.invalidate_browser_storage_summary()
                raise
            except Exception as exc:
                self.invalidate_browser_storage_summary()
                self.add_event(f"Manual browser cache cleanup failed: {exc}", "error")
                return None
            finally:
                if (
                    restart_pa_worker
                    and restart_generation == self._pa_browser_lifecycle_generation
                    and self.pa_browser_keep_open
                    and not self.uses_steam_browser_session()
                ):
                    await self._ensure_pa_browser_worker_started()

        self.invalidate_browser_storage_summary()
        removed_bytes = sum(result.removed_bytes for result in results)
        failed_paths = sum(len(result.failed_paths) for result in results)
        if removed_bytes:
            self.add_event(
                f"Cleaned {highlight(format_storage_size(removed_bytes))} of disposable browser cache.",
                "info",
            )
        elif not failed_paths:
            self.add_event("No disposable browser cache found to clean.", "info")
        if failed_paths:
            self.add_event(
                f"Browser cache cleanup partially completed; {failed_paths} cache path(s) could not be removed.",
                "warning",
            )
        return {
            "removed_bytes": removed_bytes,
            "failed_paths": failed_paths,
            "results": results,
        }

    # Event log delegates: storage, rendering, and the classification policy live in
    # services.event_log; these keep the call sites and tests on one façade.
    def add_event(self, message, level="info", notable=False, divider=None):
        self.event_log.add(message, level, notable=notable, divider=divider)

    @property
    def events(self):
        return self.event_log.plain_events

    def events_for_filter(self, log_filter="all", dividers=False):
        return self.event_log.rendered_for_filter(log_filter, dividers=dividers)

    def has_unseen_alerts(self):
        return self.event_log.has_unseen_alerts()

    def mark_alerts_seen(self):
        self.event_log.mark_alerts_seen()

    def set_update_check_on_startup(self, enabled):
        self.update_check_on_startup = bool(enabled)
        if self.persist_ui_settings:
            self.update_check_on_startup = save_update_check_on_startup(self.update_check_on_startup)
        return self.update_check_on_startup

    async def check_for_update(self, manual=False):
        """Check GitHub for a newer published version (notify-only).

        Startup checks (``manual=False``) skip the remote lookup in test mode and when the
        user turned off the startup check, but still record the running version. A manual
        check always runs. The check is network-soft: it never raises, and on startup a
        failure stays silent. Returns the result, or ``None`` when the remote lookup was
        skipped.
        """
        if not manual:
            # Always record the running version at startup so the log shows the check ran,
            # even when the remote lookup is skipped (test mode or disabled).
            self.add_event(f"{APP_TITLE} {highlight(f'v{APP_VERSION}')}.", "info", notable=True)
        if not manual and (self.test_mode_enabled or not self.update_check_on_startup):
            return None
        if self.update_check_in_progress:
            return None

        self.update_check_in_progress = True
        try:
            result = await asyncio.to_thread(run_update_check)
        finally:
            self.update_check_in_progress = False

        if result.status == "update-available":
            self.update_check_completed = True
            self.available_update_version = result.latest_version
            self._announce_available_update(result.latest_version, manual=manual)
        elif result.status == "up-to-date":
            self.update_check_completed = True
            self.available_update_version = None
            if manual:
                self.add_event(
                    f"You are on the latest version ({result.current_version}).",
                    "info",
                    notable=True,
                )
        elif manual:  # error, surfaced only for an explicit user-initiated check
            self.add_event(
                "Could not check for updates. Check your connection and try again.",
                "warning",
            )
        return result

    def _announce_available_update(self, latest_version, manual=False):
        message = (
            f"Update available: version {highlight(latest_version)} (you have {APP_VERSION}). "
            f"Download it from {RELEASES_URL}"
        )
        if manual:
            self.add_event(message, "warning", notable=True)
            return
        self.add_event(message, "warning", notable=True)

    def current_delay_label(self):
        if self.delay == "custom":
            matching_key = self.matching_delay_choice(self.custom_delay_range)
            if matching_key:
                return self.delay_choices[matching_key][0]
            return "Custom"
        return self.delay_choices[self.delay][0]

    def matching_delay_choice(self, bounds):
        bounds = tuple(bounds)
        for key, (_label, preset_bounds) in self.delay_choices.items():
            if tuple(preset_bounds) == bounds:
                return key
        return None

    def current_delay_bounds(self):
        if self.delay == "custom":
            return self.custom_delay_range
        return self.delay_choices[self.delay][1]

    def current_delay_range(self):
        low, high = self.current_delay_bounds()
        return f"{low}-{high}s"

    def purchase_delay_range(self):
        low, high = self.purchase_delay_bounds
        return f"{self._format_seconds(low)}-{self._format_seconds(high)}s"

    def set_custom_delay_range(self, low, high):
        low = int(low)
        high = int(high)
        if low <= 0 or high <= 0 or low > high:
            raise ValueError("Custom delay must use positive seconds with min less than or equal to max.")
        self.custom_delay_range = (low, high)
        self.delay = self.matching_delay_choice(self.custom_delay_range) or "custom"
        self._persist_polling_settings()

    def set_delay_choice(self, delay):
        delay = str(delay)
        if delay not in self.delay_choices:
            raise ValueError("Unknown polling delay preset.")
        self.delay = delay
        self.custom_delay_range = tuple(self.delay_choices[delay][1])
        self._persist_polling_settings()

    def set_custom_delay_choice(self):
        self.delay = "custom"
        self._persist_polling_settings()

    def set_purchase_delay_range(self, low, high):
        low = float(low)
        high = float(high)
        if low < 0 or high < 0 or low > high:
            raise ValueError("Purchase delay must use non-negative seconds with min less than or equal to max.")
        self.purchase_delay_bounds = (low, high)
        if self.persist_ui_settings:
            save_purchase_delay_bounds(self.purchase_delay_bounds)

    def set_spend_cap(self, spend_cap):
        spend_cap = int(spend_cap or 0)
        if spend_cap < 0:
            raise ValueError("Spend cap must be 0 or a positive integer.")
        self.max_spend = spend_cap or None
        if self.persist_ui_settings:
            save_spend_cap(self.max_spend)

    def set_purchase_submission_enabled(self, enabled):
        self.purchase_submission_enabled = bool(enabled)
        # Any time buy mode is on, there is nothing pending to auto-resume. This also means a
        # user explicitly enabling buy mode clears a stale auto-resume flag.
        if self.purchase_submission_enabled:
            self.buy_mode_resume_pending = False
        if self.persist_ui_settings:
            save_buy_mode(self.purchase_submission_enabled)
        return self.purchase_submission_enabled

    def set_scan_scope(self, include_outfit_boxes=None, include_outfit_pieces=None):
        boxes = self.include_outfit_boxes if include_outfit_boxes is None else bool(include_outfit_boxes)
        pieces = self.include_outfit_pieces if include_outfit_pieces is None else bool(include_outfit_pieces)
        if not boxes and not pieces:
            raise ValueError("Choose at least one outfit scan category.")

        self.include_outfit_boxes = boxes
        self.include_outfit_pieces = pieces
        if self.persist_ui_settings:
            saved_scope = save_scan_scope(self.include_outfit_boxes, self.include_outfit_pieces)
            self.include_outfit_boxes = bool(saved_scope["include_outfit_boxes"])
            self.include_outfit_pieces = bool(saved_scope["include_outfit_pieces"])
        return self.include_outfit_boxes, self.include_outfit_pieces

    def set_include_outfit_boxes(self, enabled):
        self.set_scan_scope(include_outfit_boxes=enabled)
        return self.include_outfit_boxes

    def set_include_outfit_pieces(self, enabled):
        self.set_scan_scope(include_outfit_pieces=enabled)
        return self.include_outfit_pieces

    def has_scan_scope(self):
        return self.include_outfit_boxes or self.include_outfit_pieces

    def scan_scope_label(self):
        if self.include_outfit_boxes and self.include_outfit_pieces:
            return "Outfit boxes + pieces"
        if self.include_outfit_boxes:
            return "Outfit boxes"
        if self.include_outfit_pieces:
            return "Outfit pieces"
        return "No categories selected"

    def pause_buy_mode_for_session_refresh(self, reason):
        if not self.purchase_submission_enabled:
            return False
        self.set_purchase_submission_enabled(False)
        # Remember that the *app* paused buy mode (not the user) so it can auto-resume once the
        # session is refreshed, keeping the monitor continuous across transient session expiry.
        self.buy_mode_resume_pending = True
        self.add_event(
            f"{reason} Buy mode paused; it will resume automatically once the session is refreshed.",
            "warning",
            notable=True,
        )
        return True

    def resume_buy_mode_after_refresh(self):
        if not self.buy_mode_resume_pending:
            return False
        # set_purchase_submission_enabled(True) clears buy_mode_resume_pending.
        self.set_purchase_submission_enabled(True)
        self.add_event("Marketplace session refreshed; buy mode resumed.", "success", notable=True)
        return True

    def _persist_polling_settings(self):
        if self.persist_ui_settings:
            save_polling_settings(self.delay, self.custom_delay_range)

    def _set_saved_session_last_known_valid(self, valid):
        self.saved_session_last_known_valid = bool(valid)
        if self.persist_ui_settings:
            self.saved_session_last_known_valid = save_saved_session_last_known_valid(valid)
        return self.saved_session_last_known_valid

    def _api_has_session_cookies(self):
        if hasattr(self.api_handler, "has_session_cookies"):
            return self.api_handler.has_session_cookies()
        session = getattr(self.api_handler, "session", None)
        return bool(getattr(session, "cookies", None))

    def _auth_context_snapshot(self):
        return self._auth_context_generation, self.account_mode

    def _auth_context_is_current(self, snapshot, *, expected_mode=None):
        generation, mode = snapshot
        return (
            generation == self._auth_context_generation
            and mode == self.account_mode
            and (expected_mode is None or mode == expected_mode)
        )

    def _discard_stale_imported_session(self):
        # validate_and_save_imported_session temporarily installs its candidate
        # session before awaiting server validation. If a reset wins that race, the
        # old auth task must erase its candidate (including any saved copy) without
        # applying success side effects to the new context.
        self.steam_auto_reauth_enabled = False
        if hasattr(self.api_handler, "clear_session"):
            self.api_handler.clear_session()
        else:
            self.api_handler.login_status = False
        self._set_saved_session_last_known_valid(False)

    async def _saved_session_is_valid(self, auth_context=None):
        if not self._api_has_session_cookies():
            return False
        try:
            status = await self._check_session_expired()
        except MarketplaceAPIError:
            return False

        if auth_context is not None and not self._auth_context_is_current(auth_context):
            return False

        if status == 0:
            self.api_handler.login_status = True
            self._set_saved_session_last_known_valid(True)
            if self.uses_steam_browser_session():
                self.steam_auto_reauth_enabled = True
            self.start_login_status_checker()
            self.resume_buy_mode_after_refresh()
            return True

        self.api_handler.login_status = False
        return False

    def _format_seconds(self, value):
        value = float(value)
        if value.is_integer():
            return str(int(value))
        return f"{value:g}"

    def runtime_label(self):
        started_at = None
        if self.checker_enabled:
            started_at = self.checker_started_at
        elif self.single_item_test_checker_enabled:
            started_at = self.single_item_test_checker_started_at

        if started_at is None:
            return "00:00:00"
        return format_duration(time.monotonic() - started_at)

    def monitor_running(self):
        return self.checker_enabled or self.single_item_test_checker_enabled

    def uses_steam_browser_session(self):
        return self.account_mode == STEAM_BROWSER_MODE

    def account_mode_label(self):
        return account_mode_label(self.account_mode)

    def account_mode_detail(self):
        return account_mode_detail(self.account_mode)

    def steam_browser_profile_needs_setup(self):
        return self.uses_steam_browser_session() and not self.steam_browser_profile_prepared

    def steam_auto_reauth_available(self):
        return self.uses_steam_browser_session() and (
            self.steam_browser_profile_prepared or self.steam_auto_reauth_enabled
        )

    def _set_steam_pa_cookie_consent_prepared(self, prepared):
        self.steam_pa_cookie_consent_prepared = bool(prepared)
        if self.persist_ui_settings:
            self.steam_pa_cookie_consent_prepared = save_steam_pa_cookie_consent_prepared(prepared)
        return self.steam_pa_cookie_consent_prepared

    def _set_pa_browser_profile_prepared(self, prepared):
        self.pa_browser_profile_prepared = bool(prepared)
        if self.persist_ui_settings:
            self.pa_browser_profile_prepared = save_pa_browser_profile_prepared(prepared)
        return self.pa_browser_profile_prepared

    def invalidate_pa_browser_identity(self):
        """Require the next PA auth context to clear cookies before login."""
        return self._set_pa_browser_profile_prepared(False)

    @property
    def pa_browser_worker_running(self):
        worker = self._pa_browser_worker
        return bool(worker is not None and worker.running)

    @property
    def pa_browser_worker_owns_profile(self):
        worker = self._pa_browser_worker
        return bool(worker is not None and worker.owns_profile)

    @property
    def pa_browser_worker_has_resources(self):
        worker = self._pa_browser_worker
        return bool(worker is not None and worker.has_resources)

    async def ensure_pa_browser_worker_started(self, *, cleanup_browser_cache=False, auth_context=None):
        if auth_context is None:
            auth_context = self._auth_context_snapshot()
        async with self.browser_auth_lock:
            return await self._ensure_pa_browser_worker_started(
                cleanup_browser_cache=cleanup_browser_cache,
                auth_context=auth_context,
            )

    async def _ensure_pa_browser_worker_started(self, *, cleanup_browser_cache=False, auth_context=None):
        if auth_context is None:
            auth_context = self._auth_context_snapshot()
        generation = self._pa_browser_lifecycle_generation
        if not self._pa_browser_worker_start_allowed(generation, auth_context):
            return False
        if self.pa_browser_worker_running:
            return True

        # A user can close the only Chrome page while Patchright still owns the persistent
        # profile. Dispose that stale context before touching its cache or launching again.
        if self._pa_browser_worker is not None:
            try:
                await self._close_pa_browser_worker()
            except BrowserAuthError as exc:
                self.add_event(
                    f"Pearl Abyss Chrome worker could not release its stale profile: {exc}",
                    "error",
                )
                return False
            if not self._pa_browser_worker_start_allowed(generation, auth_context):
                return False

        if cleanup_browser_cache:
            await self._clean_browser_cache_before_auth(PA_MARKET_PROFILE_PATH, "Pearl Abyss Account")

        # Cache cleanup runs in a thread. The user may disable the setting, change modes, or
        # exit while it is in flight; never launch a now-unwanted visible window afterward.
        if not self._pa_browser_worker_start_allowed(generation, auth_context):
            return False

        worker = PersistentPABrowserWorker(profile_path=PA_MARKET_PROFILE_PATH)
        self._pa_browser_worker = worker
        start_operation = asyncio.create_task(
            worker.start(status_callback=self._browser_auth_status)
        )
        self._pa_browser_start_task = start_operation
        try:
            try:
                await start_operation
            finally:
                if self._pa_browser_start_task is start_operation:
                    self._pa_browser_start_task = None
        except asyncio.CancelledError:
            # Worker.close() cancels an in-progress launch. Treat that as an intentional stop,
            # while preserving ordinary external cancellation semantics for the caller.
            if not self._pa_browser_worker_start_allowed(generation, auth_context):
                return False
            raise
        except BrowserAuthError as exc:
            self.add_event(f"Pearl Abyss Chrome worker failed to start: {exc}", "error")
            return False

        if not self._pa_browser_worker_start_allowed(generation, auth_context):
            try:
                await worker.close(status_callback=self._browser_auth_status)
            except BrowserAuthError as exc:
                self.add_event(
                    f"Pearl Abyss Chrome worker stopped being needed but could not close: {exc}",
                    "error",
                )
            else:
                if self._pa_browser_worker is worker:
                    self._pa_browser_worker = None
                self.invalidate_browser_storage_summary()
            return False

        self.invalidate_browser_storage_summary()
        return True

    def _pa_browser_worker_start_allowed(self, generation, auth_context):
        return bool(
            not self._pa_browser_stop_requests
            and generation == self._pa_browser_lifecycle_generation
            and self._auth_context_is_current(auth_context, expected_mode=PA_CREDENTIALS_MODE)
            and self.pa_browser_keep_open
        )

    async def stop_pa_browser_worker(self):
        self._pa_browser_lifecycle_generation += 1
        self._pa_browser_stop_requests += 1
        start_operation = self._pa_browser_start_task
        if start_operation is not None and not start_operation.done():
            start_operation.cancel()
        try:
            # Do not wait on browser_auth_lock here: a retained worker can interrupt its own
            # in-flight launch/auth operation, while a cleanup with no worker observes the
            # lifecycle generation after its filesystem thread has been safely joined.
            try:
                return await self._close_pa_browser_worker()
            finally:
                # The worker performs a bounded operation drain. Retrieve a completed launch
                # result here, but never reintroduce an unbounded shutdown wait at the manager.
                if start_operation is not None and start_operation.done():
                    await asyncio.gather(start_operation, return_exceptions=True)
        finally:
            self._pa_browser_stop_requests -= 1

    async def stop_pa_browser_worker_best_effort(self, reason="Pearl Abyss Chrome worker could not close"):
        """Try to release the PA browser without failing a broader lifecycle action."""
        try:
            return await self.stop_pa_browser_worker()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.add_event(f"{reason}: {exc}", "error")
            return False

    async def _close_pa_browser_worker(self):
        worker = self._pa_browser_worker
        if worker is None:
            return False
        closed = await worker.close(status_callback=self._browser_auth_status)
        if self._pa_browser_worker is worker:
            self._pa_browser_worker = None
            self.invalidate_browser_storage_summary()
        return closed

    async def set_pa_browser_keep_open(self, enabled):
        enabled = bool(enabled)
        if not enabled:
            # Keep routing on the worker branch until its profile ownership is fully released.
            # A refresh already inside browser_auth_lock then fails cleanly at the stopping worker
            # instead of opening a disposable context against the same profile mid-close.
            try:
                await self.stop_pa_browser_worker()
            except BrowserAuthError as exc:
                self.add_event(f"Pearl Abyss Chrome worker could not be disabled: {exc}", "error")
                return True
            self.pa_browser_keep_open = False
            if self.persist_ui_settings:
                self.pa_browser_keep_open = save_pa_browser_keep_open(False)
            self.add_event("Pearl Abyss Chrome worker disabled.", "info")
            return False

        # Enabling waits for any disposable PA refresh to release the profile before changing the
        # routing preference. This prevents an in-flight legacy refresh from suddenly switching
        # to a worker that does not exist yet, and prevents two contexts from opening one profile.
        async with self.browser_auth_lock:
            self.pa_browser_keep_open = True
            if self.persist_ui_settings:
                self.pa_browser_keep_open = save_pa_browser_keep_open(True)

            if self.uses_steam_browser_session():
                self.add_event(
                    "Pearl Abyss Chrome worker enabled; it will start when Pearl Abyss Account is selected.",
                    "info",
                )
                return True

            started = await self._ensure_pa_browser_worker_started(cleanup_browser_cache=True)
            if not started:
                self.add_event(
                    "Pearl Abyss Chrome worker is enabled but could not start. Toggle it off and on to retry.",
                    "warning",
                )
            return self.pa_browser_keep_open

    async def _acquire_pa_market_cookies(self, *, allow_worker_start=False, **kwargs):
        if self._pa_browser_stop_requests:
            raise BrowserAuthError("Pearl Abyss Chrome worker is stopping.")
        if not self.pa_browser_keep_open:
            if self._pa_browser_worker is not None:
                raise BrowserAuthError(
                    "Pearl Abyss Chrome worker still owns its browser profile. Close Chrome manually before refreshing."
                )
            return await acquire_market_cookies(**kwargs)

        if not self.pa_browser_worker_running:
            if not allow_worker_start or not await self._ensure_pa_browser_worker_started(
                cleanup_browser_cache=True,
            ):
                raise BrowserAuthError(
                    "Pearl Abyss Chrome worker is not running. Open App Settings and restart it before refreshing."
                )
        return await self._pa_browser_worker.acquire_market_cookies(**kwargs)

    async def _update_pa_browser_landing_validation(self, valid):
        """Best-effort bridge from authoritative HTTP validation to the parked page."""
        worker = self._pa_browser_worker
        update = getattr(worker, "update_session_validation", None)
        if not callable(update):
            return False
        try:
            result = update(bool(valid))
            if hasattr(result, "__await__"):
                return await result
            return bool(result)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The landing page is cosmetic and must never change an authentication result.
            return False

    def set_account_mode(self, mode):
        normalized = normalize_account_mode(mode)
        if normalized != self.account_mode:
            self._auth_context_generation += 1
            self.steam_auto_reauth_enabled = False
            self.buy_mode_resume_pending = False
        self.account_mode = save_account_mode(normalized) if self.persist_ui_settings else normalized
        self.api_handler.account_mode = self.account_mode
        return self.account_mode

    async def change_account_mode(self, mode):
        normalized = normalize_account_mode(mode)
        previous_mode = self.account_mode
        if normalized != previous_mode:
            # Arm the purchase stop before publishing the new mode or awaiting any
            # browser cleanup. Otherwise an old-mode request can finish in that gap
            # and be interpreted by the new mode's recovery branch.
            await self.reset_authentication_context("Login method changed")
            self.account_mode = save_account_mode(normalized) if self.persist_ui_settings else normalized
            self.api_handler.account_mode = self.account_mode
            if self.uses_steam_browser_session():
                await self.stop_pa_browser_worker_best_effort(
                    "Login method changed, but Pearl Abyss Chrome worker cleanup did not finish"
                )
            if not self.uses_steam_browser_session() and self.pa_browser_keep_open:
                await self.ensure_pa_browser_worker_started(cleanup_browser_cache=True)
            return True
        self.account_mode = save_account_mode(normalized) if self.persist_ui_settings else normalized
        self.api_handler.account_mode = self.account_mode
        return False

    async def reset_authentication_context(self, reason):
        # Invalidate every saved-session check or browser refresh that started in
        # the previous context before this coroutine reaches its first await.
        self._auth_context_generation += 1
        self.steam_auto_reauth_enabled = False
        self.buy_mode_resume_pending = False
        self.set_purchase_submission_enabled(False)

        purchase_was_in_progress = self.purchase_in_progress
        if purchase_was_in_progress:
            self.pending_auth_reset_reason = reason
            self.checker_stop_requested = True
            self._checker_purchase_stop_event.set()
            if self._purchase_owner_task is self.single_item_test_checker_task:
                self._single_item_test_purchase_stop_event.set()
            self.add_event(
                f"{reason}. The in-flight purchase request will finish and be recorded, remaining attempts "
                "will be skipped, then the monitor will stop and the marketplace session will be cleared.",
                "warning",
            )

        # The stop state above is synchronous. Cleanup is allowed to await only
        # after an in-flight purchase can already observe STOP.
        await self.stop_login_status_checker()
        if purchase_was_in_progress:
            # The purchase may have completed (and applied the deferred clear) while
            # the checker was stopping. Report whether that clear is now complete,
            # but never perform it a second time here.
            return self.pending_auth_reset_reason is None

        await self.stop_checker()
        await self.stop_single_item_test_checker()
        self._clear_marketplace_session(reason)
        return True

    def _complete_pending_auth_reset_if_ready(self):
        if self.pending_auth_reset_reason and not self.purchase_in_progress:
            self.checker_stop_requested = True
            self._clear_marketplace_session(self.pending_auth_reset_reason)
            return True
        return False

    def _clear_marketplace_session(self, reason):
        # A deferred clear may run after another auth task started. Advance again so
        # that task cannot repopulate the session after this explicit reset.
        self._auth_context_generation += 1
        self.pending_auth_reset_reason = None
        self.steam_auto_reauth_enabled = False
        self.buy_mode_resume_pending = False
        self.set_purchase_submission_enabled(False)
        self.simulated_session_enabled = False
        if hasattr(self.api_handler, "clear_session"):
            self.api_handler.clear_session()
        else:
            self.api_handler.login_status = False
        self._set_saved_session_last_known_valid(False)
        self.add_event(
            f"{reason}. Marketplace session cleared. Refresh Session before buying.",
            "warning",
            notable=True,
        )

    def monitor_status_label(self):
        if self.single_item_test_checker_enabled:
            return "Test Scan"
        if self.checker_enabled:
            return "Running"
        return "Stopped"

    def monitor_mode_label(self):
        if self.single_item_test_checker_enabled:
            return "Test buy" if self.single_item_test_purchase_enabled else "Single item"
        return "Buy mode" if self.purchase_submission_enabled else "Watch only"

    async def start_checker(self):
        if not self.has_scan_scope():
            self.add_event(
                "Monitor cannot start until at least one outfit scan category is selected.",
                "warning",
            )
            return False

        if self.purchase_submission_enabled and not self.api_handler.login_status:
            return False

        if self.single_item_test_checker_task is not None and not self.single_item_test_checker_task.done():
            return False

        if self.checker_task is not None and not self.checker_task.done():
            self.checker_enabled = True
            return False

        if self.pa_browser_keep_open and not self.uses_steam_browser_session():
            await self.ensure_pa_browser_worker_started(cleanup_browser_cache=True)

        self._active_detection_episodes = {}
        self.checker_stop_requested = False
        self._checker_purchase_stop_event = asyncio.Event()
        self.checker_started_at = time.monotonic()
        self.checker_task = asyncio.create_task(self.checker())
        self.checker_task.add_done_callback(self._handle_checker_done)
        self.checker_enabled = True
        mode = self.monitor_mode_label()
        mode_markup = highlight_brand(mode) if self.purchase_submission_enabled else highlight(mode)
        scope = self.scan_scope_label()
        self.add_event(
            f"Monitor started — {mode_markup} · {highlight(scope)}.",
            "info",
            notable=True,
            divider=f"monitor started · {mode.lower()} · {scope.lower()}",
        )
        return True

    async def start_single_item_test_checker(self, allow_purchase=False):
        if not self.test_mode_enabled:
            return False

        if self.checker_task is not None and not self.checker_task.done():
            return False

        if self.single_item_test_checker_task is not None and not self.single_item_test_checker_task.done():
            self.single_item_test_checker_enabled = True
            return False

        self.single_item_test_checker_started_at = time.monotonic()
        self.single_item_test_purchase_enabled = bool(allow_purchase)
        self._single_item_test_purchase_stop_event = asyncio.Event()
        self.single_item_test_checker_task = asyncio.create_task(self.single_item_test_checker())
        self.single_item_test_checker_task.add_done_callback(self._handle_single_item_test_checker_done)
        self.single_item_test_checker_enabled = True
        return True

    def _handle_checker_done(self, task):
        if task.cancelled():
            return

        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return

        if exc is not None:
            self.add_event(f"Monitor stopped after an unexpected error: {exc}", "error")

        should_flush_pending_coverage = not self.checker_stop_requested
        self.checker_enabled = False
        self.checker_stop_requested = False
        self.checker_started_at = None
        if self.checker_task is task:
            self.checker_task = None
        if should_flush_pending_coverage:
            self._schedule_scan_coverage_flush()

    def _handle_single_item_test_checker_done(self, task):
        if task.cancelled():
            return

        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return

        if exc is not None:
            self.add_event(f"Single-item test monitor stopped after an unexpected error: {exc}", "error")

        self.single_item_test_checker_enabled = False
        self.single_item_test_purchase_enabled = False
        self.single_item_test_checker_started_at = None
        if self.single_item_test_checker_task is task:
            self.single_item_test_checker_task = None

    async def stop_checker(self):
        task = self.checker_task
        was_running = bool(task and not task.done())
        self.checker_stop_requested = True
        self._checker_purchase_stop_event.set()
        if task and not task.done():
            purchase_owned_by_checker = self.purchase_in_progress and self._purchase_owner_task is task
            if purchase_owned_by_checker:
                self.add_event(
                    "Monitor stop requested. Waiting for the in-flight purchase request to finish safely.",
                    "warning",
                    notable=True,
                )
            else:
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self.checker_enabled = False
        self.checker_stop_requested = False
        self.checker_started_at = None
        self.checker_task = None
        await self.flush_scan_coverage()
        await self.flush_stats_writes()
        if was_running:
            self.add_event("Monitor stopped.", "info", notable=True, divider="monitor stopped")
        return was_running

    async def stop_single_item_test_checker(self):
        task = self.single_item_test_checker_task
        was_running = bool(task and not task.done())
        self._single_item_test_purchase_stop_event.set()
        if task and not task.done():
            purchase_owned_by_checker = self.purchase_in_progress and self._purchase_owner_task is task
            if not purchase_owned_by_checker:
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self.single_item_test_checker_enabled = False
        self.single_item_test_purchase_enabled = False
        self.single_item_test_checker_started_at = None
        self.single_item_test_checker_task = None
        await self.flush_stats_writes()
        return was_running

    def start_login_status_checker(self):
        if self.login_checker_task is None or self.login_checker_task.done():
            self.login_checker_task = asyncio.create_task(self.login_status_checker())

    async def stop_login_status_checker(self):
        if self.login_checker_task and not self.login_checker_task.done():
            self.login_checker_task.cancel()
            try:
                await self.login_checker_task
            except asyncio.CancelledError:
                pass
        self.login_checker_task = None

    async def checker(self):
        try:
            while not self.checker_stop_requested:
                try:
                    buy_list = await self.api_handler.check_stock(
                        include_outfit_boxes=self.include_outfit_boxes,
                        include_outfit_pieces=self.include_outfit_pieces,
                    )
                    await self._record_scan_coverage()
                    await self.process_detected_outfits(buy_list)
                    self.consecutive_cycle_errors = 0
                except Exception as exc:
                    self.consecutive_cycle_errors += 1
                    self.add_event(f"Monitor cycle failed: {exc}", "error")

                if self._complete_pending_auth_reset_if_ready() or self.checker_stop_requested:
                    break

                sleep_duration = self.next_sleep_duration()
                await asyncio.sleep(sleep_duration)
        except asyncio.CancelledError:
            raise

    async def single_item_test_checker(self):
        if not self.test_mode_enabled:
            self.add_event("Single-item test monitor is only available in test mode.", "warning")
            return

        item_name = SINGLE_ITEM_TEST_TARGET["name"]
        try:
            while not self._single_item_test_purchase_stop_event.is_set():
                try:
                    buy_list = await check_single_item_stock(self.api_handler, SINGLE_ITEM_TEST_TARGET)
                    await self.process_detected_outfits(
                        buy_list,
                        allow_purchase=self.single_item_test_purchase_enabled,
                        item_noun="test item",
                        adjust_pricing=False,
                    )
                    self.single_item_test_cycle_errors = 0
                except Exception as exc:
                    self.single_item_test_cycle_errors += 1
                    self.add_event(f"{item_name} test scan failed: {exc}", "error")

                if self._single_item_test_purchase_stop_event.is_set():
                    break

                sleep_duration = self.next_single_item_test_sleep_duration()
                await asyncio.sleep(sleep_duration)
        except asyncio.CancelledError:
            raise

    def next_sleep_duration(self):
        return self._next_sleep_duration(self.consecutive_cycle_errors)

    def next_single_item_test_sleep_duration(self):
        return self._next_sleep_duration(self.single_item_test_cycle_errors)

    def _next_sleep_duration(self, cycle_errors):
        low, high = self.current_delay_bounds()
        if cycle_errors <= 0:
            return random.uniform(low, high)

        multiplier = min(MAX_ERROR_BACKOFF_MULTIPLIER, 1 + cycle_errors)
        return random.uniform(low * multiplier, high * multiplier)

    async def process_detected_outfits(self, buy_list, allow_purchase=None, item_noun="outfit", adjust_pricing=True):
        new_episode_count = await self._record_detection_history(buy_list)

        if not buy_list:
            return

        # The session counter tracks new availability episodes (same scale as the
        # stats.db history); buying always operates on the full list.
        detected_count = self._detected_outfit_count(buy_list)
        self.session_detected_outfits += new_episode_count
        purchase_enabled = self.purchase_submission_enabled if allow_purchase is None else allow_purchase
        # The total is summed stock across distinct listings, so "4 -> 5" is ambiguous
        # on its own: spell out the listing count whenever more than one item id is in
        # the scan, and how much of the total is genuinely new.
        distinct_listings = len({str(item[0]) for item in buy_list})
        if distinct_listings > 1:
            detected_label = (
                f"{detected_count} available across {distinct_listings} "
                f"{self._pluralize(item_noun, distinct_listings)}"
            )
        else:
            detected_label = f"{detected_count} available {self._pluralize(item_noun, detected_count)}"
        if new_episode_count != detected_count:
            detected_label = f"{detected_label} ({new_episode_count} new)"
        subject = item_noun[:1].upper() + item_noun[1:]

        if purchase_enabled:
            # Buy mode logs every scan because a purchase attempt follows; the
            # "(N new)" suffix keeps the log on the same episode scale as the
            # Detected counter.
            self.add_event(
                f"{subject} detected: {highlight(detected_label)}. Attempting purchase.",
                "success",
                notable=True,
            )
            await self.buy_item(buy_list, adjust_pricing=adjust_pricing)
        elif new_episode_count > 0:
            # Watch-only logs once per episode start; repeat sightings of the same
            # lingering listings would otherwise fill the event log every poll.
            self.add_event(f"{subject} detected: {highlight(detected_label)}.", "success", notable=True)

    def _detected_outfit_count(self, buy_list):
        return sum(int(item[1]) for item in buy_list)

    def _pluralize(self, noun, count):
        if count == 1:
            return noun
        return f"{noun}s"

    async def debug_fake_outfit_detection(self):
        if not self.test_mode_enabled:
            return False

        self._active_detection_episodes.clear()
        await self.process_detected_outfits(DEBUG_OUTFIT_LISTING, allow_purchase=False)
        return True

    async def debug_fake_multi_outfit_detection(self):
        if not self.test_mode_enabled:
            return False

        self._active_detection_episodes.clear()
        await self.process_detected_outfits(DEBUG_MULTI_OUTFIT_INITIAL_LISTING, allow_purchase=False)
        await self.process_detected_outfits(DEBUG_MULTI_OUTFIT_JOINED_LISTING, allow_purchase=False)
        return True

    async def debug_simulate_purchase_success(self):
        if not self.test_mode_enabled:
            return False

        self._active_detection_episodes.clear()
        new_episode_count = await self._record_detection_history(DEBUG_OUTFIT_LISTING)
        detected_count = self._detected_outfit_count(DEBUG_OUTFIT_LISTING)
        self.session_detected_outfits += new_episode_count
        self.add_event(
            f"Outfit detected: {highlight(detected_count)} available outfits. Simulating purchase.",
            "success",
            notable=True,
        )

        adjusted_buy_list = await self.adjust_prices(DEBUG_OUTFIT_LISTING)
        await self.record_purchase_summary(
            self._simulated_purchase_summary(adjusted_buy_list, "Simulated purchase succeeded")
        )
        return True

    async def debug_simulate_bundled_purchase_success(self, progress_callback=None):
        if not self.test_mode_enabled:
            return False

        self._active_detection_episodes.clear()
        new_episode_count = await self._record_detection_history(DEBUG_BUNDLED_OUTFIT_LISTING)
        detected_count = self._detected_outfit_count(DEBUG_BUNDLED_OUTFIT_LISTING)
        self.session_detected_outfits += new_episode_count
        self.add_event(
            f"Outfit detected: {highlight(detected_count)} available outfits in one bundled buy list.",
            "success",
            notable=True,
        )

        adjusted_buy_list = await self.adjust_prices(DEBUG_BUNDLED_OUTFIT_LISTING)
        item_id, stock, price = adjusted_buy_list[0]
        purchase_records = []
        self._sync_purchase_progress_to_committed()
        self.purchase_in_progress = True
        try:
            stock_count = int(stock)
            for index in range(stock_count):
                record = {
                    "item_id": item_id,
                    "price": int(price),
                    "count": 1,
                    "result_code": 0,
                }
                purchase_records.append(record)
                self._note_purchase_progress(record)
                if progress_callback is not None:
                    try:
                        progress_callback()
                    except Exception:
                        pass
                if index < stock_count - 1:
                    await asyncio.sleep(DEBUG_BUNDLED_PURCHASE_TICK_SECONDS)

            await self.record_purchase_summary(
                {
                    "purchase_records": purchase_records,
                    "events": [
                        {
                            "level": "success",
                            "message": f"Simulated bundled buy list succeeded for {len(purchase_records)} outfits.",
                        }
                    ],
                }
            )
        finally:
            self.purchase_in_progress = False
            self._complete_pending_auth_reset_if_ready()
        return True

    async def debug_run_live_buy_error_probe(self):
        if not self.test_mode_enabled:
            return False

        if self.simulated_session_enabled:
            self.add_event("Live buy error probe requires a real marketplace session; disable test session first.", "warning")
            return False

        if not getattr(self.api_handler, "login_status", False):
            self.add_event("Live buy error probe requires an online marketplace session.", "warning")
            return False

        target = LIVE_BUY_ERROR_TEST_TARGET
        buy_list = live_buy_error_test_listing(target)
        self._active_detection_episodes.clear()
        self.add_event(
            f"Live buy error probe submitting item {target['main_key']} at {target['max_buy_price']} silver.",
            "warning",
        )
        await self.process_detected_outfits(
            buy_list,
            allow_purchase=True,
            item_noun="live buy probe",
            adjust_pricing=False,
        )
        return True

    def debug_invalidate_marketplace_session(self):
        if not self.test_mode_enabled:
            return False

        self._debug_invalidate_marketplace_state()
        self.add_event(
            "Test: app marketplace session cleared; the next recovery will also clear the browser "
            "marketplace session before re-authentication.",
            "warning",
        )
        return True

    def _debug_invalidate_marketplace_state(self):
        """Invalidate app-owned market state without changing browser-profile preparation."""
        was_logged_in = bool(getattr(self.api_handler, "login_status", False))
        self._auth_context_generation += 1
        self.simulated_session_enabled = False
        self.debug_force_purchase_session_expired = True
        if hasattr(self.api_handler, "clear_session"):
            self.api_handler.clear_session()
        else:
            self.api_handler.login_status = False
        self._set_saved_session_last_known_valid(False)
        if self.uses_steam_browser_session() and was_logged_in:
            self.steam_auto_reauth_enabled = True

    async def debug_expire_pa_login_session(self):
        """Prepare a genuine PA credential-login test in the retained browser worker."""
        if not self.test_mode_enabled:
            return False
        if self.uses_steam_browser_session():
            self.add_event("Expire PA Login is only available in Pearl Abyss Account mode.", "warning")
            return False
        if not self.pa_browser_keep_open or not self.pa_browser_worker_running:
            self.add_event(
                "Expire PA Login requires the Keep Open Chrome worker to be running.",
                "warning",
                notable=True,
            )
            return False

        auth_context = self._auth_context_snapshot()
        async with self.browser_auth_lock:
            if not self._auth_context_is_current(auth_context, expected_mode=PA_CREDENTIALS_MODE):
                return False
            worker = self._pa_browser_worker
            if worker is None or not worker.running:
                self.add_event(
                    "Expire PA Login could not run because the Keep Open Chrome worker is closed.",
                    "warning",
                    notable=True,
                )
                return False
            try:
                cleared_count = await worker.clear_pa_login_session_cookies()
            except BrowserAuthError as exc:
                self.add_event(f"Expire PA Login failed: {exc}", "error", notable=True)
                return False
            if not self._auth_context_is_current(auth_context, expected_mode=PA_CREDENTIALS_MODE):
                return False

            self._debug_invalidate_marketplace_state()
            self.invalidate_browser_storage_summary()
            self.add_event(
                "Test: app marketplace session plus browser marketplace and Pearl Abyss login "
                f"cookies cleared ({highlight(cleared_count)} cookies); consent and saved "
                "credentials were preserved. Run Session Check to test automatic credential login.",
                "warning",
                notable=True,
            )
            return True

    def debug_toggle_steam_auto_reauth(self):
        if not self.test_mode_enabled or not self.uses_steam_browser_session():
            return None

        self.steam_auto_reauth_enabled = not self.steam_auto_reauth_enabled
        return self.steam_auto_reauth_enabled

    async def debug_run_reauthentication_check(self):
        if not self.test_mode_enabled:
            return False

        self.add_event("Simulated purchase response: login session expired.", "warning")
        recovered = await self._recover_purchase_session_for_retry(
            force_browser_refresh=True,
            clear_browser_market_session=True,
        )
        if recovered:
            self.debug_force_purchase_session_expired = False
        return recovered

    async def debug_run_session_check_now(self):
        """Run one iteration of the production periodic session checker on demand (test mode).

        This is the real `_run_one_session_check` path: it detects expiry (honoring the "Expire
        Session" override) and, if expired, runs `handle_expired_session` exactly as the idle bot
        would. Use "Expire Session" first to exercise the detect -> auto re-auth flow.
        """
        if not self.test_mode_enabled:
            return None

        self.add_event("Test: running the periodic session check now.", "info")
        result = await self._run_one_session_check()
        if result and self.debug_force_purchase_session_expired:
            # A forced-expiry run that recovered: drop the override so later checks see the real
            # session instead of staying permanently "expired".
            self.debug_force_purchase_session_expired = False
        return result

    def reset_steam_initial_setup_status(self):
        """Mark Steam initial setup incomplete and clear the one-time consent flag.

        Ungated maintenance action used by App Settings; the test-mode debug
        control delegates here.
        """
        self.steam_browser_profile_prepared = save_steam_browser_profile_prepared(False)
        self._set_steam_pa_cookie_consent_prepared(False)
        self.steam_auto_reauth_enabled = False
        self.invalidate_browser_storage_summary()
        self.add_event("Initial Steam setup status reset to incomplete.", "warning")
        return True

    def debug_clear_steam_initial_setup_status(self):
        if not self.test_mode_enabled:
            return False

        return self.reset_steam_initial_setup_status()

    async def debug_clear_market_cookies_keep_steam_login(self):
        if not self.test_mode_enabled:
            return False

        if not self.uses_steam_browser_session():
            self.add_event(
                "Clear cookies (keep Steam login) is only available in Steam Account mode.",
                "warning",
            )
            return False

        try:
            cleared_count = await clear_market_cookies_keep_steam_login(profile_path=STEAM_MARKET_PROFILE_PATH)
        except BrowserAuthError as exc:
            self.add_event(f"Test cookie clear failed: {exc}", "error")
            return False

        # Re-arm the re-auth flow so the next refresh runs the full cookie-box + Steam-button path,
        # while keeping the Steam login and the saved Steam setup so no Steam re-login is needed.
        self._set_steam_pa_cookie_consent_prepared(False)
        self._set_saved_session_last_known_valid(False)
        self.invalidate_browser_storage_summary()
        self.add_event(
            f"Test mode: cleared {cleared_count} non-Steam cookies (market/PA/consent); kept Steam login. "
            "Run Reauth Check to exercise the full re-auth flow.",
            "warning",
        )
        return True

    async def _clear_pa_browser_profile_cookies_in_auth_scope(self):
        """Clear the PA profile while ``browser_auth_lock`` is already held."""
        if self.pa_browser_worker_owns_profile:
            worker_was_running = self.pa_browser_worker_running
            cleared_count = await self._pa_browser_worker.clear_cookies()
            if not worker_was_running:
                # This is maintenance inside an existing auth scope, not a new user/lifecycle
                # stop request, so close the owned worker without advancing its generation.
                await self._close_pa_browser_worker()
            return cleared_count

        if self._pa_browser_worker is not None:
            await self._close_pa_browser_worker()
        return await clear_steam_browser_profile_cookies(profile_path=PA_MARKET_PROFILE_PATH)

    async def clear_pa_browser_identity_cookies(self):
        """Clear cookies that could silently reopen a previously saved PA account."""
        # Persist the unprepared state before waiting for an in-flight auth cycle.
        # If clearing fails or the app exits, the next PA refresh must try again
        # instead of trusting cookies from the former account.
        self._set_pa_browser_profile_prepared(False)
        async with self.browser_auth_lock:
            try:
                cleared_count = await self._clear_pa_browser_profile_cookies_in_auth_scope()
            except BrowserAuthError as exc:
                if self._pa_browser_worker is not None:
                    try:
                        await self._close_pa_browser_worker()
                    except BrowserAuthError as close_exc:
                        self.add_event(
                            f"Pearl Abyss Chrome worker profile could not be released: {close_exc}",
                            "error",
                        )
                self.add_event(f"Pearl Abyss browser cookie clear failed: {exc}", "error")
                return False

            self.invalidate_browser_storage_summary()
            self.add_event(
                "Browser cookies cleared from the app-owned Pearl Abyss Account profile "
                f"({highlight(cleared_count)} cookies).",
                "warning",
            )
            return True

    async def clear_browser_session_cookies(self):
        """Clear cookies from the app-owned browser profile for the active login method.

        Ungated maintenance action used by App Settings for recovering a stuck
        login. Clears the Steam profile in Steam mode (and resets Steam setup
        state) or the Pearl Abyss profile in PA mode.
        """
        # Resolve the target at invocation time. A concurrent mode change must not
        # redirect an already-requested maintenance action to the other profile.
        if not self.uses_steam_browser_session():
            return await self.clear_pa_browser_identity_cookies()

        async with self.browser_auth_lock:
            try:
                cleared_count = await clear_steam_browser_profile_cookies(
                    profile_path=STEAM_MARKET_PROFILE_PATH
                )
            except BrowserAuthError as exc:
                self.add_event(f"Browser cookie clear failed: {exc}", "error")
                return False

            self.steam_browser_profile_prepared = save_steam_browser_profile_prepared(False)
            self._set_steam_pa_cookie_consent_prepared(False)
            self.steam_auto_reauth_enabled = False
            self.invalidate_browser_storage_summary()
            self.add_event(
                "Browser cookies cleared from the app-owned Steam Account profile "
                f"({highlight(cleared_count)} cookies).",
                "warning",
            )
            return True

    async def debug_clear_steam_browser_cookies(self):
        if not self.test_mode_enabled:
            return False

        try:
            cleared_count = await clear_steam_browser_profile_cookies()
        except BrowserAuthError as exc:
            self.add_event(f"Steam browser cookie clear failed: {exc}", "error")
            return False

        self.steam_browser_profile_prepared = save_steam_browser_profile_prepared(False)
        self._set_steam_pa_cookie_consent_prepared(False)
        self.steam_auto_reauth_enabled = False
        self.invalidate_browser_storage_summary()
        self.add_event(f"Steam browser cookies cleared from the app-owned profile ({cleared_count} cookies).", "warning")
        return True

    async def prepare_steam_browser_profile(self, *, allow_inactive_mode=False, cleanup_browser_cache=True):
        async with self.browser_auth_lock:
            return await self._prepare_steam_browser_profile_in_auth_scope(
                allow_inactive_mode=allow_inactive_mode,
                cleanup_browser_cache=cleanup_browser_cache,
            )

    async def _prepare_steam_browser_profile_in_auth_scope(
        self,
        *,
        allow_inactive_mode=False,
        cleanup_browser_cache=True,
    ):
        if not self.uses_steam_browser_session() and not allow_inactive_mode:
            self.add_event("Switch to Steam Account before running initial Steam setup.", "warning")
            return False

        if self.steam_browser_profile_prepared:
            self.add_event("Initial Steam browser setup is already complete.", "info")
            return True

        if cleanup_browser_cache:
            await self._clean_browser_cache_before_auth(STEAM_MARKET_PROFILE_PATH, "Steam Account")

        try:
            await prepare_steam_browser_profile(status_callback=self._browser_auth_status)
        except BrowserAuthError as exc:
            self.add_event(f"Initial Steam browser setup failed: {exc}", "error")
            return False

        self.steam_browser_profile_prepared = save_steam_browser_profile_prepared(True)
        self.steam_auto_reauth_enabled = True
        self.add_event(
            "Initial Steam browser setup saved. Refresh Session can now open the market login.",
            "success",
            notable=True,
        )
        return True

    async def _recover_purchase_session_for_retry(
        self,
        *,
        force_browser_refresh=False,
        retry_purchase=True,
        clear_browser_market_session=False,
    ):
        auth_context = self._auth_context_snapshot()
        self.add_event("Login session expired. Attempting to re-authenticate.", "warning", notable=True)

        if auth_context[1] == STEAM_BROWSER_MODE:
            refreshed = await self.refresh_browser_session(
                force_refresh=force_browser_refresh,
                **(
                    {"clear_market_session_before_auth": True}
                    if clear_browser_market_session
                    else {}
                ),
            )
            if not self._auth_context_is_current(auth_context):
                return False
            if refreshed:
                success_message = "Re-authentication succeeded. Retrying purchase request."
                if not retry_purchase:
                    success_message = (
                        "Re-authentication succeeded. The partial purchase batch will not be replayed."
                    )
                self.add_event(success_message, "success", notable=True)
                return True

            # Pause and arm auto-resume (PA path does the same just below): otherwise buy mode stays
            # on against a dead session and the monitor keeps firing failed buys until the periodic
            # checker eventually pauses it.
            self.pause_buy_mode_for_session_refresh("Re-authentication failed.")
            failure_message = "Re-authentication failed. Purchase retry skipped."
            if not retry_purchase:
                failure_message = "Re-authentication failed after the partial purchase batch."
            self.add_event(failure_message, "error")
            return False

        refreshed = await self.refresh_pa_browser_session(
            force_refresh=force_browser_refresh,
            **(
                {"clear_market_session_before_auth": True}
                if clear_browser_market_session
                else {}
            ),
        )
        if not self._auth_context_is_current(auth_context):
            return False
        if refreshed:
            self._set_saved_session_last_known_valid(True)
            self.add_event("Re-authentication succeeded. Retrying purchase request.", "success", notable=True)
            return True

        self._set_saved_session_last_known_valid(False)
        self.pause_buy_mode_for_session_refresh("Re-authentication failed.")
        self.add_event("Re-authentication failed.", "error")
        return False

    def set_simulated_session(self, enabled):
        if not self.test_mode_enabled:
            return False

        self.simulated_session_enabled = bool(enabled)
        self.api_handler.login_status = bool(enabled)
        if enabled and not getattr(self.api_handler, "email", None):
            self.api_handler.email = SIMULATED_SESSION_EMAIL
        if not enabled and getattr(self.api_handler, "email", None) == SIMULATED_SESSION_EMAIL:
            self.api_handler.email = None
        if not enabled:
            self.set_purchase_submission_enabled(False)
        return True

    def _simulated_purchase_summary(self, buy_list, label="Test-mode purchase simulated"):
        purchase_records = []
        for item_id, stock, price in buy_list:
            purchase_records.append(
                {
                    "item_id": item_id,
                    "price": int(price),
                    "count": int(stock),
                    "result_code": 0,
                }
            )

        purchased_count = purchase_record_count(purchase_records)
        return {
            "purchase_records": purchase_records,
            "events": [
                {
                    "level": "success",
                    "message": f"{label} for {purchased_count} outfit.",
                }
            ],
        }

    def _note_purchase_progress(self, purchase_record):
        # Synchronous, exception-isolated observer invoked from the buy loop per secured
        # item. Plain integer bumps only — the buy path's timing is untouched. Both values
        # compute before either counter moves, so a malformed record is an atomic no-op.
        try:
            count = purchase_record_count([purchase_record])
            silver = purchase_record_spend([purchase_record])
        except Exception:
            return
        self.purchase_progress_count += count
        self.purchase_progress_silver += silver

    def _sync_purchase_progress_to_committed(self):
        self.purchase_progress_count = max(self.purchase_progress_count, self.session_successful_purchases)
        self.purchase_progress_silver = max(self.purchase_progress_silver, self.session_silver_spent)

    def _purchase_stop_event_for_task(self, task):
        if task is self.checker_task:
            return self._checker_purchase_stop_event
        if task is self.single_item_test_checker_task:
            return self._single_item_test_purchase_stop_event
        return None

    async def _submit_purchase_batch(self, buy_list, stop_event):
        kwargs = {
            "purchase_delay_bounds": self.purchase_delay_bounds,
            "on_purchase": self._note_purchase_progress,
        }
        if stop_event is not None:
            kwargs["stop_event"] = stop_event
        return await self.api_handler.buy_item(buy_list, **kwargs)

    async def _record_stopped_purchase_summary(self, summary, *, current_auth_result):
        await self.record_purchase_summary(summary)
        # An auth failure from the request that just completed is still meaningful
        # even though STOP forbids browser recovery or another buy. Do not apply this
        # to the stale pre-refresh summary when STOP arrives during a successful refresh.
        # APIHandler summaries always expose the terminal auth state explicitly.
        # Prefer that field so an earlier 2000 followed by a successful internal
        # re-auth is not mistaken for an invalid final session when STOP arrives.
        if "auth_failed" in summary:
            terminal_auth_failure = summary.get("auth_failed") is True
        else:
            terminal_auth_failure = (
                self._purchase_summary_has_auth_failure(summary)
                or self._purchase_summary_ends_with_expired_session(summary)
            )
        if current_auth_result and terminal_auth_failure:
            self.api_handler.login_status = False
            self._set_saved_session_last_known_valid(False)
            self.pause_buy_mode_for_session_refresh(
                "The stopped purchase response reported an invalid session."
            )

    async def buy_item(self, buy_list, adjust_pricing=True):
        purchase_task = asyncio.current_task()
        purchase_auth_context = self._auth_context_snapshot()
        purchase_stop_event = self._purchase_stop_event_for_task(purchase_task)
        self._sync_purchase_progress_to_committed()
        self._purchase_owner_task = purchase_task
        self.purchase_in_progress = True
        try:
            if adjust_pricing:
                updated_buy_list = await self.adjust_prices(buy_list)
            else:
                updated_buy_list = self._normalize_buy_list(buy_list)

            capped_buy_list = self._apply_spend_cap(updated_buy_list)

            if not capped_buy_list:
                self.add_event("Purchase skipped: spend cap would be exceeded.", "warning", notable=True)
                return

            # A mode/session reset may have started while pricing was being
            # prepared. Never submit the first request under a different context.
            if not self._auth_context_is_current(purchase_auth_context):
                return

            if self.simulated_session_enabled:
                simulated_summary = self._simulated_purchase_summary(capped_buy_list)
                for record in simulated_summary.get("purchase_records", []):
                    self._note_purchase_progress(record)
                await self.record_purchase_summary(simulated_summary)
                return

            try:
                summary = await self._submit_purchase_batch(capped_buy_list, purchase_stop_event)
            except MarketplaceAPIError as exc:
                if not self._auth_context_is_current(purchase_auth_context):
                    self.add_event(f"Purchase request failed while the authentication context was changing: {exc}", "error")
                    return
                if not self.uses_steam_browser_session() and self._requires_browser_verification(exc):
                    self.pause_buy_mode_for_session_refresh("Purchase authentication failed.")
                self.add_event(f"Purchase request failed: {exc}", "error")
                return

            if not self._auth_context_is_current(purchase_auth_context):
                await self.record_purchase_summary(summary)
                return

            if purchase_stop_event is not None and purchase_stop_event.is_set():
                await self._record_stopped_purchase_summary(summary, current_auth_result=True)
                return

            if (
                not self.uses_steam_browser_session()
                and not self._purchase_summary_has_nonreplayable_effect(summary)
            ):
                pa_auth_retry_needed = (
                    self._purchase_summary_requires_browser_verification(summary)
                    or self._purchase_summary_has_expired_session(summary)
                    or self._purchase_summary_has_auth_failure(summary)
                )
                if pa_auth_retry_needed and await self._recover_purchase_session_for_retry():
                    if purchase_stop_event is not None and purchase_stop_event.is_set():
                        await self._record_stopped_purchase_summary(summary, current_auth_result=False)
                        return
                    try:
                        summary = await self._submit_purchase_batch(capped_buy_list, purchase_stop_event)
                    except MarketplaceAPIError as exc:
                        if not self._auth_context_is_current(purchase_auth_context):
                            self.add_event(
                                f"Purchase retry failed while the authentication context was changing: {exc}",
                                "error",
                            )
                            return
                        if self._requires_browser_verification(exc):
                            self.pause_buy_mode_for_session_refresh("Purchase authentication failed.")
                        self.add_event(f"Purchase retry failed: {exc}", "error")
                        return

            if not self._auth_context_is_current(purchase_auth_context):
                await self.record_purchase_summary(summary)
                return

            # A login-method change or monitor STOP can arrive while the PA retry request is
            # in flight. Record that completed response, then leave before current account-mode
            # state can reinterpret it and launch the other mode's recovery browser.
            if purchase_stop_event is not None and purchase_stop_event.is_set():
                await self._record_stopped_purchase_summary(summary, current_auth_result=True)
                return

            if not self.uses_steam_browser_session() and self._purchase_summary_has_auth_failure(summary):
                self.api_handler.login_status = False
                self._set_saved_session_last_known_valid(False)
                self.pause_buy_mode_for_session_refresh("Purchase authentication failed.")

            steam_retry_submitted = False
            steam_auth_issue = self.uses_steam_browser_session() and (
                self._purchase_summary_has_expired_session(summary)
                or self._purchase_summary_has_auth_failure(summary)
            )
            if steam_auth_issue:
                if self._purchase_summary_has_nonreplayable_effect(summary):
                    await self.record_purchase_summary(summary)
                    self.add_event(
                        "Remaining purchase attempts were skipped to avoid replaying a partially successful batch.",
                        "warning",
                        notable=True,
                    )
                    await self._recover_purchase_session_for_retry(retry_purchase=False)
                    return

                if await self._recover_purchase_session_for_retry():
                    if purchase_stop_event is not None and purchase_stop_event.is_set():
                        await self._record_stopped_purchase_summary(summary, current_auth_result=False)
                        return
                    try:
                        summary = await self._submit_purchase_batch(capped_buy_list, purchase_stop_event)
                    except MarketplaceAPIError as exc:
                        if not self._auth_context_is_current(purchase_auth_context):
                            self.add_event(
                                f"Purchase retry failed while the authentication context was changing: {exc}",
                                "error",
                            )
                            return
                        self.add_event(f"Purchase retry failed: {exc}", "error")
                        return
                    steam_retry_submitted = True

            if steam_retry_submitted:
                if not self._auth_context_is_current(purchase_auth_context):
                    await self.record_purchase_summary(summary)
                    return
                if purchase_stop_event is not None and purchase_stop_event.is_set():
                    await self._record_stopped_purchase_summary(summary, current_auth_result=True)
                    return

                retry_auth_issue = (
                    self._purchase_summary_has_expired_session(summary)
                    or self._purchase_summary_has_auth_failure(summary)
                )
                if retry_auth_issue:
                    await self.record_purchase_summary(summary)
                    if summary.get("purchase_records"):
                        self.add_event(
                            "Remaining purchase attempts were skipped to avoid replaying a partially successful retry.",
                            "warning",
                            notable=True,
                        )
                    self.api_handler.login_status = False
                    self._set_saved_session_last_known_valid(False)
                    self.pause_buy_mode_for_session_refresh(
                        "Purchase retry still reported an invalid or expired session."
                    )
                    return

            await self.record_purchase_summary(summary)
        finally:
            if self._purchase_owner_task is purchase_task:
                self.purchase_in_progress = False
                self._purchase_owner_task = None
                self._complete_pending_auth_reset_if_ready()

    def _purchase_summary_has_expired_session(self, summary):
        for result in summary.get("results", []):
            if isinstance(result, dict) and result.get("result_code") == 2000:
                return True
        return False

    def _purchase_summary_requires_browser_verification(self, summary):
        for event in summary.get("events", []):
            if isinstance(event, dict):
                message = event.get("message", "")
            else:
                message = event
            if self._requires_browser_verification(message):
                return True
        return False

    def _purchase_summary_has_auth_failure(self, summary):
        if summary.get("auth_failed") is True:
            return True

        for event in summary.get("events", []):
            if isinstance(event, dict):
                message = event.get("message", "")
            else:
                message = event
            normalized = str(message).lower()
            if any(
                marker in normalized
                for marker in (
                    "re-authentication failed",
                    "login session is invalid",
                    "refresh session before buying",
                    "browser verification",
                    "requires browser",
                )
            ):
                return True
        return False

    def _purchase_summary_ends_with_expired_session(self, summary):
        for result in reversed(summary.get("results", [])):
            if isinstance(result, dict) and "result_code" in result:
                return result.get("result_code") == 2000
        return False

    def _purchase_summary_has_nonreplayable_effect(self, summary):
        if summary.get("purchase_records"):
            return True
        return any(
            isinstance(result, dict)
            and (
                result.get("outcome") == "preorder"
                or result.get("reservation_id") not in (None, "", 0, "0")
            )
            for result in summary.get("results", [])
        )

    def _normalize_buy_list(self, buy_list):
        return [[str(item_id), str(stock), str(price)] for item_id, stock, price in buy_list]

    async def record_purchase_summary(self, summary):
        purchase_records = summary.get("purchase_records", [])
        purchased_count = purchase_record_count(purchase_records)
        silver_spent = purchase_record_spend(purchase_records)

        if purchased_count > 0 or silver_spent > 0:
            self.session_successful_purchases += purchased_count
            self.session_silver_spent += silver_spent
            # Episode dedup can suppress a re-detection that is then bought (fast
            # relist), so keep the session invariant detected >= purchased instead
            # of letting the success rate climb past 100%.
            if self.session_successful_purchases > self.session_detected_outfits:
                self.session_detected_outfits = self.session_successful_purchases
            self.lifetime_successful_purchases += purchased_count
            self.lifetime_silver_spent += silver_spent
            # History first: the purchase event row is the valuable record, so it
            # must be queued before anything else on this path can fail.
            await self._record_purchase_history(purchase_records)
            await self.save_local_data()

        summary_events = summary.get("events", [])
        for event in summary_events:
            # Purchase outcomes are always notable: they are the reason the app runs.
            if isinstance(event, dict):
                self.add_event(event.get("message", ""), event.get("level", "info"), notable=True)
            else:
                self.add_event(event, "success" if "succeeded" in event else "warning", notable=True)

        if purchased_count == 0 and not summary_events:
            self.add_event("Purchase attempt completed without a successful request.", "warning", notable=True)

    def _apply_spend_cap(self, buy_list):
        if self.max_spend is None:
            return buy_list

        capped = []
        remaining = self.max_spend - self.session_silver_spent
        if remaining <= 0:
            return capped

        for item_id, stock, price in buy_list:
            item_price = int(price)
            if item_price <= 0:
                continue
            allowed_count = min(int(stock), remaining // item_price)
            if allowed_count > 0:
                capped.append([item_id, str(allowed_count), price])
                remaining -= allowed_count * item_price
            if remaining <= 0:
                break

        return capped

    async def adjust_prices(self, buy_list):
        return apply_price_rules(buy_list)

    async def login(self):
        auth_context = self._auth_context_snapshot()
        session_check_error = None
        try:
            status = await self.api_handler.is_session_expired()
        except MarketplaceAPIError as exc:
            if not self._auth_context_is_current(auth_context):
                return
            self.api_handler.login_status = False
            session_check_error = exc
            status = -1

        if not self._auth_context_is_current(auth_context):
            return

        if status == 0:
            self.api_handler.login_status = True
            self._set_saved_session_last_known_valid(True)
            if self.uses_steam_browser_session():
                self.steam_auto_reauth_enabled = True
            elif self.pa_browser_keep_open and not self.pa_browser_worker_running:
                # Refresh Session is an explicit worker-restart point even when the saved HTTP
                # session is still valid and no browser authentication is otherwise necessary.
                await self.ensure_pa_browser_worker_started(
                    cleanup_browser_cache=True,
                    auth_context=auth_context,
                )
                if self._pa_browser_stop_requests or not self._auth_context_is_current(auth_context):
                    return
            # Routine check outcome (nothing changed) — stays out of the Activity tail.
            self.add_event("Existing marketplace session is valid.", "success")
            self.start_login_status_checker()
            self.resume_buy_mode_after_refresh()
            return

        if auth_context[1] == STEAM_BROWSER_MODE:
            refreshed = await self.refresh_browser_session(
                session_check_error=session_check_error,
                cleanup_browser_cache=True,
            )
            if not self._auth_context_is_current(auth_context):
                return
            if not refreshed:
                self.pause_buy_mode_for_session_refresh("Steam Account refresh failed.")
            return

        refreshed = await self.refresh_pa_browser_session(
            session_check_error=session_check_error,
            cleanup_browser_cache=True,
        )
        if not self._auth_context_is_current(auth_context):
            return
        if not refreshed:
            self.pause_buy_mode_for_session_refresh("Pearl Abyss Account refresh failed.")

    def _requires_browser_verification(self, exc):
        message = str(exc).lower()
        return any(marker in message for marker in BROWSER_VERIFICATION_MARKERS)

    def _pa_browser_login_credentials(self):
        try:
            saved_email, saved_password = load_credentials()
        except CredentialStoreError as exc:
            return None, None, exc

        if saved_email:
            self.api_handler.email = saved_email
        if saved_password:
            self.api_handler.password = saved_password
        return saved_email, saved_password, None

    async def refresh_pa_browser_session(
        self,
        session_check_error=None,
        login_error=None,
        *,
        auto_pa_login=None,
        force_refresh=False,
        cleanup_browser_cache=False,
        clear_market_session_before_auth=False,
    ):
        auth_context = self._auth_context_snapshot()
        async with self.browser_auth_lock:
            if not self._auth_context_is_current(auth_context, expected_mode=PA_CREDENTIALS_MODE):
                return False

            if not force_refresh and await self._saved_session_is_valid(auth_context):
                return True
            if not self._auth_context_is_current(auth_context, expected_mode=PA_CREDENTIALS_MODE):
                return False

            details = []
            if session_check_error:
                details.append(f"Session check failed: {session_check_error}.")
            if login_error:
                details.append(f"Pearl Abyss Account login needs browser verification: {login_error}.")

            email, password, credential_error = self._pa_browser_login_credentials()
            saved_credentials_ready = bool(email and password)
            auto_submit_credentials = saved_credentials_ready if auto_pa_login is None else bool(auto_pa_login)
            auto_submit_credentials = auto_submit_credentials and saved_credentials_ready

            if not saved_credentials_ready:
                self.api_handler.login_status = False
                self._set_saved_session_last_known_valid(False)
                if credential_error:
                    details.append(f"Saved credentials unavailable: {credential_error}.")
                elif email:
                    details.append(
                        "Pearl Abyss Account credentials are incomplete. Save the account password before refreshing."
                    )
                else:
                    details.append(
                        "Pearl Abyss Account credentials are not saved. Save credentials before refreshing the session."
                    )
                self.add_event(" ".join(details), "warning")
                return False

            if credential_error:
                details.append(f"Saved credentials unavailable: {credential_error}.")
            worker_closed_without_restart = False
            if self.pa_browser_keep_open and self.pa_browser_worker_running:
                details.append(
                    "Refreshing session in the Pearl Abyss Chrome worker; keep its window minimized."
                )
            elif self.pa_browser_keep_open and cleanup_browser_cache:
                details.append(
                    "Restarting the Pearl Abyss Chrome worker for this manual refresh; keep its window minimized."
                )
            elif self.pa_browser_keep_open:
                worker_closed_without_restart = True
                details.append(
                    "The Pearl Abyss Chrome worker is closed; automatic refresh will not reopen Chrome."
                )
            elif auto_submit_credentials:
                details.append("Refreshing session — opening the Pearl Abyss browser; saved credentials will be submitted.")
            else:
                details.append("Refreshing session — opening the Pearl Abyss browser for manual login.")
            # A routine refresh is a notable info line; it only escalates to a warning when
            # it carries an actual failure detail (session check / login / credential error).
            opening_level = "warning" if len(details) > 1 or worker_closed_without_restart else "info"
            self.add_event(" ".join(details), opening_level, notable=True)

            try:
                profile_requires_fresh_setup = not self.pa_browser_profile_prepared
                bootstrap_url = BDO_SITE_BOOTSTRAP_URL if profile_requires_fresh_setup else None

                if cleanup_browser_cache and not self.pa_browser_keep_open:
                    await self._clean_browser_cache_before_auth(PA_MARKET_PROFILE_PATH, "Pearl Abyss Account")
                    if not self._auth_context_is_current(auth_context, expected_mode=PA_CREDENTIALS_MODE):
                        return False

                cookies = await self._acquire_pa_market_cookies(
                    allow_worker_start=cleanup_browser_cache,
                    status_callback=self._browser_auth_status,
                    auto_steam_login=False,
                    auto_pa_login=auto_submit_credentials,
                    pa_email=email if auto_submit_credentials else None,
                    pa_password=password if auto_submit_credentials else None,
                    profile_path=PA_MARKET_PROFILE_PATH,
                    bootstrap_url=bootstrap_url,
                    clear_cookies_before_auth=profile_requires_fresh_setup,
                    account_label="Pearl Abyss Account",
                    announce_opening=False,
                    **(
                        {"clear_market_session_before_auth": True}
                        if clear_market_session_before_auth
                        else {}
                    ),
                )
            except BrowserAuthError as exc:
                if not self._auth_context_is_current(auth_context, expected_mode=PA_CREDENTIALS_MODE):
                    return False
                self.api_handler.login_status = False
                self._set_saved_session_last_known_valid(False)
                self.add_event(f"Pearl Abyss Account browser refresh failed: {exc}", "error")
                return False

            if not self._auth_context_is_current(auth_context, expected_mode=PA_CREDENTIALS_MODE):
                return False

            try:
                session_valid = await self.api_handler.validate_and_save_imported_session(cookies)
            except MarketplaceAPIError as exc:
                if not self._auth_context_is_current(auth_context, expected_mode=PA_CREDENTIALS_MODE):
                    self._discard_stale_imported_session()
                    return False
                await self._update_pa_browser_landing_validation(False)
                self.api_handler.login_status = False
                self._set_saved_session_last_known_valid(False)
                self.add_event(f"Pearl Abyss Account browser session validation failed: {exc}", "error")
                return False

            if not self._auth_context_is_current(auth_context, expected_mode=PA_CREDENTIALS_MODE):
                self._discard_stale_imported_session()
                return False

            await self._update_pa_browser_landing_validation(session_valid)
            if session_valid:
                self.api_handler.login_status = True
                self._set_pa_browser_profile_prepared(True)
                self._set_saved_session_last_known_valid(True)
                self.start_login_status_checker()
                self.add_event("Pearl Abyss Account session validated and saved.", "success", notable=True)
                self.resume_buy_mode_after_refresh()
                return True

            self.api_handler.login_status = False
            self._set_saved_session_last_known_valid(False)
            self.add_event(
                "Pearl Abyss Account browser session validation failed. Complete login in the browser and retry.",
                "error",
            )
            return False

    async def refresh_browser_session(
        self,
        session_check_error=None,
        *,
        auto_steam_login=None,
        force_refresh=False,
        cleanup_browser_cache=False,
        clear_market_session_before_auth=False,
    ):
        auth_context = self._auth_context_snapshot()
        async with self.browser_auth_lock:
            if not self._auth_context_is_current(auth_context, expected_mode=STEAM_BROWSER_MODE):
                return False

            if not force_refresh and await self._saved_session_is_valid(auth_context):
                return True
            if not self._auth_context_is_current(auth_context, expected_mode=STEAM_BROWSER_MODE):
                return False

            if self.steam_browser_profile_needs_setup():
                self.add_event("Initial Steam browser setup is required before the market login refresh.", "warning")
                prepared = await self._prepare_steam_browser_profile_in_auth_scope(
                    cleanup_browser_cache=cleanup_browser_cache
                )
                if not self._auth_context_is_current(auth_context, expected_mode=STEAM_BROWSER_MODE):
                    return False
                if not prepared:
                    self.api_handler.login_status = False
                    self._set_saved_session_last_known_valid(False)
                    return False

            if auto_steam_login is None:
                auto_steam_login = self.steam_auto_reauth_available()

            if session_check_error:
                self.add_event(
                    f"Session check failed: {session_check_error}. Opening Steam Account browser session.",
                    "warning",
                    notable=True,
                )

            try:
                handle_pa_cookie_consent = bool(auto_steam_login and not self.steam_pa_cookie_consent_prepared)
                if cleanup_browser_cache:
                    await self._clean_browser_cache_before_auth(STEAM_MARKET_PROFILE_PATH, "Steam Account")
                    if not self._auth_context_is_current(auth_context, expected_mode=STEAM_BROWSER_MODE):
                        return False
                cookies = await acquire_market_cookies(
                    status_callback=self._browser_auth_status,
                    auto_steam_login=auto_steam_login,
                    handle_pa_cookie_consent=handle_pa_cookie_consent,
                    pa_cookie_consent_callback=self._set_steam_pa_cookie_consent_prepared,
                    **(
                        {"clear_market_session_before_auth": True}
                        if clear_market_session_before_auth
                        else {}
                    ),
                )
            except BrowserAuthError as exc:
                if not self._auth_context_is_current(auth_context, expected_mode=STEAM_BROWSER_MODE):
                    return False
                self.api_handler.login_status = False
                self._set_saved_session_last_known_valid(False)
                # Deliberately do NOT touch steam_pa_cookie_consent_prepared here. Cookie consent is
                # persisted in the browser profile the moment it is accepted, so it is only invalidated
                # by clearing cookies (the clear_* / reset paths handle that) -- never by a refresh
                # failure. Re-arming it on auth errors (the user closing the browser, a transient
                # timeout, etc.) would needlessly re-run the slow first-time cookie path and re-show the
                # setup notice on the next routine refresh, even though consent was never lost.
                self.add_event(f"Steam Account refresh failed: {exc}", "error")
                return False

            if not self._auth_context_is_current(auth_context, expected_mode=STEAM_BROWSER_MODE):
                return False

            try:
                session_valid = await self.api_handler.validate_and_save_imported_session(cookies)
            except MarketplaceAPIError as exc:
                if not self._auth_context_is_current(auth_context, expected_mode=STEAM_BROWSER_MODE):
                    self._discard_stale_imported_session()
                    return False
                self.api_handler.login_status = False
                self._set_saved_session_last_known_valid(False)
                self.add_event(f"Steam Account session validation failed: {exc}", "error")
                return False

            if not self._auth_context_is_current(auth_context, expected_mode=STEAM_BROWSER_MODE):
                self._discard_stale_imported_session()
                return False

            if session_valid:
                self.api_handler.login_status = True
                if handle_pa_cookie_consent and not self.steam_pa_cookie_consent_prepared:
                    self._set_steam_pa_cookie_consent_prepared(True)
                self._set_saved_session_last_known_valid(True)
                self.steam_auto_reauth_enabled = True
                self.add_event("Steam Account session validated and saved.", "success", notable=True)
                self.start_login_status_checker()
                self.resume_buy_mode_after_refresh()
                return True

            self.api_handler.login_status = False
            self._set_saved_session_last_known_valid(False)
            self.add_event("Steam Account session validation failed. Complete login in the browser and retry.", "error")
            return False

    def _browser_auth_status(self, message, level="info"):
        # Browser warnings are actionable (verification, rejected credentials, or manual input),
        # so keep them visible in the dashboard Activity tail as well as the full Logs stream.
        self.add_event(message, level, notable=level in {"warning", "error"})

    async def initial_login_check(self):
        auth_context = self._auth_context_snapshot()
        if not self.saved_session_last_known_valid:
            self.api_handler.login_status = False
            # A fresh start with no saved session is the normal logged-out state, not a
            # fault — routine dim info, the negative twin of "session is valid". It stays in
            # the full log (guidance to refresh) but off the tail and out of the Alerts filter.
            if self.uses_steam_browser_session():
                self.add_event(
                    "No previously validated Steam Account session is saved. Refresh Session to open the login browser.",
                    "info",
                )
            else:
                self.add_event(
                    "No previously validated marketplace session is saved. Refresh Session to open the login browser.",
                    "info",
                )
            return

        if not self._api_has_session_cookies():
            self.api_handler.login_status = False
            self._set_saved_session_last_known_valid(False)
            self.add_event("No saved marketplace session cookies found. Refresh Session to log in.", "warning")
            return

        try:
            status = await self.api_handler.is_session_expired()
        except MarketplaceAPIError as exc:
            if not self._auth_context_is_current(auth_context):
                return
            self.api_handler.login_status = False
            self.add_event(f"Saved marketplace session check failed: {exc}", "warning")
            return

        if not self._auth_context_is_current(auth_context):
            return

        if status == 0:
            self.api_handler.login_status = True
            self._set_saved_session_last_known_valid(True)
            if self.uses_steam_browser_session():
                self.steam_auto_reauth_enabled = True
            self.start_login_status_checker()
            # Routine startup check outcome (nothing changed) — stays out of the Activity tail.
            self.add_event("Saved marketplace session is valid.", "success")
        else:
            self.api_handler.login_status = False
            self._set_saved_session_last_known_valid(False)
            if self.uses_steam_browser_session():
                self.add_event(
                    "Saved Steam Account session is invalid or expired. Refresh Session to open the login browser.",
                    "warning",
                )
            else:
                self.add_event(
                    "Saved marketplace session is invalid or expired. Refresh Session to open the login browser.",
                    "warning",
                )

    async def login_status_checker(self):
        try:
            while True:
                await asyncio.sleep(random.uniform(1800, 2400))
                if not await self._run_one_session_check():
                    break
        except asyncio.CancelledError:
            raise

    async def _check_session_expired(self):
        # Test-only override: "Expire Session" sets this flag so the production session check below
        # reports expiry without a live API call, letting the real detect -> re-auth path be tested.
        if self.test_mode_enabled and self.debug_force_purchase_session_expired:
            return -1
        return await self.api_handler.is_session_expired()

    async def _run_one_session_check(self):
        """Run one periodic session check and react to it.

        Returns True to keep the periodic checker running (session valid, recovered, or a transient
        check error) and False to stop it (session expired and re-authentication failed). The "Run
        Session Check" test control calls this same method so the test path is the production path.
        """
        auth_context = self._auth_context_snapshot()
        try:
            status = await self._check_session_expired()
        except MarketplaceAPIError as exc:
            if not self._auth_context_is_current(auth_context):
                return False
            self.add_event(f"Session check failed: {exc}", "error")
            return True

        if not self._auth_context_is_current(auth_context):
            return False

        if status == -1:
            return await self.handle_expired_session()

        self.api_handler.login_status = True
        self.add_event("Session still valid.")
        return True

    async def handle_expired_session(self):
        auth_context = self._auth_context_snapshot()
        self.api_handler.login_status = False
        clear_browser_market_session = bool(
            self.test_mode_enabled and self.debug_force_purchase_session_expired
        )
        if auth_context[1] == STEAM_BROWSER_MODE:
            if self.steam_auto_reauth_available():
                self.add_event("Session expired. Attempting automatic Steam Account re-authentication.", "warning")
                refreshed = await self.refresh_browser_session(
                    auto_steam_login=True,
                    **(
                        {"clear_market_session_before_auth": True}
                        if clear_browser_market_session
                        else {}
                    ),
                )
                if not self._auth_context_is_current(auth_context):
                    return False
                if refreshed:
                    self.add_event("Session expired. Re-authentication successful.", "success", notable=True)
                    return True

            # Mirror the PA path below: pause AND arm auto-resume so buy mode comes back on its own
            # once a later refresh succeeds. set_purchase_submission_enabled(False) alone would leave
            # buy mode stuck off (it never sets buy_mode_resume_pending), breaking continuity.
            if not self.pause_buy_mode_for_session_refresh("Session expired. Steam Account refresh required."):
                self.add_event(
                    "Session expired. Refresh the Steam Account session from Session before buying.",
                    "warning",
                )
            return False

        self.add_event("Session expired. Attempting Pearl Abyss Account browser re-authentication.", "warning")
        refreshed = await self.refresh_pa_browser_session(
            **(
                {"clear_market_session_before_auth": True}
                if clear_browser_market_session
                else {}
            ),
        )
        if not self._auth_context_is_current(auth_context):
            return False
        if refreshed:
            self.add_event("Session expired. Re-authentication successful.", "success", notable=True)
            return True

        self._set_saved_session_last_known_valid(False)
        self.pause_buy_mode_for_session_refresh("Session expired. Pearl Abyss Account refresh required.")
        self.add_event("Session expired. Re-authentication failed.", "error")
        return False
