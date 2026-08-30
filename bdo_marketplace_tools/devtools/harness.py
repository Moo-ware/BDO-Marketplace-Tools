"""Opt-in developer facade built on narrow production service seams."""

import asyncio

from bdo_marketplace_tools.devtools.probes import SingleItemProbeController
from bdo_marketplace_tools.devtools.scenarios import (
    DEBUG_BUNDLED_OUTFIT_LISTING,
    DEBUG_BUNDLED_PURCHASE_TICK_SECONDS,
    DEBUG_MULTI_OUTFIT_INITIAL_LISTING,
    DEBUG_MULTI_OUTFIT_JOINED_LISTING,
    DEBUG_OUTFIT_LISTING,
    LIVE_BUY_ERROR_TEST_TARGET,
    SIMULATED_SESSION_EMAIL,
    SINGLE_ITEM_TEST_TARGET,
    live_buy_error_test_listing,
    simulated_purchase_summary,
)
from bdo_marketplace_tools.market.browser_auth import BrowserAuthError
from bdo_marketplace_tools.services.session_recovery import SessionRecoveryOptions
from bdo_marketplace_tools.storage.app_settings import PA_CREDENTIALS_MODE, STEAM_BROWSER_MODE
from bdo_marketplace_tools.ui.display import highlight


class DeveloperSessionFaults:
    """One authentication-context-scoped synthetic expiry request."""

    def __init__(self):
        self._armed_context = None

    def arm(self, auth_context):
        self._armed_context = auth_context

    def options_for(self, auth_context=None):
        if self._armed_context is None:
            return SessionRecoveryOptions()
        if auth_context != self._armed_context:
            self._armed_context = None
            return SessionRecoveryOptions()
        return SessionRecoveryOptions(force_expired=True, clear_market_session=True)

    def session_validated(self, auth_context=None):
        if self._armed_context == auth_context:
            self._armed_context = None

    def context_changed(self, auth_context=None):
        if self._armed_context != auth_context:
            self._armed_context = None


class SwitchablePurchaseExecutor:
    """Delegate normally, or synthesize success for one exact auth context."""

    def __init__(self, real_executor):
        self.real_executor = real_executor
        self._simulation_context = None

    @property
    def simulation_context(self):
        return self._simulation_context

    @property
    def simulation_enabled(self):
        return self._simulation_context is not None

    def enable_simulation(self, auth_context):
        self._simulation_context = auth_context

    def disable_simulation(self):
        self._simulation_context = None

    def context_changed(self, auth_context=None):
        if self._simulation_context != auth_context:
            self._simulation_context = None

    async def submit(
        self,
        buy_list,
        *,
        auth_context,
        purchase_delay_bounds,
        on_purchase,
        stop_event=None,
    ):
        if self._simulation_context is not None:
            if self._simulation_context == auth_context:
                summary = simulated_purchase_summary(buy_list)
                if on_purchase is not None:
                    for record in summary["purchase_records"]:
                        try:
                            on_purchase(record)
                        except Exception:
                            pass
                return summary
            self._simulation_context = None

        return await self.real_executor.submit(
            buy_list,
            auth_context=auth_context,
            purchase_delay_bounds=purchase_delay_bounds,
            on_purchase=on_purchase,
            stop_event=stop_event,
        )


