import asyncio
from pathlib import Path

from bdo_marketplace_tools.market import browser_auth
from bdo_marketplace_tools.market.browser_landing import (
    initial_landing_state,
    landing_auth_started,
    landing_cookie_capture_finished,
    landing_cookies_cleared,
    landing_validation_finished,
    render_browser_landing,
)
from bdo_marketplace_tools.storage.paths import PA_MARKET_PROFILE_PATH


PARKED_PAGE_URL = "about:blank"
PARK_PAGE_TIMEOUT_MS = 5000
PLAYWRIGHT_STOP_TIMEOUT_SECONDS = 2.0
WORKER_OPERATION_DRAIN_TIMEOUT_SECONDS = 2.0


class PersistentPABrowserWorker:
    """Own one reusable Patchright Chrome context for Pearl Abyss authentication."""

    def __init__(self, *, profile_path=PA_MARKET_PROFILE_PATH, account_label="Pearl Abyss Account"):
        self.profile_path = Path(profile_path)
        self.account_label = str(account_label)
        self._lock = asyncio.Lock()
        self._playwright = None
        self._context = None
        self._page = None
        self._auth_dialog_state = None
        self._active_operation_tasks = set()
        self._close_requests = 0
        # Closing invalidates every operation that started before it. This remains changed after
        # close() returns, so a cancellation-resistant launch cannot resume once the stop gate is
        # no longer raised and quietly recreate Chrome resources.
        self._lifecycle_generation = 0
        self._landing_state = initial_landing_state(self.account_label)

    @property
    def landing_state(self):
        return self._landing_state

    @property
    def context_alive(self):
        context = self._context
        if context is None:
            return False
        try:
            browser = getattr(context, "browser", None)
            is_connected = getattr(browser, "is_connected", None)
            if callable(is_connected) and not is_connected():
                return False
            getattr(context, "pages")
        except Exception:
            return False
        return True

    @property
    def has_resources(self):
        """Whether teardown still has browser/runtime resources to account for."""
        return self._context is not None or self._playwright is not None

    @property
    def owns_profile(self):
        """Whether a retained context may still own the persistent Chrome profile."""
        return self._context is not None

    @property
    def running(self):
        if not self.context_alive:
            return False
        try:
            pages = getattr(self._context, "pages")
        except Exception:
            return False
        return any(not browser_auth._page_is_closed(page) for page in pages or [])

    async def start(self, status_callback=None):
        """Start the worker once. Existing live workers are reused."""
        if self._close_requests:
            raise browser_auth.BrowserAuthError("Pearl Abyss Chrome worker is stopping.")
        lifecycle_generation = self._lifecycle_generation
        current_task = asyncio.current_task()
        self._active_operation_tasks.add(current_task)
        try:
            return await self._start(
                status_callback=status_callback,
                lifecycle_generation=lifecycle_generation,
            )
        finally:
            self._active_operation_tasks.discard(current_task)

    async def _start(self, status_callback=None, *, lifecycle_generation):
        async with self._lock:
            self._require_current_operation(lifecycle_generation)
            if self.running:
                await self._ensure_worker_page(lifecycle_generation=lifecycle_generation)
                self._require_current_operation(lifecycle_generation)
                return False
            await self._discard_resources(status_callback=None)
            self._require_current_operation(lifecycle_generation)

            async_playwright = browser_auth._import_async_playwright(
                "Patchright is not installed. Install requirements, then run `patchright install chromium`."
            )
            self.profile_path.mkdir(parents=True, exist_ok=True)

            playwright = None
            context = None
            try:
                playwright = await async_playwright().start()
                # Publish the runtime before the potentially slow Chrome launch so close() can
                # force-stop it if launch ignores task cancellation.
                self._playwright = playwright
                self._require_current_operation(lifecycle_generation)
                context = await browser_auth._launch_persistent_chrome_context(playwright, self.profile_path)
                self._context = context
                self._require_current_operation(lifecycle_generation)
                self._auth_dialog_state = browser_auth._new_auth_dialog_state()
                browser_auth._install_auth_dialog_handlers(context, self._auth_dialog_state)
                await browser_auth._inject_setup_notice(context)
                self._require_current_operation(lifecycle_generation)
                self._landing_state = initial_landing_state(self.account_label)
                self._page = await self._ensure_worker_page(
                    lifecycle_generation=lifecycle_generation,
                )
                self._require_current_operation(lifecycle_generation)
                await self._park_after_cycle(
                    status_callback,
                    lifecycle_generation=lifecycle_generation,
                )
                self._require_current_operation(lifecycle_generation)
                if not self.running:
                    raise browser_auth.BrowserAuthError(
                        "Pearl Abyss Chrome worker could not create its parked page."
                    )
                await browser_auth._emit_status(
                    status_callback,
                    "Pearl Abyss Chrome worker is ready. Keep its window open and minimized while monitoring.",
                    "info",
                )
                self._require_current_operation(lifecycle_generation)
                return True
            except asyncio.CancelledError:
                self._playwright = playwright
                self._context = context
                # A concurrent close owns teardown after canceling this operation. External task
                # cancellation still cleans its partially started resources before propagating.
                if not self._close_requests:
                    await self._discard_resources(status_callback=None)
                raise
            except browser_auth.BrowserAuthError:
                self._playwright = playwright
                self._context = context
                await self._discard_resources(status_callback=None)
                raise
            except Exception as exc:
                self._playwright = playwright
                self._context = context
                await self._discard_resources(status_callback=None)
                raise browser_auth.BrowserAuthError(browser_auth._browser_launch_error_message(exc)) from exc

    async def acquire_market_cookies(
        self,
        status_callback=None,
        *,
        profile_path=None,
        announce_opening=True,
        **kwargs,
    ):
        """Run one auth cycle without giving up ownership of the retained context."""
        # Match the disposable entry point's lifecycle-only arguments so the task-manager router
        # can forward one stable call shape. The worker already owns its profile and its caller
        # normally announces the refresh before routing here.
        del profile_path
        if announce_opening:
            await browser_auth._emit_status(
                status_callback,
                "Using the open Pearl Abyss Chrome worker for authentication.",
                "info",
            )
        current_task = asyncio.current_task()
        if self._close_requests:
            raise browser_auth.BrowserAuthError("Pearl Abyss Chrome worker is stopping.")
        lifecycle_generation = self._lifecycle_generation
        self._active_operation_tasks.add(current_task)
        try:
            try:
                async with self._lock:
                    self._require_current_operation(lifecycle_generation)
                    if not self.running:
                        raise browser_auth.BrowserAuthError(
                            "Pearl Abyss Chrome worker is not running. Start it from App Settings before refreshing."
                        )

                    page = await self._ensure_worker_page(
                        lifecycle_generation=lifecycle_generation,
                    )
                    self._require_current_operation(lifecycle_generation)
                    self._landing_state = landing_auth_started(self._landing_state)
                    try:
                        try:
                            cookies = await browser_auth._acquire_market_cookies_in_context(
                                self._context,
                                status_callback,
                                page=page,
                                auth_dialog_state=self._auth_dialog_state,
                                inject_setup_notice=False,
                                **kwargs,
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            self._landing_state = landing_cookie_capture_finished(
                                self._landing_state,
                                captured=False,
                            )
                            raise
                        else:
                            self._landing_state = landing_cookie_capture_finished(
                                self._landing_state,
                                captured=True,
                            )
                            return cookies
                    finally:
                        if self._operation_is_current(lifecycle_generation) and not self._task_is_cancelling(
                            current_task
                        ):
                            stalled_dialog_tasks = [
                                task
                                for task in (self._auth_dialog_state or {}).get("tasks", ())
                                if not task.done()
                            ]
                            if stalled_dialog_tasks:
                                # A retained context must not collect one cancellation-resistant
                                # dialog task per auth cycle. Stop this worker so a later explicit
                                # restart gets a fresh driver/context instead.
                                await self._discard_resources(status_callback=None)
                                raise browser_auth.BrowserAuthError(
                                    "Pearl Abyss Chrome worker stopped because a browser dialog "
                                    "handler did not finish. Restart it from App Settings."
                                )
                            await self._park_after_cycle(
                                status_callback,
                                lifecycle_generation=lifecycle_generation,
                            )
            except asyncio.CancelledError:
                raise
            except browser_auth.BrowserAuthError:
                raise
            except Exception as exc:
                raise browser_auth.BrowserAuthError(
                    browser_auth._browser_launch_error_message(exc)
                ) from exc
        finally:
            self._active_operation_tasks.discard(current_task)

    async def close(self, status_callback=None):
        """Close the worker and Patchright runtime; safe to call repeatedly."""
        self._lifecycle_generation += 1
        self._close_requests += 1
        current_task = asyncio.current_task()
        try:
            try:
                operation_tasks = [
                    task
                    for task in tuple(self._active_operation_tasks)
                    if task is not current_task and not task.done()
                ]
                for task in operation_tasks:
                    task.cancel()
                if operation_tasks:
                    done, pending = await asyncio.wait(
                        operation_tasks,
                        timeout=WORKER_OPERATION_DRAIN_TIMEOUT_SECONDS,
                    )
                    if done:
                        await asyncio.gather(*done, return_exceptions=True)
                    if pending:
                        await self._interrupt_stalled_operations()
                        done_after_interrupt, pending = await asyncio.wait(
                            pending,
                            timeout=WORKER_OPERATION_DRAIN_TIMEOUT_SECONDS,
                        )
                        if done_after_interrupt:
                            await asyncio.gather(*done_after_interrupt, return_exceptions=True)
                        if pending:
                            raise browser_auth.BrowserAuthError(
                                "Pearl Abyss Chrome worker did not stop promptly. "
                                "Close Chrome manually or restart the app."
                            )
                async with self._lock:
                    had_resources = self.has_resources
                    await self._discard_resources(status_callback=status_callback)
                    return had_resources
            except asyncio.CancelledError:
                raise
            except browser_auth.BrowserAuthError:
                raise
            except Exception as exc:
                raise browser_auth.BrowserAuthError(
                    browser_auth._browser_launch_error_message(exc)
                ) from exc
        finally:
            self._close_requests -= 1

    async def _interrupt_stalled_operations(self):
        """Best-effort driver interruption after an operation ignores cancellation."""
        context = self._context
        if context is not None:
            try:
                await browser_auth._close_browser_context(context, status_callback=None)
            except Exception:
                pass

        playwright = self._playwright
        if playwright is not None:
            try:
                await asyncio.wait_for(
                    playwright.stop(),
                    timeout=PLAYWRIGHT_STOP_TIMEOUT_SECONDS,
                )
            except Exception:
                pass

    async def clear_cookies(self):
        """Clear the retained PA profile without opening a competing Chrome context."""
        async def clear_all(context):
            cookies = await context.cookies()
            await context.clear_cookies()
            return len(cookies)

        return await self._clear_retained_cookies(clear_all)

    async def clear_pa_login_session_cookies(self):
        """Force the next PA recovery through account login in the retained context."""
        return await self._clear_retained_cookies(
            browser_auth._clear_pa_login_session_cookies_in_context
        )

    async def _clear_retained_cookies(self, clear_operation):
        if self._close_requests:
            raise browser_auth.BrowserAuthError("Pearl Abyss Chrome worker is stopping.")
        lifecycle_generation = self._lifecycle_generation
        current_task = asyncio.current_task()
        self._active_operation_tasks.add(current_task)
        try:
            try:
                async with self._lock:
                    self._require_current_operation(lifecycle_generation)
                    if not self.context_alive:
                        raise browser_auth.BrowserAuthError("Pearl Abyss Chrome worker is not running.")
                    cleared_count = await clear_operation(self._context)
                    self._require_current_operation(lifecycle_generation)
                    self._landing_state = landing_cookies_cleared(self._landing_state)
                    if self.running:
                        await self._park_after_cycle(
                            lifecycle_generation=lifecycle_generation,
                        )
                    return cleared_count
            except asyncio.CancelledError:
                raise
            except browser_auth.BrowserAuthError:
                raise
            except Exception as exc:
                raise browser_auth.BrowserAuthError(
                    browser_auth._browser_launch_error_message(exc)
                ) from exc
        finally:
            self._active_operation_tasks.discard(current_task)

    async def update_session_validation(self, valid, *, checked_at=None):
        """Update the existing parked page without ever creating or reopening one."""
        self._landing_state = landing_validation_finished(
            self._landing_state,
            valid=bool(valid),
            checked_at=checked_at,
        )
        if self._close_requests:
            return False

        lifecycle_generation = self._lifecycle_generation
        current_task = asyncio.current_task()
        self._active_operation_tasks.add(current_task)
        try:
            try:
                async with self._lock:
                    self._require_current_operation(lifecycle_generation)
                    live_pages = self._live_pages()
                    page = self._page if self._page in live_pages else None
                    if page is None or getattr(page, "url", "") != PARKED_PAGE_URL:
                        return False
                    rendered = await render_browser_landing(
                        page,
                        self._landing_state,
                        timeout_ms=PARK_PAGE_TIMEOUT_MS,
                    )
                    self._require_current_operation(lifecycle_generation)
                    return rendered
            except asyncio.CancelledError:
                raise
            except Exception:
                # Landing state is cosmetic. A disconnected or manually closed page must not
                # affect the already-completed HTTP session result or create a replacement page.
                return False
        finally:
            self._active_operation_tasks.discard(current_task)

    def _live_pages(self):
        context = self._context
        if context is None:
            return []
        try:
            pages = getattr(context, "pages", []) or []
        except Exception:
            return []
        return [page for page in pages if not browser_auth._page_is_closed(page)]

    def _operation_is_current(self, lifecycle_generation):
        return (
            lifecycle_generation == self._lifecycle_generation
            and not self._close_requests
        )

    def _require_current_operation(self, lifecycle_generation):
        if not self._operation_is_current(lifecycle_generation):
            raise browser_auth.BrowserAuthError("Pearl Abyss Chrome worker is stopping.")

    @staticmethod
    def _task_is_cancelling(task):
        cancelling = getattr(task, "cancelling", None)
        return bool(cancelling and cancelling())

    async def _ensure_worker_page(self, *, lifecycle_generation):
        self._require_current_operation(lifecycle_generation)
        pages = self._live_pages()
        page = self._page if self._page in pages else None
        if page is None:
            self._require_current_operation(lifecycle_generation)
            page = pages[0] if pages else await asyncio.wait_for(
                self._context.new_page(),
                timeout=PARK_PAGE_TIMEOUT_MS / 1000,
            )
            self._require_current_operation(lifecycle_generation)
        self._page = page

        extras = [candidate for candidate in self._live_pages() if candidate is not page]
        if extras:
            self._require_current_operation(lifecycle_generation)
            await asyncio.gather(
                *[browser_auth._close_page_quickly(candidate) for candidate in extras],
                return_exceptions=True,
            )
            self._require_current_operation(lifecycle_generation)
        return page

    async def _park_after_cycle(self, status_callback=None, *, lifecycle_generation):
        self._require_current_operation(lifecycle_generation)
        if not self.running:
            return
        old_pages = self._live_pages()
        preferred_page = self._page if self._page in old_pages else None
        park_candidates = ([preferred_page] if preferred_page is not None else []) + [
            page for page in old_pages if page is not preferred_page
        ]
        parked_page = None
        # Recycle a live auth page instead of opening a fresh tab. Creating a page can restore a
        # minimized Chrome window, while navigating to about:blank still releases the prior
        # document. A page is created only by _ensure_worker_page during initial startup.
        for candidate in park_candidates:
            try:
                self._require_current_operation(lifecycle_generation)
                await candidate.goto(
                    PARKED_PAGE_URL,
                    wait_until="commit",
                    timeout=PARK_PAGE_TIMEOUT_MS,
                )
                self._require_current_operation(lifecycle_generation)
                parked_page = candidate
                break
            except asyncio.CancelledError:
                raise
            except Exception:
                self._require_current_operation(lifecycle_generation)
        if parked_page is None:
            self._page = None
            if old_pages:
                await asyncio.gather(
                    *[browser_auth._close_page_quickly(page) for page in old_pages],
                    return_exceptions=True,
                )
            browser_auth._prune_auth_dialog_pages(self._auth_dialog_state)
            await browser_auth._emit_status(
                status_callback,
                "Pearl Abyss Chrome worker lost its parked page. Restart it from App Settings.",
                "warning",
            )
            return

        self._page = parked_page
        await render_browser_landing(
            parked_page,
            self._landing_state,
            timeout_ms=PARK_PAGE_TIMEOUT_MS,
        )
        self._require_current_operation(lifecycle_generation)
        pages_to_close = [page for page in old_pages if page is not parked_page]
        if pages_to_close:
            await asyncio.gather(
                *[browser_auth._close_page_quickly(page) for page in pages_to_close],
                return_exceptions=True,
            )
        remaining_pages = [page for page in self._live_pages() if page is not parked_page]
        if remaining_pages or browser_auth._page_is_closed(parked_page):
            await browser_auth._emit_status(
                status_callback,
                "Pearl Abyss Chrome worker could not close an old page and was stopped. Restart it from App Settings.",
                "warning",
            )
            await self._discard_resources(status_callback=None)
            raise browser_auth.BrowserAuthError(
                "Pearl Abyss Chrome worker could not restore its one-page parked state."
            )
        browser_auth._prune_auth_dialog_pages(self._auth_dialog_state)

    async def _discard_resources(self, status_callback=None):
        context, playwright = self._context, self._playwright
        auth_dialog_state = self._auth_dialog_state
        context_released = context is None
        runtime_stopped = playwright is None

        try:
            if auth_dialog_state is not None:
                await browser_auth._drain_auth_dialog_tasks(auth_dialog_state)
            if context is not None:
                context_released = await browser_auth._close_browser_context(context, status_callback)
        finally:
            if playwright is not None:
                try:
                    await asyncio.wait_for(
                        playwright.stop(),
                        timeout=PLAYWRIGHT_STOP_TIMEOUT_SECONDS,
                    )
                    runtime_stopped = True
                except Exception:
                    pass

        # A confirmed context close or a confirmed stop of its owned runtime releases the profile.
        profile_released = context_released or (playwright is not None and runtime_stopped)
        if not profile_released:
            raise browser_auth.BrowserAuthError(
                "Pearl Abyss Chrome worker could not release its browser profile. Close Chrome manually and retry."
            )

        # Do not forget a driver runtime whose stop failed. The Chrome profile is safe once the
        # context closed, but keeping this handle lets a later close retry instead of leaking one
        # Patchright driver per restart.
        if playwright is not None and not runtime_stopped:
            if self._context is context:
                self._context = None
            self._page = None
            self._auth_dialog_state = None
            raise browser_auth.BrowserAuthError(
                "Pearl Abyss Chrome closed, but its browser driver did not stop. Retry or restart the app."
            )

        if self._context is context:
            self._context = None
        if self._playwright is playwright:
            self._playwright = None
        self._page = None
        self._auth_dialog_state = None
