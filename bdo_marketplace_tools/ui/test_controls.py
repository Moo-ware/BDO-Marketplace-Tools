from __future__ import annotations

import random
from typing import TYPE_CHECKING

from rich.table import Table
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from bdo_marketplace_tools.ui.display import format_compact_silver
from bdo_marketplace_tools.ui.modals import ConfirmBuyModeScreen, DashboardModalScreen
from bdo_marketplace_tools.ui.widgets import ModalAction

if TYPE_CHECKING:
    from bdo_marketplace_tools.devtools.harness import DeveloperTools


TEST_LOG_MESSAGES = (
    ("Synthetic scan completed: no outfits detected.", "info"),
    ("Synthetic outfit detected in premium category.", "success"),
    ("Synthetic purchase skipped: test spend cap reached.", "warning"),
    ("Synthetic session refresh warning for layout testing.", "warning"),
    ("Synthetic marketplace response error for log sizing.", "error"),
    ("Synthetic purchase request succeeded for one outfit.", "success"),
)


class ConfirmLiveTestBuyScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]
    CSS = DashboardModalScreen.CSS
    AUTO_FOCUS = None

    def __init__(self, item_id: str, price: str, account: str, buy_delay: str) -> None:
        super().__init__()
        self.item_id = item_id
        self.price = price
        self.account = account
        self.buy_delay = buy_delay

    def compose(self) -> ComposeResult:
        review = Table.grid(padding=(0, 2))
        review.add_column(style="bold #6f6f6f", no_wrap=True)
        review.add_column()
        review.add_row("Account", self.account)
        review.add_row("Item", self.item_id)
        review.add_row("Price", self.price)
        review.add_row("Buy delay", self.buy_delay)
        with Vertical(id="confirm-dialog", classes="modal-card") as dialog:
            dialog.border_title = "Confirm Live Test Buy"
            dialog.border_subtitle = "esc"
            yield Static(
                "This sends one real marketplace buy request through the normal purchase pipeline:",
                classes="modal-note",
            )
            yield Static(review)
            with Horizontal(id="confirm-actions", classes="modal-actions"):
                yield ModalAction("Submit Test Buy", "confirm-live-test-buy")
                yield Static("", classes="modal-actions-spacer")
                yield ModalAction("Cancel", "cancel-live-test-buy")

    def on_modal_action_pressed(self, event: ModalAction.Pressed) -> None:
        self.dismiss(event.action.action_id == "confirm-live-test-buy")

    def action_cancel(self) -> None:
        self.dismiss(False)


