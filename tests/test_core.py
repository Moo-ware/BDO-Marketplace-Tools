import asyncio
import json
import os
import requests
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import main as app_main
from rich.console import Console
from textual.color import Color
from textual.widgets import Button, Input, Select, Static, Tooltip
from bdo_marketplace_tools.market.api_handler import (
    APIHandler,
    DEFAULT_PURCHASE_DELAY_BOUNDS,
    MARKET_AJAX_HEADER,
    MarketplaceAPIError,
    MarketplaceResponseError,
    marketplace_silver_balance,
    purchase_result_message,
)
from bdo_marketplace_tools.market.browser_auth import (
    BDO_SITE_BOOTSTRAP_URL,
    BrowserAuthError,
    BrowserAuthUnavailable,
    AUTH_DIALOG_INVALID_CREDENTIALS,
    AUTH_DIALOG_VERIFICATION_REQUIRED,
    COOKIE_CONSENT_NOT_FOUND,
    COOKIE_CONSENT_SAVED,
    STEAM_AUTO_LOGIN_CLICKED,
    STEAM_AUTO_LOGIN_DISABLED,
    STEAM_AUTO_LOGIN_MANUAL_NEEDED,
    STEAM_AUTO_LOGIN_SKIPPED,
    PA_AUTO_LOGIN_DISABLED,
    PA_AUTO_LOGIN_MANUAL_NEEDED,
    PA_AUTO_LOGIN_SUBMITTED,
    PA_AUTO_LOGIN_WAITING,
    PA_CONSENT_BUTTON_READY_TIMEOUT_MS,
    STEAM_BROWSER_CHANNEL,
    STEAM_COMMUNITY_URL,
    STEAM_LOGIN_COOKIE_NAMES,
    STEAM_MARKET_PROFILE_PATH,
    STEAM_PROFILE_COOKIE_URLS,
    STEAM_STORE_URL,
    _accept_required_cookie_consent_if_available,
    _browser_launch_error_message,
    _classify_url,
    acquire_market_cookies,
    clear_market_cookies_keep_steam_login,
    clear_steam_browser_profile_cookies,
    _handle_auth_dialog,
    _import_async_playwright,
    _close_browser_context,
    _has_steam_login_cookie,
    _install_auth_dialog_handlers,
    _is_steam_auth_cookie,
    _market_cookie_capture_ready,
    _maybe_run_steam_auto_login,
    _maybe_run_pa_credentials_login,
    _maybe_prepare_pa_cookie_consent,
    _new_auth_dialog_state,
    _new_steam_auto_login_state,
    _new_pa_auto_login_state,
    _new_pa_cookie_consent_state,
    _record_pa_login_process_submit,
    _should_attempt_steam_auto_login,
    _status_for_state,
    _steam_store_logged_in_dom_ready,
    _wait_for_market_cookies,
    _wait_for_steam_profile_login,
)
from bdo_marketplace_tools.market.pricing import (
    CLASSIC_OUTFIT_MAX_PRICE,
    OUTFIT_SET_MAX_PRICE,
    PREMIUM_OUTFIT_MAX_PRICE,
    apply_price_rules,
    purchase_record_count,
    purchase_record_spend,
)
from bdo_marketplace_tools.market.test_mode import (
    LIVE_BUY_ERROR_TEST_TARGET,
    SINGLE_ITEM_TEST_TARGET,
    check_single_item_stock,
    live_buy_error_test_listing,
    parse_single_item_stock_response,
)
from bdo_marketplace_tools.storage import app_settings as account_mode_module
from bdo_marketplace_tools.storage import browser_profile_cache as browser_profile_cache_module
from bdo_marketplace_tools.storage import credentials as credentials_module
from bdo_marketplace_tools.storage import stats_db as stats_db_module
from bdo_marketplace_tools.services import task_manager as task_manager_module
from bdo_marketplace_tools.services import update_checker as update_checker_module
from bdo_marketplace_tools.storage.paths import PA_MARKET_PROFILE_PATH
from bdo_marketplace_tools.storage import paths as paths_module
from bdo_marketplace_tools.storage.app_settings import PA_CREDENTIALS_MODE, STEAM_BROWSER_MODE
from bdo_marketplace_tools.storage.browser_profile_cache import (
    BrowserProfileCleanupResult,
    BrowserProfileStorageSummary,
    DEFAULT_DISPOSABLE_CACHE_CLEANUP_THRESHOLD_BYTES,
    MIB,
    clean_disposable_browser_profile_cache,
    format_storage_size,
    measure_browser_profile_storage,
)
from bdo_marketplace_tools.services.task_manager import BackgroundTasks
from bdo_marketplace_tools.ui import charts as charts_module
from bdo_marketplace_tools.ui.display import COLOR_EVENT_ROUTINE
from bdo_marketplace_tools.ui.app import (
    BANNER_ART,
    BANNER_BLINK_ART,
    BANNER_STAR_ART,
    DEFAULT_THEME,
    MASCOT_ZZZ_TRAIL,
    RUNNING_QUIPS,
    STATUS_STYLES,
    DashboardTile,
    MarketplaceToolsApp,
    ModalAction,
)
from bdo_marketplace_tools.ui.widgets import DailyActivityChart
from bdo_marketplace_tools.version import APP_CHANNEL, APP_VERSION, PROJECT_NAME, SETTINGS_SCHEMA_VERSION


LOCAL_DATA = {
    "successful_purchases": 0,
    "silver_spent": 0,
}

EXPECTED_APP_SETTINGS_VERSION = {
    "schema": SETTINGS_SCHEMA_VERSION,
    "app": APP_VERSION,
    "channel": APP_CHANNEL,
    "project": PROJECT_NAME,
}


class FakeAPI:
    login_status = False
    email = None
    password = None
    account_mode = PA_CREDENTIALS_MODE
    session_cleared = False

    async def get_mp_inventory(self):
        return {
            "myWalletList": [
                {"mainKey": 1, "subKey": 0, "name": "Silver", "count": "123456789"},
            ],
            "useValuePackage": True,
            "totalWeight": 12,
            "maxWeight": 100,
        }

    async def check_stock(self):
        return []

    async def is_session_expired(self):
        return 0 if self.login_status else -1

    async def login(self):
        self.login_status = True
        return 1

    def save_session(self):
        pass

    def has_session_cookies(self):
        return bool(getattr(self, "session_has_cookies", self.login_status))

    def clear_session(self, save=True):
        self.login_status = False
        self.session_cleared = True


class FakeApp:
    instances = []

    def __init__(self, task_manager, api_handler, launch_mode="live"):
        self.task_manager = task_manager
        self.api_handler = api_handler
        self.launch_mode = launch_mode
        self.run_async = AsyncMock()
        self.instances.append(self)


class LaunchModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_test_mode_skips_startup_session_check(self):
        fake_api = FakeAPI()
        with patch("bdo_marketplace_tools.services.task_manager._load_local_data", return_value=LOCAL_DATA.copy()):
            fake_manager = BackgroundTasks(fake_api, persist_ui_settings=False)
        fake_manager.initial_login_check = AsyncMock()
        FakeApp.instances = []

        with patch("main.APIHandler", return_value=fake_api), patch(
            "main.BackgroundTasks",
            return_value=fake_manager,
        ), patch("main.MarketplaceToolsApp", FakeApp):
            await app_main.run_app(test_mode=True)

        fake_manager.initial_login_check.assert_not_called()
        self.assertFalse(fake_api.login_status)
        self.assertEqual(FakeApp.instances[0].launch_mode, "test")
        self.assertTrue(any("Test mode active" in event for event in fake_manager.events))

    async def test_live_mode_runs_startup_session_check(self):
        fake_api = FakeAPI()
        with patch("bdo_marketplace_tools.services.task_manager._load_local_data", return_value=LOCAL_DATA.copy()):
            fake_manager = BackgroundTasks(fake_api, persist_ui_settings=False)
        fake_manager.initial_login_check = AsyncMock()
        FakeApp.instances = []

        with patch("main.APIHandler", return_value=fake_api), patch(
            "main.BackgroundTasks",
            return_value=fake_manager,
        ), patch("main.MarketplaceToolsApp", FakeApp):
            await app_main.run_app(test_mode=False)

        fake_manager.initial_login_check.assert_awaited_once()
        self.assertEqual(FakeApp.instances[0].launch_mode, "live")

    def test_env_var_enables_test_mode(self):
        with patch.dict("os.environ", {"BDO_MARKET_TEST_MODE": "true"}):
            self.assertTrue(app_main.env_test_mode())

        with patch.dict("os.environ", {"BDO_MARKET_TEST_MODE": "0"}):
            self.assertFalse(app_main.env_test_mode())


class PricingTests(unittest.TestCase):
    def test_apply_price_rules_uses_known_rules_and_reports_fallbacks(self):
        adjusted, fallbacks = apply_price_rules(
            [
                ["premium", "1", "2020000000"],
                ["classic", "1", "1630000000"],
                ["set", "1", "1100000000"],
                ["unknown", "2", "12345"],
            ]
        )

        self.assertEqual(
            adjusted,
            [
                ["premium", "1", PREMIUM_OUTFIT_MAX_PRICE],
                ["classic", "1", CLASSIC_OUTFIT_MAX_PRICE],
                ["set", "1", OUTFIT_SET_MAX_PRICE],
                ["unknown", "2", OUTFIT_SET_MAX_PRICE],
            ],
        )
        self.assertEqual(
            fallbacks,
            [{"item_id": "unknown", "detected_price": "12345", "adjusted_price": OUTFIT_SET_MAX_PRICE}],
        )

    def test_purchase_record_helpers_sum_actual_successes(self):
        records = [
            {"item_id": "a", "price": 100, "count": 2},
            {"item_id": "b", "price": 250, "count": 1},
        ]

        self.assertEqual(purchase_record_count(records), 3)
        self.assertEqual(purchase_record_spend(records), 450)


class APIResultTests(unittest.TestCase):
    def test_async_playwright_import_error_preserves_install_message(self):
        expected_message = "Install requirements before opening Chrome."
        real_import = __import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "patchright.async_api" and "async_playwright" in fromlist:
                raise ImportError("missing patchright")
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=fake_import):
            with self.assertRaises(BrowserAuthUnavailable) as context:
                _import_async_playwright(expected_message)

        self.assertEqual(str(context.exception), expected_message)

    def test_browser_auth_launch_error_names_missing_patchright_browser_install(self):
        message = _browser_launch_error_message(
            RuntimeError("Executable doesn't exist at C:/ms-playwright/chromium/chrome.exe")
        )

        self.assertIn("Patchright Chromium is not installed", message)
        self.assertIn("py -m patchright install chromium", message)

    def test_browser_auth_uses_installed_chrome_channel(self):
        self.assertEqual(STEAM_BROWSER_CHANNEL, "chrome")

    def test_cookiebot_required_consent_click_is_best_effort(self):
        clicked_selectors = []
        statuses = []

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def click(self, timeout=None):
                if self.selector != "#CybotCookiebotDialogBodyButtonDecline":
                    raise RuntimeError("not found")
                clicked_selectors.append((self.selector, timeout))

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)
                self.selector = selector

            async def wait_for(self, state=None, timeout=None):
                raise AssertionError("fast consent click should not wait for dialog state")

        class FakePage:
            def locator(self, selector):
                return FakeLocator(selector)

        async def status_callback(message, level):
            statuses.append((message, level))

        self.assertEqual(
            asyncio.run(_accept_required_cookie_consent_if_available(FakePage(), status_callback=status_callback)),
            COOKIE_CONSENT_SAVED,
        )
        self.assertEqual(
            clicked_selectors,
            [("#CybotCookiebotDialogBodyButtonDecline", PA_CONSENT_BUTTON_READY_TIMEOUT_MS)],
        )
        self.assertEqual(statuses, [("Required cookie consent saved in the Steam browser profile.", "info")])

    def test_cookiebot_required_consent_returns_saved_without_hidden_wait(self):
        statuses = []
        wait_calls = []

        class FakeFirstLocator:
            async def click(self, timeout=None):
                return None

        class FakeLocator:
            first = FakeFirstLocator()

            async def wait_for(self, state=None, timeout=None):
                wait_calls.append((state, timeout))
                raise RuntimeError("dialog still visible")

        class FakePage:
            def locator(self, selector):
                return FakeLocator()

        async def status_callback(message, level):
            statuses.append((message, level))

        self.assertEqual(
            asyncio.run(_accept_required_cookie_consent_if_available(FakePage(), status_callback=status_callback)),
            COOKIE_CONSENT_SAVED,
        )
        self.assertEqual(wait_calls, [])
        self.assertEqual(statuses, [("Required cookie consent saved in the Steam browser profile.", "info")])

    def test_cookiebot_required_consent_returns_not_found_silently(self):
        clicked_selectors = []
        statuses = []

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def click(self, timeout=None):
                clicked_selectors.append((self.selector, timeout))
                raise RuntimeError("not found")

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

            async def wait_for(self, state=None, timeout=None):
                raise AssertionError("fast consent click should not wait for dialog state")

        class FakePage:
            def locator(self, selector):
                return FakeLocator(selector)

        async def status_callback(message, level):
            statuses.append((message, level))

        self.assertEqual(
            asyncio.run(_accept_required_cookie_consent_if_available(FakePage(), status_callback=status_callback)),
            COOKIE_CONSENT_NOT_FOUND,
        )
        self.assertEqual(
            clicked_selectors,
            [(selector, PA_CONSENT_BUTTON_READY_TIMEOUT_MS) for selector in (
                "#CybotCookiebotDialogBodyButtonDecline",
                "button:has-text('Only Accept Required')",
                "button:has-text('Accept Necessary')",
                "button:has-text('Accept Required')",
            )],
        )
        self.assertEqual(statuses, [])

    def test_pa_cookie_consent_probe_clicks_required_cookie_button_once(self):
        clicked_selectors = []
        statuses = []
        tracking = _new_pa_cookie_consent_state()

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def click(self, timeout=None):
                if self.selector != "#CybotCookiebotDialogBodyButtonDecline":
                    raise RuntimeError("not found")
                clicked_selectors.append((self.selector, timeout))

            async def is_visible(self, timeout=None):
                return self.selector == "#btnSteam"

        class FakeLocator:
            def __init__(self, selector):
                self.selector = selector
                self.first = FakeFirstLocator(selector)

            async def wait_for(self, state=None, timeout=None):
                if self.selector != "#CybotCookiebotDialog":
                    raise RuntimeError("not found")
                if state in {"visible", "hidden"}:
                    return None
                raise RuntimeError("unexpected state")

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FakeLocator(selector)

        async def status_callback(message, level):
            statuses.append((message, level))

        result = asyncio.run(
            _maybe_prepare_pa_cookie_consent(
                FakePage(),
                "pa",
                tracking=tracking,
                status_callback=status_callback,
            )
        )

        self.assertEqual(result, COOKIE_CONSENT_SAVED)
        self.assertEqual(
            clicked_selectors,
            [("#CybotCookiebotDialogBodyButtonDecline", PA_CONSENT_BUTTON_READY_TIMEOUT_MS)],
        )
        self.assertEqual(statuses, [("Required cookie consent saved in the Pearl Abyss login page.", "info")])

    def test_pa_cookie_consent_probe_does_not_mark_done_when_banner_is_absent(self):
        clicked_selectors = []
        tracking = _new_pa_cookie_consent_state()

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def click(self, timeout=None):
                clicked_selectors.append((self.selector, timeout))
                raise RuntimeError("not found")

            async def is_visible(self, timeout=None):
                raise AssertionError("fast consent probe should not inspect Steam button visibility")

        class FakeLocator:
            def __init__(self, selector):
                self.selector = selector
                self.first = FakeFirstLocator(selector)

            async def wait_for(self, state=None, timeout=None):
                raise RuntimeError("dialog not visible")

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FakeLocator(selector)

        page = FakePage()
        result = asyncio.run(_maybe_prepare_pa_cookie_consent(page, "pa", tracking=tracking))
        second_result = asyncio.run(_maybe_prepare_pa_cookie_consent(page, "pa", tracking=tracking))

        self.assertEqual(result, COOKIE_CONSENT_NOT_FOUND)
        self.assertEqual(second_result, COOKIE_CONSENT_NOT_FOUND)
        self.assertEqual(
            clicked_selectors,
            [
                ("#CybotCookiebotDialogBodyButtonDecline", PA_CONSENT_BUTTON_READY_TIMEOUT_MS),
                ("button:has-text('Only Accept Required')", PA_CONSENT_BUTTON_READY_TIMEOUT_MS),
                ("button:has-text('Accept Necessary')", PA_CONSENT_BUTTON_READY_TIMEOUT_MS),
                ("button:has-text('Accept Required')", PA_CONSENT_BUTTON_READY_TIMEOUT_MS),
                ("#CybotCookiebotDialogBodyButtonDecline", PA_CONSENT_BUTTON_READY_TIMEOUT_MS),
                ("button:has-text('Only Accept Required')", PA_CONSENT_BUTTON_READY_TIMEOUT_MS),
                ("button:has-text('Accept Necessary')", PA_CONSENT_BUTTON_READY_TIMEOUT_MS),
                ("button:has-text('Accept Required')", PA_CONSENT_BUTTON_READY_TIMEOUT_MS),
            ],
        )

    def test_pa_cookie_consent_probe_missing_button_returns_not_found_immediately(self):
        tracking = _new_pa_cookie_consent_state()

        class FakeFirstLocator:
            async def click(self, timeout=None):
                raise RuntimeError("not found")

            async def is_visible(self, timeout=None):
                raise AssertionError("fast consent probe should not inspect Steam button visibility")

        class FakeLocator:
            first = FakeFirstLocator()

            async def wait_for(self, state=None, timeout=None):
                raise RuntimeError("dialog not visible")

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FakeLocator()

        result = asyncio.run(_maybe_prepare_pa_cookie_consent(FakePage(), "pa", tracking=tracking))

        self.assertEqual(result, COOKIE_CONSENT_NOT_FOUND)

    def test_market_cookie_wait_clicks_cookiebot_then_steam_in_same_poll(self):
        fresh = [
            {
                "name": "TradeAuth_Session",
                "value": "fresh-token",
                "domain": "na-trade.naeu.playblackdesert.com",
                "path": "/",
            }
        ]
        consent_results = []

        class FakeFirstLocator:
            def __init__(self, page, selector):
                self.page = page
                self.selector = selector

            async def click(self, timeout=None):
                if self.selector == "#CybotCookiebotDialogBodyButtonDecline":
                    self.page.clicked.append(("cookie", self.selector, timeout))
                    return None
                if self.selector == "#btnSteam":
                    self.page.clicked.append(("steam", self.selector, timeout))
                    self.page.steam_clicked = True
                    return None
                raise RuntimeError("not found")

            async def is_visible(self, timeout=None):
                return False

        class FakeLocator:
            def __init__(self, page, selector):
                self.first = FakeFirstLocator(page, selector)

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"
            frames = []

            def __init__(self):
                self.clicked = []
                self.steam_clicked = False

            def is_closed(self):
                return False

            def locator(self, selector):
                return FakeLocator(self, selector)

        class FakeContext:
            def __init__(self):
                self.page = FakePage()
                self.pages = [self.page]

            def on(self, event, handler):
                pass

            async def cookies(self, *_args):
                return fresh if self.page.steam_clicked else []

        async def run():
            context = FakeContext()
            cookies = await _wait_for_market_cookies(
                context,
                status_callback=None,
                timeout_seconds=1,
                auto_steam_login=True,
                handle_pa_cookie_consent=True,
                pa_cookie_consent_callback=consent_results.append,
            )
            return context.page.clicked, cookies

        clicked, cookies = asyncio.run(run())

        self.assertEqual(
            clicked,
            [
                ("cookie", "#CybotCookiebotDialogBodyButtonDecline", PA_CONSENT_BUTTON_READY_TIMEOUT_MS),
                ("steam", "#btnSteam", 1000),
            ],
        )
        self.assertEqual([cookie["name"] for cookie in cookies], ["TradeAuth_Session"])
        self.assertEqual(consent_results, [True])

    def test_steam_profile_login_detection_uses_auth_cookie_names_only(self):
        self.assertEqual(STEAM_LOGIN_COOKIE_NAMES, {"steamLoginSecure"})
        self.assertTrue(
            _has_steam_login_cookie(
                [
                    {"name": "sessionid", "domain": ".steampowered.com"},
                    {"name": "steamLoginSecure", "domain": ".steampowered.com"},
                ]
            )
        )
        self.assertFalse(
            _has_steam_login_cookie(
                [
                    {"name": "sessionid", "domain": ".steampowered.com"},
                    {"name": "browserid", "domain": ".steampowered.com"},
                ]
            )
        )

    def test_steam_profile_login_detection_observes_local_cookie_store(self):
        statuses = []
        cookie_url_calls = []

        class FakePage:
            url = STEAM_STORE_URL

            def is_closed(self):
                return False

        class FakeContext:
            pages = [FakePage()]

            async def cookies(self, urls):
                cookie_url_calls.append(tuple(urls))
                return [{"name": "steamLoginSecure", "domain": ".steampowered.com"}]

        async def status_callback(message, level):
            statuses.append((message, level))

        asyncio.run(_wait_for_steam_profile_login(FakeContext(), status_callback, timeout_seconds=1))

        self.assertEqual(cookie_url_calls, [STEAM_PROFILE_COOKIE_URLS])
        self.assertEqual(
            statuses,
            [("Steam login detected in the browser profile.", "info")],
        )
        self.assertNotIn("api.steampowered.com", "".join(cookie_url_calls[0]))
        self.assertIn(STEAM_STORE_URL, cookie_url_calls[0])
        self.assertIn(STEAM_COMMUNITY_URL, cookie_url_calls[0])

    def test_steam_profile_login_detection_observes_loaded_store_dom(self):
        statuses = []
        checked_selectors = []

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def is_visible(self, timeout=None):
                checked_selectors.append((self.selector, timeout))
                return self.selector == "#account_pulldown"

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = STEAM_STORE_URL

            def is_closed(self):
                return False

            def locator(self, selector):
                return FakeLocator(selector)

        class FakeContext:
            pages = [FakePage()]

            async def cookies(self, urls):
                return []

        async def status_callback(message, level):
            statuses.append((message, level))

        asyncio.run(_wait_for_steam_profile_login(FakeContext(), status_callback, timeout_seconds=1))

        self.assertEqual(checked_selectors, [("#account_pulldown", 500)])
        self.assertEqual(statuses, [("Steam login detected in the browser profile.", "info")])

    def test_steam_profile_login_wait_prompts_remember_me(self):
        warn = AsyncMock()

        class FakePage:
            url = "https://steamcommunity.com/login"

            def is_closed(self):
                return False

        class FakeContext:
            pages = [FakePage()]

            async def cookies(self, urls):
                return []

        async def run():
            with patch(
                "bdo_marketplace_tools.market.browser_auth._set_setup_notice_warning",
                new=warn,
            ), patch("bdo_marketplace_tools.market.browser_auth.STEAM_PROFILE_SETUP_POLL_SECONDS", 0.01):
                with self.assertRaisesRegex(BrowserAuthError, "Steam setup timed out"):
                    await _wait_for_steam_profile_login(FakeContext(), None, timeout_seconds=0.05)

        asyncio.run(run())

        self.assertGreaterEqual(warn.await_count, 1)
        self.assertIn("Remember me", warn.await_args.args[1])

    def test_steam_profile_logged_in_dom_ignores_non_store_hosts(self):
        class FakePage:
            url = "https://steamcommunity.com/"

            def locator(self, selector):
                raise AssertionError("Non-store pages should not be queried for Store DOM markers.")

        self.assertFalse(asyncio.run(_steam_store_logged_in_dom_ready(FakePage())))

    def test_clear_steam_browser_profile_cookies_clears_persistent_context(self):
        class FakePlaywrightManager:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeContext:
            pages = []

            def __init__(self):
                self.cleared = False
                self.closed = False
                self.new_page_called = False

            async def new_page(self):
                self.new_page_called = True
                return object()

            async def cookies(self):
                return [
                    {"name": "steamLoginSecure", "value": "secret"},
                    {"name": "sessionid", "value": "also-secret"},
                ]

            async def clear_cookies(self):
                self.cleared = True

            async def close(self):
                self.closed = True

        fake_context = FakeContext()
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "patchright.async_api.async_playwright",
            return_value=FakePlaywrightManager(),
        ), patch(
            "bdo_marketplace_tools.market.browser_auth._launch_persistent_chrome_context",
            new=AsyncMock(return_value=fake_context),
        ):
            cleared_count = asyncio.run(clear_steam_browser_profile_cookies(profile_path=Path(temp_dir)))

        self.assertEqual(cleared_count, 2)
        self.assertTrue(fake_context.new_page_called)
        self.assertTrue(fake_context.cleared)
        self.assertTrue(fake_context.closed)

    def test_is_steam_auth_cookie_matches_steam_domains_only(self):
        self.assertTrue(_is_steam_auth_cookie({"name": "steamLoginSecure", "domain": "steamcommunity.com"}))
        self.assertTrue(_is_steam_auth_cookie({"name": "steamLoginSecure", "domain": ".store.steampowered.com"}))
        self.assertTrue(_is_steam_auth_cookie({"name": "sessionid", "domain": "login.steampowered.com"}))
        # Marketplace / Pearl Abyss / consent cookies are not Steam auth cookies.
        self.assertFalse(_is_steam_auth_cookie({"name": "TradeAuth_Session", "domain": "na-trade.naeu.playblackdesert.com"}))
        self.assertFalse(_is_steam_auth_cookie({"name": "CookieConsent", "domain": ".playblackdesert.com"}))
        self.assertFalse(_is_steam_auth_cookie({"name": "x", "domain": "account.pearlabyss.com"}))
        # A look-alike domain must not be matched by the suffix check.
        self.assertFalse(_is_steam_auth_cookie({"name": "x", "domain": "notsteampowered.com.evil.com"}))
        self.assertFalse(_is_steam_auth_cookie({"name": "x", "domain": ""}))

    def test_clear_market_cookies_keep_steam_login_keeps_only_steam(self):
        class FakePlaywrightManager:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        all_cookies = [
            {"name": "steamLoginSecure", "value": "secret", "domain": "steamcommunity.com", "path": "/"},
            {"name": "sessionid", "value": "s", "domain": ".store.steampowered.com", "path": "/"},
            {"name": "TradeAuth_Session", "value": "t", "domain": "na-trade.naeu.playblackdesert.com", "path": "/"},
            {"name": "CookieConsent", "value": "c", "domain": ".playblackdesert.com", "path": "/"},
            {"name": "pa", "value": "p", "domain": "account.pearlabyss.com", "path": "/"},
        ]

        class FakeContext:
            pages = []

            def __init__(self):
                self.cleared = False
                self.closed = False
                self.added = None

            async def new_page(self):
                return object()

            async def cookies(self):
                return list(all_cookies)

            async def clear_cookies(self):
                self.cleared = True

            async def add_cookies(self, cookies):
                self.added = list(cookies)

            async def close(self):
                self.closed = True

        fake_context = FakeContext()
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "patchright.async_api.async_playwright",
            return_value=FakePlaywrightManager(),
        ), patch(
            "bdo_marketplace_tools.market.browser_auth._launch_persistent_chrome_context",
            new=AsyncMock(return_value=fake_context),
        ):
            cleared_count = asyncio.run(clear_market_cookies_keep_steam_login(profile_path=Path(temp_dir)))

        # Three non-Steam cookies cleared; the two Steam cookies re-added so the login survives.
        self.assertEqual(cleared_count, 3)
        self.assertTrue(fake_context.cleared)
        self.assertEqual(
            [cookie["name"] for cookie in fake_context.added],
            ["steamLoginSecure", "sessionid"],
        )
        self.assertTrue(fake_context.closed)

    def test_market_cookie_acquisition_bootstraps_profile_before_auth_start(self):
        class FakePlaywrightManager:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakePage:
            def __init__(self):
                self.goto_calls = []
                self.closed = False

            async def goto(self, url, wait_until=None, timeout=None):
                self.goto_calls.append((url, wait_until, timeout))

            def is_closed(self):
                return self.closed

            async def close(self, run_before_unload=None):
                self.closed = True

        class FakeContext:
            def __init__(self, page):
                self.pages = [page]
                self.closed = False

            async def close(self):
                self.closed = True

        fake_page = FakePage()
        fake_context = FakeContext(fake_page)
        captured_cookies = [{"name": "TradeAuth_Session", "value": "ok"}]
        auth_start_url = "https://na-trade.naeu.playblackdesert.com/"

        async def wait_for_cookies(*_args, **_kwargs):
            for _attempt in range(10):
                if len(fake_page.goto_calls) >= 2:
                    break
                await asyncio.sleep(0)
            return captured_cookies

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "patchright.async_api.async_playwright",
            return_value=FakePlaywrightManager(),
        ), patch(
            "bdo_marketplace_tools.market.browser_auth._launch_persistent_chrome_context",
            new=AsyncMock(return_value=fake_context),
        ), patch(
            "bdo_marketplace_tools.market.browser_auth._accept_required_cookie_consent_if_available",
            new=AsyncMock(return_value=COOKIE_CONSENT_NOT_FOUND),
        ) as cookie_consent, patch(
            "bdo_marketplace_tools.market.browser_auth._wait_for_market_cookies",
            new=AsyncMock(side_effect=wait_for_cookies),
        ) as wait_for_cookies:
            cookies = asyncio.run(
                acquire_market_cookies(
                    profile_path=Path(temp_dir),
                    start_url=auth_start_url,
                    bootstrap_url=BDO_SITE_BOOTSTRAP_URL,
                    account_label="Pearl Abyss Account",
                )
            )

        self.assertEqual(cookies, captured_cookies)
        self.assertEqual(
            fake_page.goto_calls,
            [
                (BDO_SITE_BOOTSTRAP_URL, "domcontentloaded", 60000),
                (auth_start_url, "domcontentloaded", 60000),
            ],
        )
        cookie_consent.assert_awaited_once_with(
            fake_page,
            None,
            profile_label="Pearl Abyss Account browser profile",
        )
        wait_for_cookies.assert_awaited_once()
        self.assertTrue(fake_page.closed)
        self.assertTrue(fake_context.closed)

    def _run_acquire_for_notice(self, *, handle_pa_cookie_consent):
        class FakePlaywrightManager:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakePage:
            def __init__(self):
                self.closed = False

            async def goto(self, url, wait_until=None, timeout=None):
                pass

            def is_closed(self):
                return self.closed

            async def close(self, run_before_unload=None):
                self.closed = True

        class FakeContext:
            def __init__(self, page):
                self.pages = [page]
                self.closed = False
                self.init_scripts = []

            async def add_init_script(self, script):
                self.init_scripts.append(script)

            async def close(self):
                self.closed = True

        fake_context = FakeContext(FakePage())

        async def wait_for_cookies(*_args, **_kwargs):
            return [{"name": "TradeAuth_Session", "value": "ok"}]

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "patchright.async_api.async_playwright",
            return_value=FakePlaywrightManager(),
        ), patch(
            "bdo_marketplace_tools.market.browser_auth._launch_persistent_chrome_context",
            new=AsyncMock(return_value=fake_context),
        ), patch(
            "bdo_marketplace_tools.market.browser_auth._wait_for_market_cookies",
            new=AsyncMock(side_effect=wait_for_cookies),
        ):
            asyncio.run(
                acquire_market_cookies(
                    profile_path=Path(temp_dir),
                    auto_steam_login=True,
                    handle_pa_cookie_consent=handle_pa_cookie_consent,
                    account_label="Steam Account",
                )
            )
        return fake_context

    def test_setup_notice_injected_on_auth_popup(self):
        fake_context = self._run_acquire_for_notice(handle_pa_cookie_consent=True)

        self.assertEqual(len(fake_context.init_scripts), 1)
        script = fake_context.init_scripts[0]
        self.assertIn("Setting up your session", script)
        # Paints the box element that the main-world warning flip later targets by id.
        self.assertIn("__bdo_setup_notice__", script)
        # Dims the whole page (a scrim) so it's obvious not to interact while the app drives login.
        self.assertIn("__bdo_setup_scrim__", script)
        # Cosmetic only: it must never be able to intercept a click.
        self.assertIn("pointer-events:none", script)

    def test_setup_notice_injected_even_without_cookie_consent_handling(self):
        # The notice is no longer gated on first-time cookie-consent handling; it shows on every
        # embedded auth pop-up (login / reauth) so the user doesn't click during slow page loads.
        fake_context = self._run_acquire_for_notice(handle_pa_cookie_consent=False)

        self.assertEqual(len(fake_context.init_scripts), 1)
        self.assertIn("Setting up your session", fake_context.init_scripts[0])

    def test_browser_notice_scripts_are_ascii(self):
        from bdo_marketplace_tools.market.browser_auth import (
            SETUP_NOTICE_CREDENTIALS_SCRIPT,
            SETUP_NOTICE_SCRIPT,
            SETUP_NOTICE_WARN_SCRIPT,
            STEAM_REMEMBER_ME_GUIDE_SCRIPT,
        )

        for script in (
            SETUP_NOTICE_SCRIPT,
            SETUP_NOTICE_WARN_SCRIPT,
            SETUP_NOTICE_CREDENTIALS_SCRIPT,
            STEAM_REMEMBER_ME_GUIDE_SCRIPT,
        ):
            self.assertTrue(script.isascii())

    def test_set_setup_notice_warning_evaluates_in_page(self):
        from bdo_marketplace_tools.market.browser_auth import (
            SETUP_NOTICE_CAPTCHA_MESSAGE,
            _set_setup_notice_warning,
        )

        calls = []

        class FakePage:
            async def evaluate(self, script, arg=None):
                calls.append((script, arg))

        asyncio.run(_set_setup_notice_warning(FakePage(), SETUP_NOTICE_CAPTCHA_MESSAGE))

        self.assertEqual(len(calls), 1)
        # Self-contained main-world DOM flip (not a call into the isolated-world init script).
        self.assertIn("Manual action required", calls[0][0])
        self.assertIn("__bdo_setup_notice__", calls[0][0])
        # The manual-action flip lifts the dim/blur scrim so the page is usable.
        self.assertIn("__bdo_setup_scrim__", calls[0][0])
        self.assertEqual(calls[0][1], SETUP_NOTICE_CAPTCHA_MESSAGE)

    def test_set_setup_notice_warning_is_best_effort(self):
        from bdo_marketplace_tools.market.browser_auth import _set_setup_notice_warning

        class FakePageNoEvaluate:
            pass

        class FakePageRaises:
            async def evaluate(self, script, arg=None):
                raise RuntimeError("boom")

        # A missing evaluate or an exception must never propagate out of the cosmetic notice helper.
        asyncio.run(_set_setup_notice_warning(FakePageNoEvaluate(), "x"))
        asyncio.run(_set_setup_notice_warning(FakePageRaises(), "x"))

    def test_steam_remember_me_guide_targets_checkbox_by_label(self):
        from bdo_marketplace_tools.market.browser_auth import (
            STEAM_REMEMBER_ME_GUIDE_SCRIPT,
            _show_steam_remember_me_guide,
        )

        # Located by the visible label text + role, never by Steam's build-hashed class names.
        self.assertIn("remember me", STEAM_REMEMBER_ME_GUIDE_SCRIPT.lower())
        self.assertIn('role="checkbox"', STEAM_REMEMBER_ME_GUIDE_SCRIPT)
        # Cosmetic only: it must never intercept the user's click on the checkbox.
        self.assertIn("pointer-events:none", STEAM_REMEMBER_ME_GUIDE_SCRIPT)

        calls = []

        class FakePage:
            async def evaluate(self, script, *args):
                calls.append(script)

        asyncio.run(_show_steam_remember_me_guide(FakePage()))
        self.assertEqual(len(calls), 1)
        self.assertIn("__bdo_remember_ring__", calls[0])

    def test_steam_remember_me_guide_is_best_effort(self):
        from bdo_marketplace_tools.market.browser_auth import _show_steam_remember_me_guide

        class FakePageNoEvaluate:
            pass

        class FakePageRaises:
            async def evaluate(self, script, *args):
                raise RuntimeError("boom")

        asyncio.run(_show_steam_remember_me_guide(FakePageNoEvaluate()))
        asyncio.run(_show_steam_remember_me_guide(FakePageRaises()))

    def test_set_setup_notice_credentials_rejected_uses_distinct_state(self):
        from bdo_marketplace_tools.market.browser_auth import (
            SETUP_NOTICE_CREDENTIALS_SCRIPT,
            _set_setup_notice_credentials_rejected,
        )

        # A calm "fix it in the app, safe to close" card, distinct from the red "act here" warning.
        self.assertIn("Wrong email or password", SETUP_NOTICE_CREDENTIALS_SCRIPT)
        self.assertIn("Safe to close this window", SETUP_NOTICE_CREDENTIALS_SCRIPT)
        self.assertIn("in the app", SETUP_NOTICE_CREDENTIALS_SCRIPT)
        # Re-dims the page (the user must not log in here) and stays click-through.
        self.assertIn("__bdo_setup_scrim__", SETUP_NOTICE_CREDENTIALS_SCRIPT)
        self.assertIn("pointer-events:none", SETUP_NOTICE_CREDENTIALS_SCRIPT)

        calls = []

        class FakePage:
            async def evaluate(self, script, *args):
                calls.append(script)

        asyncio.run(_set_setup_notice_credentials_rejected(FakePage()))
        self.assertEqual(len(calls), 1)
        self.assertIn("__bdo_setup_notice__", calls[0])

    def test_set_setup_notice_credentials_rejected_is_best_effort(self):
        from bdo_marketplace_tools.market.browser_auth import _set_setup_notice_credentials_rejected

        class FakePageNoEvaluate:
            pass

        class FakePageRaises:
            async def evaluate(self, script, *args):
                raise RuntimeError("boom")

        asyncio.run(_set_setup_notice_credentials_rejected(FakePageNoEvaluate()))
        asyncio.run(_set_setup_notice_credentials_rejected(FakePageRaises()))

    def test_market_cookie_wait_dialog_manual_attention_flips_notice_to_warning(self):
        warn = AsyncMock()
        statuses = []
        dialog_state = _new_auth_dialog_state()
        dialog_state["manual_attention"] = {
            "message": "Please complete the verification.",
            "type": "alert",
            "category": AUTH_DIALOG_VERIFICATION_REQUIRED,
        }

        class FakePage:
            url = "https://account.pearlabyss.com/en-US/Member/Login"
            frames = []

            def is_closed(self):
                return False

            def locator(self, _selector):
                return None

        class FakeContext:
            pages = [FakePage()]

            def on(self, event, handler):
                pass

            def remove_listener(self, event, handler):
                pass

            async def cookies(self, *_args):
                return []

        async def status_callback(message, level):
            statuses.append((message, level))

        async def run():
            with patch(
                "bdo_marketplace_tools.market.browser_auth._maybe_prepare_pa_cookie_consent",
                new=AsyncMock(return_value=None),
            ), patch(
                "bdo_marketplace_tools.market.browser_auth._set_setup_notice_warning",
                new=warn,
            ), patch("bdo_marketplace_tools.market.browser_auth.BROWSER_AUTH_POLL_SECONDS", 0.01):
                with self.assertRaisesRegex(BrowserAuthError, "login was completed"):
                    await _wait_for_market_cookies(
                        FakeContext(),
                        status_callback=status_callback,
                        timeout_seconds=0.05,
                        auto_pa_login=True,
                        pa_email="user@example.com",
                        pa_password="secret",
                        account_label="Pearl Abyss Account",
                        auth_dialog_state=dialog_state,
                    )

        asyncio.run(run())

        self.assertGreaterEqual(warn.await_count, 1)
        self.assertIn("verification", warn.await_args.args[1].lower())
        self.assertEqual(
            [event for event in statuses if event[1] == "warning"],
            [("Pearl Abyss verification is required. Complete it manually in the browser.", "warning")],
        )

    def test_market_cookie_wait_manual_attention_also_blocks_steam_auto_login(self):
        # E: a captured dialog (manual_attention) must stop *all* auto-login, not just PA. On a Steam
        # page _should_attempt_steam_auto_login would otherwise return True, so the gate is the only
        # thing preventing a click.
        self.assertTrue(_should_attempt_steam_auto_login("steam", True, False))

        steam_login = AsyncMock(return_value=STEAM_AUTO_LOGIN_CLICKED)
        statuses = []
        dialog_state = _new_auth_dialog_state()
        dialog_state["manual_attention"] = {
            "message": "Please complete the verification.",
            "type": "alert",
            "category": AUTH_DIALOG_VERIFICATION_REQUIRED,
        }

        class FakePage:
            url = "https://steamcommunity.com/openid/login"
            frames = []

            def is_closed(self):
                return False

            def locator(self, _selector):
                return None

        class FakeContext:
            pages = [FakePage()]

            def on(self, event, handler):
                pass

            def remove_listener(self, event, handler):
                pass

            async def cookies(self, *_args):
                return []

        async def status_callback(message, level):
            statuses.append((message, level))

        async def run():
            with patch(
                "bdo_marketplace_tools.market.browser_auth._maybe_run_steam_auto_login",
                new=steam_login,
            ), patch(
                "bdo_marketplace_tools.market.browser_auth._set_setup_notice_warning",
                new=AsyncMock(),
            ), patch("bdo_marketplace_tools.market.browser_auth.BROWSER_AUTH_POLL_SECONDS", 0.01):
                with self.assertRaisesRegex(BrowserAuthError, "login was completed"):
                    await _wait_for_market_cookies(
                        FakeContext(),
                        status_callback=status_callback,
                        timeout_seconds=0.05,
                        auto_steam_login=True,
                        auto_pa_login=False,
                        account_label="Steam Account",
                        auth_dialog_state=dialog_state,
                    )

        asyncio.run(run())

        self.assertEqual(steam_login.await_count, 0)

    def test_browser_context_close_closes_pages_before_context(self):
        async def run_close():
            close_order = []

            class FakePage:
                def __init__(self):
                    self.closed = False

                def is_closed(self):
                    return self.closed

                async def close(self, run_before_unload=None):
                    self.closed = True
                    close_order.append(("page", run_before_unload))

            class FakeContext:
                def __init__(self):
                    self.page = FakePage()
                    self.pages = [self.page]

                async def close(self):
                    close_order.append(("context", None))

            fake_context = FakeContext()
            await _close_browser_context(fake_context)
            return fake_context, close_order

        fake_context, close_order = asyncio.run(run_close())

        self.assertTrue(fake_context.page.closed)
        self.assertEqual(close_order, [("page", False), ("context", None)])

    def test_browser_context_close_warns_when_context_close_hangs(self):
        async def run_close():
            statuses = []

            class FakeContext:
                pages = []

                async def close(self):
                    await asyncio.Event().wait()

            async def status_callback(message, level):
                statuses.append((message, level))

            with patch("bdo_marketplace_tools.market.browser_auth.BROWSER_CONTEXT_CLOSE_TIMEOUT_SECONDS", 0.01):
                await _close_browser_context(FakeContext(), status_callback=status_callback)
            return statuses

        statuses = asyncio.run(run_close())

        self.assertEqual(
            statuses,
            [("Browser cookies were captured, but Chrome did not close cleanly. Close it manually if it remains open.", "warning")],
        )

    def test_market_cookie_wait_timeout_uses_account_label(self):
        class FakeContext:
            pages = []

            async def cookies(self, *_args):
                return []

        with self.assertRaisesRegex(BrowserAuthError, "Pearl Abyss Account browser session timed out"):
            asyncio.run(
                _wait_for_market_cookies(
                    FakeContext(),
                    status_callback=None,
                    timeout_seconds=0,
                    account_label="Pearl Abyss Account",
                )
            )

    def test_market_cookie_capture_reads_cookies_on_oauth_callback_response(self):
        # The market session cookie only becomes visible to the polling path once the callback
        # has fired; the page never reaches "market" state. Capture must still close, driven by
        # the OAuth callback response listener rather than waiting for the market document.
        fresh = [
            {
                "name": "TradeAuth_Session",
                "value": "fresh-token",
                "domain": "na-trade.naeu.playblackdesert.com",
                "path": "/",
            }
        ]
        callback_url = "https://na-trade.naeu.playblackdesert.com/Pearlabyss/Oauth2CallBack?code=secret"

        class FakeResponse:
            url = callback_url

        class FakePage:
            url = "https://account.pearlabyss.com/en-US/Member/Login"

            def is_closed(self):
                return False

        class FakeContext:
            def __init__(self):
                self.pages = [FakePage()]
                self.handler = None
                self.callback_fired = False

            def on(self, event, handler):
                if event == "response":
                    self.handler = handler

            def remove_listener(self, event, handler):
                pass

            async def cookies(self, *_args):
                # Baseline and the polling path never see the session cookie; only the read
                # triggered by the callback response does.
                return list(fresh) if self.callback_fired else []

        async def run():
            context = FakeContext()
            task = asyncio.ensure_future(
                _wait_for_market_cookies(
                    context,
                    status_callback=None,
                    timeout_seconds=5,
                    account_label="Pearl Abyss Account",
                )
            )
            for _ in range(5):
                await asyncio.sleep(0)
                if context.handler is not None:
                    break
            context.callback_fired = True
            context.handler(FakeResponse())
            return await asyncio.wait_for(task, timeout=2)

        result = asyncio.run(run())
        self.assertEqual([cookie["name"] for cookie in result], ["TradeAuth_Session"])

    def test_market_cookie_wait_pa_submit_keeps_polling_until_session_captured(self):
        fresh = [
            {
                "name": "TradeAuth_Session",
                "value": "fresh-token",
                "domain": "na-trade.naeu.playblackdesert.com",
                "path": "/",
            }
        ]

        class FakeFirstLocator:
            async def is_visible(self, timeout=None):
                return False

        class FakeLocator:
            first = FakeFirstLocator()

        class FakePage:
            url = "https://account.pearlabyss.com/en-US/Member/Login"
            frames = []

            def is_closed(self):
                return False

            def locator(self, _selector):
                return FakeLocator()

        class FakeContext:
            def __init__(self, auto_login):
                self.pages = [FakePage()]
                self.auto_login = auto_login

            def on(self, event, handler):
                pass

            def remove_listener(self, event, handler):
                pass

            async def cookies(self, *_args):
                if self.auto_login.await_count >= 1:
                    return list(fresh)
                return []

        async def run():
            auto_login = AsyncMock(return_value=PA_AUTO_LOGIN_SUBMITTED)
            context = FakeContext(auto_login)
            with patch(
                "bdo_marketplace_tools.market.browser_auth._maybe_run_pa_credentials_login",
                new=auto_login,
            ):
                cookies = await _wait_for_market_cookies(
                    context,
                    status_callback=None,
                    timeout_seconds=1,
                    auto_pa_login=True,
                    pa_email="user@example.com",
                    pa_password="secret",
                    account_label="Pearl Abyss Account",
                )
            return cookies, auto_login.await_count

        cookies, auto_login_calls = asyncio.run(run())

        self.assertEqual([cookie["name"] for cookie in cookies], ["TradeAuth_Session"])
        self.assertEqual(auto_login_calls, 1)

    def test_market_cookie_wait_pa_manual_needed_stops_auto_login_attempts(self):
        class FakeFirstLocator:
            async def is_visible(self, timeout=None):
                return False

        class FakeLocator:
            first = FakeFirstLocator()

        class FakePage:
            url = "https://account.pearlabyss.com/en-US/Member/Login"
            frames = []

            def is_closed(self):
                return False

            def locator(self, _selector):
                return FakeLocator()

        class FakeContext:
            pages = [FakePage()]

            def on(self, event, handler):
                pass

            def remove_listener(self, event, handler):
                pass

            async def cookies(self, *_args):
                return []

        async def run():
            auto_login = AsyncMock(
                side_effect=[
                    PA_AUTO_LOGIN_SUBMITTED,
                    PA_AUTO_LOGIN_MANUAL_NEEDED,
                    AssertionError("PA auto-login should stop after manual fallback"),
                ]
            )
            with patch(
                "bdo_marketplace_tools.market.browser_auth._maybe_run_pa_credentials_login",
                new=auto_login,
            ), patch("bdo_marketplace_tools.market.browser_auth.BROWSER_AUTH_POLL_SECONDS", 0.01):
                with self.assertRaisesRegex(BrowserAuthError, "Pearl Abyss Account browser session timed out"):
                    await _wait_for_market_cookies(
                        FakeContext(),
                        status_callback=None,
                        timeout_seconds=0.08,
                        auto_pa_login=True,
                        pa_email="user@example.com",
                        pa_password="secret",
                        account_label="Pearl Abyss Account",
                    )
            return auto_login.await_count

        auto_login_calls = asyncio.run(run())

        self.assertEqual(auto_login_calls, 2)

    def test_auth_dialog_verification_is_captured_and_accepted(self):
        dialog_state = _new_auth_dialog_state()
        accepted = []

        class FakeDialog:
            message = "Please complete the verification."
            type = "alert"

            async def accept(self):
                accepted.append(True)

        asyncio.run(_handle_auth_dialog(FakeDialog(), dialog_state))

        self.assertEqual(dialog_state["manual_attention"]["category"], AUTH_DIALOG_VERIFICATION_REQUIRED)
        self.assertEqual(dialog_state["manual_attention"]["message"], "Please complete the verification.")
        self.assertEqual(accepted, [True])

    def test_auth_dialog_invalid_credentials_is_captured_and_accepted(self):
        dialog_state = _new_auth_dialog_state()
        accepted = []

        class FakeDialog:
            message = "account.pearlabyss.com says Please double-check your email and password."
            type = "alert"

            async def accept(self):
                accepted.append(True)

        asyncio.run(_handle_auth_dialog(FakeDialog(), dialog_state))

        self.assertEqual(dialog_state["manual_attention"]["category"], AUTH_DIALOG_INVALID_CREDENTIALS)
        self.assertIn("double-check your email and password", dialog_state["manual_attention"]["message"])
        self.assertEqual(accepted, [True])

    def test_auth_dialog_listener_attaches_existing_and_future_pages(self):
        dialog_state = _new_auth_dialog_state()

        class FakePage:
            def __init__(self):
                self.handlers = {}

            def on(self, event, handler):
                self.handlers[event] = handler

        class FakeContext:
            def __init__(self):
                self.page = FakePage()
                self.new_page = FakePage()
                self.pages = [self.page]
                self.handlers = {}

            def on(self, event, handler):
                self.handlers[event] = handler

        context = FakeContext()
        _install_auth_dialog_handlers(context, dialog_state)
        context.handlers["page"](context.new_page)

        self.assertIn("dialog", context.page.handlers)
        self.assertIn("dialog", context.new_page.handlers)

    def test_steam_auto_login_disabled_does_not_click_buttons(self):
        clicked_selectors = []
        statuses = []

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def click(self, timeout=None):
                clicked_selectors.append((self.selector, timeout))

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FakeLocator(selector)

        async def status_callback(message, level):
            statuses.append((message, level))

        result = asyncio.run(
            _maybe_run_steam_auto_login(
                FakePage(),
                "pa",
                enabled=False,
                tracking=_new_steam_auto_login_state(),
                status_callback=status_callback,
            )
        )

        self.assertEqual(result, STEAM_AUTO_LOGIN_DISABLED)
        self.assertEqual(clicked_selectors, [])
        self.assertEqual(statuses, [])

    def test_pa_credentials_auto_login_fills_fields_and_clicks_login(self):
        filled_fields = []
        clicked_selectors = []
        statuses = []

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def fill(self, value, timeout=None):
                if self.selector not in {"#_email", "#_password"}:
                    raise RuntimeError("not found")
                filled_fields.append((self.selector, value, timeout))

            async def click(self, timeout=None):
                if self.selector != "#btnLogin":
                    raise RuntimeError("not found")
                clicked_selectors.append((self.selector, timeout))

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FakeLocator(selector)

        async def status_callback(message, level):
            statuses.append((message, level))

        result = asyncio.run(
            _maybe_run_pa_credentials_login(
                FakePage(),
                "pa",
                enabled=True,
                email="user@example.com",
                password="secret",
                tracking=_new_pa_auto_login_state(),
                status_callback=status_callback,
            )
        )

        self.assertEqual(result, PA_AUTO_LOGIN_SUBMITTED)
        self.assertEqual(
            filled_fields,
            [
                ("#_email", "user@example.com", 1000),
                ("#_password", "secret", 1000),
            ],
        )
        self.assertEqual(clicked_selectors, [("#btnLogin", 1000)])
        self.assertEqual(statuses, [("Automatic Pearl Abyss login submitted saved credentials.", "info")])

    def test_pa_credentials_auto_login_requires_saved_password(self):
        clicked_selectors = []

        class FakeFirstLocator:
            async def fill(self, *_args, **_kwargs):
                clicked_selectors.append("fill")

            async def click(self, *_args, **_kwargs):
                clicked_selectors.append("click")

        class FakeLocator:
            first = FakeFirstLocator()

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, _selector):
                return FakeLocator()

        result = asyncio.run(
            _maybe_run_pa_credentials_login(
                FakePage(),
                "pa",
                enabled=True,
                email="user@example.com",
                password=None,
                tracking=_new_pa_auto_login_state(),
            )
        )

        self.assertEqual(result, PA_AUTO_LOGIN_DISABLED)
        self.assertEqual(clicked_selectors, [])

    def test_pa_credentials_auto_login_retries_when_login_form_returns_after_submit(self):
        filled_fields = []
        clicked_selectors = []
        statuses = []
        tracking = _new_pa_auto_login_state()

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def fill(self, value, timeout=None):
                if self.selector not in {"#_email", "#_password"}:
                    raise RuntimeError("not found")
                filled_fields.append((self.selector, value, timeout))

            async def click(self, timeout=None):
                if self.selector != "#btnLogin":
                    raise RuntimeError("not found")
                clicked_selectors.append((self.selector, timeout))

            async def is_visible(self, timeout=None):
                return self.selector in {"#_email", "#_password"}

            async def input_value(self, timeout=None):
                if self.selector == "#_password":
                    return ""
                raise RuntimeError("not found")

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FakeLocator(selector)

        async def status_callback(message, level):
            statuses.append((message, level))

        async def run():
            page = FakePage()
            return [
                await _maybe_run_pa_credentials_login(
                    page,
                    "pa",
                    enabled=True,
                    email="user@example.com",
                    password="secret",
                    tracking=tracking,
                    status_callback=status_callback,
                    now=0,
                ),
                await _maybe_run_pa_credentials_login(
                    page,
                    "pa",
                    enabled=True,
                    email="user@example.com",
                    password="secret",
                    tracking=tracking,
                    status_callback=status_callback,
                    now=1,
                ),
                await _maybe_run_pa_credentials_login(
                    page,
                    "pa",
                    enabled=True,
                    email="user@example.com",
                    password="secret",
                    tracking=tracking,
                    status_callback=status_callback,
                    now=2.1,
                ),
            ]

        results = asyncio.run(run())

        self.assertEqual(results, [PA_AUTO_LOGIN_SUBMITTED, PA_AUTO_LOGIN_WAITING, PA_AUTO_LOGIN_SUBMITTED])
        self.assertEqual(clicked_selectors, [("#btnLogin", 1000), ("#btnLogin", 1000)])
        self.assertEqual(
            filled_fields,
            [
                ("#_email", "user@example.com", 1000),
                ("#_password", "secret", 1000),
                ("#_email", "user@example.com", 1000),
                ("#_password", "secret", 1000),
            ],
        )
        self.assertEqual(
            statuses,
            [
                ("Automatic Pearl Abyss login submitted saved credentials.", "info"),
                ("Pearl Abyss login did not submit cleanly; retrying saved credentials (1/2).", "warning"),
            ],
        )

    def test_pa_credentials_auto_login_stops_on_invalid_credentials_dialog(self):
        clicked_selectors = []
        statuses = []
        tracking = _new_pa_auto_login_state()
        dialog_state = _new_auth_dialog_state()

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def fill(self, _value, timeout=None):
                if self.selector not in {"#_email", "#_password"}:
                    raise RuntimeError("not found")

            async def click(self, timeout=None):
                if self.selector != "#btnLogin":
                    raise RuntimeError("not found")
                clicked_selectors.append((self.selector, timeout))

            async def is_visible(self, timeout=None):
                return self.selector in {"#_email", "#_password"}

            async def input_value(self, timeout=None):
                return "" if self.selector == "#_password" else "user@example.com"

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FakeLocator(selector)

        class FakeDialog:
            message = "Please double-check your email and password."
            type = "alert"

            async def accept(self):
                pass

        async def status_callback(message, level):
            statuses.append((message, level))

        async def run():
            page = FakePage()
            first = await _maybe_run_pa_credentials_login(
                page,
                "pa",
                enabled=True,
                email="user@example.com",
                password="secret",
                tracking=tracking,
                dialog_state=dialog_state,
                status_callback=status_callback,
                now=0,
            )
            await _handle_auth_dialog(FakeDialog(), dialog_state)
            second = await _maybe_run_pa_credentials_login(
                page,
                "pa",
                enabled=True,
                email="user@example.com",
                password="secret",
                tracking=tracking,
                dialog_state=dialog_state,
                status_callback=status_callback,
                now=2.1,
            )
            return [first, second]

        results = asyncio.run(run())

        self.assertEqual(results, [PA_AUTO_LOGIN_SUBMITTED, PA_AUTO_LOGIN_MANUAL_NEEDED])
        self.assertEqual(clicked_selectors, [("#btnLogin", 1000)])
        self.assertIn(
            ("Pearl Abyss rejected the saved email/password. Update saved credentials before refreshing again.", "warning"),
            statuses,
        )

    def test_pa_credentials_auto_login_network_submit_allows_two_retries_only(self):
        clicked_selectors = []
        statuses = []
        tracking = _new_pa_auto_login_state()

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def fill(self, _value, timeout=None):
                if self.selector not in {"#_email", "#_password"}:
                    raise RuntimeError("not found")

            async def click(self, timeout=None):
                if self.selector != "#btnLogin":
                    raise RuntimeError("not found")
                clicked_selectors.append((self.selector, timeout))

            async def is_visible(self, timeout=None):
                return self.selector in {"#_email", "#_password"}

            async def input_value(self, timeout=None):
                if self.selector == "#_password":
                    return ""
                raise RuntimeError("not found")

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FakeLocator(selector)

        async def status_callback(message, level):
            statuses.append((message, level))

        async def run():
            page = FakePage()
            first = await _maybe_run_pa_credentials_login(
                page,
                "pa",
                enabled=True,
                email="user@example.com",
                password="secret",
                tracking=tracking,
                status_callback=status_callback,
                now=0,
            )
            _record_pa_login_process_submit(tracking)
            second = await _maybe_run_pa_credentials_login(
                page,
                "pa",
                enabled=True,
                email="user@example.com",
                password="secret",
                tracking=tracking,
                status_callback=status_callback,
                now=2.1,
            )
            _record_pa_login_process_submit(tracking)
            third = await _maybe_run_pa_credentials_login(
                page,
                "pa",
                enabled=True,
                email="user@example.com",
                password="secret",
                tracking=tracking,
                status_callback=status_callback,
                now=4.2,
            )
            _record_pa_login_process_submit(tracking)
            fourth = await _maybe_run_pa_credentials_login(
                page,
                "pa",
                enabled=True,
                email="user@example.com",
                password="secret",
                tracking=tracking,
                status_callback=status_callback,
                now=6.3,
            )
            return [first, second, third, fourth]

        results = asyncio.run(run())

        self.assertEqual(
            results,
            [
                PA_AUTO_LOGIN_SUBMITTED,
                PA_AUTO_LOGIN_SUBMITTED,
                PA_AUTO_LOGIN_SUBMITTED,
                PA_AUTO_LOGIN_MANUAL_NEEDED,
            ],
        )
        self.assertEqual(clicked_selectors, [("#btnLogin", 1000), ("#btnLogin", 1000), ("#btnLogin", 1000)])
        self.assertIn(
            ("Pearl Abyss login returned to the login page; retrying saved credentials (1/2).", "warning"),
            statuses,
        )
        self.assertIn(
            ("Pearl Abyss login returned to the login page; retrying saved credentials (2/2).", "warning"),
            statuses,
        )

    def test_pa_credentials_auto_login_does_not_retry_without_login_form(self):
        clicked_selectors = []
        tracking = _new_pa_auto_login_state()
        form_visible = True

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def fill(self, _value, timeout=None):
                if not form_visible or self.selector not in {"#_email", "#_password"}:
                    raise RuntimeError("not found")

            async def click(self, timeout=None):
                if not form_visible or self.selector != "#btnLogin":
                    raise RuntimeError("not found")
                clicked_selectors.append((self.selector, timeout))

            async def is_visible(self, timeout=None):
                return form_visible and self.selector in {"#_email", "#_password"}

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/LoginProcess"

            def locator(self, selector):
                return FakeLocator(selector)

        async def run():
            nonlocal form_visible
            page = FakePage()
            first = await _maybe_run_pa_credentials_login(
                page,
                "pa",
                enabled=True,
                email="user@example.com",
                password="secret",
                tracking=tracking,
                now=0,
            )
            form_visible = False
            second = await _maybe_run_pa_credentials_login(
                page,
                "pa",
                enabled=True,
                email="user@example.com",
                password="secret",
                tracking=tracking,
                now=3,
            )
            return [first, second]

        results = asyncio.run(run())

        self.assertEqual(results, [PA_AUTO_LOGIN_SUBMITTED, PA_AUTO_LOGIN_WAITING])
        self.assertEqual(clicked_selectors, [("#btnLogin", 1000)])

    def test_pa_credentials_auto_login_stable_key_survives_page_object_churn(self):
        clicked_selectors = []
        tracking = _new_pa_auto_login_state()

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def fill(self, _value, timeout=None):
                if self.selector not in {"#_email", "#_password"}:
                    raise RuntimeError("not found")

            async def click(self, timeout=None):
                if self.selector != "#btnLogin":
                    raise RuntimeError("not found")
                clicked_selectors.append((self.selector, timeout))

            async def is_visible(self, timeout=None):
                return self.selector in {"#_email", "#_password"}

            async def input_value(self, timeout=None):
                return "" if self.selector == "#_password" else "user@example.com"

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FakeLocator(selector)

        async def run():
            first = await _maybe_run_pa_credentials_login(
                FakePage(),
                "pa",
                enabled=True,
                email="user@example.com",
                password="secret",
                tracking=tracking,
                now=0,
            )
            second = await _maybe_run_pa_credentials_login(
                FakePage(),
                "pa",
                enabled=True,
                email="user@example.com",
                password="secret",
                tracking=tracking,
                now=0.5,
            )
            return [first, second]

        results = asyncio.run(run())

        self.assertEqual(results, [PA_AUTO_LOGIN_SUBMITTED, PA_AUTO_LOGIN_WAITING])
        self.assertEqual(clicked_selectors, [("#btnLogin", 1000)])

    def test_pa_credentials_auto_login_does_not_retry_when_password_remains_filled(self):
        clicked_selectors = []
        statuses = []
        tracking = _new_pa_auto_login_state()

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def fill(self, _value, timeout=None):
                if self.selector not in {"#_email", "#_password"}:
                    raise RuntimeError("not found")

            async def click(self, timeout=None):
                if self.selector != "#btnLogin":
                    raise RuntimeError("not found")
                clicked_selectors.append((self.selector, timeout))

            async def is_visible(self, timeout=None):
                return self.selector in {"#_email", "#_password"}

            async def input_value(self, timeout=None):
                if self.selector == "#_password":
                    return "secret"
                raise RuntimeError("not found")

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FakeLocator(selector)

        async def status_callback(message, level):
            statuses.append((message, level))

        async def run():
            page = FakePage()
            return [
                await _maybe_run_pa_credentials_login(
                    page,
                    "pa",
                    enabled=True,
                    email="user@example.com",
                    password="secret",
                    tracking=tracking,
                    status_callback=status_callback,
                    now=0,
                ),
                await _maybe_run_pa_credentials_login(
                    page,
                    "pa",
                    enabled=True,
                    email="user@example.com",
                    password="secret",
                    tracking=tracking,
                    status_callback=status_callback,
                    now=3,
                ),
            ]

        results = asyncio.run(run())

        self.assertEqual(results, [PA_AUTO_LOGIN_SUBMITTED, PA_AUTO_LOGIN_WAITING])
        self.assertEqual(clicked_selectors, [("#btnLogin", 1000)])
        self.assertEqual(statuses, [("Automatic Pearl Abyss login submitted saved credentials.", "info")])

    def test_pa_credentials_auto_login_stops_after_two_technical_retries(self):
        clicked_selectors = []
        statuses = []
        tracking = _new_pa_auto_login_state()

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def fill(self, _value, timeout=None):
                if self.selector not in {"#_email", "#_password"}:
                    raise RuntimeError("not found")

            async def click(self, timeout=None):
                if self.selector != "#btnLogin":
                    raise RuntimeError("not found")
                clicked_selectors.append((self.selector, timeout))

            async def is_visible(self, timeout=None):
                return self.selector in {"#_email", "#_password"}

            async def input_value(self, timeout=None):
                if self.selector == "#_password":
                    return ""
                raise RuntimeError("not found")

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FakeLocator(selector)

        async def status_callback(message, level):
            statuses.append((message, level))

        async def run():
            page = FakePage()
            results = []
            for now in (0, 2.1, 4.2, 6.3, 8.4):
                results.append(
                    await _maybe_run_pa_credentials_login(
                        page,
                        "pa",
                        enabled=True,
                        email="user@example.com",
                        password="secret",
                        tracking=tracking,
                        status_callback=status_callback,
                        now=now,
                    )
                )
            return results

        results = asyncio.run(run())

        self.assertEqual(
            results,
            [
                PA_AUTO_LOGIN_SUBMITTED,
                PA_AUTO_LOGIN_SUBMITTED,
                PA_AUTO_LOGIN_SUBMITTED,
                PA_AUTO_LOGIN_MANUAL_NEEDED,
                PA_AUTO_LOGIN_MANUAL_NEEDED,
            ],
        )
        self.assertEqual(clicked_selectors, [("#btnLogin", 1000), ("#btnLogin", 1000), ("#btnLogin", 1000)])
        self.assertEqual(statuses[-1][0], "Pearl Abyss login returned to the login page after saved credentials were submitted. Auto-login paused; complete login manually or update saved credentials.")
        self.assertEqual(statuses[-1][1], "warning")

    def test_pa_credentials_auto_login_failed_retry_fill_does_not_consume_budget(self):
        # Regression: technical_retries used to be incremented before the retry fill, so a single
        # failed retry-fill burned the retry budget and jumped straight to manual. The budget and
        # retry status must only be consumed once a resubmit actually goes out.
        clicked_selectors = []
        statuses = []
        tracking = _new_pa_auto_login_state()
        control = {"fill_ok": True}

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def fill(self, _value, timeout=None):
                if self.selector not in {"#_email", "#_password"}:
                    raise RuntimeError("not found")
                if not control["fill_ok"]:
                    raise RuntimeError("fill failed")

            async def click(self, timeout=None):
                if self.selector != "#btnLogin":
                    raise RuntimeError("not found")
                clicked_selectors.append(self.selector)

            async def is_visible(self, timeout=None):
                return self.selector in {"#_email", "#_password"}

            async def input_value(self, timeout=None):
                if self.selector == "#_password":
                    return ""
                raise RuntimeError("not found")

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FakeLocator(selector)

        async def status_callback(message, level):
            statuses.append((message, level))

        async def attempt(page, now):
            return await _maybe_run_pa_credentials_login(
                page,
                "pa",
                enabled=True,
                email="user@example.com",
                password="secret",
                tracking=tracking,
                status_callback=status_callback,
                now=now,
            )

        async def run():
            page = FakePage()
            results = []
            results.append(await attempt(page, 0))      # initial submit succeeds
            control["fill_ok"] = False
            results.append(await attempt(page, 2.1))     # retry, fill fails -> must NOT burn the budget
            status_count_after_failed_fill = len(statuses)
            control["fill_ok"] = True
            results.append(await attempt(page, 2.4))     # retry, fill works now -> real resubmit
            results.append(await attempt(page, 4.5))     # second real retry still allowed
            results.append(await attempt(page, 6.6))     # budget now used -> manual hand-off
            return results, status_count_after_failed_fill

        results, status_count_after_failed_fill = asyncio.run(run())

        self.assertEqual(
            results,
            [
                PA_AUTO_LOGIN_SUBMITTED,
                PA_AUTO_LOGIN_WAITING,
                PA_AUTO_LOGIN_SUBMITTED,
                PA_AUTO_LOGIN_SUBMITTED,
                PA_AUTO_LOGIN_MANUAL_NEEDED,
            ],
        )
        # Initial submit + exactly two real retry submits; the failed-fill poll never clicked.
        self.assertEqual(clicked_selectors, ["#btnLogin", "#btnLogin", "#btnLogin"])
        self.assertEqual(status_count_after_failed_fill, 1)
        self.assertEqual(
            statuses,
            [
                ("Automatic Pearl Abyss login submitted saved credentials.", "info"),
                ("Pearl Abyss login did not submit cleanly; retrying saved credentials (1/2).", "warning"),
                ("Pearl Abyss login did not submit cleanly; retrying saved credentials (2/2).", "warning"),
                (
                    "Pearl Abyss login returned to the login page after saved credentials were submitted. Auto-login paused; complete login manually or update saved credentials.",
                    "warning",
                ),
            ],
        )

    def test_steam_auto_login_clicks_pa_steam_button(self):
        clicked_selectors = []
        statuses = []

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def click(self, timeout=None):
                if self.selector != "#btnSteam":
                    raise RuntimeError("not found")
                clicked_selectors.append((self.selector, timeout))

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FakeLocator(selector)

        async def status_callback(message, level):
            statuses.append((message, level))

        result = asyncio.run(
            _maybe_run_steam_auto_login(
                FakePage(),
                "pa",
                enabled=True,
                tracking=_new_steam_auto_login_state(),
                status_callback=status_callback,
            )
        )

        self.assertEqual(result, STEAM_AUTO_LOGIN_CLICKED)
        self.assertEqual(clicked_selectors, [("#btnSteam", 1000)])
        self.assertEqual(statuses, [("Steam re-auth submitted the Pearl Abyss Steam login.", "info")])

    def test_steam_auto_login_clicks_pa_steam_button_inside_frame(self):
        clicked_selectors = []
        statuses = []

        class MissingFirstLocator:
            async def click(self, timeout=None):
                raise RuntimeError("not found")

        class MissingLocator:
            first = MissingFirstLocator()

        class FrameFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def click(self, timeout=None):
                if self.selector != "#btnSteam":
                    raise RuntimeError("not found")
                clicked_selectors.append((self.selector, timeout))

        class FrameLocator:
            def __init__(self, selector):
                self.first = FrameFirstLocator(selector)

        class FakeFrame:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FrameLocator(selector)

        class FakePage:
            url = "https://na-trade.naeu.playblackdesert.com/"
            frames = [FakeFrame()]

            def locator(self, selector):
                return MissingLocator()

        async def status_callback(message, level):
            statuses.append((message, level))

        result = asyncio.run(
            _maybe_run_steam_auto_login(
                FakePage(),
                "market",
                enabled=True,
                tracking=_new_steam_auto_login_state(),
                status_callback=status_callback,
            )
        )

        self.assertEqual(result, STEAM_AUTO_LOGIN_CLICKED)
        self.assertEqual(clicked_selectors, [("#btnSteam", 1000)])
        self.assertEqual(statuses, [("Steam re-auth submitted the Pearl Abyss Steam login.", "info")])

    def test_steam_auto_login_logs_pa_click_once_across_page_scopes(self):
        statuses = []
        tracking = _new_steam_auto_login_state()

        class MissingFirstLocator:
            async def click(self, timeout=None):
                raise RuntimeError("not found")

        class MissingLocator:
            first = MissingFirstLocator()

        class ClickFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def click(self, timeout=None):
                if self.selector != "#btnSteam":
                    raise RuntimeError("not found")

        class ClickLocator:
            def __init__(self, selector):
                self.first = ClickFirstLocator(selector)

        class TopLevelLoginPage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"
            frames = []

            def locator(self, selector):
                return ClickLocator(selector)

        class LoginFrame:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return ClickLocator(selector)

        class FramedLoginPage:
            url = "https://na-trade.naeu.playblackdesert.com/"
            frames = [LoginFrame()]

            def locator(self, selector):
                return MissingLocator()

        async def status_callback(message, level):
            statuses.append((message, level))

        async def run():
            return [
                await _maybe_run_steam_auto_login(
                    TopLevelLoginPage(),
                    "pa",
                    enabled=True,
                    tracking=tracking,
                    status_callback=status_callback,
                ),
                await _maybe_run_steam_auto_login(
                    FramedLoginPage(),
                    "market",
                    enabled=True,
                    tracking=tracking,
                    status_callback=status_callback,
                ),
            ]

        results = asyncio.run(run())

        self.assertEqual(results, [STEAM_AUTO_LOGIN_CLICKED, STEAM_AUTO_LOGIN_CLICKED])
        self.assertEqual(statuses, [("Steam re-auth submitted the Pearl Abyss Steam login.", "info")])

    def test_steam_auto_login_does_not_probe_top_level_market_page_for_pa_button(self):
        clicked_selectors = []

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def click(self, timeout=None):
                clicked_selectors.append((self.selector, timeout))

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = "https://na-trade.naeu.playblackdesert.com/"
            frames = []

            def locator(self, selector):
                return FakeLocator(selector)

        result = asyncio.run(
            _maybe_run_steam_auto_login(
                FakePage(),
                "market",
                enabled=True,
                tracking=_new_steam_auto_login_state(),
            )
        )

        self.assertEqual(result, STEAM_AUTO_LOGIN_SKIPPED)
        self.assertEqual(clicked_selectors, [])

    def test_steam_auto_login_clicks_steam_sign_in_button(self):
        clicked_selectors = []
        statuses = []

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def click(self, timeout=None):
                if self.selector != "#imageLogin":
                    raise RuntimeError("not found")
                clicked_selectors.append((self.selector, timeout))

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = "https://steamcommunity.com/openid/login"

            def locator(self, selector):
                return FakeLocator(selector)

        async def status_callback(message, level):
            statuses.append((message, level))

        result = asyncio.run(
            _maybe_run_steam_auto_login(
                FakePage(),
                "steam",
                enabled=True,
                tracking=_new_steam_auto_login_state(),
                status_callback=status_callback,
            )
        )

        self.assertEqual(result, STEAM_AUTO_LOGIN_CLICKED)
        self.assertEqual(clicked_selectors, [("#imageLogin", 1000)])
        self.assertEqual(statuses, [("Steam re-auth confirmed the Steam sign-in.", "info")])

    def test_steam_auto_login_leaves_otp_status_only(self):
        clicked_selectors = []
        statuses = []
        state, _is_callback = _classify_url("https://account.pearlabyss.com/en-us/Member/Login/CheckOtp")

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def click(self, timeout=None):
                clicked_selectors.append((self.selector, timeout))

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login/CheckOtp"

            def locator(self, selector):
                return FakeLocator(selector)

        async def status_callback(message, level):
            statuses.append((message, level))

        result = asyncio.run(
            _maybe_run_steam_auto_login(
                FakePage(),
                state,
                enabled=True,
                tracking=_new_steam_auto_login_state(),
                status_callback=status_callback,
            )
        )

        self.assertEqual(state, "otp")
        self.assertEqual(_status_for_state(state), ("OTP required. Complete verification in the browser.", "warning"))
        self.assertEqual(result, STEAM_AUTO_LOGIN_SKIPPED)
        self.assertEqual(clicked_selectors, [])
        self.assertEqual(statuses, [])

    def test_steam_auto_login_missing_button_reports_manual_input_without_failing(self):
        statuses = []

        class FakeFirstLocator:
            async def click(self, timeout=None):
                raise RuntimeError("not found")

        class FakeLocator:
            first = FakeFirstLocator()

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FakeLocator()

        async def status_callback(message, level):
            statuses.append((message, level))

        result = asyncio.run(
            _maybe_run_steam_auto_login(
                FakePage(),
                "pa",
                enabled=True,
                tracking=_new_steam_auto_login_state(),
                status_callback=status_callback,
                now=10,
                missing_notice_seconds=0,
            )
        )

        self.assertEqual(result, STEAM_AUTO_LOGIN_MANUAL_NEEDED)
        self.assertEqual(
            statuses,
            [("Automatic Steam re-auth is waiting for manual input on the Pearl Abyss page.", "warning")],
        )

    def test_steam_auto_login_retries_click_every_poll_until_navigation(self):
        click_times = []
        statuses = []
        tracking = _new_steam_auto_login_state()

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def click(self, timeout=None):
                if self.selector != "#btnSteam":
                    raise RuntimeError("not found")
                click_times.append(timeout)

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FakeLocator(selector)

        async def status_callback(message, level):
            statuses.append((message, level))

        page = FakePage()

        async def run():
            results = []
            for now in (0.0, 0.25, 0.5):
                results.append(
                    await _maybe_run_steam_auto_login(
                        page,
                        "pa",
                        enabled=True,
                        tracking=tracking,
                        status_callback=status_callback,
                        now=now,
                    )
                )
            return results

        results = asyncio.run(run())

        self.assertEqual(results, [STEAM_AUTO_LOGIN_CLICKED, STEAM_AUTO_LOGIN_CLICKED, STEAM_AUTO_LOGIN_CLICKED])
        self.assertEqual(click_times, [1000, 1000, 1000])
        clicked_messages = [
            message for (message, _level) in statuses if message == "Steam re-auth submitted the Pearl Abyss Steam login."
        ]
        self.assertEqual(len(clicked_messages), 1)

    def test_steam_auto_login_clickable_button_does_not_exhaust_manual_cap(self):
        statuses = []
        tracking = _new_steam_auto_login_state()

        class FakeFirstLocator:
            def __init__(self, selector):
                self.selector = selector

            async def click(self, timeout=None):
                if self.selector != "#btnSteam":
                    raise RuntimeError("not found")

        class FakeLocator:
            def __init__(self, selector):
                self.first = FakeFirstLocator(selector)

        class FakePage:
            url = "https://account.pearlabyss.com/en-us/Member/Login"

            def locator(self, selector):
                return FakeLocator(selector)

        async def status_callback(message, level):
            statuses.append((message, level))

        page = FakePage()

        async def run():
            results = []
            for now in range(12):
                results.append(
                    await _maybe_run_steam_auto_login(
                        page, "pa", enabled=True, tracking=tracking, status_callback=status_callback, now=now
                    )
                )
            return results

        results = asyncio.run(run())

        self.assertEqual(results, [STEAM_AUTO_LOGIN_CLICKED] * 12)
        manual_messages = [
            message
            for (message, level) in statuses
            if level == "warning" and "waiting for manual input" in message
        ]
        self.assertEqual(manual_messages, [])

    def test_steam_auto_login_skips_market_page_after_auth_flow_seen(self):
        self.assertTrue(_should_attempt_steam_auto_login("market", False, False))
        self.assertFalse(_should_attempt_steam_auto_login("market", True, False))
        self.assertFalse(_should_attempt_steam_auto_login("market", False, True))
        self.assertTrue(_should_attempt_steam_auto_login("pa", True, False))

    def test_market_cookie_capture_requires_fresh_session_after_auth_flow(self):
        baseline = {"old"}
        stale = [{"name": "TradeAuth_Session", "value": "old"}]
        fresh = [{"name": "TradeAuth_Session", "value": "new"}]
        token_only = [{"name": "__RequestVerificationToken", "value": "t"}]

        # No marketplace session cookie at all -> never ready.
        self.assertFalse(_market_cookie_capture_ready(token_only, baseline, False, True, True))
        self.assertFalse(_market_cookie_capture_ready([], baseline, True, True, True))

        # A fresh session cookie issued after an auth flow -> ready immediately (no market wait).
        self.assertTrue(_market_cookie_capture_ready(fresh, baseline, False, False, True))
        # Fresh cookie with only the OAuth callback observed -> ready.
        self.assertTrue(_market_cookie_capture_ready(fresh, baseline, True, False, False))

        # A stale pre-login cookie during an auth flow must NOT trigger capture (finding A).
        self.assertFalse(_market_cookie_capture_ready(stale, baseline, False, False, True))
        self.assertFalse(_market_cookie_capture_ready(stale, baseline, False, True, True))

        # Saved browser session still valid: reached market with no auth detour -> ready.
        self.assertTrue(_market_cookie_capture_ready(stale, baseline, False, True, False))
        # First-ever login where the profile had no prior session cookie -> fresh, ready.
        self.assertTrue(_market_cookie_capture_ready(fresh, set(), False, False, True))

    def test_browser_auth_launch_error_names_missing_google_chrome(self):
        message = _browser_launch_error_message(
            RuntimeError("Executable doesn't exist at C:/Program Files/Google/Chrome/Application/chrome.exe")
        )

        self.assertIn("Google Chrome is not available", message)

    def test_purchase_result_messages_name_known_codes(self):
        self.assertIn("identical order already exists", purchase_result_message(30, "item", "100"))
        self.assertIn("price mismatch", purchase_result_message(-14, "item", "100"))
        self.assertIn("not enough silver", purchase_result_message(-16, "item", "100"))
        self.assertIn("duplicate pre-order", purchase_result_message(34, "item", "100"))
        self.assertIn("resultCode 999", purchase_result_message(999, "item", "100"))

    def test_purchase_result_code_validation(self):
        handler = object.__new__(APIHandler)

        self.assertEqual(APIHandler._purchase_result_code(handler, {"resultCode": "-14"}), -14)
        with self.assertRaises(MarketplaceResponseError):
            APIHandler._purchase_result_code(handler, {})

    def test_missing_session_file_is_initialized_with_empty_cookie_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "data" / "session.json"
            handler = object.__new__(APIHandler)

            with patch("bdo_marketplace_tools.market.api_handler.SESSION_COOKIE_PATH", session_path):
                status = APIHandler.load_session(handler)

            self.assertEqual(status, -1)
            self.assertTrue(session_path.exists())
            self.assertEqual(json.loads(session_path.read_text(encoding="utf-8")), {"version": 1, "cookies": []})

    def test_market_ajax_headers_keep_browser_like_shape_without_client_hints(self):
        handler = object.__new__(APIHandler)

        headers = APIHandler._market_headers(
            handler,
            "https://na-trade.naeu.playblackdesert.com/Home/list/hot",
            ajax=True,
        )

        self.assertEqual(headers["Origin"], "https://na-trade.naeu.playblackdesert.com")
        self.assertEqual(headers["Referer"], "https://na-trade.naeu.playblackdesert.com/Home/list/hot")
        self.assertEqual(headers["X-Requested-With"], MARKET_AJAX_HEADER)
        self.assertIn("Chrome/", headers["User-Agent"])
        self.assertNotIn("Sec-Fetch-Site", headers)
        self.assertNotIn("Sec-CH-UA", headers)

    def test_marketplace_silver_balance_reads_silver_wallet_row(self):
        wallet = {
            "myWalletList": [
                {"mainKey": 2, "subKey": 0, "name": "Other", "count": 5},
                {"mainKey": 1, "subKey": 0, "name": "Silver", "count": "123456789"},
            ]
        }

        self.assertEqual(marketplace_silver_balance(wallet), 123_456_789)
        self.assertIsNone(marketplace_silver_balance({"myWalletList": []}))
        with self.assertRaises(MarketplaceResponseError):
            marketplace_silver_balance({"myWalletList": [{"mainKey": 1, "subKey": 0, "name": "Silver"}]})


class LocalRuntimeFileTests(unittest.TestCase):
    def test_missing_account_mode_settings_defaults_to_pa_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            settings_path = temp_path / "data" / "app_settings.json"

            with patch("bdo_marketplace_tools.storage.app_settings.APP_SETTINGS_PATH", settings_path):
                self.assertEqual(account_mode_module.load_account_mode(), PA_CREDENTIALS_MODE)
                self.assertFalse(account_mode_module.load_steam_browser_profile_prepared())
                self.assertFalse(account_mode_module.load_steam_pa_cookie_consent_prepared())
                self.assertFalse(account_mode_module.load_pa_browser_profile_prepared())
                self.assertEqual(account_mode_module.load_browser_cache_cleanup_threshold_mb(), 150)
                self.assertEqual(account_mode_module.save_account_mode(STEAM_BROWSER_MODE), STEAM_BROWSER_MODE)
                self.assertTrue(account_mode_module.save_steam_browser_profile_prepared(True))
                self.assertTrue(account_mode_module.save_steam_pa_cookie_consent_prepared(True))
                self.assertTrue(account_mode_module.save_pa_browser_profile_prepared(True))
                self.assertEqual(account_mode_module.save_browser_cache_cleanup_threshold_mb(256), 256)
                self.assertEqual(account_mode_module.load_account_mode(), STEAM_BROWSER_MODE)
                self.assertTrue(account_mode_module.load_steam_browser_profile_prepared())
                self.assertTrue(account_mode_module.load_steam_pa_cookie_consent_prepared())
                self.assertTrue(account_mode_module.load_pa_browser_profile_prepared())
                self.assertEqual(account_mode_module.load_browser_cache_cleanup_threshold_mb(), 256)
                self.assertEqual(account_mode_module.account_mode_label(STEAM_BROWSER_MODE), "Steam Account")
                self.assertEqual(account_mode_module.account_mode_label(PA_CREDENTIALS_MODE), "Pearl Abyss Account")
                self.assertEqual(account_mode_module.normalize_account_mode("Steam Account"), STEAM_BROWSER_MODE)
                self.assertEqual(
                    account_mode_module.normalize_account_mode("Pearl Abyss Account"),
                    PA_CREDENTIALS_MODE,
                )

            self.assertTrue(settings_path.exists())
            saved_settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_settings["version"], EXPECTED_APP_SETTINGS_VERSION)
            self.assertEqual(saved_settings["account"]["mode"], STEAM_BROWSER_MODE)
            self.assertIsNone(saved_settings["account"]["email"])
            self.assertTrue(saved_settings["steam_browser"]["profile_prepared"])
            self.assertTrue(saved_settings["steam_browser"]["pa_cookie_consent_prepared"])
            self.assertTrue(saved_settings["pa_browser"]["profile_prepared"])
            self.assertFalse(saved_settings["session"]["saved_session_last_known_valid"])
            self.assertEqual(saved_settings["maintenance"]["browser_cache_cleanup_threshold_mb"], 256)
            self.assertEqual(saved_settings["ui"]["polling"]["selected"], "3")
            self.assertEqual(saved_settings["ui"]["polling"]["custom_range"], [15, 30])
            self.assertEqual(saved_settings["ui"]["buy_delay"]["range"], [1.0, 2.5])
            self.assertIsNone(saved_settings["ui"]["spend_cap"])
            self.assertFalse(saved_settings["ui"]["buy_mode"])
            self.assertNotIn("event_log_view", saved_settings["ui"])

    def test_app_settings_persist_ui_preferences(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            settings_path = temp_path / "data" / "app_settings.json"

            with patch("bdo_marketplace_tools.storage.app_settings.APP_SETTINGS_PATH", settings_path):
                account_mode_module.save_polling_settings("custom", (8, 13))
                account_mode_module.save_purchase_delay_bounds((4.5, 8))
                account_mode_module.save_spend_cap(123456789)
                account_mode_module.save_buy_mode(True)
                account_mode_module.save_saved_session_last_known_valid(True)
                account_mode_module.save_browser_cache_cleanup_threshold_mb(0)
                settings = account_mode_module.read_app_settings()

            self.assertEqual(settings["ui"]["polling"]["selected"], "custom")
            self.assertEqual(settings["ui"]["polling"]["custom_range"], [8, 13])
            self.assertEqual(settings["ui"]["buy_delay"]["range"], [4.5, 8.0])
            self.assertEqual(settings["ui"]["spend_cap"], 123456789)
            self.assertTrue(settings["ui"]["buy_mode"])
            self.assertTrue(settings["session"]["saved_session_last_known_valid"])
            self.assertEqual(settings["maintenance"]["browser_cache_cleanup_threshold_mb"], 0)
            with patch("bdo_marketplace_tools.storage.app_settings.APP_SETTINGS_PATH", settings_path):
                self.assertFalse(account_mode_module.save_saved_session_last_known_valid(False))
                self.assertFalse(account_mode_module.load_saved_session_last_known_valid())
                self.assertEqual(account_mode_module.save_browser_cache_cleanup_threshold_mb("512"), 512)
                with self.assertRaises(ValueError):
                    account_mode_module.save_browser_cache_cleanup_threshold_mb("-1")

    def test_background_tasks_load_and_save_persisted_ui_preferences(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            settings_path = temp_path / "data" / "app_settings.json"

            with patch("bdo_marketplace_tools.storage.app_settings.APP_SETTINGS_PATH", settings_path):
                account_mode_module.save_polling_settings("custom", (8, 13))
                account_mode_module.save_purchase_delay_bounds((4.5, 8))
                account_mode_module.save_spend_cap(250)
                account_mode_module.save_buy_mode(True)
                account_mode_module.save_browser_cache_cleanup_threshold_mb(512)

                with patch("bdo_marketplace_tools.services.task_manager._load_local_data", return_value=LOCAL_DATA.copy()):
                    manager = BackgroundTasks(FakeAPI())

                self.assertEqual(manager.delay, "custom")
                self.assertEqual(manager.current_delay_bounds(), (8, 13))
                self.assertEqual(manager.purchase_delay_bounds, (4.5, 8.0))
                self.assertEqual(manager.max_spend, 250)
                self.assertTrue(manager.purchase_submission_enabled)
                self.assertEqual(manager.browser_cache_cleanup_threshold_mb, 512)

                manager.set_delay_choice("2")
                manager.set_purchase_delay_range("1.25", "3.5")
                manager.set_spend_cap(0)
                manager.set_purchase_submission_enabled(False)
                manager.set_browser_cache_cleanup_threshold_mb(64)

                with patch("bdo_marketplace_tools.services.task_manager._load_local_data", return_value=LOCAL_DATA.copy()):
                    restored = BackgroundTasks(FakeAPI())

            self.assertEqual(restored.delay, "2")
            self.assertEqual(restored.current_delay_bounds(), (5, 10))
            self.assertEqual(restored.purchase_delay_bounds, (1.25, 3.5))
            self.assertIsNone(restored.max_spend)
            self.assertFalse(restored.purchase_submission_enabled)
            self.assertEqual(restored.browser_cache_cleanup_threshold_mb, 64)

    def test_old_resource_settings_are_ignored_for_fresh_start(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            settings_path = temp_path / "data" / "app_settings.json"
            old_settings_path = temp_path / "resources" / "app_settings.json"
            old_info_path = temp_path / "resources" / "info.json"
            old_settings_path.parent.mkdir(parents=True)
            old_settings_path.write_text(
                json.dumps(
                    {
                        "account_mode": STEAM_BROWSER_MODE,
                        "steam_browser_profile_prepared": True,
                    }
                ),
                encoding="utf-8",
            )
            old_info_path.write_text(
                json.dumps({"email": "user@example.com", "password": "old-secret"}),
                encoding="utf-8",
            )

            with patch("bdo_marketplace_tools.storage.app_settings.APP_SETTINGS_PATH", settings_path):
                settings = account_mode_module.read_app_settings()

            self.assertEqual(settings["account"]["mode"], PA_CREDENTIALS_MODE)
            self.assertIsNone(settings["account"]["email"])
            self.assertFalse(settings["steam_browser"]["profile_prepared"])
            self.assertFalse(settings["steam_browser"]["pa_cookie_consent_prepared"])
            self.assertFalse(settings["pa_browser"]["profile_prepared"])
            self.assertEqual(settings["maintenance"]["browser_cache_cleanup_threshold_mb"], 150)
            saved_settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_settings, settings)

    def test_app_settings_refresh_version_metadata_when_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            settings_path = temp_path / "data" / "app_settings.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "account": {"mode": STEAM_BROWSER_MODE, "email": "user@example.com"},
                        "steam_browser": {"profile_prepared": True},
                    }
                ),
                encoding="utf-8",
            )

            with patch("bdo_marketplace_tools.storage.app_settings.APP_SETTINGS_PATH", settings_path):
                settings = account_mode_module.read_app_settings()

            self.assertEqual(settings["version"], EXPECTED_APP_SETTINGS_VERSION)
            self.assertEqual(settings["account"]["mode"], STEAM_BROWSER_MODE)
            self.assertEqual(settings["account"]["email"], "user@example.com")
            self.assertTrue(settings["steam_browser"]["profile_prepared"])
            self.assertFalse(settings["steam_browser"]["pa_cookie_consent_prepared"])
            self.assertFalse(settings["pa_browser"]["profile_prepared"])
            self.assertEqual(settings["maintenance"]["browser_cache_cleanup_threshold_mb"], 150)
            saved_settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_settings["version"], EXPECTED_APP_SETTINGS_VERSION)

    def test_api_handler_initializes_account_mode_from_saved_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            settings_path = temp_path / "data" / "app_settings.json"
            session_path = temp_path / "data" / "session.json"

            with patch("bdo_marketplace_tools.storage.app_settings.APP_SETTINGS_PATH", settings_path):
                account_mode_module.save_account_mode(STEAM_BROWSER_MODE)
                with patch("bdo_marketplace_tools.market.api_handler.SESSION_COOKIE_PATH", session_path):
                    handler = APIHandler()

            self.assertEqual(handler.account_mode, STEAM_BROWSER_MODE)

    def test_missing_app_settings_credentials_are_initialized_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            settings_path = temp_path / "data" / "app_settings.json"

            with patch("bdo_marketplace_tools.storage.app_settings.APP_SETTINGS_PATH", settings_path):
                self.assertEqual(credentials_module.load_credentials(), (None, None))

            self.assertTrue(settings_path.exists())
            saved_settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertIsNone(saved_settings["account"]["email"])

    def test_credentials_use_project_keyring_namespace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            settings_path = temp_path / "data" / "app_settings.json"
            keyring_mock = Mock()
            keyring_mock.get_password.return_value = "saved-secret"

            with patch("bdo_marketplace_tools.storage.app_settings.APP_SETTINGS_PATH", settings_path), patch.object(
                credentials_module, "keyring", keyring_mock
            ):
                credentials_module.save_credentials("user@example.com", "new-secret")
                self.assertEqual(credentials_module.load_credentials(), ("user@example.com", "saved-secret"))

            keyring_mock.set_password.assert_called_once_with(
                "bdo-marketplace-tools", "user@example.com", "new-secret"
            )
            keyring_mock.get_password.assert_called_once_with("bdo-marketplace-tools", "user@example.com")
            saved_settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_settings["account"]["email"], "user@example.com")
            self.assertNotIn("new-secret", json.dumps(saved_settings))

    def test_missing_stats_db_is_initialized_with_default_totals(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "data" / "stats.db"
            legacy_path = temp_path / "data" / "local_stats.json"

            with patch("bdo_marketplace_tools.services.task_manager.STATS_DB_PATH", db_path), patch(
                "bdo_marketplace_tools.services.task_manager.LOCAL_DATA_PATH", legacy_path
            ):
                data = task_manager_module._load_local_data()

            self.assertEqual(data, LOCAL_DATA)
            self.assertTrue(db_path.exists())
            with closing(sqlite3.connect(db_path)) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, stats_db_module.SCHEMA_VERSION)
            # The retired JSON stats file is never created or written anymore.
            self.assertFalse(legacy_path.exists())

    def test_legacy_local_stats_json_seeds_lifetime_totals_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "data" / "stats.db"
            legacy_path = temp_path / "data" / "local_stats.json"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text(
                json.dumps({"successful_purchases": 7, "silver_spent": 12345}),
                encoding="utf-8",
            )

            seeded = stats_db_module.load_lifetime_stats(path=db_path, legacy_json_path=legacy_path)
            self.assertEqual(seeded, {"successful_purchases": 7, "silver_spent": 12345})

            legacy_path.write_text(
                json.dumps({"successful_purchases": 999, "silver_spent": 1}),
                encoding="utf-8",
            )
            reread = stats_db_module.load_lifetime_stats(path=db_path, legacy_json_path=legacy_path)
            self.assertEqual(reread, {"successful_purchases": 7, "silver_spent": 12345})

    def test_save_lifetime_stats_upserts_totals(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "data" / "stats.db"
            legacy_path = Path(temp_dir) / "data" / "local_stats.json"

            stats_db_module.save_lifetime_stats(3, 500, path=db_path)
            stats_db_module.save_lifetime_stats(5, 900, path=db_path)

            data = stats_db_module.load_lifetime_stats(path=db_path, legacy_json_path=legacy_path)
            self.assertEqual(data, {"successful_purchases": 5, "silver_spent": 900})

    def test_unversioned_stats_db_is_promoted_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "data" / "stats.db"
            legacy_path = Path(temp_dir) / "data" / "local_stats.json"
            db_path.parent.mkdir(parents=True)
            detected_at = int(datetime(2026, 7, 3, 9, 0).timestamp())
            with closing(sqlite3.connect(db_path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE lifetime_stats (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        successful_purchases INTEGER NOT NULL DEFAULT 0,
                        silver_spent INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT
                    );
                    CREATE TABLE outfit_events (
                        id INTEGER PRIMARY KEY,
                        event_type TEXT NOT NULL CHECK (event_type IN ('detection', 'purchase')),
                        occurred_at INTEGER NOT NULL,
                        item_id TEXT,
                        quantity INTEGER NOT NULL DEFAULT 1,
                        silver INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE daily_coverage (
                        day TEXT PRIMARY KEY,
                        scans INTEGER NOT NULL DEFAULT 0
                    );
                    INSERT INTO lifetime_stats (id, successful_purchases, silver_spent, updated_at)
                        VALUES (1, 4, 8000, 'prototype');
                    INSERT INTO daily_coverage (day, scans) VALUES ('2026-07-03', 3);
                    """
                )
                connection.execute(
                    "INSERT INTO outfit_events (event_type, occurred_at, item_id, quantity, silver)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (stats_db_module.DETECTION_EVENT, detected_at, "outfit-a", 2, 0),
                )
                connection.commit()
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)

            data = stats_db_module.load_lifetime_stats(path=db_path, legacy_json_path=legacy_path)
            trends = stats_db_module.load_trends(1, path=db_path, today=date(2026, 7, 3))

            self.assertEqual(data, {"successful_purchases": 4, "silver_spent": 8000})
            self.assertEqual(trends["daily"][-1]["detected"], 2)
            self.assertEqual(trends["daily"][-1]["scans"], 3)
            with closing(sqlite3.connect(db_path)) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, stats_db_module.SCHEMA_VERSION)

    def test_old_session_file_is_ignored_for_fresh_start(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            session_path = temp_path / "data" / "session.json"
            old_session_path = temp_path / "resources" / "session.json"
            old_session_path.parent.mkdir(parents=True)
            old_session_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "cookies": [
                            {
                                "name": "TradeAuth_Session",
                                "value": "abc123",
                                "domain": "na-trade.naeu.playblackdesert.com",
                                "path": "/",
                                "secure": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            handler = object.__new__(APIHandler)

            with patch("bdo_marketplace_tools.market.api_handler.SESSION_COOKIE_PATH", session_path):
                status = APIHandler.load_session(handler)

            self.assertEqual(status, -1)
            self.assertIsNone(handler.session.cookies.get("TradeAuth_Session"))
            self.assertTrue(session_path.exists())
            self.assertEqual(json.loads(session_path.read_text(encoding="utf-8")), {"version": 1, "cookies": []})

    def test_browser_profile_paths_use_data_directory(self):
        self.assertIn("data", STEAM_MARKET_PROFILE_PATH.parts)
        self.assertEqual(STEAM_MARKET_PROFILE_PATH.name, "steam-market")
        self.assertIn("data", PA_MARKET_PROFILE_PATH.parts)
        self.assertEqual(PA_MARKET_PROFILE_PATH.name, "pa-market")

    def test_browser_profile_storage_measures_total_and_disposable_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / "steam-market"
            (profile / "BrowserMetrics").mkdir(parents=True)
            (profile / "BrowserMetrics" / "metrics.pma").write_bytes(b"a" * 4)
            (profile / "Default" / "Code Cache").mkdir(parents=True)
            (profile / "Default" / "Code Cache" / "script").write_bytes(b"b" * 6)
            (profile / "Default" / "Network").mkdir(parents=True)
            (profile / "Default" / "Network" / "Cookies").write_bytes(b"c" * 8)

            summary = measure_browser_profile_storage(profile)

        self.assertEqual(summary.total_bytes, 18)
        self.assertEqual(summary.disposable_bytes, 10)

    def test_browser_profile_cache_cleanup_only_removes_allowlisted_disposable_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / "pa-market"
            disposable_files = [
                profile / "BrowserMetrics" / "metrics.pma",
                profile / "Default" / "Cache" / "Cache_Data" / "data_0",
                profile / "Default" / "Code Cache" / "js" / "compiled",
                profile / "Default" / "GPUCache" / "gpu",
                profile / "GrShaderCache" / "shader",
                profile / "component_crx_cache" / "component",
            ]
            preserved_files = [
                profile / "Default" / "Network" / "Cookies",
                profile / "Default" / "Local Storage" / "leveldb" / "state",
                profile / "Default" / "Session Storage" / "session",
                profile / "Default" / "IndexedDB" / "db",
                profile / "Default" / "History",
                profile / "Default" / "Preferences",
            ]
            for index, path in enumerate(disposable_files + preserved_files, start=1):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(bytes([index]) * index)

            result = clean_disposable_browser_profile_cache(profile, threshold_bytes=1)

            self.assertTrue(result.removed_anything)
            self.assertFalse(result.had_failures)
            for path in disposable_files:
                self.assertFalse(path.exists(), path)
            for path in preserved_files:
                self.assertTrue(path.exists(), path)
            self.assertGreater(result.removed_bytes, 0)

    def test_browser_profile_cache_cleanup_missing_paths_are_noop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / "empty-profile"

            result = clean_disposable_browser_profile_cache(profile, threshold_bytes=1)

        self.assertTrue(result.skipped)
        self.assertFalse(result.removed_anything)
        self.assertFalse(result.had_failures)

    def test_browser_profile_cache_cleanup_skip_avoids_full_profile_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / "small-profile"

            with patch(
                "bdo_marketplace_tools.storage.browser_profile_cache._disposable_path_size",
                return_value=10,
            ) as disposable_size, patch(
                "bdo_marketplace_tools.storage.browser_profile_cache._path_size",
                side_effect=AssertionError("full profile scan should be skipped"),
            ) as path_size:
                result = clean_disposable_browser_profile_cache(profile, threshold_bytes=20)

        disposable_size.assert_called_once_with(profile)
        path_size.assert_not_called()
        self.assertTrue(result.skipped)
        self.assertEqual(result.total_bytes_before, 0)
        self.assertEqual(result.disposable_bytes_before, 10)

    def test_browser_profile_cache_cleanup_delete_failure_is_non_fatal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / "steam-market"
            cache_file = profile / "Default" / "Cache" / "Cache_Data" / "data_0"
            cache_file.parent.mkdir(parents=True)
            cache_file.write_bytes(b"cache")

            with patch("bdo_marketplace_tools.storage.browser_profile_cache.shutil.rmtree", side_effect=OSError("locked")):
                result = clean_disposable_browser_profile_cache(profile, threshold_bytes=1)

            self.assertFalse(result.removed_anything)
            self.assertTrue(result.had_failures)
            self.assertIn("Default/Cache", result.failed_paths)
            self.assertTrue(cache_file.exists())

    def test_browser_profile_cache_cleanup_path_safety_blocks_outside_targets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = root / "steam-market"
            outside = root / "outside-cache"
            outside.mkdir()
            (outside / "do-not-delete").write_bytes(b"keep")

            with patch.object(
                browser_profile_cache_module,
                "DISPOSABLE_BROWSER_PROFILE_PATHS",
                ("../outside-cache",),
            ):
                result = clean_disposable_browser_profile_cache(profile, threshold_bytes=0)

            self.assertTrue(result.had_failures)
            self.assertTrue((outside / "do-not-delete").exists())

    def test_format_storage_size_uses_binary_units(self):
        self.assertEqual(format_storage_size(0), "0 B")
        self.assertEqual(format_storage_size(1024), "1.0 KiB")
        self.assertEqual(format_storage_size(2 * 1024 * 1024), "2.0 MiB")


class StatsHistoryTests(unittest.TestCase):
    def test_load_trends_buckets_daily_weekday_and_hourly_activity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "stats.db"
            today = date(2026, 7, 3)  # a Friday
            thursday_noon = datetime(2026, 7, 2, 12, 30)
            friday_evening = datetime(2026, 7, 3, 20, 5)

            stats_db_module.record_detection_events(
                [["outfit-a", "2", "100"], ["outfit-b", "1", "100"]],
                at=thursday_noon.timestamp(),
                path=db_path,
            )
            stats_db_module.record_detection_events(
                [["outfit-a", "1", "100"]],
                at=friday_evening.timestamp(),
                path=db_path,
            )
            stats_db_module.record_purchase_events(
                [{"item_id": "outfit-a", "price": "2170000000", "count": 1}],
                at=friday_evening.timestamp(),
                path=db_path,
            )
            stats_db_module.add_daily_coverage({"2026-07-03": 40}, path=db_path)
            stats_db_module.add_daily_coverage({"2026-07-03": 2, "2026-07-02": 5}, path=db_path)

            trends = stats_db_module.load_trends(7, path=db_path, today=today)

            self.assertEqual(len(trends["daily"]), 7)
            daily = {row["day"].isoformat(): row for row in trends["daily"]}
            self.assertEqual(daily["2026-07-02"]["detected"], 3)
            self.assertEqual(daily["2026-07-02"]["purchased"], 0)
            self.assertEqual(daily["2026-07-02"]["scans"], 5)
            self.assertEqual(daily["2026-07-03"]["detected"], 1)
            self.assertEqual(daily["2026-07-03"]["purchased"], 1)
            self.assertEqual(daily["2026-07-03"]["scans"], 42)

            self.assertEqual(trends["weekday"]["detected"][3], 3)  # Thursday
            self.assertEqual(trends["weekday"]["detected"][4], 1)  # Friday
            self.assertEqual(trends["weekday"]["purchased"][4], 1)
            self.assertEqual(trends["hourly"][3][12], 3)
            self.assertEqual(trends["hourly"][4][20], 1)

    def test_load_trends_ignores_events_outside_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "stats.db"
            today = date(2026, 7, 3)
            stale_moment = datetime(2026, 5, 1, 10, 0)

            stats_db_module.record_detection_events(
                [["outfit-old", "4", "100"]],
                at=stale_moment.timestamp(),
                path=db_path,
            )
            stats_db_module.add_daily_coverage({"2026-05-01": 99}, path=db_path)

            trends = stats_db_module.load_trends(30, path=db_path, today=today)

            self.assertEqual(len(trends["daily"]), 30)
            self.assertTrue(all(row["detected"] == 0 for row in trends["daily"]))
            self.assertTrue(all(row["scans"] == 0 for row in trends["daily"]))
            self.assertEqual(sum(trends["weekday"]["detected"]), 0)
            self.assertEqual(sum(value for row in trends["hourly"] for value in row), 0)

    def test_detection_rows_with_zero_stock_are_not_recorded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "stats.db"
            today = date(2026, 7, 3)

            stats_db_module.record_detection_events(
                [["outfit-a", "0", "100"]],
                at=datetime(2026, 7, 3, 9, 0).timestamp(),
                path=db_path,
            )

            trends = stats_db_module.load_trends(7, path=db_path, today=today)
            self.assertTrue(all(row["detected"] == 0 for row in trends["daily"]))


class TrendChartTests(unittest.TestCase):
    def test_daily_activity_chart_renders_scale_coverage_and_legend(self):
        today = date(2026, 7, 3)  # a Friday
        daily = [
            {"day": today - timedelta(days=offset), "detected": 0, "purchased": 0, "scans": 0}
            for offset in range(6, -1, -1)
        ]
        daily[-1].update(detected=4, purchased=2, scans=30)
        daily[-2].update(detected=1, scans=12)

        rendered = charts_module.daily_activity_chart(daily).plain

        self.assertIn("4 ┤", rendered)
        self.assertIn("3 ┤", rendered)
        self.assertIn("2 ┤", rendered)
        self.assertIn("1 ┤", rendered)
        self.assertIn("▔", rendered)
        self.assertIn("Fri", rendered)  # 7-day view labels columns by weekday
        self.assertIn("detected", rendered)
        self.assertIn("purchased", rendered)
        self.assertIn("monitored", rendered)
        legend_rows = [
            index
            for index, line in enumerate(rendered.splitlines())
            if any(label in line for label in ("detected", "purchased", "monitored", "offline"))
        ]
        self.assertEqual(legend_rows, [0, 2, 4, 6])

    def test_daily_activity_chart_uses_steady_rounded_axis_ticks(self):
        today = date(2026, 7, 3)
        daily = [
            {"day": today - timedelta(days=offset), "detected": 0, "purchased": 0, "scans": 0}
            for offset in range(29, -1, -1)
        ]
        daily[-2].update(detected=18, purchased=2, scans=8)
        daily[-1].update(detected=23, purchased=5, scans=8)

        rendered = charts_module.daily_activity_chart(daily).plain

        self.assertIn("25 ┤", rendered)
        self.assertIn("20 ┤", rendered)
        self.assertIn("15 ┤", rendered)
        self.assertIn("10 ┤", rendered)
        self.assertIn(" 5 ┤", rendered)
        self.assertNotIn("23 ┤", rendered)

    def test_daily_activity_chart_keeps_small_purchase_segment_proportional_with_spike(self):
        today = date(2026, 7, 3)
        daily = [
            {"day": today - timedelta(days=offset), "detected": 0, "purchased": 0, "scans": 0}
            for offset in range(29, -1, -1)
        ]
        daily[-2].update(detected=18, purchased=2, scans=8)
        daily[-1].update(detected=100, purchased=0, scans=8)

        rendered = charts_module.daily_activity_chart(daily)

        mixed_spans = [
            span for span in rendered.spans
            if " on " in str(span.style) and rendered.plain[span.start:span.end].strip()
        ]
        self.assertTrue(mixed_spans)
        self.assertTrue(rendered.plain.splitlines()[charts_module.CHART_HEIGHT].lstrip().startswith("┼"))

    def test_daily_activity_chart_notes_empty_window(self):
        today = date(2026, 7, 3)
        daily = [{"day": today, "detected": 0, "purchased": 0, "scans": 0}]

        rendered = charts_module.daily_activity_chart(daily).plain

        self.assertIn("No outfits in this window yet", rendered)

    def test_weekday_chart_scales_bars_and_shows_counts(self):
        rendered = charts_module.weekday_chart(
            {"detected": [10, 0, 5, 0, 0, 0, 0], "purchased": [0] * 7}
        ).plain

        lines = rendered.splitlines()
        self.assertEqual(len(lines), 7)
        self.assertEqual(lines[0], "Mon  " + "█" * 20 + "  10")
        self.assertEqual(lines[2], "Wed  " + "█" * 10 + "░" * 10 + "  5")
        self.assertEqual(lines[1], "Tue  " + "░" * 20 + "  0")

    def test_daily_chart_tooltip_geometry_matches_axis_scale_width(self):
        # 9 detections render against a rounded axis scale of 10, so the y-axis
        # labels are two characters wide; hover mapping must use the same width.
        today = date(2026, 7, 3)
        daily = [
            {"day": today - timedelta(days=offset), "detected": 0, "purchased": 0, "scans": 0}
            for offset in range(29, -1, -1)
        ]
        daily[-1]["detected"] = 9

        chart = DailyActivityChart()
        chart._daily = list(daily)
        chart._set_geometry(charts_module.CHART_HEIGHT)

        rendered = charts_module.daily_activity_chart(daily).plain
        top_line = rendered.splitlines()[0]
        self.assertTrue(top_line.startswith("10 ┤"))  # bars begin right after "10 ┤ "
        first_bar_column = top_line.index("┤") + 2

        self.assertEqual(chart._column_start, len("10") + 3)
        self.assertEqual(first_bar_column, chart._column_start)
        self.assertEqual(chart._day_index_for_x(chart._column_start), 0)
        self.assertIsNone(chart._day_index_for_x(chart._column_start - 1))
        self.assertEqual(chart._day_index_for_x(chart._column_start + chart._column_width), 1)
        # The gap column between bars maps to no day.
        self.assertIsNone(chart._day_index_for_x(chart._column_start + chart._bar_width))

    def test_daily_chart_renders_unpurchased_detections_in_dark_gray(self):
        # The detected-only remainder stays neutral and quiet so purchased
        # remains the clear primary signal.
        self.assertEqual(charts_module.STYLE_DETECTED, "#3f3f3f")

        today = date(2026, 7, 3)
        daily = [
            {"day": today - timedelta(days=offset), "detected": 0, "purchased": 0, "scans": 0}
            for offset in range(29, -1, -1)
        ]
        daily[-1].update(detected=8, purchased=3, scans=8)

        rendered = charts_module.daily_activity_chart(daily)

        self.assertNotIn("▒", rendered.plain)  # solid two-tone, no texture
        detected_solid = [
            span for span in rendered.spans
            if str(span.style) == charts_module.STYLE_DETECTED
            and "█" in rendered.plain[span.start:span.end]
        ]
        purchased_solid = [
            span for span in rendered.spans
            if str(span.style) == charts_module.STYLE_PURCHASED
            and "█" in rendered.plain[span.start:span.end]
        ]
        self.assertTrue(detected_solid)
        self.assertTrue(purchased_solid)

    def test_hour_heatmap_chart_shades_active_hours(self):
        hourly = [[0] * 24 for _ in range(7)]
        hourly[4][20] = 8
        hourly[4][21] = 1

        rendered_chart = charts_module.hour_heatmap_chart(hourly)
        rendered = rendered_chart.plain

        lines = rendered.splitlines()
        self.assertIn("20", lines[0])
        friday_row = lines[5]  # header row + Mon..Thu rows
        self.assertTrue(friday_row.startswith("Fri"))
        self.assertIn("██", friday_row)
        self.assertIn("░░", friday_row)
        self.assertIn("less", rendered)
        self.assertIn("more", rendered)
        styles = [str(span.style) for span in rendered_chart.spans]
        self.assertIn(charts_module._HEAT_STYLES[0], styles)
        self.assertIn(charts_module._HEAT_STYLES[-1], styles)


class APIBuyFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_item_stock_check_uses_public_trademarket_sublist_endpoint(self):
        handler = object.__new__(APIHandler)
        captured = {}

        class StockResponse:
            status_code = 200
            url = "https://na-trade.naeu.playblackdesert.com/Trademarket/GetWorldMarketSubList"
            headers = {"Content-Type": "application/json; charset=utf-8"}

            def json(self):
                return {
                    "resultCode": 0,
                    "resultMsg": "10007-0-7-77000-6-479508-23100-82500-77000-1780084000|",
                }

        async def fake_request(client, method, url, context, **kwargs):
            captured.update({"client": client, "method": method, "url": url, "context": context, **kwargs})
            return StockResponse()

        handler._request = fake_request
        handler._json_response = APIHandler._json_response.__get__(handler, APIHandler)

        result = await check_single_item_stock(handler)

        self.assertEqual(result, [["10007", "6", "92500"]])
        self.assertIs(captured["client"], requests)
        self.assertEqual(captured["method"], "POST")
        self.assertTrue(captured["url"].endswith("/Trademarket/GetWorldMarketSubList"))
        self.assertEqual(captured["json"], {"keyType": 0, "mainKey": 10007})
        self.assertEqual(
            captured["headers"],
            {
                "Content-Type": "application/json",
                "User-Agent": "BlackDesert",
            },
        )
        self.assertNotIn("data", captured)

    async def test_single_item_stock_check_does_not_require_session_or_login_state(self):
        handler = object.__new__(APIHandler)

        class FakeResponse:
            status_code = 200
            url = "https://na-trade.naeu.playblackdesert.com/Trademarket/GetWorldMarketSubList"
            headers = {"Content-Type": "application/json; charset=utf-8"}

            def json(self):
                return {
                    "resultCode": 0,
                    "resultMsg": "10007-0-7-77000-1-479508-23100-82500-77000-1780084000|",
                }

        async def fake_request(*args, **kwargs):
            return FakeResponse()

        handler._request = fake_request
        handler._json_response = APIHandler._json_response.__get__(handler, APIHandler)

        result = await check_single_item_stock(handler)

        self.assertEqual(result, [["10007", "1", "92500"]])

    async def test_single_item_stock_check_rejects_public_endpoint_error_code(self):
        handler = object.__new__(APIHandler)

        class ErrorResponse:
            status_code = 200
            url = "https://na-trade.naeu.playblackdesert.com/Trademarket/GetWorldMarketSubList"
            headers = {"Content-Type": "application/json; charset=utf-8"}

            def json(self):
                return {"resultCode": -1, "resultMsg": "/Error"}

        async def fake_request(*args, **kwargs):
            return ErrorResponse()

        handler._request = fake_request
        handler._json_response = APIHandler._json_response.__get__(handler, APIHandler)

        with self.assertRaises(MarketplaceResponseError):
            await check_single_item_stock(handler)

    async def test_single_item_stock_response_returns_empty_when_target_row_has_no_stock(self):
        response_json = {
            "resultCode": 0,
            "resultMsg": "10007-0-7-77000-0-479508-23100-82500-77000-1780084000|",
        }

        result = parse_single_item_stock_response(
            response_json,
            SINGLE_ITEM_TEST_TARGET,
            "single-item test",
        )

        self.assertEqual(result, [])

    async def test_single_item_stock_response_matches_target_enhancement_range(self):
        response_json = {
            "resultCode": 0,
            "resultMsg": (
                "10007-8-10-1630000-3-754-500000-5000000-1720000-1777516443|"
                "10007-0-7-77000-2-479508-23100-82500-77000-1780084000|"
            ),
        }

        result = parse_single_item_stock_response(
            response_json,
            SINGLE_ITEM_TEST_TARGET,
            "single-item test",
        )

        self.assertEqual(result, [["10007", "2", "92500"]])

    async def test_single_item_stock_response_uses_configured_test_buy_price_not_public_prices(self):
        target = {
            **SINGLE_ITEM_TEST_TARGET,
            "max_buy_price": "92500",
        }
        response_json = {
            "resultCode": 0,
            "resultMsg": "10007-0-7-77000-1-479508-23100-99999-77000-1780084000|",
        }

        result = parse_single_item_stock_response(
            response_json,
            target,
            "single-item test",
        )

        self.assertEqual(result, [["10007", "1", "92500"]])

    async def test_single_item_stock_response_rejects_api_error_codes(self):
        with self.assertRaises(MarketplaceResponseError):
            parse_single_item_stock_response(
                {"resultCode": 2000, "resultMsg": "expired"},
                SINGLE_ITEM_TEST_TARGET,
                "single-item test",
            )

    async def test_buy_item_validates_session_before_purchase_requests(self):
        handler = object.__new__(APIHandler)
        calls = []

        async def invalid_session():
            calls.append("ensure_session_valid")
            return False

        handler.ensure_session_valid = invalid_session

        summary = await APIHandler.buy_item(handler, [["item", "1", "100"]])

        self.assertEqual(calls, ["ensure_session_valid"])
        self.assertEqual(summary["attempted"], 0)
        self.assertEqual(summary["purchased"], 0)
        self.assertTrue(summary["auth_failed"])
        self.assertEqual(summary["events"][0]["level"], "error")

    async def test_pa_direct_password_login_is_disabled_before_network_request(self):
        handler = object.__new__(APIHandler)
        handler.login_status = False
        handler._request = AsyncMock()

        with self.assertRaisesRegex(MarketplaceResponseError, "browser session refresh"):
            await APIHandler.login(handler)

        handler._request.assert_not_called()
        self.assertFalse(handler.login_status)

    async def test_check_stock_uses_persistent_public_category_clients(self):
        handler = object.__new__(APIHandler)
        male_client = object()
        female_client = object()
        handler.public_market_sessions = {
            "male": male_client,
            "female": female_client,
        }
        captured_clients = []

        class FakeResponse:
            content = b"compressed"

        async def fake_request(client, *args, **kwargs):
            captured_clients.append(client)
            return FakeResponse()

        handler._request = fake_request
        handler._parse_world_market_response = lambda _content, context: [[context, "1", "100"]]

        result = await APIHandler.check_stock(handler)

        self.assertEqual(captured_clients, [male_client, female_client])
        self.assertEqual(
            result,
            [["male outfit stock", "1", "100"], ["female outfit stock", "1", "100"]],
        )

    async def test_buy_item_returns_structured_purchase_records(self):
        handler = object.__new__(APIHandler)
        session_requests = []

        async def valid_session():
            return True

        class FakeResponse:
            def json(self):
                return {"resultCode": 0}

        async def fake_session_request(*args, **kwargs):
            session_requests.append((args, kwargs))
            return FakeResponse()

        handler.ensure_session_valid = valid_session
        handler._session_request = fake_session_request
        handler._json_response = APIHandler._json_response.__get__(handler, APIHandler)
        handler._purchase_result_code = APIHandler._purchase_result_code.__get__(handler, APIHandler)

        sleep_mock = AsyncMock()
        with patch("bdo_marketplace_tools.market.api_handler.asyncio.sleep", new=sleep_mock):
            summary = await APIHandler.buy_item(handler, [["item", "2", "100"]])

        self.assertEqual(summary["attempted"], 2)
        self.assertEqual(summary["purchased"], 2)
        self.assertEqual(
            summary["purchase_records"],
            [
                {"item_id": "item", "price": 100, "submitted_price": 100, "count": 1, "result_code": 0},
                {"item_id": "item", "price": 100, "submitted_price": 100, "count": 1, "result_code": 0},
            ],
        )
        headers = session_requests[0][1]["headers"]
        self.assertEqual(headers["Origin"], "https://na-trade.naeu.playblackdesert.com")
        self.assertEqual(headers["Referer"], "https://na-trade.naeu.playblackdesert.com/")
        self.assertNotIn("X-Requested-With", headers)
        payload = session_requests[0][1]["data"]
        self.assertEqual(payload["buyMainKey"], "item")
        self.assertEqual(payload["buySubKey"], "0")
        self.assertEqual(payload["buyKeyType"], "0")
        self.assertEqual(payload["buyChooseKey"], "0")
        self.assertEqual(payload["buyPrice"], "100")
        self.assertEqual(payload["buyCount"], "1")
        self.assertEqual(sleep_mock.await_count, 1)

    async def test_buy_item_uses_configured_delay_between_purchase_attempts(self):
        handler = object.__new__(APIHandler)

        async def valid_session():
            return True

        class FakeResponse:
            def json(self):
                return {"resultCode": 0}

        async def fake_session_request(*args, **kwargs):
            return FakeResponse()

        handler.ensure_session_valid = valid_session
        handler._session_request = fake_session_request
        handler._json_response = APIHandler._json_response.__get__(handler, APIHandler)
        handler._purchase_result_code = APIHandler._purchase_result_code.__get__(handler, APIHandler)

        with patch("bdo_marketplace_tools.market.api_handler.random.uniform", return_value=4.5) as uniform_mock:
            with patch("bdo_marketplace_tools.market.api_handler.asyncio.sleep", new=AsyncMock()) as sleep_mock:
                summary = await APIHandler.buy_item(
                    handler,
                    [["item", "3", "100"]],
                    purchase_delay_bounds=(4, 7),
                )

        self.assertEqual(summary["attempted"], 3)
        self.assertEqual(summary["purchase_delay_bounds"], (4.0, 7.0))
        self.assertEqual(uniform_mock.call_count, 2)
        uniform_mock.assert_called_with(4.0, 7.0)
        self.assertEqual(sleep_mock.await_count, 2)
        sleep_mock.assert_awaited_with(4.5)

    async def test_buy_item_retries_same_item_once_after_session_expiry_result(self):
        handler = object.__new__(APIHandler)
        handler.login_status = True
        handler.session = type("Session", (), {"cookies": {"session": "present"}})()
        ensure_calls = 0
        session_payloads = []
        response_payloads = [
            {"resultCode": 2000},
            {"resultCode": 0, "resultMsg": "item-0-1-100-1-100-0-0-0-False"},
        ]

        async def valid_session():
            nonlocal ensure_calls
            ensure_calls += 1
            handler.login_status = True
            return True

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        async def fake_session_request(*args, **kwargs):
            session_payloads.append(kwargs["data"])
            return FakeResponse(response_payloads.pop(0))

        handler.ensure_session_valid = valid_session
        handler._session_request = fake_session_request
        handler._json_response = APIHandler._json_response.__get__(handler, APIHandler)
        handler._purchase_result_code = APIHandler._purchase_result_code.__get__(handler, APIHandler)
        handler._purchase_result_details = APIHandler._purchase_result_details.__get__(handler, APIHandler)
        handler._optional_positive_int = APIHandler._optional_positive_int.__get__(handler, APIHandler)

        summary = await APIHandler.buy_item(
            handler,
            [["item", "1", "100"]],
            purchase_delay_bounds=(0, 0),
        )

        self.assertEqual(ensure_calls, 1)
        self.assertEqual([payload["buyMainKey"] for payload in session_payloads], ["item", "item"])
        self.assertEqual(summary["attempted"], 2)
        self.assertEqual([record["result_code"] for record in summary["results"]], [2000, 0])
        self.assertEqual(summary["purchased"], 1)
        self.assertFalse(summary["auth_failed"])
        self.assertEqual(summary["purchase_records"][0]["item_id"], "item")

    async def test_buy_item_marks_auth_failed_when_session_stays_expired_after_retry(self):
        handler = object.__new__(APIHandler)
        handler.login_status = True
        handler.session = type("Session", (), {"cookies": {"session": "present"}})()
        response_payloads = [{"resultCode": 2000}, {"resultCode": 2000}]

        async def valid_session():
            handler.login_status = True
            return True

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        async def fake_session_request(*_args, **_kwargs):
            return FakeResponse(response_payloads.pop(0))

        handler.ensure_session_valid = valid_session
        handler._session_request = fake_session_request
        handler._json_response = APIHandler._json_response.__get__(handler, APIHandler)
        handler._purchase_result_code = APIHandler._purchase_result_code.__get__(handler, APIHandler)
        handler._purchase_result_details = APIHandler._purchase_result_details.__get__(handler, APIHandler)
        handler._optional_positive_int = APIHandler._optional_positive_int.__get__(handler, APIHandler)

        summary = await APIHandler.buy_item(
            handler,
            [["item", "1", "100"]],
            purchase_delay_bounds=(0, 0),
        )

        self.assertTrue(summary["auth_failed"])
        self.assertEqual(summary["attempted"], 2)
        self.assertTrue(any("session still expired" in event["message"] for event in summary["events"]))

    async def test_buy_item_rejects_invalid_purchase_delay_bounds(self):
        handler = object.__new__(APIHandler)

        with self.assertRaises(MarketplaceResponseError):
            await APIHandler.buy_item(handler, [["item", "1", "100"]], purchase_delay_bounds=(5, 1))

    async def test_buy_item_uses_actual_purchase_price_from_result_message(self):
        handler = object.__new__(APIHandler)

        async def valid_session():
            return True

        class FakeResponse:
            def json(self):
                return {"resultCode": 0, "resultMsg": "10007-0-1-82500-1-79000-0-0-0-False"}

        async def fake_session_request(*args, **kwargs):
            return FakeResponse()

        handler.ensure_session_valid = valid_session
        handler._session_request = fake_session_request
        handler._json_response = APIHandler._json_response.__get__(handler, APIHandler)
        handler._purchase_result_code = APIHandler._purchase_result_code.__get__(handler, APIHandler)
        handler._purchase_result_details = APIHandler._purchase_result_details.__get__(handler, APIHandler)
        handler._optional_positive_int = APIHandler._optional_positive_int.__get__(handler, APIHandler)

        with patch("bdo_marketplace_tools.market.api_handler.asyncio.sleep", new=AsyncMock()):
            summary = await APIHandler.buy_item(handler, [["10007", "1", "82500"]])

        self.assertEqual(summary["purchased"], 1)
        self.assertEqual(
            summary["purchase_records"],
            [{"item_id": "10007", "price": 79000, "submitted_price": 82500, "count": 1, "result_code": 0}],
        )
        self.assertIn("79,000", summary["events"][0]["message"])
        self.assertIn("silver", summary["events"][0]["message"])
        self.assertIn("bid up to 82,500", summary["events"][0]["message"])

    async def test_buy_item_notifies_observer_per_item_and_isolates_observer_failures(self):
        def make_handler():
            handler = object.__new__(APIHandler)

            async def valid_session():
                return True

            class FakeResponse:
                def json(self):
                    return {"resultCode": 0, "resultMsg": "10007-0-1-82500-1-79000-0-0-0-False"}

            async def fake_session_request(*args, **kwargs):
                return FakeResponse()

            handler.ensure_session_valid = valid_session
            handler._session_request = fake_session_request
            handler._json_response = APIHandler._json_response.__get__(handler, APIHandler)
            handler._purchase_result_code = APIHandler._purchase_result_code.__get__(handler, APIHandler)
            handler._purchase_result_details = APIHandler._purchase_result_details.__get__(handler, APIHandler)
            handler._optional_positive_int = APIHandler._optional_positive_int.__get__(handler, APIHandler)
            return handler

        # The observer fires once per secured item (stock 2 -> two attempts -> two calls).
        seen = []
        with patch("bdo_marketplace_tools.market.api_handler.asyncio.sleep", new=AsyncMock()):
            summary = await APIHandler.buy_item(
                make_handler(), [["10007", "2", "82500"]], on_purchase=seen.append
            )
        self.assertEqual(summary["purchased"], 2)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0]["price"], 79000)
        self.assertEqual(seen[0]["count"], 1)

        # A broken observer never affects the purchase flow: same summary, no exception.
        def broken(_record):
            raise RuntimeError("observer boom")

        with patch("bdo_marketplace_tools.market.api_handler.asyncio.sleep", new=AsyncMock()):
            summary = await APIHandler.buy_item(
                make_handler(), [["10007", "2", "82500"]], on_purchase=broken
            )
        self.assertEqual(summary["purchased"], 2)
        self.assertEqual(len(summary["purchase_records"]), 2)

    async def test_buy_item_result_code_zero_preorder_does_not_count_as_purchase(self):
        handler = object.__new__(APIHandler)

        async def valid_session():
            return True

        class FakeResponse:
            def json(self):
                return {"resultCode": 0, "resultMsg": "10007-0-1-79000-0-79000-79000-92404197-0-False"}

        async def fake_session_request(*args, **kwargs):
            return FakeResponse()

        handler.ensure_session_valid = valid_session
        handler._session_request = fake_session_request
        handler._json_response = APIHandler._json_response.__get__(handler, APIHandler)
        handler._purchase_result_code = APIHandler._purchase_result_code.__get__(handler, APIHandler)
        handler._purchase_result_details = APIHandler._purchase_result_details.__get__(handler, APIHandler)
        handler._optional_positive_int = APIHandler._optional_positive_int.__get__(handler, APIHandler)

        summary = await APIHandler.buy_item(handler, [["10007", "1", "79000"]])

        self.assertEqual(summary["purchased"], 0)
        self.assertEqual(summary["purchase_records"], [])
        self.assertEqual(summary["results"][0]["outcome"], "preorder")
        self.assertEqual(summary["results"][0]["reservation_id"], 92404197)
        self.assertIn("pre-order", summary["events"][0]["message"])

    async def test_session_refresh_uses_browser_observed_ajax_body(self):
        handler = object.__new__(APIHandler)
        handler.session = type("Session", (), {"cookies": {"session": "present"}})()
        captured = {}

        class FakeResponse:
            def json(self):
                return {"_resultCode": 0}

        async def fake_session_request(method, url, context, **kwargs):
            captured.update({"method": method, "url": url, "context": context, **kwargs})
            return FakeResponse()

        handler._session_request = fake_session_request
        handler._json_response = APIHandler._json_response.__get__(handler, APIHandler)

        self.assertEqual(await APIHandler.is_session_expired(handler), 0)
        self.assertEqual(captured["data"], {"_isCalc": "false"})
        self.assertEqual(captured["headers"]["X-Requested-With"], MARKET_AJAX_HEADER)

    async def test_session_refresh_accepts_public_result_code_shape(self):
        handler = object.__new__(APIHandler)
        handler.session = type("Session", (), {"cookies": {"session": "present"}})()

        class FakeResponse:
            def json(self):
                return {"resultCode": 0, "resultMsg": ""}

        async def fake_session_request(*args, **kwargs):
            return FakeResponse()

        handler._session_request = fake_session_request
        handler._json_response = APIHandler._json_response.__get__(handler, APIHandler)

        self.assertEqual(await APIHandler.is_session_expired(handler), 0)

    async def test_browser_cookie_import_filters_market_domains_and_saves_after_validation(self):
        handler = object.__new__(APIHandler)
        handler.session = requests.Session()
        handler.login_status = False
        handler.is_session_expired = AsyncMock(return_value=0)
        handler.save_session = Mock()
        cookies = [
            {
                "name": "TradeAuth_Session",
                "value": "market-session",
                "domain": "na-trade.naeu.playblackdesert.com",
                "path": "/",
                "secure": True,
            },
            {
                "name": "steamLoginSecure",
                "value": "do-not-import",
                "domain": "steamcommunity.com",
                "path": "/",
                "secure": True,
            },
        ]

        self.assertTrue(await APIHandler.validate_and_save_imported_session(handler, cookies))

        saved_cookies = handler.session.cookies.get_dict(domain="na-trade.naeu.playblackdesert.com")
        self.assertEqual(saved_cookies, {"TradeAuth_Session": "market-session"})
        self.assertTrue(handler.login_status)
        handler.save_session.assert_called_once()

    async def test_failed_browser_cookie_validation_preserves_existing_session(self):
        handler = object.__new__(APIHandler)
        previous_session = requests.Session()
        previous_session.cookies.set("TradeAuth_Session", "old", domain="na-trade.naeu.playblackdesert.com", path="/")
        handler.session = previous_session
        handler.login_status = True
        handler.is_session_expired = AsyncMock(return_value=-1)
        handler.save_session = Mock()

        result = await APIHandler.validate_and_save_imported_session(
            handler,
            [
                {
                    "name": "TradeAuth_Session",
                    "value": "new",
                    "domain": "na-trade.naeu.playblackdesert.com",
                    "path": "/",
                }
            ],
        )

        self.assertFalse(result)
        self.assertIs(handler.session, previous_session)
        self.assertTrue(handler.login_status)
        handler.save_session.assert_not_called()

    async def test_steam_mode_session_validation_does_not_attempt_password_login(self):
        handler = object.__new__(APIHandler)
        handler.account_mode = STEAM_BROWSER_MODE
        handler.login_status = True
        handler.email = "user@example.com"
        handler.password = "secret"
        handler.is_session_expired = AsyncMock(return_value=-1)
        handler.login = AsyncMock(return_value=1)

        self.assertFalse(await APIHandler.ensure_session_valid(handler))
        handler.login.assert_not_called()
        self.assertFalse(handler.login_status)

    async def test_pa_session_validation_does_not_attempt_password_login(self):
        handler = object.__new__(APIHandler)
        handler.account_mode = PA_CREDENTIALS_MODE
        handler.login_status = True
        handler.email = "user@example.com"
        handler.password = "secret"
        handler.is_session_expired = AsyncMock(return_value=-1)
        handler.login = AsyncMock(return_value=1)

        self.assertFalse(await APIHandler.ensure_session_valid(handler))
        handler.login.assert_not_called()
        self.assertFalse(handler.login_status)


class BackgroundTaskTests(unittest.IsolatedAsyncioTestCase):
    def make_task_manager(self, test_mode_enabled=False, stats_db_path=None):
        owns_stats_temp = stats_db_path is None
        with patch("bdo_marketplace_tools.services.task_manager._load_local_data", return_value=LOCAL_DATA.copy()), patch(
            "bdo_marketplace_tools.services.task_manager.load_account_mode",
            return_value=PA_CREDENTIALS_MODE,
        ), patch(
            "bdo_marketplace_tools.services.task_manager.load_steam_browser_profile_prepared",
            return_value=True,
        ), patch("bdo_marketplace_tools.services.task_manager.load_pa_browser_profile_prepared", return_value=True):
            manager = BackgroundTasks(FakeAPI(), test_mode_enabled=test_mode_enabled, persist_ui_settings=False)
        if stats_db_path is None:
            stats_temp = tempfile.TemporaryDirectory()
            self.addCleanup(stats_temp.cleanup)
            stats_db_path = Path(stats_temp.name) / ("stats.test.db" if test_mode_enabled else "stats.db")
        manager.stats_db_path = stats_db_path
        manager.legacy_stats_path = None
        if owns_stats_temp:
            self.addAsyncCleanup(manager.flush_stats_writes)
        return manager

    async def test_detection_and_purchase_history_uses_mode_specific_db(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "stats.db"
            test_db_path = Path(temp_dir) / "stats.test.db"

            manager = self.make_task_manager(stats_db_path=db_path)
            await manager.process_detected_outfits([["outfit-a", "2", "100"]], allow_purchase=False)
            await manager._record_purchase_history([{"item_id": "outfit-a", "price": "10", "count": 1}])
            await manager.flush_stats_writes()

            test_manager = self.make_task_manager(test_mode_enabled=True, stats_db_path=test_db_path)
            await test_manager.process_detected_outfits([["outfit-b", "5", "100"]], allow_purchase=False)
            await test_manager.flush_stats_writes()

            trends = stats_db_module.load_trends(1, path=db_path)
            today_row = trends["daily"][-1]
            self.assertEqual(today_row["detected"], 2)
            self.assertEqual(today_row["purchased"], 1)
            self.assertEqual(manager.stats_history_revision, 2)
            test_trends = stats_db_module.load_trends(1, path=test_db_path)
            self.assertEqual(test_trends["daily"][-1]["detected"], 5)
            self.assertEqual(test_trends["daily"][-1]["purchased"], 0)
            self.assertEqual(test_manager.stats_history_revision, 1)

    async def test_test_mode_refuses_production_stats_db_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "stats.db"
            test_db_path = Path(temp_dir) / "stats.test.db"

            with patch("bdo_marketplace_tools.services.task_manager.STATS_DB_PATH", db_path), patch(
                "bdo_marketplace_tools.services.task_manager.TEST_STATS_DB_PATH",
                test_db_path,
            ):
                manager = self.make_task_manager(test_mode_enabled=True, stats_db_path=test_db_path)
                manager.stats_db_path = db_path

                with self.assertRaisesRegex(RuntimeError, "production stats DB"):
                    await manager.process_detected_outfits([["10007", "1", "82500"]], allow_purchase=False)

                await manager.flush_stats_writes()

            self.assertFalse(db_path.exists())
            self.assertFalse(test_db_path.exists())

    async def test_detection_history_records_episode_starts_not_repeated_sightings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "stats.db"

            manager = self.make_task_manager(stats_db_path=db_path)
            await manager.process_detected_outfits([["outfit-a", "2", "100"]], allow_purchase=False)
            await manager.process_detected_outfits([["outfit-a", "2", "100"]], allow_purchase=False)
            await manager.flush_stats_writes()

            trends = stats_db_module.load_trends(1, path=db_path)
            self.assertEqual(trends["daily"][-1]["detected"], 2)
            self.assertEqual(manager.stats_history_revision, 1)

            await manager.process_detected_outfits([], allow_purchase=False)
            await manager.process_detected_outfits([["outfit-a", "2", "100"]], allow_purchase=False)
            await manager.flush_stats_writes()

            trends = stats_db_module.load_trends(1, path=db_path)
            self.assertEqual(trends["daily"][-1]["detected"], 2)
            self.assertEqual(manager.stats_history_revision, 1)

            await manager.process_detected_outfits([], allow_purchase=False)
            await manager.process_detected_outfits([], allow_purchase=False)
            await manager.process_detected_outfits([["outfit-a", "3", "100"]], allow_purchase=False)
            await manager.flush_stats_writes()

            trends = stats_db_module.load_trends(1, path=db_path)
            self.assertEqual(trends["daily"][-1]["detected"], 5)
            self.assertEqual(manager.stats_history_revision, 2)

    async def test_detection_history_retries_background_write_without_duplicating_episode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "stats.db"
            original_record = stats_db_module.record_detection_events
            attempts = 0

            def flaky_record(*args, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("locked")
                return original_record(*args, **kwargs)

            manager = self.make_task_manager(stats_db_path=db_path)
            with patch(
                "bdo_marketplace_tools.services.task_manager.stats_db.record_detection_events",
                side_effect=flaky_record,
            ):
                await manager.process_detected_outfits([["outfit-a", "1", "100"]], allow_purchase=False)
                await manager.process_detected_outfits([["outfit-a", "1", "100"]], allow_purchase=False)
                await manager.flush_stats_writes()

            self.assertEqual(attempts, 2)
            self.assertIn("outfit-a", manager._active_detection_episodes)
            trends = stats_db_module.load_trends(1, path=db_path)
            self.assertEqual(trends["daily"][-1]["detected"], 1)
            self.assertEqual(manager.stats_history_revision, 1)

    async def test_session_detected_counter_matches_episode_dedup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "stats.db"

            manager = self.make_task_manager(stats_db_path=db_path)
            await manager.process_detected_outfits([["outfit-a", "2", "100"]], allow_purchase=False)
            await manager.process_detected_outfits([["outfit-a", "2", "100"]], allow_purchase=False)
            self.assertEqual(manager.session_detected_outfits, 2)

            await manager.process_detected_outfits([], allow_purchase=False)
            await manager.process_detected_outfits([], allow_purchase=False)
            await manager.process_detected_outfits([["outfit-a", "1", "100"]], allow_purchase=False)
            self.assertEqual(manager.session_detected_outfits, 3)

            await manager.flush_stats_writes()
            trends = stats_db_module.load_trends(1, path=db_path)
            self.assertEqual(trends["daily"][-1]["detected"], manager.session_detected_outfits)

    async def test_watch_only_detection_events_follow_episodes(self):
        manager = self.make_task_manager()

        await manager.process_detected_outfits([["outfit-a", "2", "100"]], allow_purchase=False)
        await manager.process_detected_outfits([["outfit-a", "2", "100"]], allow_purchase=False)

        detection_events = [event for event in manager.events if "detected" in event]
        self.assertEqual(len(detection_events), 1)
        self.assertIn("2 available outfits", detection_events[0])
        self.assertNotIn("new", detection_events[0])  # first sighting: everything is new

        # A new listing joins the lingering one: log it, annotated with what is new.
        await manager.process_detected_outfits(
            [["outfit-a", "2", "100"], ["outfit-b", "1", "100"]],
            allow_purchase=False,
        )

        detection_events = [event for event in manager.events if "detected" in event]
        self.assertEqual(len(detection_events), 2)
        self.assertIn("3 available across 2 outfits (1 new)", detection_events[-1])
        self.assertEqual(manager.session_detected_outfits, 3)

    async def test_buy_mode_detection_events_flag_repeat_sightings(self):
        manager = self.make_task_manager()
        manager.api_handler.buy_item = AsyncMock(return_value={"purchase_records": [], "events": []})

        await manager.process_detected_outfits([["outfit-a", "2", "100"]], allow_purchase=True, adjust_pricing=False)
        await manager.process_detected_outfits([["outfit-a", "2", "100"]], allow_purchase=True, adjust_pricing=False)

        detection_events = [event for event in manager.events if "detected" in event]
        self.assertEqual(len(detection_events), 2)  # buy mode logs every scan it acts on
        self.assertIn("2 available outfits. Attempting purchase.", detection_events[0])
        self.assertIn("2 available outfits (0 new). Attempting purchase.", detection_events[1])
        self.assertEqual(manager.api_handler.buy_item.await_count, 2)
        self.assertEqual(manager.session_detected_outfits, 2)

    async def test_purchase_history_survives_lifetime_save_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "stats.db"
            manager = self.make_task_manager(stats_db_path=db_path)

            with patch(
                "bdo_marketplace_tools.services.task_manager._save_local_data",
                side_effect=OSError("disk full"),
            ):
                await manager.record_purchase_summary(
                    {
                        "purchase_records": [{"item_id": "outfit-a", "price": "100", "count": 1}],
                        "events": [{"level": "success", "message": "Purchase succeeded."}],
                    }
                )
                await manager.flush_stats_writes()

            trends = stats_db_module.load_trends(1, path=db_path)
            self.assertEqual(trends["daily"][-1]["purchased"], 1)
            self.assertTrue(any("Stats write failed for lifetime totals" in event for event in manager.events))
            self.assertTrue(any("Purchase succeeded." in event for event in manager.events))
            # Memory totals stay ahead and self-heal on the next successful save.
            self.assertEqual(manager.lifetime_successful_purchases, 1)
            self.assertEqual(manager.lifetime_silver_spent, 100)

    async def test_purchase_of_suppressed_redetection_keeps_success_rate_sane(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "stats.db"
            manager = self.make_task_manager(stats_db_path=db_path)

            # A fast relist inside the episode window can be bought without a new
            # detection episode; detected must still keep pace with purchases.
            await manager.record_purchase_summary(
                {
                    "purchase_records": [{"item_id": "outfit-a", "price": "100", "count": 2}],
                    "events": [],
                }
            )

            self.assertEqual(manager.session_successful_purchases, 2)
            self.assertEqual(manager.session_detected_outfits, 2)
            await manager.flush_stats_writes()

    async def test_detection_history_write_does_not_delay_buy_attempt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "stats.db"
            write_release = threading.Event()

            def slow_record(*_args, **_kwargs):
                write_release.wait(2)

            manager = self.make_task_manager(stats_db_path=db_path)
            manager.purchase_submission_enabled = True
            manager.api_handler.buy_item = AsyncMock(return_value={"purchase_records": [], "events": []})

            with patch(
                "bdo_marketplace_tools.services.task_manager.stats_db.record_detection_events",
                side_effect=slow_record,
            ):
                await manager.process_detected_outfits(
                    [["outfit-a", "1", "100"]],
                    allow_purchase=True,
                    adjust_pricing=False,
                )
                manager.api_handler.buy_item.assert_awaited_once()
                self.assertFalse(write_release.is_set())
                write_release.set()
                await manager.flush_stats_writes()

    async def test_stats_history_writes_run_in_worker_thread(self):
        async def run_in_thread(func, *args, **kwargs):
            threaded_calls.append(func)
            return func(*args, **kwargs)

        threaded_calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "stats.db"

            with patch(
                "bdo_marketplace_tools.services.task_manager.asyncio.to_thread",
                side_effect=run_in_thread,
            ):
                manager = self.make_task_manager(stats_db_path=db_path)
                await manager.process_detected_outfits([["outfit-a", "2", "100"]], allow_purchase=False)
                await manager._record_purchase_history([{"item_id": "outfit-a", "price": "10", "count": 1}])
                await manager.flush_stats_writes()

            trends = stats_db_module.load_trends(1, path=db_path)

        self.assertIn(stats_db_module.record_detection_events, threaded_calls)
        self.assertIn(stats_db_module.record_purchase_events, threaded_calls)
        self.assertEqual(trends["daily"][-1]["detected"], 2)
        self.assertEqual(trends["daily"][-1]["purchased"], 1)

    async def test_scan_coverage_batches_writes_and_flushes_on_threshold_or_stop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "stats.db"

            manager = self.make_task_manager(stats_db_path=db_path)
            threshold = task_manager_module.SCAN_COVERAGE_FLUSH_THRESHOLD
            for _ in range(threshold - 1):
                await manager._record_scan_coverage()
            self.assertFalse(db_path.exists())  # still batched in memory

            await manager._record_scan_coverage()  # threshold reached -> flushed to stats.db
            await manager.flush_stats_writes()
            trends = stats_db_module.load_trends(1, path=db_path)
            self.assertEqual(trends["daily"][-1]["scans"], threshold)

            await manager._record_scan_coverage()
            await manager.flush_scan_coverage()  # the stop path flushes the remainder
            trends = stats_db_module.load_trends(1, path=db_path)
            self.assertEqual(trends["daily"][-1]["scans"], threshold + 1)

            test_db_path = Path(temp_dir) / "stats.test.db"
            test_manager = self.make_task_manager(test_mode_enabled=True, stats_db_path=test_db_path)
            await test_manager._record_scan_coverage()
            self.assertEqual(test_manager._pending_scan_count_total, 1)
            self.assertFalse(test_db_path.exists())

    async def test_unified_event_stream_filters_by_notability_and_severity(self):
        manager = self.make_task_manager()

        manager.add_event("Routine scan detail.", "info")
        manager.add_event("Outfit detected: 2 available.", "success", notable=True)
        manager.add_event("Purchase failed: price mismatch.", "warning", notable=True)
        manager.add_event("Monitor cycle failed: timeout", "error")  # errors are auto-notable

        self.assertEqual(len(manager.events), 4)
        self.assertTrue(any("Routine scan detail." in event for event in manager.events))

        notable = manager.events_for_filter("notable")
        self.assertEqual(len(notable), 3)
        self.assertFalse(any("Routine scan detail." in event for event in notable))

        alerts = manager.events_for_filter("alerts")
        self.assertEqual(len(alerts), 2)
        self.assertTrue(any("price mismatch" in event for event in alerts))
        self.assertTrue(any("Monitor cycle failed" in event for event in alerts))
        self.assertFalse(any("Outfit detected" in event for event in alerts))

        self.assertEqual(len(manager.events_for_filter("all")), 4)

    async def test_event_rendering_dims_routine_info_and_keeps_notable_bright(self):
        manager = self.make_task_manager()

        manager.add_event("Routine scan detail.", "info")
        manager.add_event("Monitor started.", "info", notable=True)
        # Routine success dims too — green is earned by notability, not level.
        manager.add_event("Settings saved: Steam Account.", "success")
        manager.add_event("Purchase succeeded.", "success", notable=True)
        # Warnings keep their color even when routine.
        manager.add_event("Fallback pricing applied.", "warning")

        routine_info, notable_info, routine_success, notable_success, warning = manager.events_for_filter("all")
        self.assertIn(COLOR_EVENT_ROUTINE, routine_info)
        self.assertNotIn(COLOR_EVENT_ROUTINE, notable_info)
        self.assertIn(COLOR_EVENT_ROUTINE, routine_success)
        self.assertNotIn(COLOR_EVENT_ROUTINE, notable_success)
        self.assertNotIn(COLOR_EVENT_ROUTINE, warning)
        # Plain accessor strips markup for programmatic use.
        self.assertNotIn("[", manager.events[0])

    async def test_identical_consecutive_events_coalesce_with_repeat_counter(self):
        manager = self.make_task_manager()

        manager.add_event("Monitor cycle failed: timeout", "error")
        manager.add_event("Monitor cycle failed: timeout", "error")
        manager.add_event("Monitor cycle failed: timeout", "error")

        events = manager.events
        self.assertEqual(len([event for event in events if "Monitor cycle failed" in event]), 1)
        self.assertIn("×3", events[-1])

        # A different message breaks the run; a later repeat starts a fresh count.
        manager.add_event("Re-authentication succeeded.", "success")
        manager.add_event("Monitor cycle failed: timeout", "error")

        events = manager.events
        self.assertEqual(len([event for event in events if "Monitor cycle failed" in event]), 2)
        self.assertNotIn("×", events[-1])

        # Same message at a different level stays a separate line.
        manager.add_event("Monitor cycle failed: timeout", "warning")
        events = manager.events
        self.assertEqual(len([event for event in events if "Monitor cycle failed" in event]), 3)

    async def test_event_log_engine_works_standalone(self):
        # The engine lives in services.event_log and needs no task manager.
        from bdo_marketplace_tools.services.event_log import EventLog

        log = EventLog(limit=2)
        log.add("one", "info")
        log.add("two", "warning")
        log.add("three", "error")

        self.assertEqual(len(log.plain_events), 2)  # limit evicts the oldest
        self.assertNotIn("one", "\n".join(log.plain_events))
        self.assertTrue(log.has_unseen_alerts())
        log.mark_alerts_seen()
        self.assertFalse(log.has_unseen_alerts())
        self.assertEqual(len(log.rendered_for_filter("alerts")), 2)

    async def test_divider_events_render_as_rules_only_when_requested(self):
        manager = self.make_task_manager()

        manager.add_event("Monitor started — Buy mode.", "info", notable=True, divider="monitor started · buy mode")
        manager.add_event("Routine scan detail.", "info")

        # The Logs view asks for dividers: the monitor event becomes a dim rule.
        with_dividers = manager.events_for_filter("all", dividers=True)
        self.assertIn("── monitor started · buy mode ·", with_dividers[0])
        self.assertIn("──────", with_dividers[0])
        self.assertNotIn("Monitor started — Buy mode.", with_dividers[0])

        # The Activity tail renders the same record as a normal line.
        without_dividers = manager.events_for_filter("notable")
        self.assertIn("Monitor started — Buy mode.", without_dividers[0])
        self.assertNotIn("──", without_dividers[0])

        # Plain accessor stays prose for tests/logic.
        self.assertIn("Monitor started — Buy mode.", manager.events[0])

    async def test_warning_and_error_events_flag_unseen_alerts(self):
        manager = self.make_task_manager()
        self.assertFalse(manager.has_unseen_alerts())

        manager.add_event("All good.", "info")
        manager.add_event("Purchase succeeded.", "success")
        self.assertFalse(manager.has_unseen_alerts())

        manager.add_event("Purchase failed: price mismatch.", "warning")
        self.assertTrue(manager.has_unseen_alerts())

        manager.mark_alerts_seen()
        self.assertFalse(manager.has_unseen_alerts())

        manager.add_event("Monitor stopped after an unexpected error.", "error")
        self.assertTrue(manager.has_unseen_alerts())

    async def test_buy_flow_ticks_per_item_purchase_progress(self):
        manager = self.make_task_manager()
        manager.simulated_session_enabled = True

        await manager.buy_item([["outfit-a", "2", "100"]], adjust_pricing=False)

        # Live progress ticks mirror the committed totals (per item on the live path,
        # per record on the simulated one) — cosmetic only, never used for stats.
        self.assertEqual(manager.purchase_progress_count, 2)
        self.assertEqual(manager.purchase_progress_silver, 200)
        self.assertEqual(manager.session_successful_purchases, 2)
        self.assertEqual(manager.session_silver_spent, 200)

        # The observer itself is exception-proof even with a malformed record.
        manager._note_purchase_progress({"unexpected": "shape"})
        self.assertEqual(manager.purchase_progress_count, 2)

    async def test_purchase_progress_syncs_to_committed_before_ticked_batch(self):
        manager = self.make_task_manager()
        manager.simulated_session_enabled = True
        manager.session_successful_purchases = 1
        manager.session_silver_spent = 50

        await manager.buy_item([["outfit-a", "2", "100"]], adjust_pricing=False)

        self.assertEqual(manager.purchase_progress_count, 3)
        self.assertEqual(manager.purchase_progress_silver, 250)
        self.assertEqual(manager.session_successful_purchases, 3)
        self.assertEqual(manager.session_silver_spent, 250)

    async def test_spend_cap_limits_items_by_price_order(self):
        manager = self.make_task_manager()
        manager.max_spend = 250

        capped = manager._apply_spend_cap([["a", "3", "100"], ["b", "2", "80"]])

        self.assertEqual(capped, [["a", "2", "100"]])

    async def test_spend_cap_applies_to_current_session_spend(self):
        manager = self.make_task_manager()
        manager.max_spend = 250
        manager.session_silver_spent = 100

        capped = manager._apply_spend_cap([["a", "3", "100"], ["b", "2", "80"]])

        self.assertEqual(capped, [["a", "1", "100"]])

        manager.session_silver_spent = 250
        self.assertEqual(manager._apply_spend_cap([["a", "1", "100"]]), [])

    async def test_login_status_checker_does_not_start_duplicates(self):
        manager = self.make_task_manager()

        async def idle_checker():
            await asyncio.sleep(60)

        manager.login_status_checker = idle_checker
        manager.start_login_status_checker()
        first_task = manager.login_checker_task
        manager.start_login_status_checker()

        self.assertIs(manager.login_checker_task, first_task)
        await manager.stop_login_status_checker()
        self.assertIsNone(manager.login_checker_task)

    async def test_monitor_start_and_stop_are_idempotent(self):
        manager = self.make_task_manager()

        async def idle_checker():
            await asyncio.sleep(60)

        manager.checker = idle_checker

        self.assertTrue(await manager.start_checker())
        first_task = manager.checker_task
        self.assertFalse(await manager.start_checker())
        self.assertIs(manager.checker_task, first_task)
        self.assertTrue(manager.checker_enabled)

        self.assertTrue(await manager.stop_checker())
        self.assertFalse(manager.checker_enabled)
        self.assertFalse(await manager.stop_checker())
        self.assertFalse(manager.checker_enabled)

    async def test_single_item_test_monitor_is_separate_and_idempotent(self):
        manager = self.make_task_manager(test_mode_enabled=True)

        async def idle_checker():
            await asyncio.sleep(60)

        manager.single_item_test_checker = idle_checker

        self.assertTrue(await manager.start_single_item_test_checker())
        first_task = manager.single_item_test_checker_task
        self.assertFalse(await manager.start_single_item_test_checker())
        self.assertIs(manager.single_item_test_checker_task, first_task)
        self.assertTrue(manager.single_item_test_checker_enabled)
        self.assertFalse(manager.single_item_test_purchase_enabled)
        self.assertFalse(manager.checker_enabled)
        self.assertFalse(await manager.start_checker())
        self.assertFalse(manager.checker_enabled)

        self.assertTrue(await manager.stop_single_item_test_checker())
        self.assertFalse(manager.single_item_test_checker_enabled)
        self.assertFalse(manager.single_item_test_purchase_enabled)
        self.assertFalse(await manager.stop_single_item_test_checker())

    async def test_test_mode_helpers_are_disabled_outside_test_mode(self):
        manager = self.make_task_manager()

        self.assertFalse(await manager.start_single_item_test_checker())
        self.assertIsNone(manager.single_item_test_checker_task)
        self.assertFalse(await manager.debug_fake_outfit_detection())
        self.assertFalse(await manager.debug_fake_multi_outfit_detection())
        self.assertFalse(await manager.debug_simulate_purchase_success())
        self.assertFalse(await manager.debug_simulate_bundled_purchase_success())
        self.assertFalse(manager.set_simulated_session(True))
        self.assertFalse(manager.simulated_session_enabled)
        self.assertFalse(manager.api_handler.login_status)

    async def test_single_item_test_checker_processes_detection_without_live_buy(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.purchase_submission_enabled = True
        stock_check = AsyncMock(return_value=[["10007", "2", "92500"]])
        manager.api_handler.buy_item = AsyncMock()

        with patch("bdo_marketplace_tools.services.task_manager.check_single_item_stock", stock_check), patch(
            "bdo_marketplace_tools.services.task_manager.random.uniform",
            return_value=3,
        ), patch(
            "bdo_marketplace_tools.services.task_manager.asyncio.sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await manager.single_item_test_checker()

        stock_check.assert_awaited_once_with(manager.api_handler, SINGLE_ITEM_TEST_TARGET)
        manager.api_handler.buy_item.assert_not_called()
        self.assertEqual(manager.session_detected_outfits, 2)
        self.assertEqual(manager.session_successful_purchases, 0)
        self.assertTrue(any("Test item detected: 2 available test items" in event for event in manager.events))

    async def test_single_item_test_checker_can_buy_without_outfit_price_adjustment(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        stock_check = AsyncMock(return_value=[["10007", "2", "92500"]])
        manager.api_handler.buy_item = AsyncMock(
            return_value={
                "purchase_records": [
                    {
                        "item_id": "10007",
                        "price": 92500,
                        "count": 2,
                        "result_code": 0,
                    }
                ],
                "events": [{"level": "success", "message": "Test buy succeeded."}],
            }
        )
        manager.single_item_test_purchase_enabled = True

        with patch("bdo_marketplace_tools.services.task_manager.check_single_item_stock", stock_check), patch.object(
            manager,
            "save_local_data",
        ) as save_mock, patch(
            "bdo_marketplace_tools.services.task_manager.random.uniform",
            return_value=3,
        ), patch(
            "bdo_marketplace_tools.services.task_manager.asyncio.sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await manager.single_item_test_checker()

        stock_check.assert_awaited_once_with(manager.api_handler, SINGLE_ITEM_TEST_TARGET)
        manager.api_handler.buy_item.assert_awaited_once_with(
            [["10007", "2", "92500"]],
            purchase_delay_bounds=DEFAULT_PURCHASE_DELAY_BOUNDS,
            on_purchase=manager._note_purchase_progress,
        )
        self.assertEqual(manager.session_detected_outfits, 2)
        self.assertEqual(manager.session_successful_purchases, 2)
        self.assertEqual(manager.session_silver_spent, 185000)
        self.assertFalse(any("Fallback pricing applied" in event for event in manager.events))
        save_mock.assert_awaited_once()

    async def test_live_buy_error_probe_uses_normal_purchase_pipeline(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.api_handler.login_status = True
        manager.api_handler.buy_item = AsyncMock(
            return_value={
                "purchase_records": [],
                "events": [{"level": "warning", "message": "Purchase failed for 15280 at 2900000000 silver."}],
            }
        )

        result = await manager.debug_run_live_buy_error_probe()

        self.assertTrue(result)
        self.assertEqual(
            live_buy_error_test_listing(),
            [[LIVE_BUY_ERROR_TEST_TARGET["main_key"], "1", LIVE_BUY_ERROR_TEST_TARGET["max_buy_price"]]],
        )
        manager.api_handler.buy_item.assert_awaited_once_with(
            [["15280", "1", "2900000000"]],
            purchase_delay_bounds=DEFAULT_PURCHASE_DELAY_BOUNDS,
            on_purchase=manager._note_purchase_progress,
        )
        self.assertTrue(any("Live buy error probe submitting item 15280" in event for event in manager.events))
        self.assertTrue(any("Live buy probe detected: 1 available live buy probe" in event for event in manager.events))
        self.assertTrue(any("Purchase failed for 15280" in event for event in manager.events))

    async def test_live_buy_error_probe_requires_test_mode_real_session(self):
        manager = self.make_task_manager()
        manager.api_handler.buy_item = AsyncMock()
        self.assertFalse(await manager.debug_run_live_buy_error_probe())
        manager.api_handler.buy_item.assert_not_called()

        test_manager = self.make_task_manager(test_mode_enabled=True)
        test_manager.api_handler.buy_item = AsyncMock()
        self.assertFalse(await test_manager.debug_run_live_buy_error_probe())
        self.assertTrue(any("requires an online marketplace session" in event for event in test_manager.events))

        test_manager.api_handler.login_status = True
        test_manager.set_simulated_session(True)
        self.assertFalse(await test_manager.debug_run_live_buy_error_probe())
        test_manager.api_handler.buy_item.assert_not_called()
        self.assertTrue(any("requires a real marketplace session" in event for event in test_manager.events))

    async def test_buy_item_can_skip_outfit_price_rules_for_validated_rows(self):
        manager = self.make_task_manager()
        manager.api_handler.buy_item = AsyncMock(return_value={"purchase_records": [], "events": []})

        await manager.buy_item([["10007", 1, 82500]], adjust_pricing=False)

        manager.api_handler.buy_item.assert_awaited_once_with(
            [["10007", "1", "82500"]],
            purchase_delay_bounds=DEFAULT_PURCHASE_DELAY_BOUNDS,
            on_purchase=manager._note_purchase_progress,
        )
        self.assertFalse(any("Fallback pricing applied" in event for event in manager.events))

    async def test_purchase_delay_range_feeds_api_buy_delay(self):
        manager = self.make_task_manager()
        manager.api_handler.buy_item = AsyncMock(return_value={"purchase_records": [], "events": []})

        manager.set_purchase_delay_range("4.5", "8")
        await manager.buy_item([["10007", 1, 82500]], adjust_pricing=False)

        self.assertEqual(manager.purchase_delay_bounds, (4.5, 8.0))
        self.assertEqual(manager.purchase_delay_range(), "4.5-8s")
        manager.api_handler.buy_item.assert_awaited_once_with(
            [["10007", "1", "82500"]],
            purchase_delay_bounds=(4.5, 8.0),
            on_purchase=manager._note_purchase_progress,
        )

        with self.assertRaises(ValueError):
            manager.set_purchase_delay_range(10, 2)

    async def test_custom_delay_range_feeds_monitor_sleep_bounds(self):
        manager = self.make_task_manager()
        manager.set_custom_delay_range(8, 13)

        self.assertEqual(manager.delay, "custom")
        self.assertEqual(manager.current_delay_label(), "Custom")
        self.assertEqual(manager.current_delay_range(), "8-13s")
        self.assertEqual(manager.current_delay_bounds(), (8, 13))

        manager.set_custom_delay_range(5, 10)
        self.assertEqual(manager.delay, "2")
        self.assertEqual(manager.current_delay_label(), "Balanced")
        self.assertEqual(manager.current_delay_bounds(), (5, 10))

        manager.set_custom_delay_range(8, 13)
        manager.api_handler.check_stock = AsyncMock(return_value=[])
        with patch("bdo_marketplace_tools.services.task_manager.random.uniform", return_value=9) as uniform_mock:
            with patch("bdo_marketplace_tools.services.task_manager.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
                with self.assertRaises(asyncio.CancelledError):
                    await manager.checker()

        uniform_mock.assert_called_once_with(8, 13)

    async def test_monitor_errors_back_off_from_normal_polling_window(self):
        manager = self.make_task_manager()
        manager.set_custom_delay_range(5, 10)
        manager.consecutive_cycle_errors = 2

        with patch("bdo_marketplace_tools.services.task_manager.random.uniform", return_value=20) as uniform_mock:
            self.assertEqual(manager.next_sleep_duration(), 20)

        uniform_mock.assert_called_once_with(15, 30)

    async def test_fake_detection_uses_watch_only_path(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.purchase_submission_enabled = True
        manager.buy_item = AsyncMock()

        await manager.debug_fake_outfit_detection()

        manager.buy_item.assert_not_called()
        self.assertEqual(manager.session_detected_outfits, 1)
        self.assertEqual(manager.session_successful_purchases, 0)
        self.assertTrue(any("Outfit detected: 1" in event for event in manager.events))

    async def test_fake_purchase_updates_success_rate_and_local_totals(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        with patch.object(manager, "save_local_data") as save_mock:
            await manager.debug_simulate_purchase_success()

        self.assertEqual(manager.session_detected_outfits, 1)
        self.assertEqual(manager.session_successful_purchases, 1)
        self.assertEqual(manager.lifetime_successful_purchases, 1)
        self.assertGreater(manager.session_silver_spent, 0)
        save_mock.assert_awaited_once()

    async def test_fake_bundled_purchase_ticks_eight_items_without_live_api(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        progress_callback = Mock()
        expected_spend = 8 * int(PREMIUM_OUTFIT_MAX_PRICE)

        with patch.object(manager, "save_local_data") as save_mock, patch(
            "bdo_marketplace_tools.services.task_manager.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep_mock:
            result = await manager.debug_simulate_bundled_purchase_success(
                progress_callback=progress_callback
            )

        self.assertTrue(result)
        self.assertEqual(manager.session_detected_outfits, 8)
        self.assertEqual(manager.purchase_progress_count, 8)
        self.assertEqual(manager.purchase_progress_silver, expected_spend)
        self.assertEqual(manager.session_successful_purchases, 8)
        self.assertEqual(manager.session_silver_spent, expected_spend)
        self.assertFalse(manager.purchase_in_progress)
        self.assertEqual(progress_callback.call_count, 8)
        self.assertEqual(sleep_mock.await_count, 7)
        self.assertEqual([args.args[0] for args in sleep_mock.await_args_list], [5.0] * 7)
        self.assertIsNone(getattr(manager.api_handler, "buy_item", None))
        self.assertTrue(any("one bundled buy list" in event for event in manager.events))
        save_mock.assert_awaited_once()

    async def test_simulated_session_buy_mode_does_not_call_purchase_api(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.set_simulated_session(True)
        manager.purchase_submission_enabled = True
        manager.api_handler.buy_item = AsyncMock()

        with patch.object(manager, "save_local_data") as save_mock:
            await manager.process_detected_outfits([["debug-premium-outfit", "1", "2020000000"]])

        manager.api_handler.buy_item.assert_not_called()
        self.assertTrue(manager.api_handler.login_status)
        self.assertTrue(manager.simulated_session_enabled)
        self.assertEqual(manager.session_detected_outfits, 1)
        self.assertEqual(manager.session_successful_purchases, 1)
        self.assertGreater(manager.session_silver_spent, 0)
        self.assertTrue(any("Test-mode purchase simulated" in event for event in manager.events))
        save_mock.assert_awaited_once()

    async def test_disabling_simulated_session_returns_to_watch_only(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.set_simulated_session(True)
        manager.purchase_submission_enabled = True

        manager.set_simulated_session(False)

        self.assertFalse(manager.api_handler.login_status)
        self.assertFalse(manager.simulated_session_enabled)
        self.assertFalse(manager.purchase_submission_enabled)

    async def test_zero_purchase_summary_does_not_save_local_data(self):
        manager = self.make_task_manager()
        with patch.object(manager, "save_local_data") as save_mock:
            await manager.record_purchase_summary(
                {
                    "purchase_records": [],
                    "events": [{"level": "warning", "message": "simulated no purchase"}],
                }
            )

        self.assertEqual(manager.session_successful_purchases, 0)
        self.assertEqual(manager.session_silver_spent, 0)
        save_mock.assert_not_called()

    async def test_zero_purchase_summary_does_not_add_generic_duplicate_when_reason_exists(self):
        manager = self.make_task_manager()

        await manager.record_purchase_summary(
            {
                "purchase_records": [],
                "events": [{"level": "warning", "message": "Purchase failed: price mismatch."}],
            }
        )

        event_text = "\n".join(manager.events)
        self.assertIn("Purchase failed: price mismatch.", event_text)
        self.assertNotIn("Purchase attempt completed without a successful request.", event_text)

    async def test_monitor_cycle_errors_do_not_kill_checker_loop(self):
        manager = self.make_task_manager()
        manager.api_handler.check_stock = AsyncMock(return_value=[["bad-item", "not-a-number", "100"]])

        with patch("bdo_marketplace_tools.services.task_manager.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await manager.checker()

        self.assertTrue(any("Monitor cycle failed" in event for event in manager.events))

    async def test_checker_done_callback_marks_crashed_monitor_stopped(self):
        manager = self.make_task_manager()

        async def crash_checker():
            raise RuntimeError("simulated monitor crash")

        manager.checker = crash_checker
        await manager.start_checker()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertFalse(manager.checker_enabled)
        self.assertIsNone(manager.checker_task)
        self.assertTrue(any("simulated monitor crash" in event for event in manager.events))

    async def test_login_passes_session_check_failure_to_pa_browser_refresh(self):
        manager = self.make_task_manager()
        manager.api_handler.email = "user@example.com"
        manager.api_handler.password = "secret"
        manager.api_handler.is_session_expired = AsyncMock(side_effect=MarketplaceAPIError("network down"))
        manager.api_handler.login = AsyncMock(return_value=0)
        manager.refresh_pa_browser_session = AsyncMock(return_value=False)

        await manager.login()

        manager.api_handler.login.assert_not_called()
        manager.refresh_pa_browser_session.assert_awaited_once()
        self.assertEqual(str(manager.refresh_pa_browser_session.await_args.kwargs["session_check_error"]), "network down")
        self.assertTrue(manager.refresh_pa_browser_session.await_args.kwargs["cleanup_browser_cache"])

    async def test_manual_pa_login_cleans_browser_cache_before_opening_browser(self):
        manager = self.make_task_manager()
        manager.api_handler.is_session_expired = AsyncMock(return_value=-1)
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)
        cleanup_result = BrowserProfileCleanupResult(
            profile_path=PA_MARKET_PROFILE_PATH,
            total_bytes_before=300,
            disposable_bytes_before=250,
            removed_bytes=200,
            removed_paths=("Default/Cache",),
        )

        with patch(
            "bdo_marketplace_tools.services.task_manager.load_credentials",
            return_value=("user@example.com", "secret"),
        ), patch(
            "bdo_marketplace_tools.services.task_manager.clean_disposable_browser_profile_cache",
            return_value=cleanup_result,
        ) as cleanup, patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            await manager.login()

        cleanup.assert_called_once()
        self.assertEqual(cleanup.call_args.args[0], PA_MARKET_PROFILE_PATH)
        browser_auth.assert_awaited_once()
        self.assertTrue(any("Cleaned 200 B of disposable Pearl Abyss Account browser cache" in event for event in manager.events))
        await manager.stop_login_status_checker()

    async def test_manual_steam_login_cleans_browser_cache_before_opening_browser(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.is_session_expired = AsyncMock(return_value=-1)
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)
        cleanup_result = BrowserProfileCleanupResult(
            profile_path=STEAM_MARKET_PROFILE_PATH,
            total_bytes_before=300,
            disposable_bytes_before=250,
            removed_bytes=200,
            removed_paths=("Default/Cache",),
        )

        with patch(
            "bdo_marketplace_tools.services.task_manager.clean_disposable_browser_profile_cache",
            return_value=cleanup_result,
        ) as cleanup, patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            await manager.login()

        cleanup.assert_called_once()
        self.assertEqual(cleanup.call_args.args[0], STEAM_MARKET_PROFILE_PATH)
        browser_auth.assert_awaited_once()
        self.assertTrue(any("Cleaned 200 B of disposable Steam Account browser cache" in event for event in manager.events))
        await manager.stop_login_status_checker()

    async def test_steam_initial_setup_cleans_browser_cache_before_opening_browser(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.steam_browser_profile_prepared = False
        cleanup_result = BrowserProfileCleanupResult(
            profile_path=STEAM_MARKET_PROFILE_PATH,
            total_bytes_before=300,
            disposable_bytes_before=250,
            removed_bytes=200,
            removed_paths=("Default/Cache",),
        )

        with patch(
            "bdo_marketplace_tools.services.task_manager.clean_disposable_browser_profile_cache",
            return_value=cleanup_result,
        ) as cleanup, patch(
            "bdo_marketplace_tools.services.task_manager.prepare_steam_browser_profile",
            new=AsyncMock(),
        ) as profile_setup, patch(
            "bdo_marketplace_tools.services.task_manager.save_steam_browser_profile_prepared",
            return_value=True,
        ):
            prepared = await manager.prepare_steam_browser_profile()

        self.assertTrue(prepared)
        cleanup.assert_called_once()
        self.assertEqual(cleanup.call_args.args[0], STEAM_MARKET_PROFILE_PATH)
        profile_setup.assert_awaited_once()
        self.assertTrue(any("Cleaned 200 B of disposable Steam Account browser cache" in event for event in manager.events))

    async def test_browser_cache_cleanup_runs_filesystem_work_in_thread(self):
        manager = self.make_task_manager()
        cleanup_result = BrowserProfileCleanupResult(
            profile_path=STEAM_MARKET_PROFILE_PATH,
            total_bytes_before=300,
            disposable_bytes_before=250,
            removed_bytes=200,
            removed_paths=("Default/Cache",),
        )

        with patch(
            "bdo_marketplace_tools.services.task_manager.asyncio.to_thread",
            new=AsyncMock(return_value=cleanup_result),
        ) as to_thread:
            cleaned = await manager._clean_browser_cache_before_auth(STEAM_MARKET_PROFILE_PATH, "Steam Account")

        self.assertTrue(cleaned)
        to_thread.assert_awaited_once_with(
            task_manager_module.clean_disposable_browser_profile_cache,
            STEAM_MARKET_PROFILE_PATH,
            threshold_bytes=DEFAULT_DISPOSABLE_CACHE_CLEANUP_THRESHOLD_BYTES,
        )
        self.assertTrue(any("Cleaned 200 B of disposable Steam Account browser cache" in event for event in manager.events))

    async def test_browser_cache_cleanup_uses_saved_threshold(self):
        manager = self.make_task_manager()
        manager.set_browser_cache_cleanup_threshold_mb(42)
        cleanup_result = BrowserProfileCleanupResult(
            profile_path=STEAM_MARKET_PROFILE_PATH,
            total_bytes_before=0,
            disposable_bytes_before=10,
            removed_bytes=0,
            skipped=True,
        )

        with patch(
            "bdo_marketplace_tools.services.task_manager.asyncio.to_thread",
            new=AsyncMock(return_value=cleanup_result),
        ) as to_thread:
            cleaned = await manager._clean_browser_cache_before_auth(STEAM_MARKET_PROFILE_PATH, "Steam Account")

        self.assertFalse(cleaned)
        to_thread.assert_awaited_once_with(
            task_manager_module.clean_disposable_browser_profile_cache,
            STEAM_MARKET_PROFILE_PATH,
            threshold_bytes=42 * MIB,
        )

    async def test_manual_browser_cache_cleanup_cleans_all_profiles_without_saved_threshold(self):
        manager = self.make_task_manager()
        manager.set_browser_cache_cleanup_threshold_mb(999)
        cleanup_results = (
            BrowserProfileCleanupResult(
                profile_path=STEAM_MARKET_PROFILE_PATH,
                total_bytes_before=300,
                disposable_bytes_before=250,
                removed_bytes=200,
                removed_paths=("Default/Cache",),
            ),
            BrowserProfileCleanupResult(
                profile_path=PA_MARKET_PROFILE_PATH,
                total_bytes_before=100,
                disposable_bytes_before=70,
                removed_bytes=60,
                removed_paths=("Default/Code Cache",),
            ),
        )

        with patch(
            "bdo_marketplace_tools.services.task_manager.asyncio.to_thread",
            new=AsyncMock(return_value=cleanup_results),
        ) as to_thread:
            result = await manager.clean_browser_cache_now()

        to_thread.assert_awaited_once_with(
            task_manager_module.clean_all_disposable_browser_profile_caches,
            threshold_bytes=1,
        )
        self.assertEqual(result["removed_bytes"], 260)
        self.assertEqual(result["failed_paths"], 0)
        self.assertTrue(any("Cleaned 260 B of disposable browser cache" in event for event in manager.events))

    async def test_browser_cache_cleanup_failure_does_not_block_manual_refresh(self):
        manager = self.make_task_manager()
        manager.api_handler.is_session_expired = AsyncMock(return_value=-1)
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)

        with patch(
            "bdo_marketplace_tools.services.task_manager.load_credentials",
            return_value=("user@example.com", "secret"),
        ), patch(
            "bdo_marketplace_tools.services.task_manager.clean_disposable_browser_profile_cache",
            side_effect=OSError("locked"),
        ) as cleanup, patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            await manager.login()

        cleanup.assert_called_once()
        browser_auth.assert_awaited_once()
        self.assertTrue(any("Browser cache cleanup skipped" in event for event in manager.events))
        self.assertTrue(manager.api_handler.login_status)
        await manager.stop_login_status_checker()

    async def test_pa_login_uses_browser_refresh_with_saved_credentials(self):
        manager = self.make_task_manager()
        manager.api_handler.email = "user@example.com"
        manager.api_handler.password = "secret"
        manager.api_handler.is_session_expired = AsyncMock(return_value=-1)
        manager.api_handler.login = AsyncMock(return_value=1)
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)

        with patch(
            "bdo_marketplace_tools.services.task_manager.load_credentials",
            return_value=("user@example.com", "secret"),
        ), patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            await manager.login()

        browser_auth.assert_awaited_once()
        self.assertEqual(browser_auth.await_args.kwargs["profile_path"], PA_MARKET_PROFILE_PATH)
        self.assertIsNone(browser_auth.await_args.kwargs["bootstrap_url"])
        self.assertEqual(browser_auth.await_args.kwargs["account_label"], "Pearl Abyss Account")
        self.assertFalse(browser_auth.await_args.kwargs["auto_steam_login"])
        self.assertTrue(browser_auth.await_args.kwargs["auto_pa_login"])
        self.assertEqual(browser_auth.await_args.kwargs["pa_email"], "user@example.com")
        self.assertEqual(browser_auth.await_args.kwargs["pa_password"], "secret")
        manager.api_handler.login.assert_not_called()
        manager.api_handler.validate_and_save_imported_session.assert_awaited_once()
        self.assertTrue(manager.api_handler.login_status)
        self.assertTrue(manager.saved_session_last_known_valid)
        self.assertFalse(manager.steam_auto_reauth_enabled)
        self.assertTrue(any("Pearl Abyss Account session validated and saved" in event for event in manager.events))
        await manager.stop_login_status_checker()

    async def test_pa_browser_refresh_bootstraps_fresh_profile_once_then_marks_prepared(self):
        manager = self.make_task_manager()
        manager.persist_ui_settings = True
        manager.pa_browser_profile_prepared = False
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)

        with patch(
            "bdo_marketplace_tools.services.task_manager.load_credentials",
            return_value=("user@example.com", "secret"),
        ), patch(
            "bdo_marketplace_tools.services.task_manager.save_pa_browser_profile_prepared",
            return_value=True,
        ) as save_prepared, patch(
            "bdo_marketplace_tools.services.task_manager.save_saved_session_last_known_valid",
            return_value=True,
        ), patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            refreshed = await manager.refresh_pa_browser_session()

        self.assertTrue(refreshed)
        browser_auth.assert_awaited_once()
        self.assertEqual(browser_auth.await_args.kwargs["bootstrap_url"], BDO_SITE_BOOTSTRAP_URL)
        save_prepared.assert_called_once_with(True)
        self.assertTrue(manager.pa_browser_profile_prepared)
        await manager.stop_login_status_checker()

    async def test_pa_browser_refresh_skips_bootstrap_after_profile_is_prepared(self):
        manager = self.make_task_manager()
        manager.pa_browser_profile_prepared = True
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)

        with patch(
            "bdo_marketplace_tools.services.task_manager.load_credentials",
            return_value=("user@example.com", "secret"),
        ), patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            refreshed = await manager.refresh_pa_browser_session()

        self.assertTrue(refreshed)
        browser_auth.assert_awaited_once()
        self.assertIsNone(browser_auth.await_args.kwargs["bootstrap_url"])
        await manager.stop_login_status_checker()

    async def test_pa_login_in_memory_credentials_without_saved_password_blocks_browser_refresh(self):
        manager = self.make_task_manager()
        manager.api_handler.email = "user@example.com"
        manager.api_handler.password = "secret"
        manager.api_handler.is_session_expired = AsyncMock(return_value=-1)
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)

        with patch(
            "bdo_marketplace_tools.services.task_manager.load_credentials",
            return_value=(None, None),
        ), patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            await manager.login()

        browser_auth.assert_not_awaited()
        manager.api_handler.validate_and_save_imported_session.assert_not_awaited()
        self.assertFalse(manager.api_handler.login_status)
        self.assertFalse(manager.saved_session_last_known_valid)
        self.assertTrue(any("Pearl Abyss Account credentials are not saved" in event for event in manager.events))

    async def test_pa_login_without_saved_credentials_blocks_browser_refresh(self):
        manager = self.make_task_manager()
        manager.api_handler.email = None
        manager.api_handler.password = None
        manager.api_handler.is_session_expired = AsyncMock(return_value=-1)
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)

        with patch(
            "bdo_marketplace_tools.services.task_manager.load_credentials",
            return_value=(None, None),
        ), patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            await manager.login()

        browser_auth.assert_not_awaited()
        manager.api_handler.validate_and_save_imported_session.assert_not_awaited()
        self.assertFalse(manager.api_handler.login_status)
        self.assertFalse(manager.saved_session_last_known_valid)
        self.assertTrue(any("Pearl Abyss Account credentials are not saved" in event for event in manager.events))

    async def test_pa_browser_refresh_loads_saved_credentials_for_auto_login(self):
        manager = self.make_task_manager()
        manager.api_handler.email = None
        manager.api_handler.password = None
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)

        with patch(
            "bdo_marketplace_tools.services.task_manager.load_credentials",
            return_value=("saved@example.com", "saved-secret"),
        ), patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            refreshed = await manager.refresh_pa_browser_session()

        self.assertTrue(refreshed)
        browser_auth.assert_awaited_once()
        self.assertTrue(browser_auth.await_args.kwargs["auto_pa_login"])
        self.assertEqual(browser_auth.await_args.kwargs["pa_email"], "saved@example.com")
        self.assertEqual(browser_auth.await_args.kwargs["pa_password"], "saved-secret")
        self.assertEqual(manager.api_handler.email, "saved@example.com")
        self.assertEqual(manager.api_handler.password, "saved-secret")
        await manager.stop_login_status_checker()

    async def test_pa_browser_refresh_serializes_concurrent_browser_launches(self):
        manager = self.make_task_manager()
        manager.api_handler.email = "user@example.com"
        manager.api_handler.password = "secret"
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)
        browser_started = asyncio.Event()
        release_browser = asyncio.Event()

        async def acquire_cookies(*_args, **_kwargs):
            browser_started.set()
            await release_browser.wait()
            return [{"name": "TradeAuth_Session", "value": "ok"}]

        with patch(
            "bdo_marketplace_tools.services.task_manager.load_credentials",
            return_value=("user@example.com", "secret"),
        ), patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(side_effect=acquire_cookies),
        ) as browser_auth:
            first_refresh = asyncio.create_task(manager.refresh_pa_browser_session())
            await browser_started.wait()
            second_refresh = asyncio.create_task(manager.refresh_pa_browser_session())
            await asyncio.sleep(0)

            self.assertEqual(browser_auth.await_count, 1)
            release_browser.set()
            results = await asyncio.gather(first_refresh, second_refresh)

        self.assertEqual(results, [True, True])
        browser_auth.assert_awaited_once()
        manager.api_handler.validate_and_save_imported_session.assert_awaited_once()
        self.assertTrue(manager.api_handler.login_status)
        await manager.stop_login_status_checker()

    async def test_steam_mode_login_uses_browser_refresh_instead_of_credentials(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.email = "user@example.com"
        manager.api_handler.password = "secret"
        manager.api_handler.is_session_expired = AsyncMock(return_value=-1)
        manager.api_handler.login = AsyncMock(return_value=1)
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)

        with patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            await manager.login()

        browser_auth.assert_awaited_once()
        self.assertTrue(browser_auth.await_args.kwargs["auto_steam_login"])
        self.assertTrue(browser_auth.await_args.kwargs["handle_pa_cookie_consent"])
        manager.api_handler.login.assert_not_called()
        manager.api_handler.validate_and_save_imported_session.assert_awaited_once()
        self.assertTrue(manager.api_handler.login_status)
        self.assertTrue(manager.saved_session_last_known_valid)
        self.assertTrue(manager.steam_auto_reauth_enabled)
        self.assertTrue(any("Steam Account session validated and saved" in event for event in manager.events))
        await manager.stop_login_status_checker()

    async def test_initial_steam_expired_session_prompts_without_enabling_auto_reauth(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.saved_session_last_known_valid = True
        manager.api_handler.session_has_cookies = True
        manager.api_handler.is_session_expired = AsyncMock(return_value=-1)

        with patch("bdo_marketplace_tools.services.task_manager.acquire_market_cookies", new=AsyncMock()) as browser_auth:
            await manager.initial_login_check()

        browser_auth.assert_not_called()
        self.assertFalse(manager.api_handler.login_status)
        self.assertFalse(manager.steam_auto_reauth_enabled)
        self.assertFalse(manager.saved_session_last_known_valid)
        self.assertTrue(any("Refresh Session to open the login browser" in event for event in manager.events))

    async def test_initial_login_check_skips_api_without_last_known_valid_saved_session(self):
        manager = self.make_task_manager()
        manager.saved_session_last_known_valid = False
        manager.api_handler.session_has_cookies = True
        manager.api_handler.is_session_expired = AsyncMock(return_value=0)

        await manager.initial_login_check()

        manager.api_handler.is_session_expired.assert_not_called()
        self.assertFalse(manager.api_handler.login_status)
        self.assertTrue(any("No previously validated marketplace session" in event for event in manager.events))
        # A fresh logged-out start is routine: dim info, off the tail, no alert dot.
        self.assertFalse(manager.has_unseen_alerts())
        self.assertFalse(
            any("No previously validated marketplace session" in event for event in manager.events_for_filter("notable"))
        )
        self.assertFalse(
            any("No previously validated marketplace session" in event for event in manager.events_for_filter("alerts"))
        )
        rendered = manager.events_for_filter("all")[-1]
        self.assertIn(COLOR_EVENT_ROUTINE, rendered)

    async def test_initial_login_check_clears_last_known_valid_when_cookies_are_missing(self):
        manager = self.make_task_manager()
        manager.saved_session_last_known_valid = True
        manager.api_handler.session_has_cookies = False
        manager.api_handler.is_session_expired = AsyncMock(return_value=0)

        await manager.initial_login_check()

        manager.api_handler.is_session_expired.assert_not_called()
        self.assertFalse(manager.saved_session_last_known_valid)
        self.assertFalse(manager.api_handler.login_status)
        self.assertTrue(any("No saved marketplace session cookies found" in event for event in manager.events))

    async def test_initial_valid_steam_session_marks_auto_reauth_available(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.saved_session_last_known_valid = True
        manager.api_handler.session_has_cookies = True
        manager.api_handler.is_session_expired = AsyncMock(return_value=0)

        await manager.initial_login_check()

        self.assertTrue(manager.api_handler.login_status)
        self.assertTrue(manager.saved_session_last_known_valid)
        self.assertTrue(manager.steam_auto_reauth_enabled)
        self.assertTrue(any("Saved marketplace session is valid" in event for event in manager.events))
        await manager.stop_login_status_checker()

    async def test_first_time_steam_refresh_prepares_profile_before_market_cookie_import(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.steam_browser_profile_prepared = False
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)

        with patch("bdo_marketplace_tools.services.task_manager.prepare_steam_browser_profile", new=AsyncMock()) as profile_setup, patch(
            "bdo_marketplace_tools.services.task_manager.save_steam_browser_profile_prepared",
            return_value=True,
        ) as save_prepared, patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            refreshed = await manager.refresh_browser_session()

        self.assertTrue(refreshed)
        profile_setup.assert_awaited_once()
        save_prepared.assert_called_once_with(True)
        browser_auth.assert_awaited_once()
        self.assertTrue(browser_auth.await_args.kwargs["auto_steam_login"])
        self.assertTrue(browser_auth.await_args.kwargs["handle_pa_cookie_consent"])
        manager.api_handler.validate_and_save_imported_session.assert_awaited_once()
        self.assertTrue(manager.steam_browser_profile_prepared)
        self.assertTrue(manager.steam_pa_cookie_consent_prepared)
        self.assertTrue(manager.saved_session_last_known_valid)
        self.assertTrue(manager.steam_auto_reauth_enabled)
        self.assertTrue(any("Initial Steam browser setup is required" in event for event in manager.events))
        self.assertTrue(any("Initial Steam browser setup saved" in event for event in manager.events))
        await manager.stop_login_status_checker()

    async def test_steam_refresh_marks_one_time_pa_cookie_consent_complete_after_success(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.steam_browser_profile_prepared = True
        manager.steam_pa_cookie_consent_prepared = False
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)

        with patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            refreshed = await manager.refresh_browser_session()

        self.assertTrue(refreshed)
        browser_auth.assert_awaited_once()
        self.assertTrue(browser_auth.await_args.kwargs["auto_steam_login"])
        self.assertTrue(browser_auth.await_args.kwargs["handle_pa_cookie_consent"])
        self.assertTrue(manager.steam_pa_cookie_consent_prepared)
        await manager.stop_login_status_checker()

    async def test_steam_refresh_skips_pa_cookie_consent_probe_after_it_is_prepared(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.steam_browser_profile_prepared = True
        manager.steam_pa_cookie_consent_prepared = True
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)

        with patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            refreshed = await manager.refresh_browser_session(force_refresh=True)

        self.assertTrue(refreshed)
        browser_auth.assert_awaited_once()
        self.assertTrue(browser_auth.await_args.kwargs["auto_steam_login"])
        self.assertFalse(browser_auth.await_args.kwargs["handle_pa_cookie_consent"])
        await manager.stop_login_status_checker()

    async def test_failed_steam_refresh_clears_last_known_valid_session_flag(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.saved_session_last_known_valid = True

        with patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(side_effect=BrowserAuthError("browser closed")),
        ):
            refreshed = await manager.refresh_browser_session()

        self.assertFalse(refreshed)
        self.assertFalse(manager.saved_session_last_known_valid)
        self.assertFalse(manager.api_handler.login_status)

    async def test_steam_refresh_browser_closed_keeps_consent_prepared(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.steam_browser_profile_prepared = True
        manager.steam_pa_cookie_consent_prepared = True

        with patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(side_effect=BrowserAuthError("Browser closed before a marketplace session could be captured.")),
        ):
            refreshed = await manager.refresh_browser_session(force_refresh=True)

        self.assertFalse(refreshed)
        # A user-closed browser must NOT re-arm consent handling -- otherwise the next routine refresh
        # re-runs the slow first-time cookie path and re-shows the setup notice.
        self.assertTrue(manager.steam_pa_cookie_consent_prepared)

    async def test_steam_refresh_timeout_keeps_consent_prepared(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.steam_browser_profile_prepared = True
        manager.steam_pa_cookie_consent_prepared = True

        with patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(
                side_effect=BrowserAuthError(
                    "Steam Account browser session timed out before Central Market cookies were captured."
                )
            ),
        ):
            refreshed = await manager.refresh_browser_session(force_refresh=True)

        self.assertFalse(refreshed)
        # No auth failure -- timeout included -- may reset consent. Cookie consent persists in the
        # browser profile and is only invalidated by clearing cookies, so the flag stays prepared and
        # the next refresh stays on the fast, notice-free path.
        self.assertTrue(manager.steam_pa_cookie_consent_prepared)

    async def test_steam_refresh_validation_failure_keeps_consent_prepared(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.steam_browser_profile_prepared = True
        manager.steam_pa_cookie_consent_prepared = True
        manager.api_handler.validate_and_save_imported_session = AsyncMock(side_effect=MarketplaceAPIError("bad session"))

        with patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ):
            refreshed = await manager.refresh_browser_session(force_refresh=True)

        self.assertFalse(refreshed)
        self.assertTrue(manager.steam_pa_cookie_consent_prepared)

    async def test_account_mode_change_stops_monitor_and_clears_session(self):
        manager = self.make_task_manager()
        manager.api_handler.login_status = True
        manager.purchase_submission_enabled = True
        manager.steam_auto_reauth_enabled = True
        manager.saved_session_last_known_valid = True

        async def idle_checker():
            await asyncio.sleep(60)

        manager.checker = idle_checker
        await manager.start_checker()
        self.assertTrue(manager.checker_enabled)

        changed = await manager.change_account_mode(STEAM_BROWSER_MODE)

        self.assertTrue(changed)
        self.assertFalse(manager.checker_enabled)
        self.assertFalse(manager.purchase_submission_enabled)
        self.assertFalse(manager.api_handler.login_status)
        self.assertTrue(manager.api_handler.session_cleared)
        self.assertFalse(manager.steam_auto_reauth_enabled)
        self.assertFalse(manager.saved_session_last_known_valid)
        self.assertTrue(any("Marketplace session cleared" in event for event in manager.events))

    async def test_account_mode_change_clears_pending_buy_mode_resume(self):
        manager = self.make_task_manager()
        manager.api_handler.login_status = True
        manager.purchase_submission_enabled = False
        manager.buy_mode_resume_pending = True

        changed = await manager.change_account_mode(STEAM_BROWSER_MODE)

        self.assertTrue(changed)
        self.assertFalse(manager.buy_mode_resume_pending)
        self.assertFalse(manager.resume_buy_mode_after_refresh())
        self.assertFalse(manager.purchase_submission_enabled)

    async def test_deferred_auth_reset_clears_pending_buy_mode_resume(self):
        manager = self.make_task_manager()
        manager.purchase_in_progress = True
        manager.purchase_submission_enabled = False
        manager.buy_mode_resume_pending = True

        cleared = await manager.reset_authentication_context("Manual session reset")

        self.assertFalse(cleared)
        self.assertFalse(manager.buy_mode_resume_pending)
        self.assertFalse(manager.resume_buy_mode_after_refresh())
        self.assertFalse(manager.purchase_submission_enabled)
        self.assertEqual(manager.pending_auth_reset_reason, "Manual session reset")

    async def test_account_mode_change_during_purchase_defers_session_clear_until_chain_finishes(self):
        manager = self.make_task_manager()
        manager.api_handler.login_status = True
        manager.purchase_submission_enabled = True
        manager.steam_auto_reauth_enabled = True
        manager.saved_session_last_known_valid = True
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_buy(*args, **kwargs):
            started.set()
            await release.wait()
            return {"purchase_records": [], "events": [{"level": "success", "message": "buy chain done"}]}

        manager.api_handler.buy_item = slow_buy
        buy_task = asyncio.create_task(manager.buy_item([["10007", "1", "82500"]], adjust_pricing=False))
        await started.wait()

        changed = await manager.change_account_mode(STEAM_BROWSER_MODE)

        self.assertTrue(changed)
        self.assertTrue(manager.purchase_in_progress)
        self.assertFalse(manager.api_handler.session_cleared)
        self.assertIsNotNone(manager.pending_auth_reset_reason)

        release.set()
        await buy_task

        self.assertFalse(manager.purchase_in_progress)
        self.assertIsNone(manager.pending_auth_reset_reason)
        self.assertFalse(manager.api_handler.login_status)
        self.assertTrue(manager.api_handler.session_cleared)
        self.assertFalse(manager.steam_auto_reauth_enabled)
        self.assertFalse(manager.saved_session_last_known_valid)
        self.assertTrue(any("buy chain done" in event for event in manager.events))
        self.assertTrue(any("Marketplace session cleared" in event for event in manager.events))

    async def test_debug_session_invalidation_clears_session_but_keeps_running_monitor_and_buy_mode(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.api_handler.login_status = True
        manager.purchase_submission_enabled = True
        manager.saved_session_last_known_valid = True

        async def idle_checker():
            await asyncio.sleep(60)

        manager.checker = idle_checker
        await manager.start_checker()
        self.assertTrue(manager.checker_enabled)

        invalidated = manager.debug_invalidate_marketplace_session()

        self.assertTrue(invalidated)
        self.assertTrue(manager.checker_enabled)
        self.assertTrue(manager.purchase_submission_enabled)
        self.assertFalse(manager.api_handler.login_status)
        self.assertTrue(manager.api_handler.session_cleared)
        self.assertTrue(manager.debug_force_purchase_session_expired)
        self.assertFalse(manager.saved_session_last_known_valid)
        self.assertTrue(any("marketplace session cleared" in event for event in manager.events))

        await manager.stop_checker()

    async def test_debug_session_invalidation_enables_auto_reauth_for_online_steam_session(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.login_status = True
        manager.saved_session_last_known_valid = True

        invalidated = manager.debug_invalidate_marketplace_session()

        self.assertTrue(invalidated)
        self.assertTrue(manager.debug_force_purchase_session_expired)
        self.assertTrue(manager.steam_auto_reauth_enabled)
        self.assertFalse(manager.api_handler.login_status)
        self.assertTrue(manager.api_handler.session_cleared)
        self.assertFalse(manager.saved_session_last_known_valid)

    async def test_debug_toggle_steam_auto_reauth_requires_test_mode_and_steam_mode(self):
        manager = self.make_task_manager(test_mode_enabled=False)
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE

        self.assertIsNone(manager.debug_toggle_steam_auto_reauth())
        self.assertFalse(manager.steam_auto_reauth_enabled)

        manager = self.make_task_manager(test_mode_enabled=True)
        self.assertIsNone(manager.debug_toggle_steam_auto_reauth())
        self.assertFalse(manager.steam_auto_reauth_enabled)

        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        self.assertTrue(manager.debug_toggle_steam_auto_reauth())
        self.assertTrue(manager.steam_auto_reauth_enabled)
        self.assertFalse(manager.debug_toggle_steam_auto_reauth())
        self.assertFalse(manager.steam_auto_reauth_enabled)

    async def test_debug_reauthentication_check_simulates_purchase_expiry_and_pa_relogin(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.api_handler.login_status = True
        manager.purchase_submission_enabled = True
        manager.debug_invalidate_marketplace_session()
        manager.api_handler.login = AsyncMock(return_value=1)

        async def successful_pa_refresh(*_args, **_kwargs):
            manager.api_handler.login_status = True
            return True

        manager.refresh_pa_browser_session = AsyncMock(side_effect=successful_pa_refresh)

        recovered = await manager.debug_run_reauthentication_check()

        self.assertTrue(recovered)
        manager.api_handler.login.assert_not_called()
        manager.refresh_pa_browser_session.assert_awaited_once_with(force_refresh=True)
        self.assertTrue(manager.api_handler.login_status)
        self.assertTrue(manager.purchase_submission_enabled)
        self.assertFalse(manager.debug_force_purchase_session_expired)
        self.assertTrue(any("Simulated purchase response: login session expired" in event for event in manager.events))
        self.assertTrue(any("Re-authentication succeeded. Retrying purchase request" in event for event in manager.events))

    async def test_debug_reauthentication_check_forces_pa_browser_refresh_when_session_looks_valid(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.api_handler.login_status = True
        manager.purchase_submission_enabled = True
        manager.pa_browser_profile_prepared = True
        manager.api_handler.is_session_expired = AsyncMock(return_value=0)
        manager.api_handler.login = AsyncMock(return_value=1)
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)
        manager.debug_invalidate_marketplace_session()

        with patch(
            "bdo_marketplace_tools.services.task_manager.load_credentials",
            return_value=("user@example.com", "secret"),
        ), patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            recovered = await manager.debug_run_reauthentication_check()

        self.assertTrue(recovered)
        manager.api_handler.login.assert_not_called()
        manager.api_handler.is_session_expired.assert_not_awaited()
        browser_auth.assert_awaited_once()
        self.assertFalse(browser_auth.await_args.kwargs["auto_steam_login"])
        self.assertTrue(browser_auth.await_args.kwargs["auto_pa_login"])
        self.assertIsNone(browser_auth.await_args.kwargs["bootstrap_url"])
        self.assertFalse(manager.debug_force_purchase_session_expired)

    async def test_debug_reauthentication_check_uses_browser_refresh_in_steam_mode(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.purchase_submission_enabled = True
        manager.api_handler.login = AsyncMock(return_value=1)
        manager.refresh_browser_session = AsyncMock(return_value=True)

        recovered = await manager.debug_run_reauthentication_check()

        self.assertTrue(recovered)
        manager.api_handler.login.assert_not_called()
        manager.refresh_browser_session.assert_awaited_once_with(force_refresh=True)
        self.assertTrue(manager.purchase_submission_enabled)
        self.assertTrue(any("Re-authentication succeeded. Retrying purchase request" in event for event in manager.events))

    async def test_debug_reauthentication_check_forces_steam_browser_refresh_when_session_looks_valid(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.login_status = True
        manager.purchase_submission_enabled = True
        manager.steam_browser_profile_prepared = True
        manager.api_handler.is_session_expired = AsyncMock(return_value=0)
        manager.api_handler.login = AsyncMock(return_value=1)
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)
        manager.debug_invalidate_marketplace_session()

        with patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            recovered = await manager.debug_run_reauthentication_check()

        self.assertTrue(recovered)
        manager.api_handler.login.assert_not_called()
        manager.api_handler.is_session_expired.assert_not_awaited()
        browser_auth.assert_awaited_once()
        self.assertTrue(browser_auth.await_args.kwargs["auto_steam_login"])
        self.assertFalse(manager.debug_force_purchase_session_expired)

    async def test_pa_purchase_reauth_uses_browser_refresh(self):
        manager = self.make_task_manager()
        manager.api_handler.ensure_session_valid = AsyncMock(return_value=True)
        manager.refresh_pa_browser_session = AsyncMock(return_value=True)

        recovered = await manager._recover_purchase_session_for_retry()

        self.assertTrue(recovered)
        manager.api_handler.ensure_session_valid.assert_not_called()
        manager.refresh_pa_browser_session.assert_awaited_once()
        self.assertTrue(any("Re-authentication succeeded. Retrying purchase request" in event for event in manager.events))

    async def test_debug_clear_steam_initial_setup_status_is_test_mode_only(self):
        manager = self.make_task_manager(test_mode_enabled=False)
        manager.steam_browser_profile_prepared = True
        manager.steam_pa_cookie_consent_prepared = True
        manager.steam_auto_reauth_enabled = True

        with patch("bdo_marketplace_tools.services.task_manager.save_steam_browser_profile_prepared") as save_setup:
            self.assertFalse(manager.debug_clear_steam_initial_setup_status())

        save_setup.assert_not_called()
        self.assertTrue(manager.steam_browser_profile_prepared)
        self.assertTrue(manager.steam_pa_cookie_consent_prepared)
        self.assertTrue(manager.steam_auto_reauth_enabled)

        manager = self.make_task_manager(test_mode_enabled=True)
        manager.steam_browser_profile_prepared = True
        manager.steam_pa_cookie_consent_prepared = True
        manager.steam_auto_reauth_enabled = True

        with patch(
            "bdo_marketplace_tools.services.task_manager.save_steam_browser_profile_prepared",
            return_value=False,
        ) as save_setup:
            self.assertTrue(manager.debug_clear_steam_initial_setup_status())

        save_setup.assert_called_once_with(False)
        self.assertFalse(manager.steam_browser_profile_prepared)
        self.assertFalse(manager.steam_pa_cookie_consent_prepared)
        self.assertFalse(manager.steam_auto_reauth_enabled)
        self.assertTrue(any("Initial Steam setup status reset" in event for event in manager.events))

    async def test_debug_clear_steam_browser_cookies_clears_profile_without_logging_values(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.steam_browser_profile_prepared = True
        manager.steam_pa_cookie_consent_prepared = True
        manager.steam_auto_reauth_enabled = True

        with patch(
            "bdo_marketplace_tools.services.task_manager.clear_steam_browser_profile_cookies",
            new=AsyncMock(return_value=3),
        ) as cookie_clear, patch(
            "bdo_marketplace_tools.services.task_manager.save_steam_browser_profile_prepared",
            return_value=False,
        ) as save_prepared:
            result = await manager.debug_clear_steam_browser_cookies()

        self.assertTrue(result)
        cookie_clear.assert_awaited_once()
        save_prepared.assert_called_once_with(False)
        self.assertFalse(manager.steam_browser_profile_prepared)
        self.assertFalse(manager.steam_pa_cookie_consent_prepared)
        self.assertFalse(manager.steam_auto_reauth_enabled)
        self.assertTrue(any("Steam browser cookies cleared" in event for event in manager.events))
        self.assertTrue(any("3 cookies" in event for event in manager.events))
        self.assertFalse(any("steamLoginSecure" in event for event in manager.events))

    async def test_debug_clear_steam_browser_cookies_is_test_mode_only(self):
        manager = self.make_task_manager(test_mode_enabled=False)

        with patch("bdo_marketplace_tools.services.task_manager.clear_steam_browser_profile_cookies") as cookie_clear:
            result = await manager.debug_clear_steam_browser_cookies()

        self.assertFalse(result)
        cookie_clear.assert_not_called()

    async def test_debug_clear_market_cookies_keep_steam_login_rearms_reauth_without_steam_relogin(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.steam_browser_profile_prepared = True
        manager.steam_pa_cookie_consent_prepared = True
        manager._set_saved_session_last_known_valid(True)

        with patch(
            "bdo_marketplace_tools.services.task_manager.clear_market_cookies_keep_steam_login",
            new=AsyncMock(return_value=4),
        ) as cookie_clear:
            result = await manager.debug_clear_market_cookies_keep_steam_login()

        self.assertTrue(result)
        cookie_clear.assert_awaited_once()
        self.assertTrue(str(cookie_clear.await_args.kwargs["profile_path"]).endswith("steam-market"))
        # Re-armed for a fresh re-auth run, but the Steam setup/login is intentionally preserved.
        self.assertFalse(manager.steam_pa_cookie_consent_prepared)
        self.assertFalse(manager.saved_session_last_known_valid)
        self.assertTrue(manager.steam_browser_profile_prepared)
        self.assertTrue(any("kept Steam login" in event for event in manager.events))
        self.assertTrue(any("4 non-Steam cookies" in event for event in manager.events))
        self.assertFalse(any("steamLoginSecure" in event for event in manager.events))

    async def test_debug_clear_market_cookies_keep_steam_login_is_test_mode_only(self):
        manager = self.make_task_manager(test_mode_enabled=False)
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE

        with patch("bdo_marketplace_tools.services.task_manager.clear_market_cookies_keep_steam_login") as cookie_clear:
            result = await manager.debug_clear_market_cookies_keep_steam_login()

        self.assertFalse(result)
        cookie_clear.assert_not_called()

    async def test_debug_clear_market_cookies_keep_steam_login_requires_steam_mode(self):
        manager = self.make_task_manager(test_mode_enabled=True)  # defaults to Pearl Abyss mode

        with patch("bdo_marketplace_tools.services.task_manager.clear_market_cookies_keep_steam_login") as cookie_clear:
            result = await manager.debug_clear_market_cookies_keep_steam_login()

        self.assertFalse(result)
        cookie_clear.assert_not_called()
        self.assertTrue(any("only available in Steam Account mode" in event for event in manager.events))

    async def test_check_session_expired_honors_force_flag(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.api_handler.is_session_expired = AsyncMock(return_value=0)

        manager.debug_force_purchase_session_expired = True
        self.assertEqual(await manager._check_session_expired(), -1)
        manager.api_handler.is_session_expired.assert_not_called()

        # Without the override it delegates to the live check.
        manager.debug_force_purchase_session_expired = False
        self.assertEqual(await manager._check_session_expired(), 0)
        manager.api_handler.is_session_expired.assert_awaited_once()

    async def test_forced_expiry_makes_saved_session_invalid_so_browser_opens(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.debug_force_purchase_session_expired = True
        manager._api_has_session_cookies = lambda: True
        # Would report valid if consulted; the override must short-circuit it to invalid.
        manager.api_handler.is_session_expired = AsyncMock(return_value=0)

        self.assertFalse(await manager._saved_session_is_valid())
        manager.api_handler.is_session_expired.assert_not_called()

    async def test_debug_run_session_check_now_runs_real_reauth_when_forced_expired(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.debug_force_purchase_session_expired = True
        manager.refresh_pa_browser_session = AsyncMock(return_value=True)
        # The live check must not be consulted while expiry is forced.
        manager.api_handler.is_session_expired = AsyncMock(side_effect=AssertionError("live check should not run"))

        result = await manager.debug_run_session_check_now()

        self.assertTrue(result)
        # It went through the production handle_expired_session -> browser refresh path.
        manager.refresh_pa_browser_session.assert_awaited_once()
        # Recovered, so the override is dropped and later checks see the real session.
        self.assertFalse(manager.debug_force_purchase_session_expired)
        self.assertTrue(any("Re-authentication successful" in event for event in manager.events))

    async def test_scheduled_pa_reauth_does_not_clean_browser_cache(self):
        manager = self.make_task_manager()
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)

        with patch(
            "bdo_marketplace_tools.services.task_manager.load_credentials",
            return_value=("user@example.com", "secret"),
        ), patch(
            "bdo_marketplace_tools.services.task_manager.clean_disposable_browser_profile_cache",
        ) as cleanup, patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            recovered = await manager.handle_expired_session()

        self.assertTrue(recovered)
        cleanup.assert_not_called()
        browser_auth.assert_awaited_once()
        await manager.stop_login_status_checker()

    async def test_scheduled_steam_auto_reauth_does_not_clean_browser_cache(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.steam_auto_reauth_enabled = True
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)

        with patch(
            "bdo_marketplace_tools.services.task_manager.clean_disposable_browser_profile_cache",
        ) as cleanup, patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            recovered = await manager.handle_expired_session()

        self.assertTrue(recovered)
        cleanup.assert_not_called()
        browser_auth.assert_awaited_once()
        await manager.stop_login_status_checker()

    async def test_buy_retry_reauth_does_not_clean_browser_cache(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.steam_auto_reauth_enabled = True
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)

        with patch(
            "bdo_marketplace_tools.services.task_manager.clean_disposable_browser_profile_cache",
        ) as cleanup, patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            recovered = await manager._recover_purchase_session_for_retry(force_browser_refresh=True)

        self.assertTrue(recovered)
        cleanup.assert_not_called()
        browser_auth.assert_awaited_once()
        await manager.stop_login_status_checker()

    async def test_debug_run_session_check_now_reports_valid_when_not_forced(self):
        manager = self.make_task_manager(test_mode_enabled=True)
        manager.debug_force_purchase_session_expired = False
        manager.api_handler.is_session_expired = AsyncMock(return_value=0)
        manager.handle_expired_session = AsyncMock(side_effect=AssertionError("valid session must not re-auth"))

        result = await manager.debug_run_session_check_now()

        self.assertTrue(result)
        self.assertTrue(manager.api_handler.login_status)
        manager.api_handler.is_session_expired.assert_awaited_once()
        self.assertTrue(any("Session still valid" in event for event in manager.events))

    async def test_debug_run_session_check_now_is_test_mode_only(self):
        manager = self.make_task_manager(test_mode_enabled=False)
        manager.handle_expired_session = AsyncMock()

        result = await manager.debug_run_session_check_now()

        self.assertIsNone(result)
        manager.handle_expired_session.assert_not_called()

    async def test_clear_browser_session_cookies_pa_mode_works_without_test_mode(self):
        manager = self.make_task_manager(test_mode_enabled=False)
        self.assertTrue(manager.pa_browser_profile_prepared)

        with patch(
            "bdo_marketplace_tools.services.task_manager.clear_steam_browser_profile_cookies",
            new=AsyncMock(return_value=2),
        ) as cookie_clear:
            result = await manager.clear_browser_session_cookies()

        self.assertTrue(result)
        cookie_clear.assert_awaited_once()
        self.assertTrue(str(cookie_clear.await_args.kwargs["profile_path"]).endswith("pa-market"))
        self.assertFalse(manager.pa_browser_profile_prepared)
        self.assertTrue(any("Browser cookies cleared" in event for event in manager.events))
        self.assertTrue(any("2 cookies" in event for event in manager.events))

    async def test_clear_browser_session_cookies_steam_mode_resets_steam_state(self):
        manager = self.make_task_manager(test_mode_enabled=False)
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.steam_browser_profile_prepared = True
        manager.steam_pa_cookie_consent_prepared = True
        manager.steam_auto_reauth_enabled = True

        with patch(
            "bdo_marketplace_tools.services.task_manager.clear_steam_browser_profile_cookies",
            new=AsyncMock(return_value=5),
        ) as cookie_clear, patch(
            "bdo_marketplace_tools.services.task_manager.save_steam_browser_profile_prepared",
            return_value=False,
        ):
            result = await manager.clear_browser_session_cookies()

        self.assertTrue(result)
        cookie_clear.assert_awaited_once()
        self.assertTrue(str(cookie_clear.await_args.kwargs["profile_path"]).endswith("steam-market"))
        self.assertFalse(manager.steam_browser_profile_prepared)
        self.assertFalse(manager.steam_pa_cookie_consent_prepared)
        self.assertFalse(manager.steam_auto_reauth_enabled)

    def test_reset_steam_initial_setup_status_works_without_test_mode(self):
        manager = self.make_task_manager(test_mode_enabled=False)
        manager.steam_browser_profile_prepared = True
        manager.steam_pa_cookie_consent_prepared = True
        manager.steam_auto_reauth_enabled = True

        with patch(
            "bdo_marketplace_tools.services.task_manager.save_steam_browser_profile_prepared",
            return_value=False,
        ):
            result = manager.reset_steam_initial_setup_status()

        self.assertTrue(result)
        self.assertFalse(manager.steam_browser_profile_prepared)
        self.assertFalse(manager.steam_pa_cookie_consent_prepared)
        self.assertFalse(manager.steam_auto_reauth_enabled)
        self.assertTrue(any("Initial Steam setup status reset" in event for event in manager.events))

    async def test_steam_buy_expired_response_refreshes_browser_and_retries_once(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.purchase_submission_enabled = True
        expired_summary = {
            "purchase_records": [],
            "results": [{"item_id": "10007", "result_code": 2000}],
            "events": [{"level": "error", "message": "Login session expired."}],
        }
        success_summary = {
            "purchase_records": [{"item_id": "10007", "price": 82500, "count": 1, "result_code": 0}],
            "results": [{"item_id": "10007", "result_code": 0}],
            "events": [{"level": "success", "message": "Purchase succeeded after retry."}],
        }
        manager.api_handler.buy_item = AsyncMock(side_effect=[expired_summary, success_summary])
        manager.refresh_browser_session = AsyncMock(return_value=True)

        with patch.object(manager, "save_local_data") as save_mock:
            await manager.buy_item([["10007", "1", "82500"]], adjust_pricing=False)

        self.assertEqual(manager.api_handler.buy_item.await_count, 2)
        manager.refresh_browser_session.assert_awaited_once()
        self.assertTrue(manager.purchase_submission_enabled)
        self.assertEqual(manager.session_successful_purchases, 1)
        self.assertTrue(any("Purchase succeeded after retry" in event for event in manager.events))
        save_mock.assert_awaited_once()

    async def test_steam_buy_expired_response_uses_enabled_auto_reauth_flow(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.steam_auto_reauth_enabled = True
        manager.purchase_submission_enabled = True
        manager.api_handler.validate_and_save_imported_session = AsyncMock(return_value=True)
        expired_summary = {
            "purchase_records": [],
            "results": [{"item_id": "10007", "result_code": 2000}],
            "events": [{"level": "error", "message": "Login session expired."}],
        }
        success_summary = {
            "purchase_records": [{"item_id": "10007", "price": 82500, "count": 1, "result_code": 0}],
            "results": [{"item_id": "10007", "result_code": 0}],
            "events": [{"level": "success", "message": "Purchase succeeded after auto re-auth."}],
        }
        manager.api_handler.buy_item = AsyncMock(side_effect=[expired_summary, success_summary])

        with patch(
            "bdo_marketplace_tools.services.task_manager.acquire_market_cookies",
            new=AsyncMock(return_value=[{"name": "TradeAuth_Session", "value": "ok"}]),
        ) as browser_auth:
            with patch.object(manager, "save_local_data"):
                await manager.buy_item([["10007", "1", "82500"]], adjust_pricing=False)

        self.assertEqual(manager.api_handler.buy_item.await_count, 2)
        browser_auth.assert_awaited_once()
        self.assertTrue(browser_auth.await_args.kwargs["auto_steam_login"])
        self.assertTrue(manager.purchase_submission_enabled)
        self.assertTrue(any("Purchase succeeded after auto re-auth" in event for event in manager.events))
        await manager.stop_login_status_checker()

    async def test_pa_buy_preflight_browser_verification_uses_manual_browser_fallback(self):
        manager = self.make_task_manager()
        manager.purchase_submission_enabled = True
        manager.refresh_pa_browser_session = AsyncMock(return_value=True)
        blocked_summary = {
            "purchase_records": [],
            "results": [],
            "events": [
                {
                    "level": "error",
                    "message": "Purchase aborted: Pearl Abyss login page requires browser verification before password login.",
                }
            ],
        }
        success_summary = {
            "purchase_records": [{"item_id": "10007", "price": 82500, "count": 1, "result_code": 0}],
            "results": [{"item_id": "10007", "result_code": 0}],
            "events": [{"level": "success", "message": "Purchase succeeded after PA browser refresh."}],
        }
        manager.api_handler.buy_item = AsyncMock(side_effect=[blocked_summary, success_summary])

        with patch.object(manager, "save_local_data") as save_mock:
            await manager.buy_item([["10007", "1", "82500"]], adjust_pricing=False)

        self.assertEqual(manager.api_handler.buy_item.await_count, 2)
        manager.refresh_pa_browser_session.assert_awaited_once()
        self.assertEqual(manager.session_successful_purchases, 1)
        self.assertTrue(any("Purchase succeeded after PA browser refresh" in event for event in manager.events))
        save_mock.assert_awaited_once()

    async def test_pa_buy_preflight_auth_failure_pauses_buy_mode(self):
        manager = self.make_task_manager()
        manager.purchase_submission_enabled = True
        manager.refresh_pa_browser_session = AsyncMock(return_value=False)
        auth_failure_summary = {
            "auth_failed": True,
            "purchase_records": [],
            "results": [],
            "events": [
                {
                    "level": "error",
                    "message": "Purchase aborted: login session is invalid and re-authentication failed.",
                }
            ],
        }
        manager.api_handler.buy_item = AsyncMock(return_value=auth_failure_summary)

        await manager.buy_item([["10007", "1", "82500"]], adjust_pricing=False)

        manager.api_handler.buy_item.assert_awaited_once()
        manager.refresh_pa_browser_session.assert_awaited_once()
        self.assertFalse(manager.purchase_submission_enabled)
        self.assertTrue(any("will resume automatically once the session is refreshed" in event for event in manager.events))

    async def test_pa_buy_structured_auth_failure_pauses_when_message_has_no_marker(self):
        manager = self.make_task_manager()
        manager.purchase_submission_enabled = True
        manager.refresh_pa_browser_session = AsyncMock(return_value=False)
        auth_failure_summary = {
            "auth_failed": True,
            "purchase_records": [],
            "results": [],
            "events": [
                {
                    "level": "error",
                    "message": "Purchase aborted: login page did not provide an OAuth return URL.",
                }
            ],
        }
        manager.api_handler.buy_item = AsyncMock(return_value=auth_failure_summary)

        await manager.buy_item([["10007", "1", "82500"]], adjust_pricing=False)

        manager.api_handler.buy_item.assert_awaited_once()
        manager.refresh_pa_browser_session.assert_awaited_once()
        self.assertFalse(manager.purchase_submission_enabled)
        self.assertTrue(any("will resume automatically once the session is refreshed" in event for event in manager.events))

    async def test_pa_buy_preflight_browser_refresh_failure_pauses_buy_mode(self):
        manager = self.make_task_manager()
        manager.purchase_submission_enabled = True
        manager.refresh_pa_browser_session = AsyncMock(return_value=False)
        blocked_summary = {
            "purchase_records": [],
            "results": [],
            "events": [
                {
                    "level": "error",
                    "message": "Purchase aborted: Pearl Abyss login page requires browser verification before password login.",
                }
            ],
        }
        manager.api_handler.buy_item = AsyncMock(return_value=blocked_summary)

        await manager.buy_item([["10007", "1", "82500"]], adjust_pricing=False)

        manager.api_handler.buy_item.assert_awaited_once()
        manager.refresh_pa_browser_session.assert_awaited_once()
        self.assertFalse(manager.purchase_submission_enabled)
        self.assertTrue(any("will resume automatically once the session is refreshed" in event for event in manager.events))

    async def test_pa_buy_browser_verification_summary_with_prior_purchase_does_not_retry_batch(self):
        manager = self.make_task_manager()
        manager.purchase_submission_enabled = True
        manager.refresh_pa_browser_session = AsyncMock(return_value=True)
        partial_summary = {
            "purchase_records": [{"item_id": "10007", "price": 82500, "count": 1, "result_code": 0}],
            "results": [{"item_id": "10007", "result_code": 0}],
            "events": [
                {"level": "success", "message": "Purchase request succeeded for 10007 at 82500 silver."},
                {
                    "level": "error",
                    "message": "Re-authentication failed: Pearl Abyss login page requires browser verification before password login.",
                },
            ],
        }
        manager.api_handler.buy_item = AsyncMock(return_value=partial_summary)

        with patch.object(manager, "save_local_data"):
            await manager.buy_item([["10007", "2", "82500"]], adjust_pricing=False)

        manager.api_handler.buy_item.assert_awaited_once()
        manager.refresh_pa_browser_session.assert_not_called()
        self.assertEqual(manager.session_successful_purchases, 1)
        self.assertFalse(manager.purchase_submission_enabled)

    async def test_login_status_checker_combines_expired_session_with_reauth_result(self):
        manager = self.make_task_manager()
        manager.api_handler.is_session_expired = AsyncMock(return_value=-1)
        manager.api_handler.login = AsyncMock(return_value=0)
        manager.refresh_pa_browser_session = AsyncMock(return_value=False)

        with patch("bdo_marketplace_tools.services.task_manager.asyncio.sleep", new=AsyncMock(return_value=None)):
            await manager.login_status_checker()

        manager.api_handler.login.assert_not_called()
        manager.refresh_pa_browser_session.assert_awaited_once()
        event_text = "\n".join(manager.events)
        self.assertIn("Session expired. Attempting Pearl Abyss Account browser re-authentication.", event_text)
        self.assertIn("Session expired. Re-authentication failed.", event_text)

    async def test_expired_pa_session_pauses_buy_mode_when_reauth_fails(self):
        manager = self.make_task_manager()
        manager.purchase_submission_enabled = True
        manager.api_handler.login = AsyncMock(return_value=0)
        manager.refresh_pa_browser_session = AsyncMock(return_value=False)

        recovered = await manager.handle_expired_session()

        self.assertFalse(recovered)
        manager.api_handler.login.assert_not_called()
        manager.refresh_pa_browser_session.assert_awaited_once()
        self.assertFalse(manager.purchase_submission_enabled)
        self.assertTrue(manager.buy_mode_resume_pending)
        self.assertTrue(any("will resume automatically once the session is refreshed" in event for event in manager.events))

    async def test_expired_steam_session_pauses_and_arms_buy_mode_when_reauth_fails(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.steam_browser_profile_prepared = True
        manager.purchase_submission_enabled = True
        manager.refresh_browser_session = AsyncMock(return_value=False)

        recovered = await manager.handle_expired_session()

        self.assertFalse(recovered)
        manager.refresh_browser_session.assert_awaited_once()
        # Steam must match PA: paused AND armed so a later refresh auto-resumes buy mode.
        self.assertFalse(manager.purchase_submission_enabled)
        self.assertTrue(manager.buy_mode_resume_pending)
        self.assertTrue(any("will resume automatically once the session is refreshed" in event for event in manager.events))

    async def test_recover_purchase_session_steam_failure_pauses_and_arms_buy_mode(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.purchase_submission_enabled = True
        manager.refresh_browser_session = AsyncMock(return_value=False)

        recovered = await manager._recover_purchase_session_for_retry()

        self.assertFalse(recovered)
        manager.refresh_browser_session.assert_awaited_once()
        self.assertFalse(manager.purchase_submission_enabled)
        self.assertTrue(manager.buy_mode_resume_pending)

    async def test_login_steam_refresh_failure_pauses_and_arms_buy_mode(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.purchase_submission_enabled = True
        manager.api_handler.is_session_expired = AsyncMock(return_value=-1)
        manager.refresh_browser_session = AsyncMock(return_value=False)

        await manager.login()

        manager.refresh_browser_session.assert_awaited_once()
        self.assertFalse(manager.purchase_submission_enabled)
        self.assertTrue(manager.buy_mode_resume_pending)

    async def test_paused_buy_mode_auto_resumes_after_refresh(self):
        manager = self.make_task_manager()
        manager.purchase_submission_enabled = True
        # App-driven pause (e.g. session expired mid-run) marks buy mode for auto-resume.
        self.assertTrue(manager.pause_buy_mode_for_session_refresh("Session expired."))
        self.assertFalse(manager.purchase_submission_enabled)
        self.assertTrue(manager.buy_mode_resume_pending)

        # A subsequent successful refresh resumes buy mode automatically (continuity).
        self.assertTrue(manager.resume_buy_mode_after_refresh())
        self.assertTrue(manager.purchase_submission_enabled)
        self.assertFalse(manager.buy_mode_resume_pending)
        self.assertTrue(any("buy mode resumed" in event.lower() for event in manager.events))

    async def test_resume_does_not_re_enable_user_disabled_buy_mode(self):
        manager = self.make_task_manager()
        # Buy mode off with nothing pending (e.g. the user turned it off, or watch-only).
        manager.purchase_submission_enabled = False
        manager.buy_mode_resume_pending = False
        self.assertFalse(manager.resume_buy_mode_after_refresh())
        self.assertFalse(manager.purchase_submission_enabled)

    async def test_expired_pa_session_uses_browser_refresh(self):
        manager = self.make_task_manager()
        manager.api_handler.login = AsyncMock(return_value=1)
        manager.refresh_pa_browser_session = AsyncMock(return_value=True)

        recovered = await manager.handle_expired_session()

        self.assertTrue(recovered)
        manager.api_handler.login.assert_not_called()
        manager.refresh_pa_browser_session.assert_awaited_once()
        self.assertTrue(any("Session expired. Re-authentication successful" in event for event in manager.events))

    async def test_login_status_checker_prompts_browser_refresh_in_steam_mode(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.steam_browser_profile_prepared = False
        manager.steam_auto_reauth_enabled = False
        manager.api_handler.is_session_expired = AsyncMock(return_value=-1)
        manager.api_handler.login = AsyncMock(return_value=1)
        manager.purchase_submission_enabled = True
        manager.refresh_browser_session = AsyncMock(return_value=True)

        with patch("bdo_marketplace_tools.services.task_manager.asyncio.sleep", new=AsyncMock(return_value=None)):
            await manager.login_status_checker()

        manager.api_handler.login.assert_not_called()
        manager.refresh_browser_session.assert_not_called()
        self.assertFalse(manager.purchase_submission_enabled)
        self.assertTrue(manager.buy_mode_resume_pending)
        self.assertTrue(any("Steam Account refresh required" in event for event in manager.events))

    async def test_expired_steam_session_auto_reauths_when_initial_setup_is_complete(self):
        manager = self.make_task_manager()
        manager.account_mode = STEAM_BROWSER_MODE
        manager.api_handler.account_mode = STEAM_BROWSER_MODE
        manager.steam_browser_profile_prepared = True
        manager.steam_auto_reauth_enabled = False
        manager.purchase_submission_enabled = True

        async def refresh_side_effect(*args, **kwargs):
            manager.api_handler.login_status = True
            return True

        manager.refresh_browser_session = AsyncMock(side_effect=refresh_side_effect)

        recovered = await manager.handle_expired_session()

        self.assertTrue(recovered)
        manager.refresh_browser_session.assert_awaited_once_with(auto_steam_login=True)
        self.assertTrue(manager.purchase_submission_enabled)
        self.assertTrue(manager.api_handler.login_status)
        self.assertTrue(any("automatic Steam Account re-authentication" in event for event in manager.events))


class TextualAppTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self, launch_mode="live"):
        stats_temp = tempfile.TemporaryDirectory()
        self.addCleanup(stats_temp.cleanup)
        stats_db_path = Path(stats_temp.name) / ("stats.test.db" if launch_mode == "test" else "stats.db")
        with patch("bdo_marketplace_tools.services.task_manager._load_local_data", return_value=LOCAL_DATA.copy()), patch(
            "bdo_marketplace_tools.services.task_manager.load_account_mode",
            return_value=PA_CREDENTIALS_MODE,
        ), patch("bdo_marketplace_tools.services.task_manager.load_steam_browser_profile_prepared", return_value=True):
            manager = BackgroundTasks(
                FakeAPI(),
                test_mode_enabled=launch_mode == "test",
                persist_ui_settings=False,
            )
        manager.stats_db_path = stats_db_path
        manager.legacy_stats_path = None
        self.addAsyncCleanup(manager.flush_stats_writes)
        app = MarketplaceToolsApp(manager, manager.api_handler, launch_mode=launch_mode)
        # UI tests must not perform the on-mount network update check; the startup-check
        # behavior is covered by the focused BackgroundTasks update tests instead.
        app.startup_update_check = AsyncMock()
        # Cosmetic animation timers (pulse/blink/celebration) are disabled so renders stay
        # deterministic; the animation tests call the animation methods directly.
        app.animations_enabled = False
        return app

    async def test_app_launches_and_navigates_tabs(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 40)) as pilot:
                self.assertEqual(app.current_view, "dashboard")
                self.assertEqual(app.theme, DEFAULT_THEME)
                self.assertEqual(len(list(app.query("#tabs .nav-tab"))), 4)
                self.assertEqual(app.query_one("#tab-settings", Static).content, "Settings")
                self.assertNotIn("Settings", [str(tab.content) for tab in app.query("#tabs .nav-tab")])
                self.assertEqual(len(list(app.query("#topbar-brand-divider"))), 1)
                self.assertEqual(app.query_one("#topbar-brand-divider", Static).content, "│")
                self.assertEqual(len(list(app.query("#topbar-settings-divider"))), 1)
                self.assertEqual(app.query_one("#brand", Static).content, "Marketplace Tools")
                self.assertEqual(app.query_one("#build-info", Static).content, f"v{APP_VERSION}")
                self.assertLessEqual(
                    len(str(app.query_one("#build-info", Static).content)),
                    int(len(f"build v{APP_VERSION}") * 0.7),
                )
                self.assertNotIn("BETA", str(app.query_one("#brand", Static).content))
                self.assertTrue(app.query_one("#tab-dashboard").has_class("nav-tab-active"))
                self.assertTrue(app.query_one("#tab-dashboard").styles.text_style.bold)
                self.assertFalse(app.query_one("#tab-dashboard").styles.text_style.underline)
                self.assertEqual(app.query_one("#banner").render(), BANNER_ART)
                self.assertTrue(app.query_one("#welcome-card").display)
                await pilot.press("s")
                self.assertEqual(app.current_view, "settings")
                self.assertFalse(app.query_one("#welcome-card").display)
                self.assertTrue(app.query_one("#tab-settings").has_class("nav-tab-active"))
                self.assertFalse(app.query_one("#tab-dashboard").has_class("nav-tab-active"))
                self.assertEqual(app.query_one("#settings-danger-card").border_title, "Danger zone")
                self.assertIn("Update", str(app.query_one("#settings-update", Static).render()))
                self.assertEqual(len(list(app.query(".settings-chip"))), 2)
                self.assertEqual(len(list(app.query(".stats-tile"))), 0)
                self.assertEqual(len(list(app.query("#settings-config-list"))), 0)
                self.assertNotIn("Configuration", str(app.query_one("#content").render()))
                await pilot.press("2")
                self.assertEqual(app.current_view, "wallet")
                self.assertIn(("wallet", "Inventory"), app.NAV_ITEMS)
                self.assertTrue(app.query_one("#tab-wallet").has_class("nav-tab-active"))
                await pilot.press("3")
                self.assertEqual(app.current_view, "stats")
                self.assertEqual(len(list(app.query("#stats-output"))), 0)
                self.assertEqual(len(list(app.query(".stats-chip"))), 4)
                await pilot.press("escape")
                self.assertEqual(app.current_view, "dashboard")
                self.assertTrue(app.query_one("#welcome-card").display)
                await pilot.press("1")
                self.assertEqual(app.current_view, "dashboard")
                await pilot.click("#tab-settings")
                await pilot.pause()
                self.assertEqual(app.current_view, "settings")
                self.assertTrue(app.query_one("#tab-settings").has_class("nav-tab-active"))
                await pilot.click("#tab-stats")
                await pilot.pause()
                self.assertEqual(app.current_view, "stats")
                self.assertTrue(app.query_one("#tab-stats").has_class("nav-tab-active"))

    async def test_stats_view_renders_trend_charts(self):
        def fake_trends(days, **kwargs):
            today = date.today()
            return {
                "daily": [
                    {
                        "day": today - timedelta(days=offset),
                        "detected": index + 1,
                        "purchased": 1 if index % 3 == 0 else 0,
                        "scans": 10 + index,
                    }
                    for index, offset in enumerate(range(days - 1, -1, -1))
                ],
                "weekday": {"detected": [0] * 7, "purchased": [0] * 7},
                "hourly": [[0] * 24 for _ in range(7)],
            }

        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)), patch(
            "bdo_marketplace_tools.storage.stats_db.load_trends", side_effect=fake_trends
        ) as load_trends_mock:
            async with app.run_test(size=(120, 50)) as pilot:
                await pilot.press("3")
                self.assertEqual(app.current_view, "stats")
                self.assertEqual(
                    str(app.query_one("#stats-trends-title", Static).content), "Daily activity · 30 days"
                )
                chart_titles = [str(title.content) for title in app.query(".stats-chart-title")]
                self.assertNotIn("Last 7 days", chart_titles)
                self.assertIn("Busiest days", chart_titles)
                self.assertIn("Listing hours", chart_titles)
                self.assertEqual(list(app.query("#stats-chart-mini")), [])
                self.assertIsNone(app.query_one("#stats-chart-daily", Static).border_title)
                # The 7/30 toggle is gone; the hero always loads the 30-day window.
                self.assertEqual(list(app.query("#stats-range-7")), [])
                self.assertEqual(list(app.query("#stats-range-30")), [])
                self.assertEqual(load_trends_mock.call_args_list[0].args[0], 30)
                daily_chart = app.query_one("#stats-chart-daily", DailyActivityChart)
                first_tooltip = daily_chart.tooltip_for_day(0)
                self.assertIn((date.today() - timedelta(days=29)).strftime("%b %d, %Y (%a)"), first_tooltip)
                self.assertIn("Detected: 1", first_tooltip)
                self.assertIn("Purchased: 1", first_tooltip)
                self.assertIn("Scans: 10", first_tooltip)
                self.assertEqual(daily_chart._day_index_for_x(daily_chart._column_start), 0)
                weekday_text = str(app.query_one("#stats-chart-weekday", Static).render())
                self.assertIn("Mon", weekday_text)
                hours_text = str(app.query_one("#stats-chart-hours", Static).render())
                self.assertIn("Listing", chart_titles[-1])
                self.assertIn("less", hours_text)
                daily_text = str(daily_chart.render())
                self.assertIn("offline", daily_text)
                self.assertIn("monitored", daily_text)

    async def test_daily_chart_tooltip_appears_after_near_instant_delay(self):
        def fake_trends(days, **kwargs):
            today = date.today()
            return {
                "daily": [
                    {
                        "day": today - timedelta(days=offset),
                        "detected": index + 1,
                        "purchased": 1,
                        "scans": 10,
                    }
                    for index, offset in enumerate(range(days - 1, -1, -1))
                ],
                "weekday": {"detected": [0] * 7, "purchased": [0] * 7},
                "hourly": [[0] * 24 for _ in range(7)],
            }

        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)), patch(
            "bdo_marketplace_tools.storage.stats_db.load_trends", side_effect=fake_trends
        ):
            async with app.run_test(size=(120, 50), tooltips=True) as pilot:
                await pilot.press("3")
                daily_chart = app.query_one("#stats-chart-daily", DailyActivityChart)

                self.assertTrue(await pilot.hover(daily_chart, offset=(daily_chart._column_start, 1)))
                await pilot.pause(app.TOOLTIP_DELAY + 0.05)

                tooltip = app.query_one("#textual-tooltip", Tooltip)
                self.assertTrue(tooltip.display)
                self.assertIn((date.today() - timedelta(days=29)).strftime("%b %d, %Y (%a)"), str(tooltip.render()))

    def test_daily_axis_labels_mark_month_changes(self):
        # A month boundary that falls between the 5-day ticks must still be labelled.
        end = date(2026, 6, 3)
        daily = [{"day": end - timedelta(days=offset)} for offset in range(29, -1, -1)]
        labels = str(charts_module._day_axis_labels(daily, 2, 2, 1))
        self.assertIn("May 5", labels)
        self.assertIn("Jun 1", labels)
        self.assertNotIn("May05", labels)

    async def test_stats_view_surfaces_trend_load_failure(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)), patch(
            "bdo_marketplace_tools.storage.stats_db.load_trends",
            side_effect=RuntimeError("database is locked"),
        ):
            async with app.run_test(size=(120, 50)) as pilot:
                await pilot.press("3")
                self.assertEqual(app.current_view, "stats")
                daily_chart = str(app.query_one("#stats-chart-daily", Static).render())
                self.assertIn("Stats charts unavailable", daily_chart)
                weekday_chart_text = str(app.query_one("#stats-chart-weekday", Static).render())
                self.assertIn("Stats charts unavailable", weekday_chart_text)

                app.refresh_stats_trends()
                chart_errors = [
                    event for event in app.task_manager.events if "Stats charts failed to load" in event
                ]
                self.assertEqual(len(chart_errors), 1)
                self.assertIn("database is locked", chart_errors[0])

    async def test_dashboard_banner_shows_when_terminal_is_large_enough(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(150, 45)):
                self.assertEqual(app.current_view, "dashboard")
                self.assertTrue(app.query_one("#welcome-card").display)
                self.assertEqual(app.query_one("#banner").render(), BANNER_ART)
                self.assertTrue(app.query_one("#banner").display)
                self.assertFalse(app.query_one("#welcome-card").has_class("-compact"))

                # Running only flips the footer text; the banner stays (auto-collapse removed).
                app.task_manager.checker_enabled = True
                app.refresh_live_widgets()
                self.assertTrue(app.query_one("#welcome-card").display)
                self.assertTrue(app.query_one("#banner").display)
                self.assertFalse(app.query_one("#welcome-card").has_class("-compact"))
                footer_text = str(app.query_one("#welcome-footer", Static).render())
                self.assertIn("Running", footer_text)
                app.task_manager.checker_enabled = False
                app.refresh_live_widgets()
                self.assertTrue(app.query_one("#banner").display)

    async def test_dashboard_animations_pulse_and_blink(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(150, 45)) as pilot:
                app.animations_enabled = True
                app._zzz_step = 0  # pin the sleep trail so banner equality is deterministic
                app.refresh_live_widgets()

                # Idle mascot dozes (eyes closed). Most blink ticks are skipped so the peek
                # stays rare (~every 8-12s); it only opens when the countdown reaches zero.
                self.assertEqual(app.query_one("#banner").render(), BANNER_BLINK_ART)
                app._idle_peek_countdown = 2
                app.blink_mascot()
                self.assertFalse(app._mascot_blinking)  # throttled — no peek this tick
                self.assertEqual(app.query_one("#banner").render(), BANNER_BLINK_ART)

                # Eyes crack open once the countdown elapses.
                app._idle_peek_countdown = 0
                app.blink_mascot()
                self.assertEqual(app.query_one("#banner").render(), BANNER_ART)  # drowsy peek
                await pilot.pause(1.5)  # the peek is held ~1.35s
                self.assertFalse(app._mascot_blinking)  # the timer ended the peek
                app._zzz_step = 0
                app.refresh_mascot_rest()
                self.assertEqual(app.query_one("#banner").render(), BANNER_BLINK_ART)

                # Idle: greeting sits under the mascot; state rides the divider rule.
                greeting = str(app.query_one("#welcome-greeting", Static).render())
                status = str(app.query_one("#welcome-footer", Static).render())
                self.assertIn("Idle", status)
                self.assertIn(app.time_greeting(), greeting)
                self.assertIn(
                    app.time_greeting(), ("Good morning!", "Good afternoon!", "Good evening!", "Up late?")
                )
                self.assertIn("all-time", status)

                # Idle chatter stays on the time greeting — it does not rotate.
                app.advance_running_quip()
                idle_greeting = str(app.query_one("#welcome-greeting", Static).render())
                self.assertIn(app.time_greeting(), idle_greeting)

                # The empty step lingers (the long gap between sets) before the next z.
                app._zzz_step = 0
                app._zzz_gap = 0
                app.advance_mascot_zzz()
                self.assertEqual(app._zzz_step, 0)  # still blank — dwelling in the gap

                # The sleep clock freezes during a peek, so no z-reveals are lost.
                app._mascot_blinking = True
                app._zzz_step = 1
                app._zzz_gap = 0
                app.advance_mascot_zzz()
                self.assertEqual(app._zzz_step, 1)  # frozen — did not advance under the guard
                app._mascot_blinking = False

                # Once the gap elapses the trail builds a z at a time.
                app._zzz_step = 0
                app._zzz_gap = app.ZZZ_GAP_TICKS - 1
                app.advance_mascot_zzz()
                self.assertEqual(app._zzz_step, 1)
                self.assertIn("z", app.query_one("#banner").render())

                app._zzz_step = len(MASCOT_ZZZ_TRAIL)
                app.refresh_mascot_rest()
                self.assertIn("Z", app.query_one("#banner").render())  # full trail
                app._zzz_step = 0
                app.refresh_mascot_rest()
                self.assertEqual(app.query_one("#banner").render(), BANNER_BLINK_ART)  # reset

                # Running: mascot wakes (eyes open), status spins on the divider, the chatter
                # rotates above it, and the strip switches to the session stats.
                app.task_manager.checker_enabled = True
                app.refresh_live_widgets()
                self.assertEqual(app.query_one("#banner").render(), BANNER_ART)  # awake
                greeting = str(app.query_one("#welcome-greeting", Static).render())
                status = str(app.query_one("#welcome-footer", Static).render())
                self.assertIn("⠋", status)
                self.assertIn("Running", status)
                self.assertIn("watching the marketplace", greeting)
                self.assertNotIn("all-time", status)
                self.assertIn("bought", status)
                self.assertIn("spent", status)
                app.advance_running_pulse()
                self.assertIn("⠙", str(app.query_one("#welcome-footer", Static).render()))
                app.advance_running_quip()
                greeting = str(app.query_one("#welcome-greeting", Static).render())
                self.assertNotIn("watching the marketplace", greeting)
                self.assertTrue(any(quip in greeting for quip in RUNNING_QUIPS[1:]))

                # Awake blink: eyes close briefly, then reopen (opposite of the idle doze).
                app.blink_mascot()
                self.assertEqual(app.query_one("#banner").render(), BANNER_BLINK_ART)
                await pilot.pause(0.3)
                self.assertEqual(app.query_one("#banner").render(), BANNER_ART)

                app.task_manager.checker_enabled = False

    async def test_mascot_flashes_star_eyes_on_successful_buy(self):
        app = self.make_app()
        app.task_manager.session_successful_purchases = 2  # a pre-existing (loaded) count
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(120, 40)) as pilot:
                app.animations_enabled = True
                await pilot.pause()
                # A count present at load is adopted as the baseline — no reaction fires.
                self.assertEqual(app._celebrated_purchases, 2)
                self.assertFalse(app._mascot_celebrating)

                # A genuine new buy flashes the star-eyes frame.
                app.task_manager.session_successful_purchases = 3
                app.refresh_live_widgets()
                await pilot.pause()
                self.assertTrue(app._mascot_celebrating)
                self.assertEqual(app.query_one("#banner", Static).render(), BANNER_STAR_ART)

                # The celebration reverts the mascot to its rest frame.
                app._end_mascot_celebrate()
                self.assertFalse(app._mascot_celebrating)
                self.assertNotEqual(app.query_one("#banner", Static).render(), BANNER_STAR_ART)

    async def test_buy_badges_pin_right_of_chatter_row_without_shifting_it(self):
        app = self.make_app()  # animations off: badges render from the count regardless
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                # No buys: the chatter carries no badges and the overlay is empty.
                greeting = str(app.query_one("#welcome-greeting", Static).render())
                self.assertNotIn("✦", greeting)
                self.assertEqual(str(app.query_one("#buy-badges", Static).render()).strip(), "")
                chatter_region = app.query_one("#welcome-greeting").region
                card_bottom = app.query_one("#welcome-card").region.bottom
                footer_present = app.query_one("#welcome-footer").region.height > 0

                # Buys populate the overlay, NOT the chatter text, and the chatter never
                # moves — its region is byte-identical with and without badges.
                app.task_manager.session_successful_purchases = 3
                app.refresh_live_widgets()
                await pilot.pause()
                self.assertNotIn("✦", str(app.query_one("#welcome-greeting", Static).render()))
                badges = app.query_one("#buy-badges")
                self.assertEqual(str(badges.render()).count("✦"), 3)
                self.assertEqual(app.query_one("#welcome-greeting").region, chatter_region)
                # Same row as the chatter, right-aligned — and the footer below is untouched.
                self.assertEqual(badges.region.y, chatter_region.y)
                self.assertEqual(badges.region.right, chatter_region.right)
                self.assertLess(badges.region.bottom, card_bottom - 1)  # above the footer
                self.assertTrue(footer_present and app.query_one("#welcome-footer").region.height > 0)

                # Past the cap it collapses to a compact count; zero buys renders nothing.
                app.task_manager.session_successful_purchases = 15
                self.assertEqual(str(app.render_buy_badges()), "✦ ×15")
                app.task_manager.session_successful_purchases = 0
                self.assertIsNone(app.render_buy_badges())

    async def test_purchase_strip_set_piece_shows_price_and_rolls(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(120, 40)) as pilot:
                app.animations_enabled = True
                app.task_manager.checker_enabled = True
                app.refresh_live_widgets()  # baseline: 0 buys, no reaction
                await pilot.pause()
                self.assertFalse(app._footer_celebrating)

                # A buy lands: the set piece takes over the strip immediately. The call
                # returns at once (non-blocking) — it never waits on the buy loop.
                app.task_manager.session_successful_purchases = 1
                app.task_manager.session_silver_spent = 890_000_000
                app.refresh_live_widgets()
                self.assertTrue(app._footer_celebrating)
                self.assertIn("BOUGHT", str(app.query_one("#welcome-footer", Static).render()))

                # Frame 2 names the count and the batch price.
                app._show_strip_coins()
                coins = str(app.query_one("#welcome-footer", Static).render())
                self.assertIn("1 outfit secured", coins)
                self.assertIn("890,000,000", coins)

                # Drive the count-up to its end; the final spent value holds briefly before
                # the strip settles back to the live stats.
                with patch.object(app, "set_timer", wraps=app.set_timer) as timer_spy:
                    app._roll_step = app.STRIP_ROLL_STEPS
                    app._advance_strip_rollup()
                timer_spy.assert_any_call(app.STRIP_ROLL_SETTLE_SECONDS, app._end_strip_celebration)
                self.assertTrue(app._footer_celebrating)
                self.assertIn("spent", str(app.query_one("#welcome-footer", Static).render()))
                app._end_strip_celebration()
                self.assertFalse(app._footer_celebrating)
                footer = str(app.query_one("#welcome-footer", Static).render())
                self.assertIn("Running", footer)
                self.assertIn("890M", footer)
                app.task_manager.checker_enabled = False

    async def test_purchase_strip_set_piece_folds_in_buys_mid_animation(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(120, 40)):
                app.animations_enabled = True
                app.task_manager.checker_enabled = True
                app.refresh_live_widgets()

                # First buy starts the set piece.
                app.task_manager.session_successful_purchases = 1
                app.task_manager.session_silver_spent = 900_000_000
                app.refresh_live_widgets()
                self.assertTrue(app._footer_celebrating)

                # A second buy lands before it finishes — the frames read the live totals,
                # so the coins frame re-renders with the folded count and batch silver.
                app.task_manager.session_successful_purchases = 2
                app.task_manager.session_silver_spent = 1_800_000_000
                app.refresh_live_widgets()
                self.assertTrue(app._footer_celebrating)
                app._render_strip_coins()
                coins = str(app.query_one("#welcome-footer", Static).render())
                self.assertIn("2 outfits secured", coins)
                self.assertIn("1,800,000,000", coins)

                # The roll ends on the latest total, not the stale one — and the fold never
                # fires a second celebration after settle.
                app._roll_step = app.STRIP_ROLL_STEPS
                app._advance_strip_rollup()
                self.assertTrue(app._footer_celebrating)
                app._end_strip_celebration()
                self.assertFalse(app._footer_celebrating)
                self.assertIn("1.8B", str(app.query_one("#welcome-footer", Static).render()))
                app.refresh_live_widgets()
                self.assertFalse(app._footer_celebrating)
                app.task_manager.checker_enabled = False

    async def test_purchase_strip_coins_frame_holds_and_ticks_through_a_buy_list(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(120, 40)):
                app.animations_enabled = True
                app.task_manager.checker_enabled = True
                app.refresh_live_widgets()

                # Item 1 of a 3-item buy list is secured: the per-item tick starts the set
                # piece while the batch is still in progress.
                app.task_manager.purchase_in_progress = True
                app.task_manager.purchase_progress_count = 1
                app.task_manager.purchase_progress_silver = 900_000_000
                app.refresh_live_widgets()
                self.assertTrue(app._footer_celebrating)

                # The coins frame holds while the buy list is processing...
                app._show_strip_coins()
                for _ in range(app.STRIP_COINS_MIN_TICKS + 2):
                    app._advance_strip_coins()
                self.assertTrue(app._footer_celebrating)
                self.assertIn("1 outfit secured", str(app.query_one("#welcome-footer", Static).render()))

                # ...and live-updates as each further item is secured.
                app.task_manager.purchase_progress_count = 3
                app.task_manager.purchase_progress_silver = 2_700_000_000
                app._advance_strip_coins()
                coins = str(app.query_one("#welcome-footer", Static).render())
                self.assertIn("3 outfits secured", coins)
                self.assertIn("2,700,000,000", coins)

                # Batch done (stats committed): the frame releases into the count-up.
                app.task_manager.purchase_in_progress = False
                app.task_manager.session_successful_purchases = 3
                app.task_manager.session_silver_spent = 2_700_000_000
                app._advance_strip_coins()
                self.assertIn("spent", str(app.query_one("#welcome-footer", Static).render()))

                app._roll_step = app.STRIP_ROLL_STEPS
                app._advance_strip_rollup()
                self.assertTrue(app._footer_celebrating)
                app._end_strip_celebration()
                self.assertFalse(app._footer_celebrating)
                # Settle baselines both signals: no re-fire on the next poll.
                app.refresh_live_widgets()
                self.assertFalse(app._footer_celebrating)
                self.assertIn("2.7B", str(app.query_one("#welcome-footer", Static).render()))
                app.task_manager.checker_enabled = False

    async def test_purchase_strip_set_piece_fires_on_test_buy_while_idle(self):
        # "Fake Buy Success" simulates a buy without the monitor running — the set piece
        # must still play (it isn't gated on monitor_running), so test buys preview it.
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(120, 40)) as pilot:
                app.animations_enabled = True
                app.refresh_live_widgets()  # baseline, monitor idle
                await pilot.pause()
                self.assertFalse(app.task_manager.monitor_running())

                app.task_manager.session_successful_purchases = 1
                app.task_manager.session_silver_spent = 890_000_000
                app.refresh_live_widgets()
                self.assertTrue(app._footer_celebrating)
                self.assertIn("BOUGHT", str(app.query_one("#welcome-footer", Static).render()))

                # Settles back to the idle strip (lifetime record), not a crash.
                app._roll_step = app.STRIP_ROLL_STEPS
                app._advance_strip_rollup()
                self.assertTrue(app._footer_celebrating)
                app._end_strip_celebration()
                self.assertFalse(app._footer_celebrating)
                self.assertIn("all-time", str(app.query_one("#welcome-footer", Static).render()))

    async def test_purchase_strip_set_piece_skipped_without_animations(self):
        app = self.make_app()  # animations off
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(120, 40)):
                app.task_manager.checker_enabled = True
                app.refresh_live_widgets()
                app.task_manager.session_successful_purchases = 1
                app.task_manager.session_silver_spent = 890_000_000
                app.refresh_live_widgets()
                # No set piece; the strip renders the live stats directly.
                self.assertFalse(app._footer_celebrating)
                self.assertNotIn("BOUGHT", str(app.query_one("#welcome-footer", Static).render()))
                app.task_manager.checker_enabled = False

    async def test_dashboard_content_remains_scrollable(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                self.assertEqual(app.current_view, "dashboard")
                self.assertEqual(app.TOOLTIP_DELAY, 0.05)
                self.assertEqual(app.query_one("#content").styles.overflow_y, "auto")
                self.assertEqual(app.query_one("#content").styles.scrollbar_visibility, "hidden")
                self.assertEqual(app.query_one("#content").styles.scrollbar_size_vertical, 0)
                self.assertEqual(app.query_one("#content").styles.scrollbar_color, Color(52, 52, 52))
                # The full event log moved to the Logs tab; the dashboard keeps a fixed tail.
                self.assertEqual(len(list(app.query("#event-log"))), 0)
                self.assertEqual(app.query_one("#activity-tail").styles.height.value, 4)
                self.assertEqual(app.query_one("#topbar").region.y, 0)
                self.assertEqual(app.query_one("#statusbar").styles.height.value, 1)
                self.assertEqual(app.query_one("#statusbar").styles.background, Color(16, 16, 16))
                self.assertEqual(len(list(app.query("#sidebar"))), 0)

                await pilot.press("4")
                self.assertEqual(app.current_view, "logs")
                self.assertGreaterEqual(app.query_one("#event-log").size.height, 6)
                # Flat full-bleed: no bordered box, header row + thin rule instead.
                self.assertEqual(str(app.query_one("#event-log").styles.border_top[0]), "")
                self.assertEqual(len(list(app.query("#event-log-rule"))), 1)
                self.assertEqual(app.query_one("#event-log-rule").region.height, 1)
                title_text = str(app.query_one("#event-log-title", Static).render())
                self.assertIn("Event Log", title_text)
                self.assertIn("event", title_text)
                self.assertEqual(app.query_one("#event-log").styles.scrollbar_color, Color(52, 52, 52))
                self.assertEqual(app.query_one("#event-log").styles.scrollbar_size_vertical, 1)

    async def test_credentials_validation_and_password_masking(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)), patch(
            "bdo_marketplace_tools.ui.app.save_credentials"
        ) as save_mock:
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.click("#tile-credentials")
                await pilot.pause()
                self.assertEqual(len(list(app.screen_stack[-1].query("#email-input"))), 0)
                self.assertEqual(len(list(app.screen_stack[-1].query("#password-input"))), 0)

                await pilot.click("#credential-action-tile")
                await pilot.pause()
                password_input = app.query_visible_one("#password-input")
                self.assertTrue(password_input.password)
                self.assertTrue(app.query_visible_one("#save-pa-credentials", Button).disabled)

                app.query_visible_one("#email-input").value = "user@example.com"
                await pilot.pause()
                self.assertTrue(app.query_visible_one("#save-pa-credentials", Button).disabled)

                app.query_visible_one("#password-input").value = "secret"
                await pilot.pause()
                self.assertFalse(app.query_visible_one("#save-pa-credentials", Button).disabled)
                await pilot.click("#save-pa-credentials")
                save_mock.assert_called_once_with("user@example.com", "secret")
                self.assertIn("Credentials saved", app.status_message)
                self.assertEqual(type(app.screen_stack[-1]).__name__, "CredentialsModal")

    async def test_pa_credentials_modal_shows_invalid_email_warning_inline(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)), patch(
            "bdo_marketplace_tools.ui.app.save_credentials"
        ) as save_mock:
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.click("#tile-credentials")
                await pilot.pause()
                await pilot.click("#credential-action-tile")
                await pilot.pause()
                app.query_visible_one("#email-input").value = "not-an-email"
                app.query_visible_one("#password-input").value = "secret"
                await pilot.pause()
                self.assertFalse(app.query_visible_one("#save-pa-credentials", Button).disabled)

                await pilot.click("#save-pa-credentials")
                await pilot.pause()

                self.assertIn(
                    "Enter a valid email address",
                    str(app.query_visible_one("#pa-credentials-warning", Static).render()),
                )
                self.assertEqual(app.status_message, "")
                save_mock.assert_not_called()

    async def test_saved_credentials_are_labeled_pa_account_not_authenticated_ready(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=("user@example.com", "secret")):
            async with app.run_test(size=(100, 36)):
                credential_status, credential_detail, credential_level, _, _ = app.credential_state()

        self.assertEqual(credential_status, "PA Account")
        self.assertEqual(credential_detail, "us**@example.com")
        self.assertEqual(credential_level, "gold")

    async def test_credentials_modal_clear_pa_account_action_clears_saved_credentials(self):
        app = self.make_app()
        stored_credentials = [("user@example.com", "secret")]

        def load_saved_credentials():
            return stored_credentials[0]

        def clear_saved_credentials():
            stored_credentials[0] = (None, None)

        with patch("bdo_marketplace_tools.ui.app.load_credentials", side_effect=load_saved_credentials), patch(
            "bdo_marketplace_tools.ui.app.clear_credentials",
            side_effect=clear_saved_credentials,
        ) as clear_mock:
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.click("#tile-credentials")
                await pilot.pause()

                clear_action = app.query_visible_one("#clear-credentials", Button)
                self.assertTrue(clear_action.display)
                self.assertEqual(clear_action.styles.color, Color(209, 106, 106))
                await pilot.click("#clear-credentials")
                await pilot.pause(0.1)

        clear_mock.assert_called_once()
        self.assertIsNone(app.api_handler.email)
        self.assertIsNone(app.api_handler.password)
        self.assertIn("Saved credentials cleared", app.status_message)

    async def test_session_modal_pa_credentials_guard_controls_refresh_button(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=("user@example.com", "secret")):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.click("#tile-session")
                await pilot.pause()

                self.assertEqual(type(app.screen_stack[-1]).__name__, "SessionModal")
                self.assertEqual(len(list(app.screen_stack[-1].query("#session-status-tile"))), 0)
                self.assertFalse(app.query_visible_one("#refresh-session", Button).disabled)
                self.assertFalse(app.query_visible_one("#refresh-session", Button).can_focus)
                console = Console(width=100, color_system=None)
                with console.capture() as capture:
                    console.print(app.query_visible_one("#session-account-tile", Static).content)
                session_account_text = capture.get()
                with console.capture() as capture:
                    console.print(app.query_visible_one("#session-credentials-tile", Static).content)
                session_credentials_text = capture.get()
                self.assertIn("us**@example.com", session_account_text)
                self.assertIn("Set", session_credentials_text)
                self.assertNotIn("us**@example.com", session_credentials_text)

                await pilot.click("#refresh-session")
                await pilot.pause()
                self.assertEqual(type(app.screen_stack[-1]).__name__, "SessionRefreshConfirmScreen")

    async def test_online_session_widget_uses_shared_authenticated_detail(self):
        pa_app = self.make_app()
        pa_app.api_handler.login_status = True
        pa_app.api_handler.email = "user@example.com"

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=("user@example.com", "secret")):
            async with pa_app.run_test(size=(100, 36)):
                self.assertEqual(pa_app.session_status_state(), ("ONLINE", "Authenticated", "success"))
                session_tile = [
                    item for item in pa_app.dashboard_tile_data(pa_app.dashboard_snapshot()) if item[0] == "session"
                ][0]
                self.assertEqual(session_tile[2], "Authenticated")

        steam_app = self.make_app()
        steam_app.task_manager.account_mode = STEAM_BROWSER_MODE
        steam_app.api_handler.account_mode = STEAM_BROWSER_MODE
        steam_app.api_handler.login_status = True

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with steam_app.run_test(size=(100, 36)):
                self.assertEqual(steam_app.session_status_state(), ("ONLINE", "Authenticated", "success"))
                session_tile = [
                    item for item in steam_app.dashboard_tile_data(steam_app.dashboard_snapshot()) if item[0] == "session"
                ][0]
                self.assertEqual(session_tile[2], "Authenticated")

    async def test_login_refresh_without_pa_credentials_runs_backend_refresh(self):
        app = self.make_app()
        app.task_manager.login = AsyncMock()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)):
                await app.login_refresh()

        app.task_manager.login.assert_awaited_once()
        # "Fetching session status" is status-bar-only now; the log stays clean.
        self.assertEqual(len(app.task_manager.events), 0)
        self.assertEqual(app.status_message, "Login check complete.")

    async def test_app_settings_clear_saved_session_button_resets_marketplace_session(self):
        app = self.make_app()
        app.api_handler.login_status = True
        app.task_manager.purchase_submission_enabled = True

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.press("s")
                self.assertEqual(app.current_view, "settings")
                self.assertEqual(len(list(app.query("#clear-saved-session"))), 1)
                self.assertIsInstance(app.query_one("#clear-saved-session"), ModalAction)
                self.assertIn("Clear Session", str(app.query_one("#clear-saved-session").render()))
                self.assertEqual(str(app.query_one("#clear-saved-session").styles.background), "Color(0, 0, 0, a=0)")
                self.assertTrue(app.query_one("#clear-saved-session").has_class("modal-action-destructive"))
                self.assertTrue(app.query_one("#settings-clear-cookies").has_class("modal-action-destructive"))
                self.assertTrue(app.query_one("#settings-reset-steam").has_class("modal-action-destructive"))
                self.assertEqual(len(list(app.query("#settings-danger-card Button"))), 0)

                # Updates section pushes the maintenance row past this terminal's height; scroll
                # it into the scrollable content view before clicking.
                app.query_one("#clear-saved-session").scroll_visible(animate=False)
                await pilot.pause()
                await pilot.click("#clear-saved-session")
                await pilot.pause(0.1)

        self.assertTrue(app.api_handler.session_cleared)
        self.assertFalse(app.api_handler.login_status)
        self.assertFalse(app.task_manager.purchase_submission_enabled)
        self.assertIn("Saved marketplace session cleared", app.status_message)
        self.assertTrue(any("Manual session reset. Marketplace session cleared" in event for event in app.task_manager.events))

    async def test_app_settings_manual_update_check_logs_once(self):
        app = self.make_app()
        result = update_checker_module.UpdateCheckResult(
            "up-to-date",
            APP_VERSION,
            latest_version=APP_VERSION,
        )

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)), patch(
            "bdo_marketplace_tools.services.task_manager.run_update_check",
            return_value=result,
        ):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.press("s")
                await app.check_for_updates_from_settings()

                self.assertEqual(
                    str(app.query_one("#settings-status", Static).content),
                    f"You are on the latest version (v{APP_VERSION}).",
                )

        latest_events = [
            event for event in app.task_manager.events if "latest version" in event
        ]
        self.assertEqual(len(latest_events), 1)
        self.assertEqual(app.status_message, f"You are on the latest version (v{APP_VERSION}).")

    async def test_dashboard_live_metrics_refresh(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                app.task_manager.session_detected_outfits = 4
                app.task_manager.session_successful_purchases = 2
                app.task_manager.session_silver_spent = 1_500_000_000
                app.refresh_live_widgets()

                rendered_tiles = []
                for tile_id in ("monitor", "spent", "credentials", "session", "polling", "buy-delay"):
                    tile = app.query_one(f"#tile-{tile_id}", DashboardTile)
                    rendered_tiles.append(str(tile.border_title))
                rendered_tiles.extend(
                    f"{tile_id} {value} {detail}"
                    for tile_id, value, detail, _level, _show_dot in app.dashboard_tile_data(app.dashboard_snapshot())
                )
                rendered_tiles = "\n".join(rendered_tiles)
                self.assertIn("Mode", rendered_tiles)
                self.assertIn("Watch only", rendered_tiles)
                self.assertIn("Spent", rendered_tiles)
                self.assertIn("Credentials", rendered_tiles)
                self.assertIn("Session", rendered_tiles)
                self.assertIn("Polling", rendered_tiles)
                self.assertIn("Buy Delay", rendered_tiles)
                self.assertIn("No account", rendered_tiles)
                self.assertIn("Refresh required", rendered_tiles)
                self.assertIn("15-30s", rendered_tiles)
                self.assertIn("Slow", rendered_tiles)
                self.assertIn("1-2.5s", rendered_tiles)
                self.assertIn("Between buys", rendered_tiles)
                self.assertIn("1.5B silver", rendered_tiles)
                self.assertIn("Cap: ∞", rendered_tiles)
                self.assertEqual(len(list(app.query("#tile-success"))), 0)
                self.assertEqual(len(list(app.query("#tile-runtime"))), 0)
                info_bar = str(app.query_one("#status-keys", Static).render())
                self.assertIn("Watch only", info_bar)
                self.assertIn("cap", info_bar)
                self.assertIn("∞", info_bar)
                self.assertNotIn("bought", info_bar)
                quit_hint = str(app.query_one("#status-state", Static).render())
                self.assertIn("quit", quit_hint)
                app.task_manager.max_spend = 10_000_000_000
                app.refresh_live_widgets()
                info_bar = str(app.query_one("#status-keys", Static).render())
                self.assertIn("10B", info_bar)
                self.assertNotIn("silver", info_bar)
                app.task_manager.max_spend = None
                app.refresh_live_widgets()

                # The toggle must actually be on screen, not just mounted (regression: fr-width
                # rows once pushed it past the deck's right edge).
                deck_region = app.query_one("#dashboard-deck").region
                toggle_region = app.query_one("#monitor-toggle").region
                self.assertGreaterEqual(toggle_region.width, 14)
                self.assertLessEqual(toggle_region.right, deck_region.right)
                self.assertGreater(toggle_region.x, app.query_one("#dashboard-tiles").region.right)

                # Running state: toggle flips to STOP (which carries the runtime) and the
                # session stats live on the welcome-card footer, not the status bar.
                toggle_console = Console(width=40, color_system=None)
                self.assertTrue(app.query_one("#monitor-toggle").has_class("toggle-start"))
                with toggle_console.capture() as toggle_capture:
                    toggle_console.print(app.query_one("#monitor-toggle", Static).content)
                self.assertIn("START", toggle_capture.get())
                app.task_manager.checker_enabled = True
                app.refresh_live_widgets()
                self.assertTrue(app.query_one("#monitor-toggle").has_class("toggle-stop"))
                with toggle_console.capture() as toggle_capture:
                    toggle_console.print(app.query_one("#monitor-toggle", Static).content)
                self.assertIn("STOP", toggle_capture.get())
                info_bar = str(app.query_one("#status-keys", Static).render())
                self.assertNotIn("bought", info_bar)
                self.assertNotIn("run ", info_bar)
                footer_text = str(app.query_one("#welcome-footer", Static).render())
                self.assertIn("Running", footer_text)
                self.assertIn("bought", footer_text)
                self.assertIn("2 of 4 seen", footer_text)
                self.assertIn("50%", footer_text)
                self.assertIn("spent", footer_text)
                self.assertIn("1.5B of ∞", footer_text)
                self.assertNotIn("all-time", footer_text)

                # A fresh run shows the stats immediately (dimmed zeros) but no misleading 0%.
                app.task_manager.session_detected_outfits = 0
                app.task_manager.session_successful_purchases = 0
                app.task_manager.session_silver_spent = 0
                app.refresh_live_widgets()
                footer_text = str(app.query_one("#welcome-footer", Static).render())
                self.assertIn("bought", footer_text)
                self.assertIn("0 of 0 seen", footer_text)
                self.assertNotIn("%", footer_text)
                self.assertIn("spent", footer_text)
                app.task_manager.session_detected_outfits = 4
                app.task_manager.session_successful_purchases = 2
                app.task_manager.session_silver_spent = 1_500_000_000
                app.refresh_live_widgets()
                app.task_manager.checker_enabled = False
                app.refresh_live_widgets()
                self.assertTrue(app.query_one("#monitor-toggle").has_class("toggle-start"))
                footer_text = str(app.query_one("#welcome-footer", Static).render())
                self.assertNotIn("Running", footer_text)
                self.assertIn("all-time", footer_text)

                self.assertIs(app.query_one("#tile-session").parent, app.query_one("#dashboard-primary-tiles"))
                self.assertIs(app.query_one("#tile-buy-delay").parent, app.query_one("#dashboard-primary-tiles"))
                self.assertIs(app.query_one("#tile-monitor").parent, app.query_one("#dashboard-secondary-tiles"))
                self.assertEqual(app.query_one("#tile-session").styles.height.value, 3)

                await pilot.click("#tile-monitor")
                await pilot.pause()
                await pilot.pause(0.05)  # let the modal screen finish mounting under load
                monitor_note = str(app.query_visible_one(".modal-note", Static).render())
                self.assertIn("online marketplace session", monitor_note)
                self.assertIn("refresh Session from the dashboard", monitor_note)
                await pilot.press("escape")
                await pilot.click("#tile-spent")
                await pilot.pause()
                self.assertEqual(app.current_view, "dashboard")
                self.assertEqual(app.query_visible_one("#spend-cap-input", Input).value, "0")
                self.assertEqual(len(list(app.screen_stack[-1].query("#spend-summary"))), 1)
                self.assertEqual(len(list(app.screen_stack[-1].query("#settings-summary"))), 0)
                await pilot.click("#save-spend-cap")
                await pilot.pause()
                self.assertEqual([type(screen).__name__ for screen in app.screen_stack], ["Screen"])
                await pilot.click("#tile-spent")
                await pilot.pause()
                await pilot.press("escape")
                await pilot.click("#tile-credentials")
                await pilot.pause()
                self.assertEqual(app.current_view, "dashboard")
                self.assertEqual(len(list(app.screen_stack[-1].query("#password-input"))), 0)
                self.assertEqual(len(list(app.screen_stack[-1].query("#clear-credentials"))), 1)
                await pilot.click("#credential-action-tile")
                await pilot.pause()
                self.assertTrue(app.query_visible_one("#password-input", Input).password)
                await pilot.press("escape")
                await pilot.press("escape")
                await pilot.click("#tile-polling")
                await pilot.pause()
                self.assertEqual(app.current_view, "dashboard")
                self.assertEqual(len(list(app.screen_stack[-1].query("#delay-select"))), 0)
                self.assertEqual(len(list(app.screen_stack[-1].query("#polling-summary"))), 0)
                polling_note = str(app.query_visible_one(".modal-note", Static).render())
                self.assertIn("how often the app checks the marketplace", polling_note)
                self.assertEqual(len(list(app.screen_stack[-1].query("#polling-recommendations"))), 1)
                self.assertEqual(len(list(app.screen_stack[-1].query("#polling-preset-1"))), 1)
                self.assertEqual(len(list(app.screen_stack[-1].query("#polling-preset-2"))), 1)
                self.assertEqual(len(list(app.screen_stack[-1].query("#polling-preset-3"))), 1)
                self.assertIn("modal-info-clickable", app.query_visible_one("#polling-preset-2").classes)
                self.assertIn("preset-selected", app.query_visible_one("#polling-preset-3").classes)
                self.assertEqual(len(list(app.screen_stack[-1].query("#settings-summary"))), 0)
                await pilot.click("#polling-preset-2")
                await pilot.pause()
                self.assertEqual(app.query_visible_one("#custom-delay-min-input", Input).value, "5")
                self.assertEqual(app.query_visible_one("#custom-delay-max-input", Input).value, "10")
                self.assertIn("preset-selected", app.query_visible_one("#polling-preset-2").classes)
                app.query_visible_one("#custom-delay-min-input", Input).value = "8"
                app.query_visible_one("#custom-delay-max-input", Input).value = "13"
                await pilot.pause()
                self.assertNotIn("preset-selected", app.query_visible_one("#polling-preset-2").classes)
                app.query_visible_one("#custom-delay-min-input", Input).value = "5"
                app.query_visible_one("#custom-delay-max-input", Input).value = "10"
                await pilot.pause()
                self.assertIn("preset-selected", app.query_visible_one("#polling-preset-2").classes)
                app.query_visible_one("#custom-delay-min-input", Input).value = "8"
                app.query_visible_one("#custom-delay-max-input", Input).value = "13"
                await pilot.click("#save-polling")
                await pilot.pause()
                self.assertEqual(app.task_manager.delay, "custom")
                self.assertEqual(app.task_manager.current_delay_bounds(), (8, 13))
                self.assertEqual([type(screen).__name__ for screen in app.screen_stack], ["Screen"])
                await pilot.click("#tile-polling")
                await pilot.pause()
                await pilot.press("escape")
                await pilot.click("#tile-buy-delay")
                await pilot.pause()
                self.assertEqual(app.query_visible_one("#purchase-delay-min-input", Input).value, "1")
                self.assertEqual(app.query_visible_one("#purchase-delay-max-input", Input).value, "2.5")
                buy_delay_note = str(app.query_visible_one(".modal-note", Static).render())
                self.assertIn("between each purchase attempt", buy_delay_note)
                self.assertIn("does not change how often the app scans", buy_delay_note)
                await pilot.press("escape")
                await pilot.click("#tile-session")
                await pilot.pause()
                self.assertEqual(app.current_view, "dashboard")
                self.assertEqual(type(app.screen_stack[-1]).__name__, "SessionModal")
                self.assertEqual(len(list(app.screen_stack[-1].query("#session-status-tile"))), 0)
                self.assertTrue(app.query_visible_one("#refresh-session", Button).disabled)
                self.assertFalse(app.query_visible_one("#refresh-session", Button).can_focus)
                console = Console(width=100, color_system=None)
                with console.capture() as capture:
                    console.print(app.query_visible_one("#session-credentials-tile", Static).content)
                session_credentials_text = capture.get()
                self.assertIn("Required", session_credentials_text)
                self.assertIn("Save PA credentials first", session_credentials_text)
                await pilot.press("escape")

    async def test_credentials_modal_can_select_steam_browser_session_mode(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)), patch(
            "bdo_marketplace_tools.services.task_manager.save_account_mode",
            return_value=STEAM_BROWSER_MODE,
        ):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.press("s")
                self.assertEqual(len(list(app.query("#account-mode-select"))), 0)
                self.assertEqual(len(list(app.query("#save-settings"))), 0)

                await pilot.press("escape")
                await pilot.click("#tile-credentials")
                await pilot.pause()
                mode_select = app.query_visible_one("#account-mode-select", Select)
                note_text = str(app.query_visible_one("#credentials-mode-note", Static).render())
                self.assertIn("visible browser login", note_text)
                self.assertIn("entered automatically", note_text)
                self.assertEqual(len(list(app.query("#credentials-email-tile"))), 0)
                self.assertEqual(len(list(app.query("#credentials-password-tile"))), 0)
                self.assertEqual(len(list(app.query("#credentials-status-tile"))), 0)
                self.assertEqual(len(list(app.screen_stack[-1].query("#email-input"))), 0)
                self.assertEqual(len(list(app.screen_stack[-1].query("#password-input"))), 0)
                setup_tile_widget = app.query_visible_one("#credential-action-tile", Static)
                self.assertEqual(setup_tile_widget.border_title, "Pearl Abyss Account")
                self.assertTrue(setup_tile_widget.display)
                self.assertIn("modal-info-clickable", setup_tile_widget.classes)
                console = Console(width=100, color_system=None)
                with console.capture() as capture:
                    console.print(setup_tile_widget.content)
                setup_tile = capture.get()
                self.assertIn("No account configured", setup_tile)
                self.assertIn("Click to enter credentials", setup_tile)
                mode_select.value = STEAM_BROWSER_MODE
                await pilot.pause()
                self.assertTrue(app.task_manager.uses_steam_browser_session())
                self.assertEqual(app.api_handler.account_mode, STEAM_BROWSER_MODE)
                self.assertEqual(setup_tile_widget.border_title, "Steam Initial Setup")
                self.assertTrue(setup_tile_widget.display)
                self.assertNotIn("modal-info-clickable", setup_tile_widget.classes)
                self.assertIn("modal-info-muted", setup_tile_widget.classes)
                self.assertEqual(len(list(app.screen_stack[-1].query("#email-input"))), 0)
                self.assertEqual(len(list(app.screen_stack[-1].query("#password-input"))), 0)
                self.assertEqual(len(list(app.screen_stack[-1].query("#save-credential-mode"))), 0)
                steam_note = str(app.query_visible_one("#credentials-mode-note", Static).render())
                self.assertIn("visible browser", steam_note)
                self.assertIn("does not use saved email or password", steam_note)

                self.assertTrue(app.task_manager.uses_steam_browser_session())
                self.assertEqual(app.api_handler.account_mode, STEAM_BROWSER_MODE)
                self.assertIn("Login method set to Steam Account", app.status_message)
                self.assertEqual(app.credential_state()[0], "Steam Account")
                self.assertEqual(app.credential_state()[2], "steam")
                self.assertEqual(app.session_status_state(), ("OFFLINE", "Refresh required", "idle"))
                self.assertFalse(app.query_visible_one("#clear-credentials", Button).display)
                self.assertEqual(type(app.screen_stack[-1]).__name__, "CredentialsModal")

                await pilot.press("escape")
                await pilot.click("#tile-session")
                await pilot.pause()
                self.assertEqual(type(app.screen_stack[-1]).__name__, "SessionModal")
                self.assertEqual(len(list(app.screen_stack[-1].query("#session-status-tile"))), 0)
                self.assertFalse(app.query_visible_one("#refresh-session", Button).disabled)
                with console.capture() as capture:
                    console.print(app.query_visible_one("#session-credentials-tile", Static).content)
                steam_session_tile = capture.get()
                self.assertIn("Initial Setup", str(app.query_visible_one("#session-credentials-tile", Static).border_title))
                self.assertIn("Complete", steam_session_tile)
                self.assertIn("Ready for market login", steam_session_tile)

    async def test_credentials_modal_shows_initial_steam_setup_only_until_profile_prepared(self):
        app = self.make_app()
        app.task_manager.steam_browser_profile_prepared = False

        async def mark_prepared(*_args, **_kwargs):
            app.task_manager.steam_browser_profile_prepared = True
            return True

        app.task_manager.prepare_steam_browser_profile = AsyncMock(side_effect=mark_prepared)
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)), patch(
            "bdo_marketplace_tools.services.task_manager.save_account_mode",
            return_value=STEAM_BROWSER_MODE,
        ) as save_mode:
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.press("escape")
                await pilot.click("#tile-credentials")
                await pilot.pause()

                self.assertEqual(len(list(app.query("#prepare-steam-profile"))), 0)
                setup_tile_widget = app.query_visible_one("#credential-action-tile", Static)
                self.assertEqual(setup_tile_widget.border_title, "Pearl Abyss Account")
                self.assertTrue(setup_tile_widget.display)
                console = Console(width=100, color_system=None)
                with console.capture() as capture:
                    console.print(setup_tile_widget.content)
                setup_tile = capture.get()
                self.assertIn("No account configured", setup_tile)
                self.assertIn("Click to enter credentials", setup_tile)

                mode_select = app.query_visible_one("#account-mode-select", Select)
                mode_select.value = STEAM_BROWSER_MODE
                await pilot.pause()

                self.assertEqual(app.task_manager.account_mode, STEAM_BROWSER_MODE)
                self.assertEqual(setup_tile_widget.border_title, "Steam Initial Setup")
                self.assertTrue(setup_tile_widget.display)
                self.assertIn("modal-info-clickable", setup_tile_widget.classes)
                self.assertNotIn("modal-info-muted", setup_tile_widget.classes)
                self.assertEqual(len(list(app.screen_stack[-1].query("#save-credential-mode"))), 0)
                self.assertIn("Run Steam Setup once", str(app.query_visible_one("#credentials-mode-note", Static).render()))

                await pilot.click("#credential-action-tile")
                await pilot.pause(0.2)

                app.task_manager.prepare_steam_browser_profile.assert_awaited_once_with(allow_inactive_mode=True)
                save_mode.assert_called_once_with(STEAM_BROWSER_MODE)
                self.assertEqual(app.task_manager.account_mode, STEAM_BROWSER_MODE)
                self.assertTrue(app.task_manager.steam_browser_profile_prepared)
                self.assertTrue(setup_tile_widget.display)
                self.assertNotIn("modal-info-clickable", setup_tile_widget.classes)
                self.assertIn("modal-info-muted", setup_tile_widget.classes)
                with console.capture() as capture:
                    console.print(app.query_visible_one("#credential-action-tile", Static).content)
                setup_tile = capture.get()
                self.assertIn("Complete", setup_tile)
                self.assertIn("Ready for market login", setup_tile)
                self.assertIn("Initial Steam setup saved", app.status_message)

    async def test_switching_back_to_pa_mode_reuses_saved_credentials(self):
        app = self.make_app()
        app.task_manager.account_mode = STEAM_BROWSER_MODE
        app.api_handler.account_mode = STEAM_BROWSER_MODE
        app.task_manager.steam_browser_profile_prepared = True

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=("user@example.com", "secret")), patch(
            "bdo_marketplace_tools.ui.app.save_credentials"
        ) as save_mock, patch("bdo_marketplace_tools.services.task_manager.save_account_mode", return_value=PA_CREDENTIALS_MODE):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.click("#tile-credentials")
                await pilot.pause()
                self.assertEqual(len(list(app.screen_stack[-1].query("#email-input"))), 0)
                self.assertEqual(len(list(app.screen_stack[-1].query("#password-input"))), 0)

                mode_select = app.query_visible_one("#account-mode-select", Select)
                mode_select.value = PA_CREDENTIALS_MODE
                await pilot.pause()
                setup_tile_widget = app.query_visible_one("#credential-action-tile", Static)
                self.assertEqual(setup_tile_widget.border_title, "Pearl Abyss Account")
                self.assertIn("modal-info-clickable", setup_tile_widget.classes)
                self.assertEqual(len(list(app.screen_stack[-1].query("#save-credential-mode"))), 0)

        save_mock.assert_not_called()
        self.assertEqual(app.task_manager.account_mode, PA_CREDENTIALS_MODE)
        self.assertEqual(app.api_handler.email, "user@example.com")
        self.assertEqual(app.api_handler.password, "secret")
        self.assertIn("Login method set to Pearl Abyss Account", app.status_message)

    async def test_buy_delay_modal_saves_valid_decimals_and_keeps_invalid_open(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.click("#tile-buy-delay")
                await pilot.pause()
                self.assertEqual(app.task_manager.purchase_delay_range(), "1-2.5s")
                self.assertEqual(app.query_visible_one("#purchase-delay-min-input", Input).value, "1")
                self.assertEqual(app.query_visible_one("#purchase-delay-max-input", Input).value, "2.5")

                app.query_visible_one("#purchase-delay-min-input", Input).value = "4.5"
                app.query_visible_one("#purchase-delay-max-input", Input).value = "8"
                await pilot.click("#save-buy-delay")
                await pilot.pause()
                self.assertEqual(app.task_manager.purchase_delay_bounds, (4.5, 8.0))
                self.assertIn("Buy delay saved", app.status_message)
                self.assertIn("4.5-8s", app.status_message)
                self.assertEqual([type(screen).__name__ for screen in app.screen_stack], ["Screen"])

                await pilot.click("#tile-buy-delay")
                await pilot.pause()
                app.query_visible_one("#purchase-delay-min-input", Input).value = "10"
                app.query_visible_one("#purchase-delay-max-input", Input).value = "2"
                await pilot.click("#save-buy-delay")
                await pilot.pause()
                self.assertEqual(app.task_manager.purchase_delay_bounds, (4.5, 8.0))
                self.assertIn("Buy delay must use non-negative seconds", app.status_message)
                self.assertEqual(type(app.screen_stack[-1]).__name__, "BuyDelayModal")

    async def test_modal_input_edits_do_not_apply_until_save(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.click("#tile-polling")
                await pilot.pause()
                await pilot.click("#polling-preset-2")
                await pilot.pause()
                self.assertEqual(app.query_visible_one("#custom-delay-min-input", Input).value, "5")
                self.assertEqual(app.query_visible_one("#custom-delay-max-input", Input).value, "10")
                self.assertIn("preset-selected", app.query_visible_one("#polling-preset-2").classes)
                self.assertEqual(app.task_manager.delay, "3")
                self.assertEqual(app.task_manager.current_delay_bounds(), (15, 30))
                app.query_visible_one("#custom-delay-min-input", Input).value = "8"
                app.query_visible_one("#custom-delay-max-input", Input).value = "13"
                await pilot.pause()
                self.assertEqual(app.task_manager.delay, "3")
                self.assertEqual(app.task_manager.current_delay_bounds(), (15, 30))
                await pilot.click("#save-polling")
                await pilot.pause()
                self.assertEqual(app.task_manager.delay, "custom")
                self.assertEqual(app.task_manager.current_delay_bounds(), (8, 13))

                await pilot.click("#tile-buy-delay")
                await pilot.pause()
                app.query_visible_one("#purchase-delay-min-input", Input).value = "4.5"
                app.query_visible_one("#purchase-delay-max-input", Input).value = "8"
                await pilot.pause()
                self.assertEqual(app.task_manager.purchase_delay_bounds, (1.0, 2.5))
                await pilot.click("#save-buy-delay")
                await pilot.pause()
                self.assertEqual(app.task_manager.purchase_delay_bounds, (4.5, 8.0))

                await pilot.click("#tile-spent")
                await pilot.pause()
                app.query_visible_one("#spend-cap-input", Input).value = "250"
                await pilot.pause()
                self.assertIsNone(app.task_manager.max_spend)
                await pilot.click("#save-spend-cap")
                await pilot.pause()
                self.assertEqual(app.task_manager.max_spend, 250)

    async def test_stats_page_uses_tiles_without_local_file_path(self):
        app = self.make_app()
        app.task_manager.session_detected_outfits = 5
        app.task_manager.session_successful_purchases = 4
        app.task_manager.session_silver_spent = 2_000_000_000
        app.task_manager.lifetime_successful_purchases = 9
        app.task_manager.lifetime_silver_spent = 12_000_000_000
        app.task_manager.reload_lifetime_stats = lambda: None

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.press("3")
                # The Stats page live-refreshes, so there is no manual refresh control.
                self.assertEqual(list(app.query("#refresh-stats")), [])
                self.assertEqual(len(list(app.query(".stats-chip"))), 4)
                stat_chips = [
                    app.query_one("#stats-session-detected", Static),
                    app.query_one("#stats-session-purchases", Static),
                    app.query_one("#stats-session-rate", Static),
                    app.query_one("#stats-session-spent", Static),
                ]
                console = Console(width=100, color_system=None)
                with console.capture() as capture:
                    for chip in stat_chips:
                        console.print(chip.content)
                rendered = capture.get()

        self.assertIn("Success Rate", rendered)
        self.assertIn("80%", rendered)
        self.assertIn("2B", rendered)
        # Lifetime totals fold into the chips as dim details.
        self.assertIn("12B lifetime", rendered)
        self.assertIn("9 lifetime", rendered)
        self.assertNotIn("Local Data File", rendered)

    async def test_marketplace_inventory_page_is_wip_and_uses_standard_action(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.press("2")
                self.assertEqual(app.current_view, "wallet")
                self.assertIn(("wallet", "Inventory"), app.NAV_ITEMS)
                self.assertTrue(app.query_one("#tab-wallet").has_class("nav-tab-active"))
                self.assertIn("WIP", str(app.query_one("#wallet-wip-note", Static).content))
                self.assertEqual(app.query_one("#wallet-wip-note", Static).styles.margin.top, 1)
                self.assertIsInstance(app.query_one("#refresh-wallet"), ModalAction)
                self.assertEqual(str(app.query_one("#refresh-wallet").styles.background), "Color(0, 0, 0, a=0)")
                self.assertEqual(list(app.query("#wallet-actions Button")), [])

                await pilot.click("#refresh-wallet")
                await pilot.pause()

        self.assertIn("Inventory loaded", app.status_message)

    async def test_purchase_success_rate_uses_color_spectrum(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)):
                # No detections yet -> neutral idle, not a red "0%" alarm.
                self.assertEqual(app.purchase_rate_style(0, 0), STATUS_STYLES["idle"])
                # Ramp endpoints anchor exactly on the palette's red and green.
                self.assertEqual(app.purchase_rate_style(0, 10), "bold rgb(209,106,106)")
                self.assertEqual(app.purchase_rate_style(8, 10), "bold rgb(126,184,138)")
                self.assertEqual(app.purchase_rate_style(10, 10), "bold rgb(126,184,138)")

                # The ramp is continuous: the green channel rises and red falls as the
                # rate climbs, and the 70s already read green-leaning (not flat yellow).
                def rgb(bought, detected):
                    style = app.purchase_rate_style(bought, detected)
                    return tuple(int(p) for p in style[style.index("(") + 1 : style.index(")")].split(","))

                low, mid, high = rgb(3, 10), rgb(5, 10), rgb(7, 10)
                # green-minus-red rises monotonically as the hit rate climbs...
                self.assertLess(low[1] - low[0], mid[1] - mid[0])
                self.assertLess(mid[1] - mid[0], high[1] - high[0])
                self.assertGreater(high[1], high[0])  # ...and by 70% green dominates red

    async def test_sidebar_test_log_button_adds_dashboard_event(self):
        app = self.make_app(launch_mode="test")
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)), patch(
            "bdo_marketplace_tools.ui.app.random.choice",
            return_value=("Synthetic layout probe.", "success"),
        ):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.press("3")
                self.assertEqual(app.current_view, "stats")
                self.assertFalse(app.query_one("#test-controls").display)

                await pilot.press("escape")
                self.assertEqual(app.current_view, "dashboard")
                self.assertTrue(app.query_one("#test-controls").display)
                await pilot.click("#add-test-log")
                self.assertEqual(app.current_view, "dashboard")
                self.assertTrue(any("Synthetic layout probe." in event for event in app.task_manager.events))

    async def test_event_log_rehydrates_after_navigation(self):
        app = self.make_app()
        app.task_manager.add_event("Persistent event before navigation.", "success", notable=True)
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                tail_text = str(app.query_one("#activity-tail", Static).render())
                self.assertIn("Persistent event before navigation.", tail_text)
                self.assertEqual(app._dashboard_snapshot, app.dashboard_snapshot())

                await pilot.press("4")
                self.assertEqual(app.current_view, "logs")
                event_text = "\n".join(line.text for line in app.query_one("#event-log").lines)
                self.assertIn("Persistent event before navigation.", event_text)

                await pilot.press("s")
                self.assertEqual(app.current_view, "settings")
                await pilot.press("4")
                self.assertEqual(app.current_view, "logs")

                event_text = "\n".join(line.text for line in app.query_one("#event-log").lines)
                self.assertIn("Persistent event before navigation.", event_text)

    async def test_event_log_filters_switch_between_all_notable_and_alerts(self):
        app = self.make_app()
        app.task_manager.add_event("Routine polling detail.", "info")
        app.task_manager.add_event("Outfit detected: 2 available.", "success", notable=True)
        app.task_manager.add_event("Purchase failed: price mismatch.", "warning", notable=True)
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.press("4")
                self.assertEqual(app.current_view, "logs")

                self.assertEqual(app.query_one("#event-log-toolbar").styles.height.value, 1)
                self.assertIn("log-filter-selected", app.query_one("#log-filter-all").classes)
                self.assertEqual(app.query_one("#log-filter-all", Static).content, "All")
                event_text = "\n".join(line.text for line in app.query_one("#event-log").lines)
                self.assertIn("Routine polling detail.", event_text)
                self.assertIn("Outfit detected", event_text)
                self.assertIn("price mismatch", event_text)

                await pilot.click("#log-filter-notable")
                await pilot.pause()
                self.assertEqual(app.log_filter, "notable")
                self.assertIn("log-filter-selected", app.query_one("#log-filter-notable").classes)
                self.assertNotIn("log-filter-selected", app.query_one("#log-filter-all").classes)
                event_text = "\n".join(line.text for line in app.query_one("#event-log").lines)
                self.assertNotIn("Routine polling detail.", event_text)
                self.assertIn("Outfit detected", event_text)

                await pilot.click("#log-filter-alerts")
                await pilot.pause()
                self.assertEqual(app.log_filter, "alerts")
                event_text = "\n".join(line.text for line in app.query_one("#event-log").lines)
                self.assertNotIn("Outfit detected", event_text)
                self.assertIn("price mismatch", event_text)

    async def test_event_log_appends_new_events_and_shows_run_dividers(self):
        app = self.make_app()
        app.task_manager.add_event("First event.", "info")
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.press("4")
                self.assertEqual(app.current_view, "logs")
                log = app.query_one("#event-log")

                with patch.object(log, "clear", wraps=log.clear) as clear_spy:
                    app.task_manager.add_event(
                        "Monitor started — Buy mode.", "info", notable=True,
                        divider="monitor started · buy mode",
                    )
                    app.task_manager.add_event("Second event.", "success", notable=True)
                    app.refresh_live_widgets()
                    await pilot.pause()

                    # New events append without redrawing, preserving scroll position.
                    clear_spy.assert_not_called()

                event_text = "\n".join(line.text for line in log.lines)
                self.assertIn("First event.", event_text)
                self.assertIn("── monitor started · buy mode ·", event_text)
                self.assertIn("Second event.", event_text)

                title_text = str(app.query_one("#event-log-title", Static).render())
                self.assertIn("3 events", title_text)

    async def test_dashboard_tail_shows_recent_events_and_logs_tab_flags_alerts(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                self.assertIn("No activity yet.", str(app.query_one("#activity-tail", Static).render()))
                self.assertEqual(str(app.query_one("#activity-title", Static).content), "Activity")

                for index in range(7):
                    app.task_manager.add_event(f"Tail event {index}.", "success", notable=True)
                # Routine info never reaches the tail, however recent.
                app.task_manager.add_event("Routine polling detail.", "info")
                app.refresh_live_widgets()
                await pilot.pause()

                tail_text = str(app.query_one("#activity-tail", Static).render())
                self.assertIn("Tail event 6.", tail_text)
                self.assertNotIn("Tail event 2.", tail_text)  # only the last 4 fit
                self.assertNotIn("Routine polling detail.", tail_text)
                self.assertNotIn("●", str(app.query_one("#tab-logs", Static).content))

                app.task_manager.add_event("Purchase failed: price mismatch.", "warning")
                app.refresh_live_widgets()
                await pilot.pause()
                self.assertIn("●", str(app.query_one("#tab-logs", Static).content))

                await pilot.press("4")
                self.assertEqual(app.current_view, "logs")
                self.assertNotIn("●", str(app.query_one("#tab-logs", Static).content))
                event_text = "\n".join(line.text for line in app.query_one("#event-log").lines)
                self.assertIn("Purchase failed: price mismatch.", event_text)

    async def test_debug_controls_are_test_mode_only(self):
        live_app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with live_app.run_test(size=(100, 36)):
                self.assertEqual(list(live_app.query("#test-controls")), [])
                await live_app.add_test_log()
                self.assertTrue(any("Debug actions" in event for event in live_app.task_manager.events))
                self.assertIn("Debug actions", live_app.status_message)

        test_app = self.make_app(launch_mode="test")
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with test_app.run_test(size=(100, 36)):
                self.assertEqual(len(list(test_app.query("#test-controls"))), 1)
                self.assertEqual(len(list(test_app.query("#toggle-test-session"))), 1)
                self.assertEqual(len(list(test_app.query("#toggle-auto-reauth"))), 1)
                self.assertEqual(len(list(test_app.query("#expire-test-session"))), 1)
                self.assertEqual(len(list(test_app.query("#run-reauth-check"))), 1)
                self.assertEqual(len(list(test_app.query("#reset-steam-setup"))), 1)
                self.assertEqual(len(list(test_app.query("#clear-browser-cookies"))), 1)
                self.assertEqual(len(list(test_app.query("#start-test-monitor"))), 1)
                self.assertEqual(len(list(test_app.query("#start-test-buy"))), 1)
                self.assertEqual(len(list(test_app.query("#live-buy-error-probe"))), 1)
                self.assertEqual(len(list(test_app.query("#stop-test-monitor"))), 1)
                self.assertEqual(len(list(test_app.query("#fake-detection"))), 1)
                self.assertEqual(len(list(test_app.query("#fake-multi-detection"))), 1)
                self.assertEqual(len(list(test_app.query("#fake-buy-success"))), 1)
                self.assertEqual(len(list(test_app.query("#fake-bundled-buy"))), 1)

    async def test_reauthentication_debug_buttons_call_test_hooks(self):
        app = self.make_app(launch_mode="test")
        app.task_manager.debug_toggle_steam_auto_reauth = Mock(return_value=True)
        app.task_manager.debug_invalidate_marketplace_session = Mock(return_value=True)
        app.task_manager.debug_run_reauthentication_check = AsyncMock(return_value=True)

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                self.assertIn("Auto Reauth", str(app.query_one("#toggle-auto-reauth", Button).render()))
                self.assertIn("Reauth Check", str(app.query_one("#run-reauth-check", Button).render()))

                await pilot.click("#toggle-auto-reauth")
                await pilot.pause(0.1)

                app.task_manager.debug_toggle_steam_auto_reauth.assert_called_once()
                self.assertIn("debug override enabled", app.status_message)

                await pilot.click("#expire-test-session")
                await pilot.pause(0.1)

                app.task_manager.debug_invalidate_marketplace_session.assert_called_once()
                self.assertIn("marketplace session cleared", app.status_message)

                await pilot.click("#run-reauth-check")
                await pilot.pause(0.1)

                app.task_manager.debug_run_reauthentication_check.assert_awaited_once()
                self.assertIn("re-authentication check succeeded", app.status_message)

    async def test_steam_setup_debug_buttons_call_test_hooks(self):
        app = self.make_app(launch_mode="test")
        app.task_manager.debug_clear_steam_initial_setup_status = Mock(return_value=True)
        app.task_manager.debug_clear_steam_browser_cookies = AsyncMock(return_value=True)

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                self.assertIn("Reset Steam Setup", str(app.query_one("#reset-steam-setup", Button).render()))
                self.assertIn("Clear Browser Cookies", str(app.query_one("#clear-browser-cookies", Button).render()))

                await pilot.click("#reset-steam-setup")
                await pilot.pause(0.1)

                app.task_manager.debug_clear_steam_initial_setup_status.assert_called_once()
                self.assertIn("Initial Steam setup status reset", app.status_message)

                await pilot.click("#clear-browser-cookies")
                await pilot.pause(0.2)

                app.task_manager.debug_clear_steam_browser_cookies.assert_awaited_once()
                self.assertIn("Browser cookies cleared", app.status_message)

    async def test_app_settings_maintenance_actions_run_in_live_mode(self):
        app = self.make_app()
        app.task_manager.clear_browser_session_cookies = AsyncMock(return_value=True)
        app.task_manager.reset_steam_initial_setup_status = Mock(return_value=True)
        app.task_manager.clean_browser_cache_now = AsyncMock(
            return_value={"removed_bytes": 2 * 1024 * 1024, "failed_paths": 0, "results": ()}
        )

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.press("s")
                self.assertEqual(app.current_view, "settings")
                self.assertFalse(app.is_test_mode)

                # The Updates section makes the settings page taller than this terminal, so the
                # scrollable maintenance actions must be brought into view before clicking.
                app.query_one("#settings-reset-steam").scroll_visible(animate=False)
                await pilot.pause()

                app.query_one("#settings-cache-threshold-input", Input).value = "321"
                await pilot.click("#settings-save-cache-limit")
                await pilot.pause(0.1)
                self.assertIsNot(app.focused, app.query_one("#settings-cache-threshold-input", Input))
                self.assertEqual(app.task_manager.browser_cache_cleanup_threshold_mb, 321)
                self.assertIn(
                    "Browser cache cleanup limit saved",
                    str(app.query_one("#settings-status", Static).render()),
                )

                await pilot.click("#settings-clean-cache")
                await pilot.pause(0.2)
                app.task_manager.clean_browser_cache_now.assert_awaited_once()
                self.assertIn(
                    "Cleaned 2.0 MiB",
                    str(app.query_one("#settings-status", Static).render()),
                )

                await pilot.click("#settings-reset-steam")
                await pilot.pause(0.1)
                app.task_manager.reset_steam_initial_setup_status.assert_called_once()
                self.assertIn(
                    "Steam initial setup reset",
                    str(app.query_one("#settings-status", Static).render()),
                )

                await pilot.click("#settings-clear-cookies")
                await pilot.pause(0.2)
                app.task_manager.clear_browser_session_cookies.assert_awaited_once()
                self.assertIn(
                    "Browser cookies cleared",
                    str(app.query_one("#settings-status", Static).render()),
                )

    async def test_app_settings_shows_browser_storage_summary(self):
        app = self.make_app()
        app.task_manager.browser_storage_summary = Mock(
            return_value=BrowserProfileStorageSummary(
                total_bytes=512 * 1024 * 1024,
                disposable_bytes=200 * 1024 * 1024,
                scanned_at=123.0,
            )
        )

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.press("s")
                rendered = str(app.query_one("#settings-storage-facts", Static).render())
                cache_input = app.query_one("#settings-cache-threshold-input", Input)
                cache_input_value = cache_input.value
                cache_input_width = cache_input.styles.width.value
                cache_input_height = cache_input.styles.height.value
                cache_input_background = str(cache_input.styles.background)
                settings_status_margin_bottom = app.query_one("#settings-status", Static).styles.margin.bottom
                cache_input.focus()
                await pilot.pause()
                await pilot.click("#settings-storage-facts")
                await pilot.pause()
                cache_input_kept_focus_after_click_away = app.focused is cache_input

        self.assertIn("Storage", rendered)
        self.assertIn("512.0 MiB", rendered)
        self.assertIn("disposable", rendered)
        self.assertIn("200.0 MiB", rendered)
        self.assertEqual(cache_input_value, str(app.task_manager.browser_cache_cleanup_threshold_mb))
        self.assertEqual(cache_input_width, 8)
        self.assertEqual(cache_input_height, 1)
        self.assertEqual(cache_input_background, "Color(16, 16, 16)")
        self.assertEqual(settings_status_margin_bottom, 1)
        self.assertFalse(cache_input_kept_focus_after_click_away)

    async def test_single_item_test_monitor_sidebar_controls_use_separate_task(self):
        app = self.make_app(launch_mode="test")
        app.api_handler.login_status = True

        async def idle_test_checker():
            await asyncio.sleep(60)

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)), patch.object(
            app.task_manager,
            "single_item_test_checker",
            new=idle_test_checker,
        ):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.click("#start-test-monitor")
                await pilot.pause(0.1)

                self.assertTrue(app.task_manager.single_item_test_checker_enabled)
                self.assertFalse(app.task_manager.checker_enabled)
                self.assertIn("Single-item test monitor started", app.status_message)
                self.assertTrue(any("buy calls are disabled" in event for event in app.task_manager.events))
                self.assertFalse(app.task_manager.single_item_test_purchase_enabled)

                await app.start_monitor()
                self.assertFalse(app.task_manager.checker_enabled)
                self.assertIn("Stop it before starting the normal monitor", app.status_message)

                await pilot.click("#stop-test-monitor")
                await pilot.pause(0.1)
                self.assertFalse(app.task_manager.single_item_test_checker_enabled)
                self.assertIn("Single-item test monitor stopped", app.status_message)

    async def test_single_item_test_buy_requires_login_and_confirmation(self):
        app = self.make_app(launch_mode="test")
        app.task_manager.set_purchase_delay_range("3.25", "5.5")

        async def idle_test_checker():
            await asyncio.sleep(60)

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)), patch.object(
            app.task_manager,
            "single_item_test_checker",
            new=idle_test_checker,
        ):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.click("#start-test-buy")
                await pilot.pause()
                self.assertFalse(app.task_manager.single_item_test_checker_enabled)
                self.assertIn("Login required", app.status_message)

                app.api_handler.login_status = True
                app.api_handler.email = "user@example.com"
                await pilot.click("#start-test-buy")
                await pilot.pause(0.1)
                self.assertFalse(app.task_manager.single_item_test_checker_enabled)
                self.assertIsInstance(app.query_visible_one("#confirm-start"), ModalAction)
                confirm_console = Console(width=80, color_system=None)
                with confirm_console.capture() as confirm_capture:
                    for widget in app.screen_stack[-1].query(Static):
                        confirm_console.print(widget.content)
                confirmation_text = confirm_capture.get()
                self.assertIn("Buy delay", confirmation_text)
                self.assertIn("3.25-5.5s", confirmation_text)

                await pilot.click("#confirm-start")
                await pilot.pause(0.1)
                self.assertTrue(app.task_manager.single_item_test_checker_enabled)
                self.assertTrue(app.task_manager.single_item_test_purchase_enabled)
                self.assertIn("Single-item buy test started", app.status_message)

                await app.task_manager.stop_single_item_test_checker()

    async def test_live_buy_error_probe_requires_login_and_confirmation(self):
        app = self.make_app(launch_mode="test")
        app.task_manager.set_purchase_delay_range("3.25", "5.5")
        app.task_manager.debug_run_live_buy_error_probe = AsyncMock(return_value=True)

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.click("#live-buy-error-probe")
                await pilot.pause()
                self.assertIn("Login required", app.status_message)
                app.task_manager.debug_run_live_buy_error_probe.assert_not_called()

                app.api_handler.login_status = True
                app.api_handler.email = "user@example.com"
                await pilot.click("#live-buy-error-probe")
                await pilot.pause(0.1)
                self.assertEqual(type(app.screen_stack[-1]).__name__, "ConfirmLiveTestBuyScreen")
                confirm_console = Console(width=80, color_system=None)
                with confirm_console.capture() as confirm_capture:
                    for widget in app.screen_stack[-1].query(Static):
                        confirm_console.print(widget.content)
                confirmation_text = confirm_capture.get()
                self.assertIn("15280", confirmation_text)
                self.assertIn("2.9B", confirmation_text)
                self.assertIn("3.25-5.5s", confirmation_text)

                await pilot.click("#confirm-live-test-buy")
                await pilot.pause(0.2)

                app.task_manager.debug_run_live_buy_error_probe.assert_awaited_once()
                self.assertIn("Live buy error probe completed", app.status_message)

    async def test_polling_modal_saves_preset_equivalent_range_as_preset(self):
        app = self.make_app()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.click("#tile-polling")
                await pilot.pause()
                await pilot.click("#polling-preset-2")
                await pilot.pause()
                await pilot.click("#save-polling")
                await pilot.pause()

        self.assertEqual(app.task_manager.delay, "2")
        self.assertEqual(app.task_manager.current_delay_bounds(), (5, 10))
        self.assertIn("Balanced (5-10s)", app.status_message)

    async def test_buy_mode_start_requires_login_logs_one_combined_warning(self):
        app = self.make_app()
        app.task_manager.purchase_submission_enabled = True
        app.api_handler.login_status = False

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)):
                await app.start_monitor()

        self.assertFalse(app.task_manager.checker_enabled)
        self.assertEqual(len(app.task_manager.events), 1)
        event_text = app.task_manager.events[0]
        self.assertIn("Login required before starting buy mode", event_text)
        self.assertIn("Login or refresh the marketplace session", event_text)

    async def test_login_refresh_uses_browser_refresh_without_direct_pa_login(self):
        app = self.make_app()
        app.api_handler.email = "user@example.com"
        app.api_handler.password = "secret"
        app.api_handler.is_session_expired = AsyncMock(return_value=-1)
        app.api_handler.login = AsyncMock(return_value=0)
        app.task_manager.refresh_pa_browser_session = AsyncMock(return_value=False)

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=("user@example.com", "secret")):
            async with app.run_test(size=(100, 36)):
                await app.login_refresh()

        event_text = "\n".join(app.task_manager.events)
        app.api_handler.login.assert_not_called()
        app.task_manager.refresh_pa_browser_session.assert_awaited_once()
        # "Fetching session status" is status-bar-only now; the log stays clean.
        self.assertEqual(len(app.task_manager.events), 0)
        self.assertNotIn("Checking marketplace session", event_text)
        self.assertNotIn("Login check complete", event_text)

    async def test_fake_detection_button_does_not_buy(self):
        app = self.make_app(launch_mode="test")
        app.task_manager.purchase_submission_enabled = True
        app.task_manager.buy_item = AsyncMock()
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.click("#fake-detection")
                await pilot.pause()  # let the async detection worker finish

        app.task_manager.buy_item.assert_not_called()
        self.assertEqual(app.task_manager.session_detected_outfits, 1)
        self.assertEqual(app.task_manager.session_successful_purchases, 0)

    async def test_fake_buy_success_button_updates_metrics_and_saves(self):
        app = self.make_app(launch_mode="test")
        with patch.object(app.task_manager, "save_local_data") as save_mock, patch(
            "bdo_marketplace_tools.ui.app.load_credentials",
            return_value=(None, None),
        ):
            # Taller window so the last debug-sidebar button (#fake-buy-success) is on-screen
            # for the pilot; the greeting line under the mascot costs the sidebar one row.
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.click("#fake-buy-success")

        self.assertEqual(app.task_manager.session_detected_outfits, 1)
        self.assertEqual(app.task_manager.session_successful_purchases, 1)
        self.assertGreater(app.task_manager.session_silver_spent, 0)
        save_mock.assert_awaited_once()

    async def test_fake_bundled_buy_button_runs_eight_item_debug_bundle(self):
        app = self.make_app(launch_mode="test")
        app.task_manager.api_handler.buy_item = AsyncMock()
        with patch.object(app.task_manager, "save_local_data") as save_mock, patch(
            "bdo_marketplace_tools.services.task_manager.DEBUG_BUNDLED_PURCHASE_TICK_SECONDS",
            0.0,
        ), patch(
            "bdo_marketplace_tools.ui.app.load_credentials",
            return_value=(None, None),
        ):
            async with app.run_test(size=(100, 42)) as pilot:
                app.query_one("#fake-bundled-buy").scroll_visible(animate=False)
                await pilot.pause()
                await pilot.click("#fake-bundled-buy")
                for _ in range(20):
                    await pilot.pause(0.05)
                    if app.task_manager.session_successful_purchases == 8:
                        break

        app.task_manager.api_handler.buy_item.assert_not_called()
        self.assertEqual(app.task_manager.session_detected_outfits, 8)
        self.assertEqual(app.task_manager.purchase_progress_count, 8)
        self.assertEqual(app.task_manager.session_successful_purchases, 8)
        self.assertFalse(app.task_manager.purchase_in_progress)
        self.assertIn("8 outfits", app.status_message)
        save_mock.assert_awaited_once()

    async def test_fake_bundled_buy_button_runs_in_background_and_live_ticks(self):
        app = self.make_app(launch_mode="test")
        app.task_manager.api_handler.buy_item = AsyncMock()
        with patch.object(app.task_manager, "save_local_data") as save_mock, patch(
            "bdo_marketplace_tools.services.task_manager.DEBUG_BUNDLED_PURCHASE_TICK_SECONDS",
            0.25,
        ), patch(
            "bdo_marketplace_tools.ui.app.load_credentials",
            return_value=(None, None),
        ):
            async with app.run_test(size=(120, 42)) as pilot:
                app.animations_enabled = True
                app.refresh_live_widgets()
                app.query_one("#fake-bundled-buy").scroll_visible(animate=False)
                await pilot.pause()
                await pilot.click("#fake-bundled-buy")

                for _ in range(20):
                    await pilot.pause(0.02)
                    if app.task_manager.purchase_progress_count >= 1:
                        break

                self.assertTrue(app.task_manager.purchase_in_progress)
                self.assertEqual(app.task_manager.purchase_progress_count, 1)
                self.assertEqual(app.task_manager.session_successful_purchases, 0)
                self.assertIn("running", app.status_message)

                app._show_strip_coins()
                footer = str(app.query_one("#welcome-footer", Static).render())
                self.assertIn("1 outfit secured", footer)
                self.assertNotIn("8 outfits secured", footer)

                for _ in range(120):
                    await pilot.pause(0.03)
                    if (
                        not app.task_manager.purchase_in_progress
                        and app.task_manager.session_successful_purchases == 8
                    ):
                        break

        app.task_manager.api_handler.buy_item.assert_not_called()
        self.assertEqual(app.task_manager.purchase_progress_count, 8)
        self.assertEqual(app.task_manager.session_successful_purchases, 8)
        self.assertFalse(app.task_manager.purchase_in_progress)
        save_mock.assert_awaited_once()

    async def test_test_mode_fake_stats_write_to_test_stats_db(self):
        app = self.make_app(launch_mode="test")
        self.assertEqual(app.task_manager.stats_db_path.name, "stats.test.db")
        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            # Taller window so the last debug-sidebar button (#fake-buy-success) is on-screen
            # for the pilot; the greeting line under the mascot costs the sidebar one row.
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.click("#fake-detection")
                await pilot.pause(0.2)
                await pilot.click("#fake-buy-success")
                await pilot.pause(0.2)
                self.assertEqual(app.task_manager.session_successful_purchases, 1)

        trends = stats_db_module.load_trends(1, path=app.task_manager.stats_db_path)
        self.assertGreaterEqual(trends["daily"][-1]["detected"], 1)
        self.assertEqual(trends["daily"][-1]["purchased"], 1)

    async def test_test_session_toggle_allows_buy_mode_without_live_purchase_api(self):
        app = self.make_app(launch_mode="test")
        app.task_manager.purchase_submission_enabled = True
        app.api_handler.buy_item = AsyncMock()

        async def idle_checker():
            await asyncio.sleep(60)

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)), patch.object(
            app.task_manager,
            "checker",
            new=idle_checker,
        ), patch.object(app.task_manager, "save_local_data"):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.click("#toggle-test-session")
                await pilot.pause()
                self.assertTrue(app.task_manager.simulated_session_enabled)
                self.assertTrue(app.api_handler.login_status)
                self.assertIn("Test session marked valid", app.status_message)

                await app.start_monitor()
                await pilot.pause(0.1)
                self.assertFalse(app.task_manager.checker_enabled)
                self.assertIsInstance(app.query_visible_one("#confirm-start"), ModalAction)

                await pilot.click("#confirm-start")
                await pilot.pause(0.1)
                self.assertTrue(app.task_manager.checker_enabled)
                self.assertTrue(app.task_manager.purchase_submission_enabled)

                await app.task_manager.buy_item([["debug-premium-outfit", "1", "2020000000"]])
                app.api_handler.buy_item.assert_not_called()
                self.assertEqual(app.task_manager.session_successful_purchases, 1)

                await pilot.click("#toggle-test-session")
                await pilot.pause()
                self.assertFalse(app.task_manager.simulated_session_enabled)
                self.assertFalse(app.api_handler.login_status)
                self.assertFalse(app.task_manager.purchase_submission_enabled)

    async def test_watch_only_start_works_logged_out_and_buy_mode_blocks(self):
        app = self.make_app()

        async def idle_checker():
            await asyncio.sleep(60)

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)), patch.object(
            app.task_manager,
            "checker",
            new=idle_checker,
        ):
            async with app.run_test(size=(100, 36)):
                await app.start_monitor()
                self.assertTrue(app.task_manager.checker_enabled)
                await app.task_manager.stop_checker()

                app.task_manager.purchase_submission_enabled = True
                app.api_handler.login_status = False
                await app.start_monitor()
                self.assertFalse(app.task_manager.checker_enabled)
                self.assertIn("Login or refresh", app.status_message)

    async def test_repeated_start_and_stop_controls_log_without_duplicate_monitor_tasks(self):
        app = self.make_app()

        async def idle_checker():
            await asyncio.sleep(60)

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)), patch.object(
            app.task_manager,
            "checker",
            new=idle_checker,
        ):
            async with app.run_test(size=(100, 36)):
                await app.stop_monitor()
                self.assertIn("already stopped", app.status_message)

                await app.start_monitor()
                first_task = app.task_manager.checker_task
                self.assertTrue(app.task_manager.checker_enabled)

                await app.start_monitor()
                self.assertIs(app.task_manager.checker_task, first_task)
                self.assertIn("already running", app.status_message)
                self.assertTrue(any("no additional monitor task started" in event for event in app.task_manager.events))

                await app.stop_monitor()
                self.assertFalse(app.task_manager.checker_enabled)

    async def test_monitor_modal_mode_selection_toggles_buy_mode(self):
        app = self.make_app()

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=(None, None)):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.click("#tile-monitor")
                await pilot.pause()
                # The modal no longer duplicates run control (big dashboard toggle owns it).
                self.assertEqual(len(list(app.screen_stack[-1].query("#modal-start-monitor"))), 0)
                self.assertEqual(len(list(app.screen_stack[-1].query("#modal-stop-monitor"))), 0)
                self.assertEqual(len(list(app.screen_stack[-1].query("#buy-mode-switch"))), 0)
                self.assertTrue(app.query_visible_one("#monitor-mode-watch").has_class("preset-selected"))
                self.assertFalse(app.query_visible_one("#monitor-mode-buy").has_class("preset-selected"))

                await pilot.click("#monitor-mode-buy")
                await pilot.pause(0.1)
                self.assertTrue(app.task_manager.purchase_submission_enabled)
                self.assertTrue(app.query_visible_one("#monitor-mode-buy").has_class("preset-selected"))
                self.assertFalse(app.query_visible_one("#monitor-mode-watch").has_class("preset-selected"))
                self.assertIn("Buy mode", app.status_message)

                await pilot.click("#monitor-mode-watch")
                await pilot.pause(0.1)
                self.assertFalse(app.task_manager.purchase_submission_enabled)
                self.assertTrue(app.query_visible_one("#monitor-mode-watch").has_class("preset-selected"))
                self.assertIn("Watch only", app.status_message)

    async def test_buy_mode_start_confirmation_closes_monitor_modal_stack(self):
        app = self.make_app()
        app.api_handler.login_status = True
        app.api_handler.email = "user@example.com"
        app.task_manager.purchase_submission_enabled = True
        app.task_manager.set_purchase_delay_range("4.5", "8")

        async def idle_checker():
            await asyncio.sleep(60)

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=("user@example.com", "secret")), patch.object(
            app.task_manager,
            "checker",
            new=idle_checker,
        ):
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.click("#tile-monitor")
                await pilot.pause()
                # Starting (via the dashboard toggle / space) while buy mode is set asks for confirmation.
                await app.start_monitor()
                await pilot.pause(0.1)
                self.assertEqual(type(app.screen_stack[-1]).__name__, "ConfirmBuyModeScreen")
                confirm_console = Console(width=80, color_system=None)
                with confirm_console.capture() as confirm_capture:
                    for widget in app.screen_stack[-1].query(Static):
                        confirm_console.print(widget.content)
                confirmation_text = confirm_capture.get()
                self.assertIn("Buy delay", confirmation_text)
                self.assertIn("4.5-8s", confirmation_text)

                await pilot.click("#confirm-start")
                await pilot.pause(0.1)
                self.assertTrue(app.task_manager.checker_enabled)
                self.assertEqual([type(screen).__name__ for screen in app.screen_stack], ["Screen"])

                await app.stop_monitor()

    async def test_running_watch_monitor_requires_confirmation_before_buy_mode(self):
        app = self.make_app()
        app.api_handler.login_status = True
        app.api_handler.email = "user@example.com"

        async def idle_checker():
            await asyncio.sleep(60)

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=("user@example.com", "secret")), patch.object(
            app.task_manager,
            "checker",
            new=idle_checker,
        ):
            async with app.run_test(size=(100, 36)) as pilot:
                await app.start_monitor()
                first_task = app.task_manager.checker_task
                self.assertTrue(app.task_manager.checker_enabled)
                self.assertFalse(app.task_manager.purchase_submission_enabled)

                await app.apply_purchase_mode(True)
                await pilot.pause(0.1)
                self.assertFalse(app.task_manager.purchase_submission_enabled)
                self.assertIs(app.task_manager.checker_task, first_task)
                self.assertIsInstance(app.query_visible_one("#confirm-start"), ModalAction)

                await pilot.click("#confirm-start")
                await pilot.pause(0.1)
                self.assertTrue(app.task_manager.purchase_submission_enabled)
                self.assertIs(app.task_manager.checker_task, first_task)
                self.assertIn("Buy mode", app.status_message)
                self.assertIn("enabled for the running monitor", app.status_message)

                await app.stop_monitor()

    async def test_buy_mode_requires_confirmation_before_starting(self):
        app = self.make_app()
        app.api_handler.login_status = True
        app.api_handler.email = "user@example.com"
        app.task_manager.purchase_submission_enabled = True

        async def idle_checker():
            await asyncio.sleep(60)

        with patch("bdo_marketplace_tools.ui.app.load_credentials", return_value=("user@example.com", "secret")), patch.object(
            app.task_manager,
            "checker",
            new=idle_checker,
        ):
            async with app.run_test(size=(100, 36)) as pilot:
                await app.start_monitor()
                await pilot.pause(0.1)
                self.assertFalse(app.task_manager.checker_enabled)
                self.assertIsInstance(app.query_visible_one("#confirm-start"), ModalAction)
                self.assertIn("modal-action-tile", app.query_visible_one("#confirm-start", ModalAction).classes)
                await pilot.click("#confirm-start")
                await pilot.pause(0.1)
                self.assertTrue(app.task_manager.checker_enabled)


class UpdateCheckerTests(unittest.TestCase):
    def test_parse_version_handles_core_and_prerelease(self):
        self.assertEqual(update_checker_module.parse_version("1.2.3"), ((1, 2, 3), 1, ""))
        self.assertEqual(update_checker_module.parse_version("v1.2"), ((1, 2, 0), 1, ""))
        self.assertEqual(update_checker_module.parse_version("1.1.0-beta"), ((1, 1, 0), 0, "beta"))
        self.assertIsNone(update_checker_module.parse_version("not-a-version"))
        self.assertIsNone(update_checker_module.parse_version(None))

    def test_is_newer_version_compares_core_then_prerelease(self):
        self.assertTrue(update_checker_module.is_newer_version("1.2.0", "1.1.0"))
        self.assertTrue(update_checker_module.is_newer_version("1.1.0", "1.1.0-beta"))
        self.assertFalse(update_checker_module.is_newer_version("1.1.0-beta", "1.1.0"))
        self.assertFalse(update_checker_module.is_newer_version("1.1.0", "1.1.0"))
        self.assertFalse(update_checker_module.is_newer_version("1.0.0", "1.1.0"))
        # An unparseable remote string must never look newer.
        self.assertFalse(update_checker_module.is_newer_version("garbage", "1.1.0"))

    def test_extract_remote_version_reads_app_version_assignment(self):
        text = 'PROJECT_NAME = "x"\nAPP_VERSION = "1.4.0-beta"\nAPP_CHANNEL = "BETA"\n'
        self.assertEqual(update_checker_module.extract_remote_version(text), "1.4.0-beta")
        self.assertIsNone(update_checker_module.extract_remote_version("nothing here"))

    def test_check_for_update_reports_each_outcome(self):
        newer = update_checker_module.check_for_update(
            current_version="1.1.0-beta",
            fetcher=lambda: 'APP_VERSION = "1.2.0"\n',
        )
        self.assertEqual(newer.status, "update-available")
        self.assertTrue(newer.update_available)
        self.assertEqual(newer.latest_version, "1.2.0")

        same = update_checker_module.check_for_update(
            current_version="1.2.0",
            fetcher=lambda: 'APP_VERSION = "1.2.0"\n',
        )
        self.assertEqual(same.status, "up-to-date")
        self.assertFalse(same.update_available)

    def test_check_for_update_treats_failures_as_soft_errors(self):
        def boom():
            raise RuntimeError("network down")

        errored = update_checker_module.check_for_update(current_version="1.1.0", fetcher=boom)
        self.assertEqual(errored.status, "error")
        self.assertFalse(errored.update_available)

        missing = update_checker_module.check_for_update(
            current_version="1.1.0",
            fetcher=lambda: "no version assignment here",
        )
        self.assertEqual(missing.status, "error")


class AppSettingsUpdatePreferenceTests(unittest.TestCase):
    def test_update_settings_default_and_persist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "data" / "app_settings.json"
            with patch("bdo_marketplace_tools.storage.app_settings.APP_SETTINGS_PATH", settings_path):
                self.assertTrue(account_mode_module.load_update_check_on_startup())
                self.assertIsNone(account_mode_module.load_last_seen_update_version())
                self.assertFalse(account_mode_module.save_update_check_on_startup(False))
                self.assertEqual(account_mode_module.save_last_seen_update_version("1.5.0"), "1.5.0")
                self.assertFalse(account_mode_module.load_update_check_on_startup())
                self.assertEqual(account_mode_module.load_last_seen_update_version(), "1.5.0")

            saved = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertFalse(saved["updates"]["check_on_startup"])
            self.assertEqual(saved["updates"]["last_seen_version"], "1.5.0")


class BackgroundTasksUpdateTests(unittest.IsolatedAsyncioTestCase):
    def _make_manager(self, *, test_mode_enabled=False):
        with patch(
            "bdo_marketplace_tools.services.task_manager._load_local_data",
            return_value=LOCAL_DATA.copy(),
        ):
            return BackgroundTasks(
                FakeAPI(),
                test_mode_enabled=test_mode_enabled,
                persist_ui_settings=False,
            )

    async def test_startup_check_announces_new_version_every_time(self):
        manager = self._make_manager()
        result = update_checker_module.UpdateCheckResult("update-available", APP_VERSION, latest_version="9.9.9")
        with patch("bdo_marketplace_tools.services.task_manager.run_update_check", return_value=result):
            first = await manager.check_for_update(manual=False)
            second = await manager.check_for_update(manual=False)

        self.assertTrue(first.update_available)
        self.assertEqual(manager.available_update_version, "9.9.9")
        # Announced on every startup check while the app is outdated.
        notices = [event for event in manager.events if "9.9.9" in event]
        self.assertEqual(len(notices), 2)
        # The available-update notice is yellow (warning level) and notable.
        from bdo_marketplace_tools.ui.display import EVENT_LEVEL_COLORS

        rendered_notices = [event for event in manager.events_for_filter("notable") if "9.9.9" in event]
        self.assertEqual(len(rendered_notices), 2)
        # The line is wrapped in warning yellow (the version highlight inside is bold white).
        self.assertIn(f"[{EVENT_LEVEL_COLORS['warning']}]Update available", rendered_notices[0])
        # Every startup also prints the running version to the log.
        self.assertTrue(any("Marketplace Tools v" in event for event in manager.events))
        self.assertTrue(second.update_available)

    async def test_startup_check_skipped_in_test_mode(self):
        manager = self._make_manager(test_mode_enabled=True)
        with patch("bdo_marketplace_tools.services.task_manager.run_update_check") as fetch:
            result = await manager.check_for_update(manual=False)
        self.assertIsNone(result)
        fetch.assert_not_called()
        # The running version is still printed even though the remote lookup is skipped.
        self.assertTrue(any("Marketplace Tools v" in event for event in manager.events))

    async def test_startup_check_skipped_when_disabled(self):
        manager = self._make_manager()
        manager.update_check_on_startup = False
        with patch("bdo_marketplace_tools.services.task_manager.run_update_check") as fetch:
            result = await manager.check_for_update(manual=False)
        self.assertIsNone(result)
        fetch.assert_not_called()
        # The running version is still printed even though the remote lookup is skipped.
        self.assertTrue(any("Marketplace Tools v" in event for event in manager.events))

    async def test_manual_check_runs_even_in_test_mode(self):
        manager = self._make_manager(test_mode_enabled=True)
        result = update_checker_module.UpdateCheckResult("up-to-date", APP_VERSION, latest_version=APP_VERSION)
        with patch("bdo_marketplace_tools.services.task_manager.run_update_check", return_value=result):
            outcome = await manager.check_for_update(manual=True)
        self.assertEqual(outcome.status, "up-to-date")
        self.assertIsNone(manager.available_update_version)
        self.assertTrue(manager.update_check_completed)
        self.assertTrue(any("latest version" in event for event in manager.events))

    async def test_manual_check_error_warns_only_on_manual(self):
        manager = self._make_manager()
        result = update_checker_module.UpdateCheckResult("error", APP_VERSION, error="boom")
        with patch("bdo_marketplace_tools.services.task_manager.run_update_check", return_value=result):
            await manager.check_for_update(manual=True)
        self.assertTrue(any("Could not check for updates" in event for event in manager.events))
        self.assertFalse(manager.update_check_completed)


class DataDirResolutionTests(unittest.TestCase):
    def test_env_override_wins(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {paths_module.DATA_DIR_ENV_VAR: temp_dir}):
                self.assertEqual(paths_module.default_data_dir(), Path(temp_dir))

    def test_local_app_data_used_when_no_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                os.environ,
                {paths_module.DATA_DIR_ENV_VAR: "", "LOCALAPPDATA": temp_dir},
            ):
                self.assertEqual(
                    paths_module.default_data_dir(),
                    Path(temp_dir) / paths_module.APP_DIR_NAME / "data",
                )

    def test_falls_back_to_xdg_when_no_windows_app_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                os.environ,
                {
                    paths_module.DATA_DIR_ENV_VAR: "",
                    "LOCALAPPDATA": "",
                    "XDG_DATA_HOME": temp_dir,
                },
            ):
                self.assertEqual(
                    paths_module.default_data_dir(),
                    Path(temp_dir) / paths_module.APP_DIR_NAME / "data",
                )


if __name__ == "__main__":
    unittest.main()
