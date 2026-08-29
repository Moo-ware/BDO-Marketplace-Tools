import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bdo_marketplace_tools.market import browser_auth
from bdo_marketplace_tools.market.browser_worker import PARKED_PAGE_URL, PersistentPABrowserWorker


async def _drain_dialog_tasks_quickly(state):
    return await browser_auth._browser_dialogs._drain_auth_dialog_tasks(
        state,
        timeout_seconds=0.01,
    )


async def _wait_through_first_cancellation(started, release):
    started.set()
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await release.wait()


class FakeEventEmitter:
    def __init__(self):
        self._listeners = {}

    def on(self, event_name, listener):
        self._listeners.setdefault(event_name, []).append(listener)

    def remove_listener(self, event_name, listener):
        listeners = self._listeners.get(event_name, [])
        if listener in listeners:
            listeners.remove(listener)

    def emit(self, event_name, value):
        for listener in tuple(self._listeners.get(event_name, ())):
            listener(value)

    def listener_count(self, event_name):
        return len(self._listeners.get(event_name, ()))


class FakePage(FakeEventEmitter):
    def __init__(self, url=PARKED_PAGE_URL):
        super().__init__()
        self.url = url
        self.frames = []
        self.closed = False
        self.close_raises = False
        self.content = ""

    def is_closed(self):
        return self.closed

    async def goto(self, url, wait_until=None, timeout=None):
        self.url = url

    async def set_content(self, html, wait_until=None, timeout=None):
        self.content = html

    async def close(self, run_before_unload=None):
        if self.close_raises:
            raise RuntimeError("page refused to close")
        self.closed = True


class FakeBrowser:
    def __init__(self, context):
        self.context = context

    def is_connected(self):
        return not self.context.closed


class FakeContext(FakeEventEmitter):
    def __init__(self):
        super().__init__()
        self.pages = []
        self.new_page_count = 0
        self.closed = False
        self.close_count = 0
        self.init_scripts = []
        self.clear_cookie_filters = []
        self.browser = FakeBrowser(self)
        self.market_cookies = [
            {
                "name": "TradeAuth_Session",
                "value": "saved-session",
                "domain": "na-trade.naeu.playblackdesert.com",
                "path": "/",
            }
        ]
        self._append_page()

    def _append_page(self):
        page = FakePage()
        self.pages.append(page)
        self.emit("page", page)
        return page

    async def new_page(self):
        self.new_page_count += 1
        return self._append_page()

    async def add_init_script(self, script):
        self.init_scripts.append(script)

    async def cookies(self, _urls=None):
        return list(self.market_cookies)

    async def clear_cookies(self, **filters):
        self.clear_cookie_filters.append(filters)
        if not filters:
            self.market_cookies = []
            return

        def matches(cookie):
            return all(cookie.get(key) == value for key, value in filters.items())

        self.market_cookies = [cookie for cookie in self.market_cookies if not matches(cookie)]

    async def close(self):
        self.close_count += 1
        self.closed = True
        for page in self.pages:
            page.closed = True


class FakePlaywright:
    def __init__(self):
        self.stop_count = 0

    async def stop(self):
        self.stop_count += 1


class FakePlaywrightManager:
    def __init__(self, playwright):
        self.playwright = playwright

    async def start(self):
        return self.playwright


class PersistentPABrowserWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.context = FakeContext()
        self.playwright = FakePlaywright()
        self.manager = FakePlaywrightManager(self.playwright)
        self.worker = PersistentPABrowserWorker(profile_path=Path(self.temp_dir.name))
        self.import_patch = patch.object(
            browser_auth,
            "_import_async_playwright",
            return_value=lambda: self.manager,
        )
        self.launch_patch = patch.object(
            browser_auth,
            "_launch_persistent_chrome_context",
            new=AsyncMock(return_value=self.context),
        )
        self.import_patch.start()
        self.launch_mock = self.launch_patch.start()
        self.addCleanup(self.import_patch.stop)
        self.addCleanup(self.launch_patch.stop)

    async def asyncTearDown(self):
        await self.worker.close()

    async def test_repeated_auth_cycles_reuse_one_context_and_keep_bounded_state(self):
        self.context.pages[0].url = "https://example.invalid/restored-page"
        self.assertTrue(await self.worker.start())
        self.assertFalse(await self.worker.start())
        initial_page = next(page for page in self.context.pages if not page.closed)
        self.assertIn("Worker ready", initial_page.content)
        self.assertIn("close this window", initial_page.content)
        self.assertIn("minimize it", initial_page.content)

        first = await self.worker.acquire_market_cookies(
            timeout_seconds=1,
            profile_path=self.worker.profile_path,
            announce_opening=False,
        )
        second = first
        for _ in range(4):
            second = await self.worker.acquire_market_cookies(timeout_seconds=1)

        self.assertEqual(first[0]["name"], "TradeAuth_Session")
        self.assertEqual(first[0]["value"], "saved-session")
        self.assertEqual(second, first)
        self.launch_mock.assert_awaited_once()
        self.assertEqual(len(self.context.init_scripts), 1)
        self.assertEqual(self.context.listener_count("page"), 1)
        self.assertEqual(self.context.listener_count("request"), 0)
        self.assertEqual(self.context.listener_count("response"), 0)
        self.assertFalse(self.worker._auth_dialog_state["tasks"])
        self.assertEqual(len(self.worker._live_pages()), 1)
        live_page = next(page for page in self.context.pages if not page.closed)
        self.assertIs(live_page, initial_page)
        self.assertEqual(self.context.new_page_count, 0)
        self.assertEqual(len(self.context.pages), 1)
        self.assertEqual(live_page.url, PARKED_PAGE_URL)
        self.assertEqual(live_page.listener_count("dialog"), 1)
        self.assertIn("Captured", live_page.content)
        self.assertIn("Checking", live_page.content)
        self.assertNotIn("saved-session", live_page.content)

        page_objects_before_validation = len(self.context.pages)
        self.assertTrue(
            await self.worker.update_session_validation(
                True,
                checked_at="Aug 27, 2026 at 04:05:06 PM",
            )
        )
        self.assertIn("Session ready", live_page.content)
        self.assertIn("Aug 27, 2026 at 04:05:06 PM", live_page.content)
        await self.worker.update_session_validation(False, checked_at="later")
        self.assertEqual(len(self.context.pages), page_objects_before_validation)
        self.assertIn("Session needs attention", live_page.content)

        self.assertTrue(await self.worker.close())
        self.assertFalse(await self.worker.close())
        self.assertEqual(self.context.close_count, 1)
        self.assertEqual(self.playwright.stop_count, 1)

    async def test_failed_cycles_are_translated_parked_and_worker_remains_reusable(self):
        await self.worker.start()
        cookies = [{"name": "TradeAuth_Session", "value": "fresh"}]

        with patch.object(
            browser_auth,
            "_acquire_market_cookies_in_context",
            new=AsyncMock(
                side_effect=(
                    browser_auth.BrowserAuthError("login failed"),
                    RuntimeError("Target page has been closed"),
                    cookies,
                )
            ),
        ):
            with self.assertRaisesRegex(browser_auth.BrowserAuthError, "login failed"):
                await self.worker.acquire_market_cookies()
            self.assertEqual(len(self.worker._live_pages()), 1)
            failed_page = self.worker._live_pages()[0]
            self.assertIn("Session needs attention", failed_page.content)

            with self.assertRaisesRegex(browser_auth.BrowserAuthError, "Target page has been closed"):
                await self.worker.acquire_market_cookies()
            self.assertEqual(await self.worker.acquire_market_cookies(), cookies)

        with patch.object(
            self.context,
            "cookies",
            new=AsyncMock(side_effect=RuntimeError("browser disconnected")),
        ):
            with self.assertRaisesRegex(browser_auth.BrowserAuthError, "browser disconnected"):
                await self.worker.clear_cookies()

        self.assertTrue(self.worker.running)
        self.assertEqual(len(self.worker._live_pages()), 1)

    async def test_test_reauth_selectively_clears_market_session_in_retained_context(self):
        await self.worker.start()
        replacement = [{"name": "TradeAuth_Session", "value": "replacement"}]

        with patch.object(
            browser_auth,
            "_wait_for_market_cookies",
            new=AsyncMock(return_value=replacement),
        ):
            cookies = await self.worker.acquire_market_cookies(
                timeout_seconds=1,
                clear_market_session_before_auth=True,
            )

        self.assertEqual(cookies, replacement)
        self.assertEqual(
            self.context.clear_cookie_filters,
            [{"name": "TradeAuth_Session"}],
        )
        self.assertEqual(len(self.worker._live_pages()), 1)

    async def test_cold_pa_login_clear_reuses_page_and_preserves_consent(self):
        await self.worker.start()
        page = self.worker._live_pages()[0]
        self.context.market_cookies = [
            {
                "name": "TradeAuth_Session",
                "value": "market",
                "domain": "na-trade.naeu.playblackdesert.com",
                "path": "/",
            },
            {
                "name": "paSession",
                "value": "account",
                "domain": "account.pearlabyss.com",
                "path": "/",
            },
            {
                "name": "paParent",
                "value": "parent",
                "domain": ".pearlabyss.com",
                "path": "/",
            },
            {
                "name": "CookieConsent",
                "value": "required-only",
                "domain": ".pearlabyss.com",
                "path": "/",
            },
            {
                "name": "blackdesert_check",
                "value": "site",
                "domain": ".playblackdesert.com",
                "path": "/",
            },
        ]

        cleared_count = await self.worker.clear_pa_login_session_cookies()

        self.assertEqual(cleared_count, 3)
        self.assertEqual(
            {cookie["name"] for cookie in self.context.market_cookies},
            {"CookieConsent", "blackdesert_check"},
        )
        self.assertEqual(self.worker._live_pages(), [page])
        self.assertEqual(self.context.new_page_count, 0)
        self.assertIn("Browser cookies cleared", page.content)

    async def test_surviving_auth_popup_is_reused_when_designated_page_cannot_be_parked(self):
        await self.worker.start()
        original_page = self.worker._live_pages()[0]
        popup_holder = []

        async def auth_cycle(context, *_args, **_kwargs):
            original_page.goto = AsyncMock(side_effect=RuntimeError("page navigation failed"))
            popup_holder.append(context._append_page())
            return [{"name": "TradeAuth_Session", "value": "captured"}]

        with patch.object(
            browser_auth,
            "_acquire_market_cookies_in_context",
            new=AsyncMock(side_effect=auth_cycle),
        ):
            await self.worker.acquire_market_cookies(timeout_seconds=1)

        self.assertEqual(self.context.new_page_count, 0)
        self.assertEqual(self.worker._live_pages(), popup_holder)
        self.assertIs(self.worker._page, popup_holder[0])
        self.assertEqual(popup_holder[0].url, PARKED_PAGE_URL)
        self.assertTrue(original_page.closed)

    async def test_startup_failure_releases_partial_resources_and_allows_retry(self):
        self.context.pages[0].goto = AsyncMock(side_effect=RuntimeError("park navigation failed"))

        with self.assertRaisesRegex(browser_auth.BrowserAuthError, "parked page"):
            await self.worker.start()

        self.assertFalse(self.worker.context_alive)
        self.assertFalse(self.worker.running)
        self.assertEqual(self.context.close_count, 1)
        self.assertEqual(self.playwright.stop_count, 1)

        retry_context = FakeContext()
        self.launch_mock.return_value = retry_context
        self.manager.playwright = FakePlaywright()

        self.assertTrue(await self.worker.start())
        self.assertTrue(self.worker.running)
        self.assertEqual(len(self.worker._live_pages()), 1)

    async def test_startup_creates_only_the_missing_initial_page_then_reuses_it(self):
        self.context.pages.clear()

        self.assertTrue(await self.worker.start())
        initial_page = self.worker._live_pages()[0]
        self.assertEqual(self.context.new_page_count, 1)

        await self.worker.acquire_market_cookies(timeout_seconds=1)

        self.assertEqual(self.context.new_page_count, 1)
        self.assertEqual(self.worker._live_pages(), [initial_page])

    async def test_close_cancels_an_active_auth_cycle_and_releases_runtime(self):
        await self.worker.start()
        auth_started = asyncio.Event()

        async def blocked_auth_cycle(*_args, **_kwargs):
            auth_started.set()
            await asyncio.Event().wait()

        with patch.object(
            browser_auth,
            "_acquire_market_cookies_in_context",
            new=AsyncMock(side_effect=blocked_auth_cycle),
        ):
            auth_task = asyncio.create_task(self.worker.acquire_market_cookies())
            await auth_started.wait()
            self.assertTrue(await self.worker.close())
            with self.assertRaises(asyncio.CancelledError):
                await auth_task

        self.assertFalse(self.worker.running)
        self.assertFalse(self.worker.has_resources)
        self.assertEqual(self.context.close_count, 1)
        self.assertEqual(self.playwright.stop_count, 1)

    async def test_close_is_bounded_when_an_active_operation_ignores_cancellation(self):
        await self.worker.start()
        auth_started = asyncio.Event()
        release_auth = asyncio.Event()

        async def cancellation_resistant_auth(*_args, **_kwargs):
            auth_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release_auth.wait()
                return [{"name": "TradeAuth_Session", "value": "late"}]

        with patch.object(
            browser_auth,
            "_acquire_market_cookies_in_context",
            new=AsyncMock(side_effect=cancellation_resistant_auth),
        ), patch(
            "bdo_marketplace_tools.market.browser_worker.WORKER_OPERATION_DRAIN_TIMEOUT_SECONDS",
            0.01,
        ):
            auth_task = asyncio.create_task(self.worker.acquire_market_cookies())
            await auth_started.wait()
            with self.assertRaisesRegex(browser_auth.BrowserAuthError, "did not stop promptly"):
                await asyncio.wait_for(self.worker.close(), timeout=0.2)

        release_auth.set()
        await auth_task
        self.assertTrue(self.worker.has_resources)
        self.assertTrue(await self.worker.close())
        self.assertFalse(self.worker.has_resources)

    async def test_late_startup_cannot_create_browser_state_after_close_times_out(self):
        launch_started = asyncio.Event()
        release_launch = asyncio.Event()
        late_context = FakeContext()
        status_callback = AsyncMock()

        async def cancellation_resistant_launch(*_args, **_kwargs):
            launch_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release_launch.wait()
                return late_context

        self.launch_mock.side_effect = cancellation_resistant_launch
        with patch(
            "bdo_marketplace_tools.market.browser_worker.WORKER_OPERATION_DRAIN_TIMEOUT_SECONDS",
            0.01,
        ):
            start_task = asyncio.create_task(self.worker.start(status_callback=status_callback))
            await launch_started.wait()
            with self.assertRaisesRegex(browser_auth.BrowserAuthError, "did not stop promptly"):
                await asyncio.wait_for(self.worker.close(), timeout=0.2)

        release_launch.set()
        with self.assertRaisesRegex(browser_auth.BrowserAuthError, "stopping"):
            await start_task

        self.assertFalse(
            any("worker is ready" in str(call.args[0]) for call in status_callback.await_args_list)
        )
        self.assertTrue(late_context.closed)
        self.assertFalse(self.worker.has_resources)

    async def test_manually_closed_worker_page_is_not_recreated_by_auth_request(self):
        await self.worker.start()
        for page in self.context.pages:
            page.closed = True
        page_objects_after_manual_close = len(self.context.pages)

        self.assertFalse(self.worker.running)
        self.assertFalse(await self.worker.update_session_validation(False))
        with self.assertRaisesRegex(browser_auth.BrowserAuthError, "not running"):
            await self.worker.acquire_market_cookies()

        self.assertTrue(self.worker.context_alive)
        self.assertEqual(await self.worker.clear_cookies(), 1)
        self.assertEqual(len(self.context.pages), page_objects_after_manual_close)
        self.assertFalse(any(not page.closed for page in self.context.pages))
        self.assertTrue(await self.worker.close())
        self.assertFalse(self.worker.context_alive)

    async def test_failed_runtime_stop_retains_handle_for_a_later_retry(self):
        await self.worker.start()
        working_stop = self.playwright.stop
        self.playwright.stop = AsyncMock(side_effect=RuntimeError("driver stop failed"))

        with self.assertRaisesRegex(browser_auth.BrowserAuthError, "driver did not stop"):
            await self.worker.close()

        self.assertFalse(self.worker.context_alive)
        self.assertTrue(self.worker.has_resources)

        self.playwright.stop = working_stop
        self.assertTrue(await self.worker.close())
        self.assertFalse(self.worker.has_resources)
        self.assertEqual(self.playwright.stop_count, 1)

    async def test_uncloseable_extra_page_stops_worker_instead_of_accumulating_pages(self):
        await self.worker.start()
        extra_page = self.context._append_page()
        extra_page.close_raises = True

        with self.assertRaisesRegex(browser_auth.BrowserAuthError, "one-page parked state"):
            await self.worker.acquire_market_cookies(timeout_seconds=1)

        self.assertFalse(self.worker.running)
        self.assertTrue(self.context.closed)
        self.assertEqual(self.playwright.stop_count, 1)

    async def test_shutdown_does_not_wait_forever_for_stalled_dialog_task(self):
        await self.worker.start()
        release_task = asyncio.Event()
        task_started = asyncio.Event()

        dialog_task = asyncio.create_task(
            _wait_through_first_cancellation(task_started, release_task)
        )
        self.worker._auth_dialog_state["tasks"].add(dialog_task)
        await task_started.wait()

        with patch.object(
            browser_auth,
            "_drain_auth_dialog_tasks",
            new=_drain_dialog_tasks_quickly,
        ):
            self.assertTrue(await asyncio.wait_for(self.worker.close(), timeout=0.2))

        release_task.set()
        await asyncio.gather(dialog_task, return_exceptions=True)
        self.assertFalse(self.worker.running)

    async def test_stalled_dialog_task_stops_worker_instead_of_accumulating_across_cycles(self):
        await self.worker.start()
        release_task = asyncio.Event()
        task_started = asyncio.Event()
        dialog_task_holder = []

        async def auth_cycle(*_args, auth_dialog_state=None, **_kwargs):
            dialog_task = asyncio.create_task(
                _wait_through_first_cancellation(task_started, release_task)
            )
            auth_dialog_state["tasks"].add(dialog_task)
            dialog_task_holder.append(dialog_task)
            await task_started.wait()
            return [{"name": "TradeAuth_Session", "value": "captured"}]

        with patch.object(
            browser_auth,
            "_acquire_market_cookies_in_context",
            new=AsyncMock(side_effect=auth_cycle),
        ), patch.object(
            browser_auth,
            "_drain_auth_dialog_tasks",
            new=_drain_dialog_tasks_quickly,
        ):
            with self.assertRaisesRegex(browser_auth.BrowserAuthError, "dialog handler did not finish"):
                await self.worker.acquire_market_cookies()

        self.assertFalse(self.worker.running)
        self.assertFalse(self.worker.has_resources)
        release_task.set()
        await asyncio.gather(*dialog_task_holder, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