class TestControls(VerticalScroll):
    """Optional developer sidebar; absent from normal application composition."""

    DEFAULT_CSS = """
    #body {
        height: 1fr;
    }

    TestControls {
        width: 26;
        min-width: 22;
        height: 1fr;
        margin-left: 1;
        overflow-y: auto;
    }

    TestControls Button {
        width: 100%;
        min-width: 0;
        margin: 0;
        text-align: left;
        content-align: left middle;
    }
    """

    BUTTONS = (
        ("Add Test Log", "add-test-log"),
        ("Toggle Test Session", "toggle-test-session"),
        ("Auto Reauth", "toggle-auto-reauth"),
        ("Expire Session", "expire-test-session"),
        ("Expire PA Login", "expire-pa-login"),
        ("Run Session Check", "run-session-check"),
        ("Reauth Check", "run-reauth-check"),
        ("Reset Steam Setup", "reset-steam-setup"),
        ("Clear Browser Cookies", "clear-browser-cookies"),
        ("Clear (Keep Steam)", "clear-cookies-keep-steam"),
        ("Start Test Scan", "start-test-monitor"),
        ("Start Test Buy", "start-test-buy"),
        ("Live 2.9B Buy", "live-buy-error-probe"),
        ("Stop Test Scan", "stop-test-monitor"),
        ("Fake Detection", "fake-detection"),
        ("Fake Multi Detect", "fake-multi-detection"),
        ("Fake Buy Success", "fake-buy-success"),
        ("Fake Bundle x8", "fake-bundled-buy"),
    )
    BUTTON_IDS = frozenset(button_id for _label, button_id in BUTTONS)

    def __init__(self, devtools: DeveloperTools) -> None:
        super().__init__(id="test-controls")
        self.devtools = devtools

    def compose(self) -> ComposeResult:
        for label, button_id in self.BUTTONS:
            yield Button(label, id=button_id, compact=True)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id not in self.BUTTON_IDS:
            return

        event.stop()
        event.button.blur()
        if button_id == "add-test-log":
            await self.add_test_log()
        elif button_id == "toggle-test-session":
            await self.toggle_test_session()
        elif button_id == "toggle-auto-reauth":
            await self.toggle_test_steam_auto_reauth()
        elif button_id == "expire-test-session":
            await self.expire_test_session()
        elif button_id == "expire-pa-login":
            self.app.run_worker(
                self.expire_test_pa_login(),
                name="expire-pa-login",
                group="pa-login-expiry",
                exclusive=True,
            )
        elif button_id == "run-session-check":
            self.app.run_worker(
                self.run_test_session_check(),
                name="test-session-check",
                group="actions",
                exclusive=True,
            )
        elif button_id == "run-reauth-check":
            self.app.run_worker(
                self.run_test_reauthentication_check(),
                name="test-reauth-check",
                group="actions",
                exclusive=True,
            )
        elif button_id == "reset-steam-setup":
            await self.reset_test_steam_setup_status()
        elif button_id == "clear-browser-cookies":
            self.app.run_worker(
                self.clear_test_browser_cookies(),
                name="clear-browser-cookies",
                group="actions",
                exclusive=True,
            )
        elif button_id == "clear-cookies-keep-steam":
            self.app.run_worker(
                self.clear_test_cookies_keep_steam(),
                name="clear-cookies-keep-steam",
                group="actions",
                exclusive=True,
            )
        elif button_id == "start-test-monitor":
            await self.start_single_item_test_monitor()
        elif button_id == "start-test-buy":
            await self.start_single_item_test_monitor(allow_purchase=True)
        elif button_id == "live-buy-error-probe":
            await self.start_live_buy_error_probe()
        elif button_id == "stop-test-monitor":
            await self.stop_single_item_test_monitor()
        elif button_id == "fake-detection":
            await self.fake_outfit_detection()
        elif button_id == "fake-multi-detection":
            await self.fake_multi_outfit_detection()
        elif button_id == "fake-buy-success":
            await self.fake_buy_success()
        elif button_id == "fake-bundled-buy":
            self.app.run_worker(
                self.fake_bundled_buy_success(),
                name="fake-bundled-buy",
                group="debug-actions",
                exclusive=True,
            )

    async def add_test_log(self) -> None:
        message, level = random.choice(TEST_LOG_MESSAGES)
        self.app.task_manager.add_event(message, level)
        self.app.set_status("Synthetic event added.")
        await self.app.return_to_dashboard()

    async def toggle_test_session(self) -> None:
        if self.devtools.probe_running:
            self.app.set_status("Stop the single-item test monitor before changing simulated session state.", "warning")
            await self.app.return_to_dashboard()
            return

        enabled = not self.devtools.simulated_session_enabled
        self.devtools.set_simulated_session(enabled)
        if enabled:
            self.app.set_status(
                "Test session marked valid. Buy mode will use simulated purchase responses.",
                "success",
            )
        else:
            self.app.sync_mode_switches(False)
            self.app.set_status("Test session marked invalid. Buy mode returned to watch only.", "warning")
        self.app.refresh_modal_summaries()
        await self.app.return_to_dashboard()

    async def toggle_test_steam_auto_reauth(self) -> None:
        enabled = self.devtools.toggle_steam_auto_reauth()
        if enabled is None:
            self.app.set_status("Select Steam Account before toggling automatic re-authentication.", "warning")
        elif enabled:
            self.app.set_status("Steam automatic re-authentication debug override enabled.", "success")
        else:
            self.app.set_status("Steam automatic re-authentication debug override disabled.", "warning")
        self.app.refresh_modal_summaries()
        await self.app.return_to_dashboard()

    async def expire_test_session(self) -> None:
        if self.devtools.expire_marketplace_session():
            self.app.set_status(
                "Test app session cleared. The next Session Check or Reauth Check will also clear "
                "the browser marketplace session.",
                "warning",
            )
            self.app.refresh_modal_summaries()
        await self.app.return_to_dashboard()

    async def expire_test_pa_login(self) -> None:
        self.app.set_status("Clearing the retained PA login session; the app remains usable.", "info")
        expired = await self.devtools.expire_pa_login_session()
        if expired:
            self.app.set_status(
                "PA login expired. Run Session Check to test automatic credential login.",
                "warning",
            )
        elif self.app.task_manager.uses_steam_browser_session():
            self.app.set_status("Expire PA Login is only available in Pearl Abyss Account mode.", "warning")
        else:
            self.app.set_status("Could not expire PA login. Start the Keep Open worker and retry.", "warning")
        self.app.refresh_modal_summaries()
        await self.app.return_to_dashboard()

    async def run_test_reauthentication_check(self) -> None:
        self.app.set_status("Running test re-authentication check; the app remains usable while Chrome works.", "info")
        recovered = await self.devtools.run_reauthentication_check()
        if recovered:
            self.app.set_status("Test re-authentication check succeeded.")
        elif self.app.task_manager.uses_steam_browser_session():
            self.app.set_status("Steam Account refresh required after test re-authentication check.")
        else:
            self.app.set_status("Test re-authentication check failed.")
        self.app.refresh_modal_summaries()
        await self.app.return_to_dashboard()

    async def run_test_session_check(self) -> None:
        self.app.set_status("Running session check; the app remains usable while Chrome works.", "info")
        result = await self.devtools.run_session_check()
        if result:
            self.app.set_status("Session check complete: session valid or re-authenticated. See log.", "info")
        else:
            self.app.set_status("Session check: re-authentication required or failed. See log.", "warning")
        self.app.refresh_modal_summaries()
        await self.app.return_to_dashboard()

    async def reset_test_steam_setup_status(self) -> None:
        if self.devtools.reset_steam_setup():
            self.app.set_status("Initial Steam setup status reset.", "warning")
            self.app.refresh_credentials_summary()
            self.app.refresh_settings_summary()
            self.app.refresh_live_widgets()
        else:
            self.app.set_status("Initial Steam setup status reset failed.", "warning")

    async def clear_test_browser_cookies(self) -> None:
        if await self.devtools.clear_browser_cookies():
            self.app.set_status("Browser cookies cleared from the Steam profile.", "warning")
        else:
            self.app.set_status("Browser cookie clear failed.", "warning")

    async def clear_test_cookies_keep_steam(self) -> None:
        if await self.devtools.clear_market_cookies_keep_steam():
            self.app.set_status("Cleared non-Steam cookies; kept Steam login. Run Reauth Check to test.", "warning")
        else:
            self.app.set_status("Cookie clear skipped (Steam Account mode only) or failed; see log.", "warning")

    async def start_single_item_test_monitor(self, allow_purchase: bool = False) -> None:
        if self.devtools.probe_running:
            self.app.set_status("Single-item test monitor already running; no additional task started.", "info")
            await self.app.return_to_dashboard()
            return

        if self.app.task_manager.checker_enabled:
            self.app.set_status("Stop the normal monitor before starting the single-item test monitor.", "warning")
            await self.app.return_to_dashboard()
            return

        if allow_purchase:
            if self.devtools.simulated_session_enabled:
                self.app.set_status(
                    "Disable the simulated test session before starting the live single-item buy test.",
                    "warning",
                )
                await self.app.return_to_dashboard()
                return

            if not self.app.api_handler.login_status:
                self.app.set_status(
                    "Login required before starting the single-item buy test. Refresh the marketplace session first.",
                    "warning",
                )
                await self.app.return_to_dashboard()
                return

            self.app.push_screen(
                ConfirmBuyModeScreen(
                    account=self.app.session_account_label(),
                    polling=(
                        f"{self.app.task_manager.current_delay_label()} "
                        f"({self.app.task_manager.current_delay_range()})"
                    ),
                    spend_cap=format_compact_silver(self.app.task_manager.max_spend),
                    buy_delay=self.app.task_manager.purchase_delay_range(),
                ),
                callback=self._handle_single_item_test_buy_confirmation,
            )
            return

        await self._start_single_item_test_monitor_now(allow_purchase=False)

    def _handle_single_item_test_buy_confirmation(self, confirmed: bool) -> None:
        if not confirmed:
            self.app.set_status("Single-item buy test canceled.", "info")
            return
        self.app.run_worker(
            self._start_single_item_test_monitor_now(allow_purchase=True),
            name="start-single-item-buy-test",
            group="actions",
            exclusive=True,
        )

    async def start_live_buy_error_probe(self) -> None:
        if self.devtools.simulated_session_enabled:
            self.app.set_status("Disable the simulated test session before sending the live buy error probe.", "warning")
            await self.app.return_to_dashboard()
            return

        if not self.app.api_handler.login_status:
            self.app.set_status(
                "Login required before sending the live buy error probe. Refresh the marketplace session first.",
                "warning",
            )
            await self.app.return_to_dashboard()
            return

        target = self.devtools.live_buy_target
        self.app.push_screen(
            ConfirmLiveTestBuyScreen(
                item_id=target["main_key"],
                price=format_compact_silver(int(target["max_buy_price"])),
                account=self.app.session_account_label(),
                buy_delay=self.app.task_manager.purchase_delay_range(),
            ),
            callback=self._handle_live_buy_error_probe_confirmation,
        )

    def _handle_live_buy_error_probe_confirmation(self, confirmed: bool) -> None:
        if not confirmed:
            self.app.set_status("Live buy error probe canceled.", "info")
            return
        self.app.run_worker(
            self._run_live_buy_error_probe_now(),
            name="live-buy-error-probe",
            group="actions",
            exclusive=True,
        )

    async def _run_live_buy_error_probe_now(self) -> None:
        target = self.devtools.live_buy_target
        self.app.set_status(
            f"Submitting live buy error probe for item {target['main_key']} "
            f"at {format_compact_silver(int(target['max_buy_price']))}.",
            "warning",
        )
        submitted = await self.devtools.run_live_buy_error_probe()
        if submitted:
            self.app.set_status("Live buy error probe completed. Check Core logs for the marketplace response.", "warning")
        else:
            self.app.set_status("Live buy error probe did not run. Check Core logs for details.", "warning")
        await self.app.return_to_dashboard()

    async def _start_single_item_test_monitor_now(self, allow_purchase: bool = False) -> None:
        item_name = self.devtools.single_item_target["name"]
        started = await self.devtools.start_single_item_probe(allow_purchase=allow_purchase)
        if started:
            if allow_purchase:
                self.app.set_status(
                    f"Single-item buy test started for {item_name}. Public detection uses the normal buy pipeline.",
                    "warning",
                )
            else:
                self.app.set_status(
                    f"Single-item test monitor started for {item_name}. Public scan only; live buy calls are disabled.",
                    "warning",
                )
        elif self.devtools.probe_running:
            self.app.set_status("Single-item test monitor already running; no additional task started.", "info")
        else:
            self.app.set_status("Single-item test monitor did not start.", "warning")
        await self.app.return_to_dashboard()

    async def stop_single_item_test_monitor(self) -> None:
        if await self.devtools.stop_single_item_probe():
            self.app.set_status("Single-item test monitor stopped.", "info")
        else:
            self.app.set_status("Single-item test monitor already stopped.", "info")
        await self.app.return_to_dashboard()

    async def fake_outfit_detection(self) -> None:
        await self.devtools.fake_detection()
        self.app.set_status("Fake detection processed through watch-only path.")
        await self.app.return_to_dashboard()

    async def fake_multi_outfit_detection(self) -> None:
        await self.devtools.fake_multi_detection()
        self.app.set_status("Fake multi-listing detection processed.")
        await self.app.return_to_dashboard()

    async def fake_buy_success(self) -> None:
        await self.devtools.simulate_purchase_success()
        self.app.set_status("Fake detection and purchase recorded.")
        await self.app.return_to_dashboard()

    async def fake_bundled_buy_success(self) -> None:
        self.app.set_status("Fake bundled buy list running: 8 outfits.")
        await self.app.return_to_dashboard()
        await self.devtools.simulate_bundled_purchase_success(
            progress_callback=self.app.refresh_live_widgets
        )
        self.app.refresh_live_widgets()
        self.app.set_status("Fake bundled buy list recorded: 8 outfits.")