class DeveloperTools:
    """Coordinate developer scenarios without making the production manager own them."""

    def __init__(self, manager, api_handler, session_faults, purchase_executor):
        self.manager = manager
        self.api_handler = api_handler
        self.session_faults = session_faults
        self.purchase_executor = purchase_executor
        self.probe = SingleItemProbeController(manager, api_handler)

    @property
    def simulated_session_enabled(self):
        return bool(
            self.purchase_executor.simulation_enabled
            and self.purchase_executor.simulation_context
            == self.manager.authentication_context()
        )

    @property
    def probe_running(self):
        return self.probe.running

    @property
    def probe_purchase_enabled(self):
        return self.probe.purchase_enabled

    @property
    def probe_started_at(self):
        return self.probe.started_at

    @property
    def single_item_target(self):
        return dict(SINGLE_ITEM_TEST_TARGET)

    @property
    def live_buy_target(self):
        return dict(LIVE_BUY_ERROR_TEST_TARGET)

    def set_simulated_session(self, enabled):
        enabled = bool(enabled)
        if enabled and self.probe_running:
            return False

        if enabled:
            auth_context = self.manager.authentication_context()
            self.session_faults.session_validated(auth_context)
            self.purchase_executor.enable_simulation(auth_context)
            self.api_handler.login_status = True
            if not getattr(self.api_handler, "email", None):
                self.api_handler.email = SIMULATED_SESSION_EMAIL
            return True

        self.purchase_executor.disable_simulation()
        self.api_handler.login_status = False
        if getattr(self.api_handler, "email", None) == SIMULATED_SESSION_EMAIL:
            self.api_handler.email = None
        self.manager.set_purchase_submission_enabled(False)
        return True

    def expire_marketplace_session(self):
        self._disable_simulation_identity()
        self.manager.invalidate_marketplace_session_state()
        auth_context = self.manager.authentication_context()
        self.session_faults.arm(auth_context)
        self.manager.add_event(
            "Test: app marketplace session cleared; the next recovery will also clear the browser "
            "marketplace session before re-authentication.",
            "warning",
        )
        return True

    async def expire_pa_login_session(self):
        if self.manager.uses_steam_browser_session():
            self.manager.add_event(
                "Expire PA Login is only available in Pearl Abyss Account mode.",
                "warning",
            )
            return False
        if not self.manager.pa_browser_keep_open or not self.manager.pa_browser_worker_running:
            self.manager.add_event(
                "Expire PA Login requires the Keep Open Chrome worker to be running.",
                "warning",
                notable=True,
            )
            return False

        self._disable_simulation_identity()
        starting_context = self.manager.authentication_context()
        try:
            cleared_count = await self.manager.clear_pa_login_session_cookies(
                expected_context=starting_context
            )
        except asyncio.CancelledError:
            committed = self._arm_pa_expiry_if_committed(starting_context)
            message = "Expire PA Login was interrupted."
            if committed:
                message = (
                    "Expire PA Login was interrupted after the app marketplace session was cleared. "
                    "Retry the action before testing credential login."
                )
            self.manager.add_event(
                message,
                "warning",
                notable=True,
            )
            raise
        except BrowserAuthError as exc:
            committed = self._arm_pa_expiry_if_committed(starting_context)
            qualifier = " after the app marketplace session was cleared" if committed else ""
            self.manager.add_event(
                f"Expire PA Login failed{qualifier}: {exc}",
                "error",
                notable=True,
            )
            return False

        if cleared_count is False or cleared_count is None:
            return False
        if not self._arm_pa_expiry_if_committed(starting_context):
            return False

        self.manager.add_event(
            "Test: app marketplace session plus browser marketplace and Pearl Abyss login "
            f"cookies cleared ({highlight(cleared_count)} cookies); consent and saved "
            "credentials were preserved. Run Session Check to test automatic credential login.",
            "warning",
            notable=True,
        )
        return True

    def toggle_steam_auto_reauth(self):
        if self.manager.account_mode != STEAM_BROWSER_MODE:
            return None
        enabled = not self.manager.steam_auto_reauth_enabled
        self.manager.set_steam_auto_reauth_enabled(enabled)
        return enabled

    async def run_reauthentication_check(self):
        self.manager.add_event("Simulated purchase response: login session expired.", "warning")
        return await self.manager.recover_purchase_session(
            force_browser_refresh=True,
            clear_market_session=True,
        )

    async def run_session_check(self):
        self.manager.add_event("Test: running the periodic session check now.", "info")
        return await self.manager.run_session_check_once()

    def reset_steam_setup(self):
        return self.manager.reset_steam_initial_setup_status()

    async def clear_browser_cookies(self):
        return await self.manager.clear_steam_browser_session_cookies()

    async def clear_market_cookies_keep_steam(self):
        return await self.manager.clear_market_cookies_keep_steam_login()

    async def fake_detection(self):
        self.manager.reset_detection_episodes()
        await self.manager.process_detected_outfits(
            DEBUG_OUTFIT_LISTING,
            allow_purchase=False,
        )
        return True

    async def fake_multi_detection(self):
        self.manager.reset_detection_episodes()
        await self.manager.process_detected_outfits(
            DEBUG_MULTI_OUTFIT_INITIAL_LISTING,
            allow_purchase=False,
        )
        await self.manager.process_detected_outfits(
            DEBUG_MULTI_OUTFIT_JOINED_LISTING,
            allow_purchase=False,
        )
        return True

    async def simulate_purchase_success(self):
        self.manager.reset_detection_episodes()
        previous_context = self.purchase_executor.simulation_context
        scenario_context = self.manager.authentication_context()
        self.purchase_executor.enable_simulation(scenario_context)
        try:
            await self.manager.process_detected_outfits(
                DEBUG_OUTFIT_LISTING,
                allow_purchase=True,
            )
        finally:
            if self.manager.authentication_context() != scenario_context or previous_context is None:
                self.purchase_executor.disable_simulation()
            else:
                self.purchase_executor.enable_simulation(previous_context)
        return True

    async def simulate_bundled_purchase_success(self, progress_callback=None):
        self.manager.reset_detection_episodes()
        await self.manager.process_detected_outfits(
            DEBUG_BUNDLED_OUTFIT_LISTING,
            allow_purchase=False,
        )
        adjusted_buy_list = await self.manager.adjust_prices(DEBUG_BUNDLED_OUTFIT_LISTING)
        item_id, stock, price = adjusted_buy_list[0]
        purchase_records = []
        for index in range(int(stock)):
            record = {
                "item_id": item_id,
                "price": int(price),
                "count": 1,
                "result_code": 0,
            }
            purchase_records.append(record)
            self.manager.note_purchase_progress(record)
            if progress_callback is not None:
                try:
                    progress_callback()
                except Exception:
                    pass
            if index < int(stock) - 1:
                await asyncio.sleep(DEBUG_BUNDLED_PURCHASE_TICK_SECONDS)

        await self.manager.record_purchase_summary(
            {
                "purchase_records": purchase_records,
                "events": [
                    {
                        "level": "success",
                        "message": (
                            "Simulated bundled buy list succeeded for "
                            f"{len(purchase_records)} outfits."
                        ),
                    }
                ],
            }
        )
        return True

    async def run_live_buy_error_probe(self):
        if self.simulated_session_enabled:
            self.manager.add_event(
                "Live buy error probe requires a real marketplace session; disable test session first.",
                "warning",
            )
            return False
        if not getattr(self.api_handler, "login_status", False):
            self.manager.add_event(
                "Live buy error probe requires an online marketplace session.",
                "warning",
            )
            return False

        target = LIVE_BUY_ERROR_TEST_TARGET
        self.manager.reset_detection_episodes()
        self.manager.add_event(
            f"Live buy error probe submitting item {target['main_key']} "
            f"at {target['max_buy_price']} silver.",
            "warning",
        )
        await self.manager.process_detected_outfits(
            live_buy_error_test_listing(target),
            allow_purchase=True,
            item_noun="live buy probe",
            adjust_pricing=False,
        )
        return True

    async def start_single_item_probe(self, allow_purchase=False):
        if allow_purchase:
            if self.simulated_session_enabled:
                self.manager.add_event(
                    "Disable the simulated test session before starting the live single-item buy test.",
                    "warning",
                )
                return False
            if not getattr(self.api_handler, "login_status", False):
                self.manager.add_event(
                    "Login required before starting the single-item buy test.",
                    "warning",
                )
                return False
        return await self.probe.start(allow_purchase=allow_purchase)

    async def stop_single_item_probe(self):
        return await self.probe.stop()

    async def shutdown(self):
        await self.probe.shutdown()
        self._disable_simulation_identity()

    def _disable_simulation_identity(self):
        self.purchase_executor.disable_simulation()
        if getattr(self.api_handler, "email", None) == SIMULATED_SESSION_EMAIL:
            self.api_handler.email = None
            self.api_handler.login_status = False

    def _arm_pa_expiry_if_committed(self, starting_context):
        current_context = self.manager.authentication_context()
        committed = (
            current_context[1] == PA_CREDENTIALS_MODE
            and current_context[0] == starting_context[0] + 1
        )
        if committed:
            self.session_faults.arm(current_context)
        return committed
