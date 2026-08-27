import random
import time
from typing import Optional

from rich.align import Align
from rich.console import Group, RenderableType
from rich.json import JSON
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, RichLog, Select, Static

from bdo_marketplace_tools.market.api_handler import marketplace_silver_balance
from bdo_marketplace_tools.market.test_mode import LIVE_BUY_ERROR_TEST_TARGET, SINGLE_ITEM_TEST_TARGET
from bdo_marketplace_tools.storage.app_settings import PA_CREDENTIALS_MODE, STEAM_BROWSER_MODE
from bdo_marketplace_tools.storage.browser_profile_cache import (
    format_storage_size,
)
from bdo_marketplace_tools.storage.credentials import CredentialStoreError, clear_credentials, load_credentials, save_credentials
from bdo_marketplace_tools.services.update_checker import RELEASES_URL
from bdo_marketplace_tools.version import APP_CHANNEL, APP_VERSION, SETTINGS_SCHEMA_VERSION
from bdo_marketplace_tools.ui.display import (
    APP_TITLE,
    COLOR_BRAND,
    COLOR_ERROR,
    COLOR_GOLD,
    COLOR_INFO,
    COLOR_TEXT_MUTED,
    COLOR_WARNING,
    format_compact_number,
    format_compact_silver,
    format_percent,
    highlight,
    highlight_brand,
    highlight_silver,
    mask_email,
)
from bdo_marketplace_tools.ui.modals import (
    BuyDelayModal,
    ConfirmLiveTestBuyScreen,
    ConfirmBuyModeScreen,
    CredentialsModal,
    DashboardModalScreen,
    MonitorModal,
    PACredentialsModal,
    PollingModal,
    SessionModal,
    SessionRefreshConfirmScreen,
    SpendCapModal,
)
from bdo_marketplace_tools.ui.styles import APP_CSS
from bdo_marketplace_tools.ui.theme import (
    BANNER_ART,
    BANNER_BLINK_ART,
    BANNER_STAR_ART,
    DEFAULT_THEME,
    IDLE_DOT,
    MASCOT_ZZZ_TRAIL,
    RUNNING_QUIPS,
    STATUS_DOT,
    STATUS_STYLES,
    TEST_LOG_MESSAGES,
    rate_spectrum_style,
)
from bdo_marketplace_tools.storage import stats_db
from bdo_marketplace_tools.ui.charts import hour_heatmap_chart, weekday_chart
from bdo_marketplace_tools.ui.widgets import (
    CredentialActionTile,
    DailyActivityChart,
    DashboardTile,
    LogFilterOption,
    ModalAction,
    MonitorModeTile,
    MonitorScopeTile,
    MonitorToggleTile,
    NavTab,
    PollingPresetTile,
    SteamSetupTile,
)


class MarketplaceToolsApp(App[None]):
    TITLE = APP_TITLE
    CSS = APP_CSS
    TOOLTIP_DELAY = 0.05

    BINDINGS = [
        Binding("escape", "show_dashboard", "Dashboard"),
        Binding("space", "toggle_monitor", "Start/Stop", show=False),
        Binding("q", "quit_app", "Quit"),
        Binding("ctrl+c", "quit_app", "Quit", show=False),
    ]

    NAV_ITEMS = [
        ("dashboard", "Dashboard"),
        ("settings", "App Settings"),
        ("wallet", "Inventory"),
        ("stats", "Stats"),
        ("exit", "Exit"),
    ]

    VIEW_TITLES = {
        "wallet": "Marketplace Inventory",
    }

    NUMBER_NAV = {
        "1": "dashboard",
        "2": "wallet",
        "3": "stats",
        "4": "logs",
        "s": "settings",
        "5": "exit",
    }

    TAB_ITEMS = [
        ("dashboard", "Dashboard"),
        ("wallet", "Inventory"),
        ("stats", "Stats"),
        ("logs", "Logs"),
    ]

    ACTIVITY_TAIL_LINES = 4

    def __init__(self, task_manager, api_handler, launch_mode: str = "live") -> None:
        super().__init__()
        self.theme = DEFAULT_THEME
        self.task_manager = task_manager
        self.api_handler = api_handler
        self.launch_mode = launch_mode
        self.task_manager.set_test_mode_enabled(self.is_test_mode)
        self.current_view = "dashboard"
        self.status_message = ""
        self.log_filter = "all"
        self._stats_trends_key: tuple[int, int] | None = None
        self._stats_trends_error_key: tuple[int, int] | None = None
        self._rendered_events: tuple | None = None
        self._rendered_tail: tuple | None = None
        self._dashboard_snapshot: tuple[str, ...] | None = None
        self._syncing_controls = False
        # Cosmetic animations (pulse/blink/quips); tests turn this off for determinism.
        self.animations_enabled = True
        self._pulse_index = 0
        self._quip_index = 0
        self._mascot_blinking = False
        self._mascot_celebrating = False
        # Easter egg: the mascot flashes star-eyes when a buy lands, and a ✦ badge joins its
        # chatter line per session buy. None means "not yet baselined" — the first refresh
        # adopts the current count so a loaded/seeded total never fires the flash; only a
        # genuine increase does.
        self._celebrated_purchases = None
        # Strip set piece (footer flash -> coins+price -> spent count-up) on a buy. Rides the
        # same trigger as the mascot star-eyes; its own flag keeps the 1s footer refresh from
        # stomping the frames. The frames read the live purchase-progress ticks, so the coins
        # frame holds and counts up while a buy list is still being processed, and buys that
        # land mid-animation fold in automatically.
        self._footer_celebrating = False
        self._reward_spent = 0
        self._roll_from = 0
        self._roll_to = 0
        self._roll_step = 0
        self._celebration_from_count = 0
        self._coins_ticks = 0
        self._zzz_step = 0
        self._zzz_gap = 0
        self._idle_peek_countdown = 1

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static(APP_TITLE, id="brand")
            yield Static("│", id="topbar-brand-divider")
            with Horizontal(id="tabs"):
                for key, label in self.TAB_ITEMS:
                    yield NavTab(key, label)
            yield Static("", id="topbar-spacer")
            yield NavTab("settings", "Settings")
            yield Static("│", id="topbar-settings-divider")
            yield Static("", id="header-session")
            yield Static(f"v{APP_VERSION}", id="build-info")
        with Vertical(id="main"):
            with Vertical(id="welcome-card"):
                yield Static(BANNER_ART, id="banner")
                # Chatter and the ✦ tally share one row: the chatter is a full-width centered
                # Static, and the badges are an auto-width overlay docked to that row's right
                # edge — so buys never nudge the centered chatter.
                with Horizontal(id="greeting-row"):
                    yield Static("", id="welcome-greeting")
                    yield Static("", id="buy-badges")
                yield Static("", id="welcome-footer")
            if self.is_test_mode:
                with Horizontal(id="body"):
                    yield Container(id="content")
                    with VerticalScroll(id="test-controls"):
                        yield Button("Add Test Log", id="add-test-log", compact=True)
                        yield Button("Toggle Test Session", id="toggle-test-session", compact=True)
                        yield Button("Auto Reauth", id="toggle-auto-reauth", compact=True)
                        yield Button("Expire Session", id="expire-test-session", compact=True)
                        yield Button("Run Session Check", id="run-session-check", compact=True)
                        yield Button("Reauth Check", id="run-reauth-check", compact=True)
                        yield Button("Reset Steam Setup", id="reset-steam-setup", compact=True)
                        yield Button("Clear Browser Cookies", id="clear-browser-cookies", compact=True)
                        yield Button("Clear (Keep Steam)", id="clear-cookies-keep-steam", compact=True)
                        yield Button("Start Test Scan", id="start-test-monitor", compact=True)
                        yield Button("Start Test Buy", id="start-test-buy", compact=True)
                        yield Button("Live 2.9B Buy", id="live-buy-error-probe", compact=True)
                        yield Button("Stop Test Scan", id="stop-test-monitor", compact=True)
                        yield Button("Fake Detection", id="fake-detection", compact=True)
                        yield Button("Fake Multi Detect", id="fake-multi-detection", compact=True)
                        yield Button("Fake Buy Success", id="fake-buy-success", compact=True)
                        yield Button("Fake Bundle x8", id="fake-bundled-buy", compact=True)
            else:
                yield Container(id="content")
        with Horizontal(id="statusbar"):
            yield Static("", id="status-keys")
            yield Static("", id="status-state")

    @property
    def is_test_mode(self) -> bool:
        return self.launch_mode == "test"

    @property
    def is_simulated_session(self) -> bool:
        return bool(getattr(self.task_manager, "simulated_session_enabled", False))

    async def on_mount(self) -> None:
        await self.show_view("dashboard")
        self.set_interval(1, self.refresh_live_widgets)
        self.set_interval(0.12, self.advance_running_pulse)
        self.set_interval(4.0, self.blink_mascot)
        self.set_interval(30.0, self.advance_running_quip)
        self.set_interval(0.8, self.advance_mascot_zzz)
        self.run_worker(self.startup_update_check(), name="startup-update-check", group="updates")

    def on_resize(self, event) -> None:
        self.refresh_layout_density()

    async def on_unmount(self) -> None:
        await self.task_manager.stop_checker()
        await self.task_manager.stop_single_item_test_checker()
        await self.task_manager.stop_login_status_checker()
        await self.task_manager.flush_stats_writes()
        self.api_handler.save_session()

    async def on_key(self, event) -> None:
        if isinstance(self.focused, Input):
            return
        target = self.NUMBER_NAV.get(event.key)
        if target:
            event.stop()
            await self.handle_nav(target)

    async def on_nav_tab_pressed(self, event: NavTab.Pressed) -> None:
        event.stop()
        await self.handle_nav(event.tab.key)

    async def on_monitor_toggle_tile_pressed(self, event: MonitorToggleTile.Pressed) -> None:
        event.stop()
        await self.toggle_monitor_from_dashboard()

    async def handle_nav(self, target: str) -> None:
        if target == "login":
            self.run_worker(self.login_refresh(), name="login-refresh", group="actions", exclusive=True)
            return
        if target == "start":
            await self.start_monitor()
            return
        if target == "stop":
            await self.stop_monitor()
            return
        if target == "exit":
            await self.action_quit_app()
            return
        await self.show_view(target)

    async def toggle_monitor_from_dashboard(self) -> None:
        if self.task_manager.checker_enabled:
            await self.stop_monitor()
            return

        await self.start_monitor()

    async def stop_monitor(self, close_modal: bool = False) -> None:
        if self.task_manager.single_item_test_checker_enabled and not self.task_manager.checker_enabled:
            was_running = await self.task_manager.stop_single_item_test_checker()
            if was_running:
                self.set_status("Single-item test monitor stopped.", "info")
            else:
                self.set_status("Monitor already stopped.", "info")
            self.refresh_live_widgets()
            if close_modal:
                self.close_active_dashboard_modal()
            return

        was_running = await self.task_manager.stop_checker()
        if was_running:
            # stop_checker logs the notable "Monitor stopped" event; status bar only here.
            self.set_status("Monitor stopped.")
        else:
            self.set_status("Monitor already stopped.", "info")
        self.refresh_live_widgets()
        if close_modal:
            self.close_active_dashboard_modal()

    async def show_view(self, view_name: str) -> None:
        self.current_view = view_name
        content = self.query_one("#content", Container)
        await content.remove_children()

        self.update_chrome_visibility()
        self.update_test_controls_visibility()

        if view_name == "dashboard":
            dashboard_tiles = Horizontal(
                Vertical(
                    Horizontal(
                        DashboardTile("session", "Session"),
                        DashboardTile("spent", "Spent"),
                        DashboardTile("buy-delay", "Buy Delay"),
                        id="dashboard-primary-tiles",
                        classes="dashboard-tile-row",
                    ),
                    Horizontal(
                        DashboardTile("monitor", "Mode"),
                        DashboardTile("polling", "Polling"),
                        DashboardTile("credentials", "Credentials"),
                        id="dashboard-secondary-tiles",
                        classes="dashboard-tile-row",
                    ),
                    id="dashboard-tiles",
                ),
                MonitorToggleTile(),
                id="dashboard-deck",
            )
            dashboard_panel = Vertical(id="dashboard-panel")
            activity_header = Horizontal(
                Static("Activity", id="activity-title"),
                Static("full log → Logs", id="activity-hint"),
                id="activity-header",
            )
            await content.mount(dashboard_panel)
            await dashboard_panel.mount(dashboard_tiles)
            await content.mount(activity_header)
            await content.mount(Static("", id="activity-tail"))
            self._dashboard_snapshot = None
            self._rendered_tail = None
        elif view_name == "logs":
            event_log = RichLog(id="event-log", markup=True, highlight=False, wrap=True)
            event_toolbar = Horizontal(
                Static("Event Log", id="event-log-title"),
                LogFilterOption("all", "All"),
                Static("·", classes="log-filter-separator"),
                LogFilterOption("notable", "Notable"),
                Static("·", classes="log-filter-separator"),
                LogFilterOption("alerts", "Alerts"),
                id="event-log-toolbar",
            )
            await content.mount(event_toolbar)
            await content.mount(Static("", id="event-log-rule"))
            await content.mount(event_log)
            self._rendered_events = None
            self.task_manager.mark_alerts_seen()
        elif view_name == "credentials":
            await self.mount_credentials(content)
        elif view_name == "settings":
            await self.mount_settings(content)
        elif view_name == "wallet":
            await self.mount_wallet(content)
        elif view_name == "stats":
            await self.mount_stats(content)

        self.refresh_live_widgets()
        self.refresh_layout_density()

    def update_test_controls_visibility(self) -> None:
        if not self.is_test_mode:
            return
        try:
            self.query_one("#test-controls").display = self.current_view != "stats"
        except Exception:
            pass

    def set_status(self, message: str, level: str | None = None) -> None:
        self.status_message = message
        if message and level:
            self.task_manager.add_event(message, level)
        self.refresh_live_widgets()

    def on_click(self, event) -> None:
        focused = self.focused
        if not isinstance(focused, Input) or focused.id != "settings-cache-threshold-input":
            return

        node = getattr(event, "widget", None) or getattr(event, "target", None)
        while node is not None:
            if node is focused:
                return
            node = getattr(node, "parent", None)
        focused.blur()

    def query_visible_one(self, selector: str, expect_type=None):
        screens = list(reversed(self.screen_stack))
        for screen in screens:
            try:
                if expect_type is None:
                    return screen.query_one(selector)
                return screen.query_one(selector, expect_type)
            except Exception:
                continue
        if expect_type is None:
            return self.query_one(selector)
        return self.query_one(selector, expect_type)

    def close_active_dashboard_modal(self) -> None:
        if self.screen_stack and isinstance(self.screen_stack[-1], DashboardModalScreen):
            self.screen_stack[-1].dismiss(None)

    def close_dashboard_modals(self) -> None:
        while self.screen_stack and isinstance(self.screen_stack[-1], DashboardModalScreen):
            self.screen_stack[-1].dismiss(None)

    def credential_state(self) -> tuple[str, str, str, Optional[str], Optional[str]]:
        if self.task_manager.uses_steam_browser_session():
            self.api_handler.email = None
            self.api_handler.password = None
            if self.task_manager.steam_browser_profile_needs_setup():
                return "Steam Setup", "Initial setup needed", "warning", None, None
            return "Steam Account", "Browser login", "steam", None, None

        state, detail, level, email, password = self.pa_credential_state()
        self.api_handler.email = email
        self.api_handler.password = password
        return state, detail, level, email, password

    def pa_credential_state(self) -> tuple[str, str, str, Optional[str], Optional[str]]:
        try:
            email, password = load_credentials()
        except CredentialStoreError as exc:
            return "Credential Store Error", str(exc), "error", None, None

        if email and password:
            return "PA Account", mask_email(email), "gold", email, password
        if email:
            return "Password Needed", mask_email(email), "warning", email, password
        return "Not Set", "No account configured", "error", email, password

    def session_status_state(self) -> tuple[str, str, str]:
        if self.is_simulated_session:
            return "TEST", "Simulated auth", "warning"
        if self.api_handler.login_status:
            return "ONLINE", "Authenticated", "success"
        # Offline while the monitor runs is a real fault; offline at rest is just "not started yet".
        level = "error" if self.task_manager.monitor_running() else "idle"
        return "OFFLINE", "Refresh required", level

    def session_account_label(self) -> str:
        if self.is_simulated_session:
            return "Test session"
        if self.task_manager.uses_steam_browser_session():
            return "Steam Account"
        if self.api_handler.email:
            return mask_email(self.api_handler.email)
        return "No account configured"

    def spend_cap_short_label(self) -> str:
        cap = self.task_manager.max_spend
        if cap is None or cap <= 0:
            return "∞"
        return format_compact_number(cap)

    def dashboard_snapshot(self) -> tuple[str, ...]:
        credential_status, credential_detail, credential_level, _, _ = self.credential_state()
        login_status, _, _ = self.session_status_state()
        monitor_status = self.task_manager.monitor_status_label()
        mode = self.task_manager.monitor_mode_label()
        purchase_rate = format_percent(
            self.task_manager.session_successful_purchases,
            self.task_manager.session_detected_outfits,
        )
        purchase_detail = (
            f"{self.task_manager.session_successful_purchases}/"
            f"{self.task_manager.session_detected_outfits} bought this session"
        )
        spend_detail = f"Cap: {self.spend_cap_short_label()} this session"

        return (
            credential_status,
            credential_detail,
            credential_level,
            login_status,
            monitor_status,
            mode,
            self.task_manager.current_delay_label(),
            self.task_manager.current_delay_range(),
            self.task_manager.purchase_delay_range(),
            purchase_rate,
            purchase_detail,
            format_compact_silver(self.task_manager.session_silver_spent),
            spend_detail,
            self.task_manager.runtime_label(),
        )

    def status_text(self, value: str, level: str, show_dot: bool = True) -> Text:
        text = Text()
        dot = IDLE_DOT if level == "idle" else STATUS_DOT
        if show_dot:
            text.append(f"{dot} ", style=STATUS_STYLES[level])
        text.append(value, style=STATUS_STYLES[level])
        return text

    def dashboard_tile_data(self, snapshot: tuple[str, ...]) -> list[tuple[str, str, str, str, bool]]:
        (
            credential_status,
            credential_detail,
            credential_level,
            login_status,
            monitor_status,
            mode,
            delay_label,
            delay_range,
            purchase_delay_range,
            _purchase_rate,
            _purchase_detail,
            silver_spent,
            spend_detail,
            _runtime,
        ) = snapshot

        spend_tile_detail = spend_detail.replace(" this session", "")
        credential_tile_detail = "No account" if credential_detail == "No account configured" else credential_detail
        # The chip shows the selected mode (run state lives on the toggle/strip/info bar);
        # green when the mode can spend silver, neutral when it only watches.
        buying = (
            self.task_manager.purchase_submission_enabled
            or self.task_manager.single_item_test_purchase_enabled
        )
        # Non-idle level flags buy mode; refresh_dashboard_tiles renders it as an amber
        # warning triangle (caution: spends silver) with neutral text, rather than coloring
        # the whole label — keeps the dashboard's color count down. Watch-only stays idle.
        mode_level = "orange" if buying else "idle"
        _session_label, session_detail, session_level = self.session_status_state()

        return [
            ("monitor", mode, monitor_status, mode_level, True),
            ("spent", silver_spent, spend_tile_detail, "info", False),
            ("polling", delay_label, delay_range, "info", False),
            ("buy-delay", purchase_delay_range, "Between buys", "info", False),
            ("credentials", credential_status, credential_tile_detail, credential_level, False),
            (
                "session",
                login_status,
                session_detail,
                session_level,
                True,
            ),
        ]

    def refresh_dashboard_tiles(self, snapshot: tuple[str, ...]) -> None:
        tm = self.task_manager
        for tile_key, value, detail, level, show_dot in self.dashboard_tile_data(snapshot):
            tile = self.query_one(f"#tile-{tile_key}", DashboardTile)
            muted = not tile.interactive
            if tile_key == "spent":
                cap_text = self.spend_cap_short_label()
                spent_text = format_compact_number(tm.session_silver_spent)
                if spent_text == "0":
                    spent_text = "0B"
                value_text = Text(spent_text, style=STATUS_STYLES["info"])
                value_text.append(f" / {cap_text}", style="#777777")
            elif tile_key == "monitor":
                # Buy mode wears a small amber warning triangle (caution: this mode spends
                # silver) with neutral white text; watch-only rests as the plain idle ○.
                if level == "idle":
                    value_text = self.status_text(value, "idle", show_dot=show_dot)
                else:
                    value_text = Text("▲ ", style=STATUS_STYLES["orange"])
                    value_text.append(value, style=STATUS_STYLES["info"])
            elif level == "muted":
                value_text = Text(value, style="dim #aaaaaa")
            else:
                value_text = self.status_text(value, level, show_dot=show_dot)
            tile.update(self.dashboard_chip(str(tile.border_title), value_text, muted))
        self.refresh_monitor_toggle()

    def refresh_monitor_toggle(self) -> None:
        try:
            toggle = self.query_one("#monitor-toggle", MonitorToggleTile)
        except Exception:
            return

        running = self.task_manager.monitor_running()
        toggle.set_class(running, "toggle-stop")
        toggle.set_class(not running, "toggle-start")
        body = Table.grid(expand=True)
        body.add_column(justify="center")
        if running:
            body.add_row(Text("■ STOP", style=f"bold {COLOR_ERROR}"))
            body.add_row(Text(self.short_runtime_label(), style="#8f5555"))
        else:
            body.add_row(Text("▶ START", style=f"bold {COLOR_BRAND}"))
            body.add_row(Text("space", style="#a06a35"))
        toggle.update(Align.center(body, vertical="middle"))

    def dashboard_chip(self, title: str, value_text: Text, muted: bool) -> RenderableType:
        label_text = Text(title, style="#6f6f6f" if muted else "bold #8f8f8f")
        body = Table.grid(expand=True)
        if muted:
            body.add_column(justify="center")
            body.add_row(label_text)
            body.add_row(value_text)
        else:
            body.add_column(justify="left", ratio=1)
            body.add_column(justify="right")
            body.add_row(label_text, Text("›", style="#7a7a7a"))
            body.add_row(value_text, Text(""))
        return body

    def purchase_rate_style(self, bought: int | None = None, detected: int | None = None) -> str:
        # Continuous red->green ramp so the rate color climbs smoothly with the hit rate.
        if detected is None:
            detected = self.task_manager.session_detected_outfits
        if bought is None:
            bought = self.task_manager.session_successful_purchases
        if detected <= 0:
            return STATUS_STYLES["idle"]
        return rate_spectrum_style(bought / detected)

    def refresh_live_widgets(self) -> None:
        self.refresh_chrome_status()
        self.refresh_logs_tab_badge()
        if self.current_view == "dashboard":
            try:
                snapshot = self.dashboard_snapshot()
                if snapshot != self._dashboard_snapshot:
                    self.refresh_dashboard_tiles(snapshot)
                    self._dashboard_snapshot = snapshot
                self.sync_activity_tail()
                self.refresh_modal_summaries()
            except Exception:
                pass
        elif self.current_view == "logs":
            try:
                self.task_manager.mark_alerts_seen()
                self.sync_event_log()
            except Exception:
                pass
        elif self.current_view == "credentials":
            self.refresh_credentials_summary()
        elif self.current_view == "settings":
            self.refresh_settings_summary()
        elif self.current_view == "stats":
            self.refresh_stats()

    def short_runtime_label(self) -> str:
        label = self.task_manager.runtime_label()
        if label.startswith("00:") and len(label) == 8:
            return label[3:]
        return label

    RUNNING_PULSE_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    # The Zzz builds quickly (one z per ~0.8s tick) then rests blank for this many ticks
    # before the next set — a long pause between sleep breaths (~5s at the 0.8s tick).
    ZZZ_GAP_TICKS = 6

    def refresh_chrome_status(self) -> None:
        tm = self.task_manager
        running = tm.monitor_running()
        try:
            session_label, _detail, session_level = self.session_status_state()
            session_text = Text()
            session_style = STATUS_STYLES.get(session_level, f"bold {COLOR_TEXT_MUTED}")
            session_dot = IDLE_DOT if session_level == "idle" else STATUS_DOT
            session_text.append(f"{session_dot} ", style=session_style)
            session_text.append(session_label, style=session_style)
            self.query_one("#header-session", Static).update(session_text)
        except Exception:
            pass

        try:
            bar = Text()
            # The bottom status bar uses the header's "│" line divider (dim #3a3a3a),
            # not the strip's brighter dot.
            separator = "  │  "
            buying = tm.purchase_submission_enabled
            bar.append(
                f"{STATUS_DOT} " if buying else f"{IDLE_DOT} ",
                style=STATUS_STYLES["success"] if buying else "#777777",
            )
            bar.append(tm.monitor_mode_label(), style="#8f8f8f")
            bar.append(separator, style="#3a3a3a")
            bar.append("cap ", style="#6f6f6f")
            bar.append(self.spend_cap_short_label(), style="#d8d3c8")
            # Session stats live on the welcome-card footer while running; the runtime
            # lives on the STOP button. The status bar stays mode + cap on every tab.
            self.query_one("#status-keys", Static).update(bar)
        except Exception:
            pass

        try:
            quit_hint = Text()
            quit_hint.append("q ", style="#6f6f6f")
            quit_hint.append("quit", style="#8f8f8f")
            self.query_one("#status-state", Static).update(quit_hint)
        except Exception:
            pass

        try:
            card = self.query_one("#welcome-card", Vertical)
            card.display = self.current_view == "dashboard"
            card.set_class(not self.should_show_banner(), "-compact")
            self.query_one("#banner", Static).display = self.should_show_banner()
            self.refresh_mascot_rest()
            self.sync_purchase_reward()
        except Exception:
            pass
        self.refresh_welcome_footer(running)

    def observed_purchase_totals(self) -> tuple[int, int]:
        # Live view of session buys: per-item progress ticks lead during a buy list; the
        # committed session totals catch up (and cover paths that never tick, like Fake Buy
        # Success). max() of the two is monotonic within a session, so nothing fires twice.
        tm = self.task_manager
        return (
            max(tm.session_successful_purchases, tm.purchase_progress_count),
            max(tm.session_silver_spent, tm.purchase_progress_silver),
        )

    def sync_purchase_reward(self) -> None:
        # Watches the observed buy totals; on a genuine increase the mascot flashes
        # star-eyes and the strip plays its set piece. The ✦ tally is a bottom-right overlay.
        count, spent = self.observed_purchase_totals()
        if self._celebrated_purchases is None or count < self._celebrated_purchases:
            # Baseline / session reset — adopt the totals silently, no reaction.
            self._celebrated_purchases = count
            self._reward_spent = spent
        elif count > self._celebrated_purchases:
            gained = count - self._celebrated_purchases
            roll_from = self._reward_spent
            self._celebrated_purchases = count
            self._reward_spent = spent
            self.celebrate_purchase()
            self.celebrate_purchase_strip(gained, roll_from, spent)
        try:
            badges = self.render_buy_badges()
            self.query_one("#buy-badges", Static).update(badges if badges is not None else Text(""))
        except Exception:
            pass

    def celebrate_purchase(self) -> None:
        if not self.animations_enabled or self.current_view != "dashboard":
            return
        try:
            banner = self.query_one("#banner", Static)
        except Exception:
            return
        if not banner.display:
            return
        self._mascot_celebrating = True
        banner.update(BANNER_STAR_ART)
        self.set_timer(1.2, self._end_mascot_celebrate)

    def _end_mascot_celebrate(self) -> None:
        self._mascot_celebrating = False
        try:
            self.query_one("#banner", Static).update(self.mascot_rest_frame())
        except Exception:
            pass

    # ── Strip set piece: flash -> coins+price -> spent count-up, on a buy ────────────
    STRIP_FLASH_SECONDS = 0.9
    STRIP_COINS_TICK = 0.2  # coins-frame refresh cadence while it live-counts the batch
    STRIP_COINS_MIN_TICKS = 6  # minimum coins-frame time (~1.2s) even for a single instant buy
    STRIP_ROLL_STEPS = 12
    STRIP_ROLL_INTERVAL = 0.12  # ~1.4s of count-up so the roll reads, not flickers
    STRIP_ROLL_SETTLE_SECONDS = 1.5  # hold the final spent value before restoring the live strip

    def celebrate_purchase_strip(self, gained: int, roll_from: int, roll_to: int) -> None:
        # Cosmetic and UI-thread only — the buy loop never waits on this. Fires whenever the
        # mascot does (any buy: live, Start Test Buy, or Fake Buy Success), so it isn't gated
        # on the monitor running; it just settles to whichever strip (running/idle) is current.
        if not self.animations_enabled or self.current_view != "dashboard":
            return
        if self._footer_celebrating:
            # Frames render from the live observed totals, so a buy landing mid-animation
            # folds in on the next frame tick — nothing to restart or retarget by hand.
            return
        observed_count, _observed_spent = self.observed_purchase_totals()
        self._roll_from = roll_from
        self._celebration_from_count = observed_count - max(1, gained)
        self._footer_celebrating = True
        self._show_strip_flash()

    def _set_strip(self, footer: Text) -> None:
        try:
            self.query_one("#welcome-greeting", Static).update(Text("Nice grab!", style=f"bold {COLOR_GOLD}"))
            self.query_one("#welcome-footer", Static).update(footer)
        except Exception:
            pass

    def _show_strip_flash(self) -> None:
        self._set_strip(Text("✦ ✦ ✦   BOUGHT   ✦ ✦ ✦", style=f"bold {COLOR_GOLD}"))
        self.set_timer(self.STRIP_FLASH_SECONDS, self._show_strip_coins)

    def _render_strip_coins(self) -> None:
        observed_count, observed_spent = self.observed_purchase_totals()
        gained = max(1, observed_count - self._celebration_from_count)
        noun = "outfit" if gained == 1 else "outfits"
        spent = max(0, observed_spent - self._roll_from)  # silver on this batch = the price
        footer = Text("◍ ◍ ◍  ", style=COLOR_GOLD)
        footer.append(f"{gained} {noun} secured", style="#d8d3c8")
        footer.append("  ·  ", style=COLOR_TEXT_MUTED)
        footer.append(f"{spent:,}", style=f"bold {COLOR_GOLD}")
        footer.append(" silver", style="#6f6f6f")
        self._set_strip(footer)

    def _show_strip_coins(self) -> None:
        self._coins_ticks = 0
        self._render_strip_coins()
        self.set_timer(self.STRIP_COINS_TICK, self._advance_strip_coins)

    def _advance_strip_coins(self) -> None:
        if not self._footer_celebrating:
            return
        self._render_strip_coins()
        self._coins_ticks += 1
        # Hold the coins frame while the buy list is still being processed — each secured
        # item ticks the count/silver up live — then move on once the batch is done (with a
        # minimum frame time so a single instant buy still reads).
        if self.task_manager.purchase_in_progress or self._coins_ticks < self.STRIP_COINS_MIN_TICKS:
            self.set_timer(self.STRIP_COINS_TICK, self._advance_strip_coins)
            return
        self._roll_step = 0
        self._advance_strip_rollup()

    def _advance_strip_rollup(self) -> None:
        # Retarget to the live total each step so the roll always converges on the truth,
        # even if another buy lands mid-roll (observed totals are monotonic in-session).
        _count, self._roll_to = self.observed_purchase_totals()
        steps = self.STRIP_ROLL_STEPS
        fraction = self._roll_step / steps
        value = round(self._roll_from + (self._roll_to - self._roll_from) * fraction)
        footer = Text("spent ", style="#6f6f6f")
        footer.append(format_compact_number(value), style=f"bold {COLOR_GOLD}")
        footer.append(f" of {self.spend_cap_short_label()}", style="#6f6f6f")
        self._set_strip(footer)
        if self._roll_step < steps:
            self._roll_step += 1
            self.set_timer(self.STRIP_ROLL_INTERVAL, self._advance_strip_rollup)
        else:
            self.set_timer(self.STRIP_ROLL_SETTLE_SECONDS, self._end_strip_celebration)

    def _end_strip_celebration(self) -> None:
        self._footer_celebrating = False
        # Re-baseline on the observed totals (more buys may have landed during the set
        # piece and were folded in live — they must not fire a second celebration).
        self._celebrated_purchases, self._reward_spent = self.observed_purchase_totals()
        self._quip_index = 0
        self.refresh_welcome_footer(self.task_manager.monitor_running())

    # One ✦ badge per session buy, trailing the mascot's chatter (newest a shade brighter).
    # Individual stars up to the cap, then a compact "✦ ×N" so a long session can't overrun
    # the centered line.
    BUY_BADGE_STAR = "✦"
    # Kept short so the right-aligned strip never reaches the centered footer stats, even at
    # the 96-col minimum width; past the cap it collapses to a compact "✦ ×N".
    BUY_BADGE_CAP = 6

    def render_buy_badges(self) -> Text | None:
        count = self.task_manager.session_successful_purchases
        if count <= 0:
            return None
        badges = Text()
        if count <= self.BUY_BADGE_CAP:
            for index in range(count):
                if index:
                    badges.append(" ")
                # Right-aligned, the row grows leftward, so the newest star lands on the
                # left — brighten it (index 0) rather than the rightmost.
                style = "#ffd9a8" if index == 0 else COLOR_BRAND
                badges.append(self.BUY_BADGE_STAR, style=style)
        else:
            badges.append(self.BUY_BADGE_STAR, style=COLOR_BRAND)
            badges.append(f" ×{count}", style="#ffd9a8")
        return badges

    def time_greeting(self) -> str:
        hour = time.localtime().tm_hour
        if 5 <= hour < 12:
            return "Good morning!"
        if 12 <= hour < 17:
            return "Good afternoon!"
        if 17 <= hour < 22:
            return "Good evening!"
        return "Up late?"

    def refresh_welcome_footer(self, running: bool) -> None:
        # Two rows: the mascot's chatter/greeting sits centered right under the art, and the
        # live state (running/idle · lifetime record) sits on the footer line below it.
        if self._footer_celebrating:
            # The strip set piece owns both rows until it settles.
            return
        try:
            tm = self.task_manager
            pool = self.chatter_pool(running)
            chatter_line = pool[self._quip_index % len(pool)]
            # The chatter stays centered on its own line, so accumulating badges never nudge
            # it — the ✦ tally lives in its own bottom-right overlay (see sync_purchase_reward).
            self.query_one("#welcome-greeting", Static).update(
                Text(chatter_line, style="bold #d8d3c8")
            )

            footer = Text()
            # One consistent divider dot everywhere in the strip, at a single bright tone,
            # so idle and running read the same (no dim/bright mismatch).
            sep = "  ·  "
            sep_style = COLOR_TEXT_MUTED
            if running:
                # Running: the strip carries the session — bought, rate, and spend read
                # against the cap. Idle keeps the lifetime record.
                frame = self.RUNNING_PULSE_FRAMES[self._pulse_index]
                footer.append(f"{frame} ", style=COLOR_BRAND)
                footer.append("Running", style=STATUS_STYLES["success"])
                detected = tm.session_detected_outfits
                bought = tm.session_successful_purchases
                footer.append(sep, style=sep_style)
                footer.append("bought ", style="#6f6f6f")
                # Bought is the number that matters, so the "seen" denominator recedes to
                # gray — bought wins by contrast without adding weight or color.
                footer.append(str(bought), style="#d8d3c8" if detected else "#6f6f6f")
                footer.append(" of ", style="#6f6f6f")
                footer.append(str(detected), style="#8f8f8f" if detected else "#6f6f6f")
                footer.append(" seen", style="#6f6f6f")
                # The rate joins only after the first detection: 0% of nothing is noise.
                if detected > 0:
                    footer.append(" (", style="#6f6f6f")
                    footer.append(
                        format_percent(bought, detected),
                        style=self.purchase_rate_style(bought, detected),
                    )
                    footer.append(")", style="#6f6f6f")
                footer.append(sep, style=sep_style)
                footer.append("spent ", style="#6f6f6f")
                spent = tm.session_silver_spent
                # Session silver in the same gold as the idle lifetime figures.
                footer.append(
                    format_compact_number(spent),
                    style=COLOR_GOLD if spent else "#6f6f6f",
                )
                footer.append(f" of {self.spend_cap_short_label()}", style="#6f6f6f")
            else:
                footer.append(f"{IDLE_DOT} Idle", style="#777777")
                footer.append(sep, style=sep_style)
                footer.append(f"{tm.lifetime_successful_purchases:,}", style=COLOR_GOLD)
                footer.append(" outfits", style=COLOR_TEXT_MUTED)
                footer.append(sep, style=sep_style)
                footer.append(format_compact_number(tm.lifetime_silver_spent), style=COLOR_GOLD)
                footer.append(" silver all-time", style=COLOR_TEXT_MUTED)
            self.query_one("#welcome-footer", Static).update(footer)
        except Exception:
            pass

    def advance_running_pulse(self) -> None:
        if not self.animations_enabled or self.current_view != "dashboard":
            return
        if not self.task_manager.monitor_running():
            return
        self._pulse_index = (self._pulse_index + 1) % len(self.RUNNING_PULSE_FRAMES)
        self.refresh_welcome_footer(True)

    def chatter_pool(self, running: bool) -> list[str]:
        # Running: the eager quips rotate. Idle: just the live time-of-day greeting (the
        # mascot's sleep is shown by the Zzz trail, not chatter).
        if running:
            return list(RUNNING_QUIPS)
        return [self.time_greeting()]

    def advance_running_quip(self) -> None:
        if not self.animations_enabled or self.current_view != "dashboard":
            return
        # The mascot chatters in both states now — eager while running, dozy while idle.
        running = self.task_manager.monitor_running()
        pool = self.chatter_pool(running)
        if len(pool) < 2:
            return
        current = self._quip_index % len(pool)
        # Random pick, but never the same line twice in a row.
        choices = [i for i in range(len(pool)) if i != current]
        self._quip_index = random.choice(choices)
        self.refresh_welcome_footer(running)

    def mascot_rest_frame(self) -> str:
        # The mascot's resting face IS the monitor state: eyes open (awake) while running,
        # eyes closed + a rising "Zzz" while idle (dozing) — so starting the bot visibly
        # wakes it. With animations off (tests), the plain logo stays neutral.
        if not self.animations_enabled:
            return BANNER_ART
        if self.task_manager.monitor_running():
            return BANNER_ART
        return self._augment_zzz(BANNER_BLINK_ART)

    def _augment_zzz(self, base_art: str) -> str:
        # Overlay the first _zzz_step cells of the trail onto blank cells of the banner
        # grid (overwrites spaces in place, so total width never changes → no shift).
        if self._zzz_step <= 0:
            return base_art
        rows = [list(line) for line in base_art.split("\n")]
        for row, col, char in MASCOT_ZZZ_TRAIL[: self._zzz_step]:
            if row < len(rows) and col < len(rows[row]):
                rows[row][col] = char
        return "\n".join("".join(row) for row in rows)

    def advance_mascot_zzz(self) -> None:
        if not self.animations_enabled or self.current_view != "dashboard":
            return
        if self.task_manager.monitor_running():
            return
        # Freeze the sleep clock while a drowsy peek owns the banner. Otherwise the step
        # would advance under the blink guard (unpainted), swallowing z-reveals — most
        # visibly the final Z — and making the cadence look random.
        if self._mascot_blinking or self._mascot_celebrating:
            return
        # A set builds quickly (z -> z -> z), then the empty step lingers ZZZ_GAP_TICKS
        # ticks so there's a long blank pause before the next set begins.
        if self._zzz_step == 0:
            self._zzz_gap += 1
            if self._zzz_gap < self.ZZZ_GAP_TICKS:
                return
            self._zzz_gap = 0
        self._zzz_step = (self._zzz_step + 1) % (len(MASCOT_ZZZ_TRAIL) + 1)
        self.refresh_mascot_rest()

    def refresh_mascot_rest(self) -> None:
        if self._mascot_blinking or self._mascot_celebrating:
            return
        try:
            self.query_one("#banner", Static).update(self.mascot_rest_frame())
        except Exception:
            pass

    def blink_mascot(self) -> None:
        if not self.animations_enabled or self.current_view != "dashboard":
            return
        if self._mascot_celebrating:
            return  # a buy celebration owns the banner; don't blink over the stars
        try:
            banner = self.query_one("#banner", Static)
        except Exception:
            return
        if not banner.display:
            return
        # Awake: a quick alert blink (eyes close briefly). Dozing: a slow drowsy peek
        # (eyes crack open briefly). The rest frame is the opposite in each state.
        running = self.task_manager.monitor_running()
        if not running:
            # The idle peek shares the 4s blink timer but should be rare — skip most
            # ticks so the sleeping eyes only crack open every ~8-12s.
            if self._idle_peek_countdown > 0:
                self._idle_peek_countdown -= 1
                return
            self._idle_peek_countdown = random.randint(1, 2)
        self._mascot_blinking = True
        if running:
            banner.update(BANNER_BLINK_ART)
            self.set_timer(0.15, self._end_mascot_blink)
        else:
            # Drowsy peek: eyes crack open but the Zzz stays — still dozing. Held long
            # (~1.35s) so the idle wake reads as a slow, sleepy stir.
            banner.update(self._augment_zzz(BANNER_ART))
            self.set_timer(1.35, self._end_mascot_blink)

    def _end_mascot_blink(self) -> None:
        self._mascot_blinking = False
        if self._mascot_celebrating:
            return  # a buy landed mid-blink; leave the stars for the celebration timer
        try:
            self.query_one("#banner", Static).update(self.mascot_rest_frame())
        except Exception:
            pass

    def should_show_banner(self) -> bool:
        size = self.size
        # The mascot is the app's identity, so it stays visible far longer than before —
        # the activity tail yields its rows first (see activity_tail_lines). Only once the
        # window is too short for even a 2-line tail does the 12-row banner give way.
        return self.current_view == "dashboard" and size.width >= 96 and size.height >= 30

    def activity_tail_lines(self) -> int:
        # Priority order when the window shrinks vertically: keep the mascot, sacrifice the
        # tail. So while the banner is shown the tail steps down 4 -> 2 to free rows. Once
        # the banner is hidden (very short windows) the card frees ~14 rows and the tail
        # returns to full height.
        if not self.should_show_banner():
            return self.ACTIVITY_TAIL_LINES
        height = self.size.height
        if height >= 34:
            return 4
        if height >= 32:
            return 3
        return 2

    def refresh_layout_density(self) -> None:
        try:
            self.refresh_chrome_status()
            self.update_chrome_visibility()
        except Exception:
            pass
        try:
            self.sync_activity_tail()
        except Exception:
            pass

    def update_chrome_visibility(self) -> None:
        for key in ("settings", *[item_key for item_key, _label in self.TAB_ITEMS]):
            try:
                tab = self.query_one(f"#tab-{key}", NavTab)
            except Exception:
                continue
            tab.set_class(key == self.current_view, "nav-tab-active")

    def sync_activity_tail(self) -> None:
        lines = self.activity_tail_lines()
        events = tuple(self.task_manager.events_for_filter("notable"))[-lines:]
        # The line count is part of the signature so a resize re-slices even when the
        # event stream itself hasn't changed.
        signature = (lines, events)
        if signature == self._rendered_tail:
            return

        tail = self.query_one("#activity-tail", Static)
        tail.styles.height = lines
        if events:
            text = Text()
            for index, event in enumerate(events):
                if index:
                    text.append("\n")
                text.append_text(Text.from_markup(event))
            tail.update(text)
        else:
            tail.update(Text("No activity yet.", style=COLOR_TEXT_MUTED))
        self._rendered_tail = signature

    def refresh_logs_tab_badge(self) -> None:
        try:
            tab = self.query_one("#tab-logs", NavTab)
        except Exception:
            return
        show_dot = self.current_view != "logs" and self.task_manager.has_unseen_alerts()
        if getattr(tab, "_alert_dot", None) == show_dot:
            return
        tab._alert_dot = show_dot
        if show_dot:
            label = Text("Logs")
            label.append(" ●", style=COLOR_BRAND)
            tab.update(label)
        else:
            tab.update("Logs")

    LOG_FILTER_LABELS = {"all": "All", "notable": "Notable", "alerts": "Alerts"}
    LOG_FILTER_EMPTY = {
        "all": "No events yet.",
        "notable": "No notable events yet.",
        "alerts": "No warnings or errors yet.",
    }

    def sync_event_log(self) -> None:
        self.refresh_event_log_filter_controls()
        events = tuple(self.task_manager.events_for_filter(self.log_filter, dividers=True))
        # The active filter is part of the signature so switching chips re-renders even
        # when the underlying stream hasn't changed.
        signature = (self.log_filter, events)
        if signature == self._rendered_events:
            return

        self.refresh_event_log_title(len(events))
        log = self.query_one("#event-log", RichLog)
        previous_filter, previous_events = self._rendered_events or (None, ())
        if (
            previous_filter == self.log_filter
            and previous_events
            and len(events) > len(previous_events)
            and events[: len(previous_events)] == previous_events
        ):
            # Pure additions: append so the reader's scroll position isn't yanked to the
            # bottom every time the monitor logs. Anything else (filter switch, a coalesce
            # counter mutating the last line, buffer eviction) falls back to a full redraw.
            for event in events[len(previous_events):]:
                log.write(event)
        else:
            log.clear()
            if events:
                for event in events:
                    log.write(event)
            else:
                log.write(self.LOG_FILTER_EMPTY[self.log_filter])
        self._rendered_events = signature

    def refresh_event_log_title(self, count: int) -> None:
        try:
            title = self.query_one("#event-log-title", Static)
        except Exception:
            return
        text = Text("Event Log", style="bold #8f8f8f")
        text.append(f"  ·  {count} {'event' if count == 1 else 'events'}", style="#5a5a5a")
        title.update(text)

    def refresh_event_log_filter_controls(self) -> None:
        for mode in self.LOG_FILTER_LABELS:
            try:
                option = self.query_one(f"#log-filter-{mode}", LogFilterOption)
            except Exception:
                continue
            option.set_class(mode == self.log_filter, "log-filter-selected")

    async def mount_credentials(self, content: Container) -> None:
        _, _, _, email, _ = self.credential_state()
        await content.mount(Static(id="credentials-summary", classes="panel"))
        await content.mount(Label("Email"))
        await content.mount(Input(value=email or "", placeholder="account@example.com", id="email-input"))
        await content.mount(Label("Password"))
        await content.mount(Input(password=True, placeholder="Stored in OS keyring", id="password-input"))
        await content.mount(
            Horizontal(
                Button("Save Credentials", id="save-credentials", variant="primary"),
                Button("Clear Saved Credentials", id="clear-credentials", variant="error"),
                classes="row",
            )
        )
        self.refresh_credentials_summary()

    def refresh_modal_tile(
        self,
        tile_id: str,
        title: str,
        value: str,
        detail: str,
        level: str = "info",
        show_dot: bool = False,
    ) -> None:
        try:
            tile = self.query_visible_one(f"#{tile_id}", Static)
        except Exception:
            return

        # border_title is no longer rendered (flat chips) but tests and callers rely on it.
        tile.border_title = title
        clickable = "modal-info-clickable" in tile.classes
        label_text = Text(title, style="bold #8f8f8f")
        value_text = self.status_text(value, level, show_dot=show_dot)
        body = Table.grid(expand=True)
        if clickable:
            body.add_column(justify="left", ratio=1)
            body.add_column(justify="right")
            body.add_row(label_text, Text("›", style="#7a7a7a"))
            body.add_row(value_text, Text(""))
            body.add_row(Text(detail, style="#6f6f6f"), Text(""))
        else:
            body.add_column(justify="left")
            body.add_row(label_text)
            body.add_row(value_text)
            body.add_row(Text(detail, style="#6f6f6f"))
        tile.update(body)

    def modal_custom_delay_bounds(self) -> tuple[int, int] | None:
        try:
            low = int(self.query_visible_one("#custom-delay-min-input", Input).value.strip())
            high = int(self.query_visible_one("#custom-delay-max-input", Input).value.strip())
        except Exception:
            return None
        if low <= 0 or high <= 0:
            return None
        return low, high

    def matching_delay_choice(self, bounds: tuple[int, int] | None) -> str | None:
        if bounds is None:
            return None
        for key, (_label, preset_bounds) in self.task_manager.delay_choices.items():
            if tuple(preset_bounds) == bounds:
                return key
        return None

    def refresh_polling_preset_tiles(self) -> None:
        try:
            self.query_visible_one("#polling-recommendations")
        except Exception:
            return

        bounds = self.modal_custom_delay_bounds() or self.task_manager.current_delay_bounds()
        selected_key = self.matching_delay_choice(bounds)
        for key, (_label, (low, high)) in self.task_manager.delay_choices.items():
            try:
                tile = self.query_visible_one(f"#polling-preset-{key}", PollingPresetTile)
            except Exception:
                continue

            selected = key == selected_key
            if selected:
                tile.add_class("preset-selected")
            else:
                tile.remove_class("preset-selected")
            detail = "Recommended" if key == "2" else ""
            body = Table.grid(expand=True)
            body.add_column(justify="center")
            body.add_row(Text(str(tile.border_title), style=f"bold {COLOR_BRAND}" if selected else "bold #8f8f8f"))
            body.add_row(Text(f"{low}-{high}s", style="#c98a50" if selected else "#d8d3c8"))
            body.add_row(Text(detail, style="#a06a35" if selected else "#6f6f6f"))
            tile.update(body)

    def refresh_credentials_summary(self) -> None:
        try:
            summary = self.query_visible_one("#credentials-summary")
        except Exception:
            return

        state, detail, _, _, password = self.credential_state()
        pa_state, pa_detail, pa_level, pa_email, _pa_password = self.pa_credential_state()
        password_detail = "Stored in OS keyring" if password else "Not set"
        setup_complete = self.task_manager.steam_browser_profile_prepared
        setup_state = "Complete" if setup_complete else "Incomplete"
        setup_detail = "Ready for market login" if setup_complete else "Run Steam Setup once"
        setup_level = "success" if setup_complete else "warning"
        if isinstance(summary, Static):
            table = Table.grid(padding=(0, 2))
            table.add_column(style="bold")
            table.add_column()
            table.add_row("Login method", self.task_manager.account_mode_label())
            table.add_row("Email", detail)
            table.add_row("Password", password_detail)
            table.add_row("Status", state)
            table.add_row("Steam initial setup", setup_state)
            summary.update(table)
            return

        self.refresh_credentials_mode_controls()
        if self.selected_account_mode() == STEAM_BROWSER_MODE:
            self.refresh_modal_tile(
                "credential-action-tile",
                "Steam Initial Setup",
                setup_state,
                setup_detail,
                setup_level,
                True,
            )
        else:
            self.refresh_modal_tile(
                "credential-action-tile",
                "Pearl Abyss Account",
                pa_detail,
                "Click to update" if pa_email else "Click to enter credentials",
                pa_level,
                pa_email is not None,
            )

    def selected_account_mode(self) -> str:
        try:
            return str(self.query_visible_one("#account-mode-select", Select).value)
        except Exception:
            return self.task_manager.account_mode

    def refresh_credentials_mode_controls(self) -> None:
        try:
            selected_mode = self.selected_account_mode()
            steam_mode = selected_mode == STEAM_BROWSER_MODE
            note = self.query_visible_one("#credentials-mode-note", Static)
        except Exception:
            return

        if steam_mode:
            if self.task_manager.steam_browser_profile_prepared:
                note.update(
                    "Steam Account does not use saved email or password. Refresh Session opens a visible browser so you can complete Steam and Pearl Abyss login there."
                )
            else:
                note.update(
                    "Run Steam Setup once to build the app-owned browser profile from the Black Desert site. Refresh Session will use the market login after setup is saved."
                )
        else:
            note.update(
                "Pearl Abyss Account uses a visible browser login. Saved credentials are entered automatically when available."
            )

        try:
            self.query_visible_one("#clear-credentials", Button).display = not steam_mode
        except Exception:
            pass

        try:
            setup_tile = self.query_visible_one("#credential-action-tile", SteamSetupTile)
            if steam_mode and not self.task_manager.steam_browser_profile_prepared:
                setup_tile.add_class("modal-info-clickable")
                setup_tile.remove_class("modal-info-muted")
            elif not steam_mode:
                setup_tile.add_class("modal-info-clickable")
                setup_tile.remove_class("modal-info-muted")
            else:
                setup_tile.remove_class("modal-info-clickable")
                setup_tile.add_class("modal-info-muted")
        except Exception:
            pass

    def refresh_pa_credentials_controls(self) -> None:
        try:
            email = self.query_visible_one("#email-input", Input).value.strip()
            password = self.query_visible_one("#password-input", Input).value
            save_button = self.query_visible_one("#save-pa-credentials", Button)
        except Exception:
            return

        save_button.disabled = not (email and password)
        if email and password:
            self.set_pa_credentials_warning("")

    def set_pa_credentials_warning(self, message: str) -> None:
        try:
            self.query_visible_one("#pa-credentials-warning", Static).update(message)
        except Exception:
            pass

    async def mount_settings(self, content: Container) -> None:
        await content.mount(Static(id="settings-identity"))

        about_card = Vertical(
            Static(id="settings-about-facts", classes="action-card-line-tight"),
            id="settings-about-card",
            classes="action-card",
        )
        await content.mount(about_card)
        about_card.border_title = "About"

        update_card = Horizontal(
            Static(id="settings-update", classes="action-card-info"),
            ModalAction("Check Now", "settings-check-update", extra_classes="modal-action-compact"),
            ModalAction("Startup: On", "settings-toggle-update-startup", extra_classes="modal-action-compact"),
            id="settings-update-card",
            classes="action-card",
        )
        await content.mount(update_card)
        update_card.border_title = "Updates"

        storage_card = Vertical(
            Static(id="settings-storage-facts", classes="action-card-line"),
            Horizontal(
                Label("Auto-clean at", classes="cache-inline-label"),
                Input(
                    value=str(self.task_manager.browser_cache_cleanup_threshold_mb),
                    type="integer",
                    id="settings-cache-threshold-input",
                    select_on_focus=False,
                ),
                Label("MiB", classes="cache-inline-label"),
                Static(classes="action-card-spacer"),
                ModalAction("Save", "settings-save-cache-limit", extra_classes="modal-action-compact"),
                ModalAction("Clean now", "settings-clean-cache", extra_classes="modal-action-compact"),
                classes="cache-controls-row",
            ),
            id="settings-storage-card",
            classes="action-card",
        )
        await content.mount(storage_card)
        storage_card.border_title = "Storage"
        await content.mount(Static("", id="settings-status"))

        await content.mount(
            Vertical(
                Static(
                    "Reset login state. Won't delete your saved credentials.",
                    id="settings-danger-note",
                    classes="action-card-line",
                ),
                Horizontal(
                    ModalAction(
                        "Clear Session",
                        "clear-saved-session",
                        extra_classes="modal-action-destructive modal-action-compact",
                    ),
                    ModalAction(
                        "Clear Cookies",
                        "settings-clear-cookies",
                        extra_classes="modal-action-destructive modal-action-compact",
                    ),
                    ModalAction(
                        "Reset Steam",
                        "settings-reset-steam",
                        extra_classes="modal-action-destructive modal-action-compact",
                    ),
                    classes="danger-actions-row",
                ),
                id="settings-danger-card",
                classes="action-card danger-card",
            )
        )
        self.query_one("#settings-danger-card", Vertical).border_title = "Danger zone"
        self.refresh_settings_summary()

    def refresh_settings_summary(self) -> None:
        try:
            identity = self.query_one("#settings-identity", Static)
        except Exception:
            return

        tm = self.task_manager

        # Identity header: product name left, version right-aligned.
        header = Table.grid(expand=True)
        header.add_column(justify="left")
        header.add_column(justify="right")
        header.add_row(
            Text("Marketplace Tools", style=f"bold {COLOR_BRAND}"),
            Text(f"v{APP_VERSION}", style=COLOR_INFO),
        )
        identity.update(header)

        # About facts: the build metadata, laid out as labeled facts rather than a jargon dump.
        facts = Text()
        for index, (label, value, value_style) in enumerate(
            (
                ("channel", str(APP_CHANNEL).lower(), COLOR_INFO),
                ("schema", str(SETTINGS_SCHEMA_VERSION), COLOR_INFO),
                ("mode", self.launch_mode, STATUS_STYLES["warning"] if self.is_test_mode else COLOR_INFO),
            )
        ):
            if index:
                facts.append("      ")
            facts.append(f"{label}  ", style="#6f6f6f")
            facts.append(value, style=value_style)
        try:
            self.query_one("#settings-about-facts", Static).update(facts)
        except Exception:
            pass

        update_line = Text()
        if tm.available_update_version:
            update_line.append("▲ ", style=STATUS_STYLES["warning"])
            update_line.append(f"v{tm.available_update_version} available", style=STATUS_STYLES["warning"])
            update_line.append(f"   ·   you have v{APP_VERSION}", style=COLOR_TEXT_MUTED)
        elif tm.update_check_completed:
            update_line.append("✓ ", style=STATUS_STYLES["success"])
            update_line.append("Up to date", style=STATUS_STYLES["success"])
            update_line.append(f"   ·   latest is v{APP_VERSION}", style=COLOR_TEXT_MUTED)
        else:
            update_line.append("Not checked yet", style=COLOR_TEXT_MUTED)
        try:
            self.query_one("#settings-update", Static).update(update_line)
        except Exception:
            pass
        try:
            self.query_one("#settings-toggle-update-startup", ModalAction).update(
                f"Startup: {'On' if tm.update_check_on_startup else 'Off'}"
            )
        except Exception:
            pass

        storage = tm.browser_storage_summary()
        storage_line = Text()
        storage_line.append(f"{format_storage_size(storage.total_bytes)} used", style=COLOR_INFO)
        storage_line.append("   ·   ", style=COLOR_TEXT_MUTED)
        storage_line.append(
            f"{format_storage_size(storage.disposable_bytes)} disposable", style=COLOR_TEXT_MUTED
        )
        try:
            self.query_one("#settings-storage-facts", Static).update(storage_line)
        except Exception:
            pass

    def refresh_spend_summary(self) -> None:
        try:
            self.query_visible_one("#spend-summary")
        except Exception:
            return

        self.refresh_modal_tile("spend-cap-tile", "Cap", format_compact_silver(self.task_manager.max_spend), "This session")
        self.refresh_modal_tile(
            "spend-session-tile",
            "Session",
            format_compact_silver(self.task_manager.session_silver_spent),
            "Silver spent",
        )

    def refresh_polling_summary(self) -> None:
        try:
            self.query_visible_one("#polling-recommendations")
        except Exception:
            return

        self.refresh_polling_preset_tiles()

    def polling_status_detail(self) -> str:
        return f"{self.task_manager.current_delay_label()} ({self.task_manager.current_delay_range()})"

    def format_delay_seconds(self, value: float) -> str:
        return self.task_manager._format_seconds(value)

    def refresh_buy_delay_summary(self) -> None:
        try:
            self.query_visible_one("#buy-delay-summary")
        except Exception:
            return

        self.refresh_modal_tile(
            "buy-delay-current-tile",
            "Current",
            self.task_manager.purchase_delay_range(),
            "Between BuyItem requests",
        )

    def refresh_monitor_summary(self) -> None:
        try:
            self.query_visible_one("#monitor-mode-options")
        except Exception:
            return

        buying = self.task_manager.purchase_submission_enabled
        mode_tiles = (
            ("watch", "Detect and log only", not buying),
            ("buy", "Auto-buys detections", buying),
        )
        for key, detail, selected in mode_tiles:
            try:
                tile = self.query_visible_one(f"#monitor-mode-{key}", MonitorModeTile)
            except Exception:
                continue
            tile.set_class(selected, "preset-selected")
            body = Table.grid(expand=True)
            body.add_column(justify="left")
            body.add_row(
                Text(str(tile.border_title), style=f"bold {COLOR_BRAND}" if selected else "bold #8f8f8f")
            )
            body.add_row(Text(detail, style="#a06a35" if selected else "#6f6f6f"))
            body.add_row(
                Text(f"{STATUS_DOT} Active", style=STATUS_STYLES["success"]) if selected else Text("")
            )
            tile.update(body)

        scope_tiles = (
            ("boxes", "Outfit boxes", "Male + female sets", self.task_manager.include_outfit_boxes),
            ("pieces", "Outfit pieces", "Male + female items", self.task_manager.include_outfit_pieces),
        )
        for key, title, detail, enabled in scope_tiles:
            try:
                tile = self.query_visible_one(f"#monitor-scope-{key}", MonitorScopeTile)
            except Exception:
                continue
            tile.set_class(enabled, "preset-selected")
            body = Table.grid(expand=True)
            body.add_column(justify="left")
            body.add_row(Text(title, style=f"bold {COLOR_BRAND}" if enabled else "bold #8f8f8f"))
            body.add_row(Text(detail, style="#a06a35" if enabled else "#6f6f6f"))
            body.add_row(
                Text(f"{STATUS_DOT} On", style=STATUS_STYLES["success"])
                if enabled
                else Text(f"{IDLE_DOT} Off", style=STATUS_STYLES["idle"])
            )
            tile.update(body)

    def refresh_session_summary(self) -> None:
        try:
            self.query_visible_one("#session-summary")
        except Exception:
            return

        account = self.session_account_label()
        self.refresh_modal_tile("session-account-tile", "Account", account, self.task_manager.account_mode_label())
        try:
            self.query_visible_one("#session-credentials-row")
            refresh_button = self.query_visible_one("#refresh-session", Button)
        except Exception:
            return

        pa_mode = not self.task_manager.uses_steam_browser_session()
        if not pa_mode:
            setup_complete = self.task_manager.steam_browser_profile_prepared
            self.refresh_modal_tile(
                "session-credentials-tile",
                "Initial Setup",
                "Complete" if setup_complete else "Incomplete",
                "Ready for market login" if setup_complete else "Open Credentials to run setup",
                "success" if setup_complete else "warning",
                True,
            )
            refresh_button.disabled = False
            return

        _state, _detail, _level, email, password = self.pa_credential_state()
        credentials_ready = bool(email and password)
        self.refresh_modal_tile(
            "session-credentials-tile",
            "Credentials",
            "Set" if credentials_ready else "Required",
            "Automatic browser login" if credentials_ready else "Save PA credentials first",
            "success" if credentials_ready else "warning",
            True,
        )
        refresh_button.disabled = not credentials_ready

    def refresh_modal_summaries(self) -> None:
        self.refresh_credentials_summary()
        self.refresh_settings_summary()
        self.refresh_spend_summary()
        self.refresh_polling_summary()
        self.refresh_buy_delay_summary()
        self.refresh_monitor_summary()
        self.refresh_session_summary()

    async def mount_wallet(self, content: Container) -> None:
        wip_note = Static(
            "WIP: Marketplace Inventory is still being polished.",
            id="wallet-wip-note",
            classes="wip-note",
        )
        wip_note.border_title = "Work In Progress"
        await content.mount(wip_note)
        await content.mount(
            Horizontal(
                ModalAction("Refresh Inventory", "refresh-wallet"),
                id="wallet-actions",
            )
        )
        await content.mount(Static("Inventory data has not been loaded yet.", id="wallet-output", classes="panel"))

    async def mount_stats(self, content: Container) -> None:
        await content.mount(
            Horizontal(
                Static(id="stats-session-detected", classes="stats-chip"),
                Static(id="stats-session-purchases", classes="stats-chip"),
                Static(id="stats-session-rate", classes="stats-chip"),
                Static(id="stats-session-spent", classes="stats-chip"),
                classes="stats-chip-row",
            )
        )
        await content.mount(
            Static("Daily activity · 30 days", id="stats-trends-title", classes="stats-chart-title")
        )
        await content.mount(DailyActivityChart())
        await content.mount(
            Horizontal(
                Vertical(
                    Static("Busiest days", classes="stats-chart-title"),
                    Static(id="stats-chart-weekday", classes="stats-chart"),
                    id="stats-col-weekday",
                    classes="stats-chart-column",
                ),
                Vertical(
                    Static("Listing hours", classes="stats-chart-title"),
                    Static(id="stats-chart-hours", classes="stats-chart"),
                    id="stats-col-hours",
                    classes="stats-chart-column",
                ),
                id="stats-chart-row",
            )
        )
        self._stats_trends_key = None
        self.refresh_stats()

    def refresh_stats(self) -> None:
        tm = self.task_manager
        tm.reload_lifetime_stats()
        detected = tm.session_detected_outfits
        bought = tm.session_successful_purchases

        detected_value = Text(str(detected), style=STATUS_STYLES["info"])
        detected_value.append(" this session", style="#6f6f6f")

        bought_value = Text(str(bought), style=STATUS_STYLES["success" if bought else "info"])
        bought_value.append(
            f" · {format_compact_number(tm.lifetime_successful_purchases)} lifetime", style="#6f6f6f"
        )

        rate_value = Text(format_percent(bought, detected), style=self.purchase_rate_style(bought, detected))
        rate_value.append(f" · {bought}/{detected}", style="#6f6f6f")

        spent_value = Text(format_compact_number(tm.session_silver_spent), style=STATUS_STYLES["info"])
        spent_value.append(
            f" · {format_compact_number(tm.lifetime_silver_spent)} lifetime", style="#6f6f6f"
        )

        for chip_id, title, value_text in (
            ("stats-session-detected", "Detected", detected_value),
            ("stats-session-purchases", "Bought", bought_value),
            ("stats-session-rate", "Success Rate", rate_value),
            ("stats-session-spent", "Silver Spent", spent_value),
        ):
            body = Table.grid(expand=True)
            body.add_column(justify="left")
            body.add_row(Text(title, style="bold #8f8f8f"))
            body.add_row(value_text)
            try:
                self.query_one(f"#{chip_id}", Static).update(body)
            except Exception:
                pass
        self.refresh_stats_trends()

    def refresh_stats_trends(self) -> None:
        # Trend queries only rerun when new history landed; the 1-second stats
        # refresh otherwise leaves the charts alone.
        key = self.task_manager.stats_history_revision
        if key == self._stats_trends_key:
            return
        try:
            trends = stats_db.load_trends(30, path=self.task_manager.stats_db_path)
        except Exception as exc:
            self.show_stats_trends_error(exc, key)
            return
        try:
            self.query_one("#stats-chart-daily", DailyActivityChart).update_daily(trends["daily"])
            self.query_one("#stats-chart-weekday", Static).update(weekday_chart(trends["weekday"]))
            self.query_one("#stats-chart-hours", Static).update(hour_heatmap_chart(trends["hourly"]))
        except Exception as exc:
            self.show_stats_trends_error(exc, key)
            return
        self._stats_trends_key = key
        self._stats_trends_error_key = None

    def show_stats_trends_error(self, exc: Exception, key: int) -> None:
        message = Text("Stats charts unavailable", style=STATUS_STYLES["warning"])
        try:
            self.query_one("#stats-chart-daily", DailyActivityChart).clear_daily()
        except Exception:
            pass
        for chart_id in ("stats-chart-daily", "stats-chart-weekday", "stats-chart-hours"):
            try:
                self.query_one(f"#{chart_id}", Static).update(message)
            except Exception:
                pass
        if self._stats_trends_error_key == key:
            return
        detail = str(exc).strip() or exc.__class__.__name__
        self.task_manager.add_event(f"Stats charts failed to load: {detail}", "warning")
        self._stats_trends_error_key = key

    def on_modal_action_pressed(self, event: ModalAction.Pressed) -> None:
        handled = {
            "refresh-wallet",
            "clear-saved-session",
            "clear-credentials",
            "settings-clear-cookies",
            "settings-reset-steam",
            "settings-check-update",
            "settings-toggle-update-startup",
            "settings-save-cache-limit",
            "settings-clean-cache",
        }
        if event.action.action_id not in handled:
            return

        event.stop()
        event.action.blur()
        action_id = event.action.action_id
        if action_id == "refresh-wallet":
            self.run_worker(self.refresh_wallet(), name="wallet-refresh", group="actions", exclusive=True)
        elif action_id == "clear-saved-session":
            self.run_worker(self.clear_saved_session(), name="clear-saved-session", group="actions", exclusive=True)
        elif action_id == "clear-credentials":
            self.run_worker(self.clear_saved_credentials(), name="clear-credentials", group="actions", exclusive=True)
        elif action_id == "settings-clear-cookies":
            self.run_worker(
                self.clear_browser_cookies_from_settings(),
                name="settings-clear-cookies",
                group="actions",
                exclusive=True,
            )
        elif action_id == "settings-reset-steam":
            self.reset_steam_setup_from_settings()
        elif action_id == "settings-check-update":
            self.run_worker(
                self.check_for_updates_from_settings(),
                name="check-update",
                group="actions",
                exclusive=True,
            )
        elif action_id == "settings-toggle-update-startup":
            self.toggle_update_startup_check()
        elif action_id == "settings-save-cache-limit":
            self.save_browser_cache_limit_from_settings()
        elif action_id == "settings-clean-cache":
            self.run_worker(
                self.clean_browser_cache_from_settings(),
                name="settings-clean-cache",
                group="actions",
                exclusive=True,
            )

    def on_log_filter_option_pressed(self, event: LogFilterOption.Pressed) -> None:
        event.stop()
        event.option.blur()
        if event.option.mode == self.log_filter or event.option.mode not in self.LOG_FILTER_LABELS:
            return
        self.log_filter = event.option.mode
        self.sync_event_log()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.button.blur()
        button_id = event.button.id
        if button_id == "save-pa-credentials":
            if await self.save_pa_credential_inputs():
                self.close_active_dashboard_modal()
        elif button_id == "clear-credentials":
            await self.clear_saved_credentials()
        elif button_id == "clear-saved-session":
            await self.clear_saved_session()
        elif button_id == "save-settings":
            await self.save_settings()
        elif button_id == "save-spend-cap":
            if self.apply_spend_cap_from_input("spend-cap-input"):
                self.refresh_modal_summaries()
                self.close_active_dashboard_modal()
        elif button_id == "save-polling":
            if self.save_polling_settings():
                self.refresh_modal_summaries()
                self.close_active_dashboard_modal()
        elif button_id == "save-buy-delay":
            if self.save_buy_delay_settings():
                self.refresh_modal_summaries()
                self.close_active_dashboard_modal()
        elif button_id == "refresh-session":
            self.push_screen(SessionRefreshConfirmScreen(), callback=self._handle_session_refresh_confirmation)
        elif button_id == "refresh-wallet":
            self.run_worker(self.refresh_wallet(), name="wallet-refresh", group="actions", exclusive=True)
        elif button_id == "add-test-log":
            await self.add_test_log()
        elif button_id == "toggle-test-session":
            await self.toggle_test_session()
        elif button_id == "toggle-auto-reauth":
            await self.toggle_test_steam_auto_reauth()
        elif button_id == "expire-test-session":
            await self.expire_test_session()
        elif button_id == "run-session-check":
            await self.run_test_session_check()
        elif button_id == "run-reauth-check":
            await self.run_test_reauthentication_check()
        elif button_id == "prepare-steam-profile":
            self.run_worker(
                self.prepare_steam_browser_profile(),
                name="prepare-steam-profile",
                group="actions",
                exclusive=True,
            )
        elif button_id == "reset-steam-setup":
            await self.reset_test_steam_setup_status()
        elif button_id == "clear-browser-cookies":
            if self._debug_action_allowed():
                self.run_worker(
                    self.clear_test_browser_cookies(),
                    name="clear-browser-cookies",
                    group="actions",
                    exclusive=True,
                )
        elif button_id == "clear-cookies-keep-steam":
            if self._debug_action_allowed():
                self.run_worker(
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
            if self._debug_action_allowed():
                self.run_worker(
                    self.fake_bundled_buy_success(),
                    name="fake-bundled-buy",
                    group="debug-actions",
                    exclusive=True,
                )

    async def on_dashboard_tile_pressed(self, event: DashboardTile.Pressed) -> None:
        event.stop()
        if not event.tile.interactive:
            return

        tile_key = event.tile.tile_key
        if tile_key == "monitor":
            self.push_screen(MonitorModal())
        elif tile_key == "spent":
            self.push_screen(SpendCapModal())
        elif tile_key == "credentials":
            self.push_screen(CredentialsModal())
        elif tile_key == "session":
            self.push_screen(SessionModal())
        elif tile_key == "polling":
            self.push_screen(PollingModal())
        elif tile_key == "buy-delay":
            self.push_screen(BuyDelayModal())
        self.call_after_refresh(self.refresh_modal_summaries)

    def _handle_session_refresh_confirmation(self, confirmed: bool) -> None:
        if confirmed:
            self.close_dashboard_modals()
            self.run_worker(self.login_refresh(), name="login-refresh", group="actions", exclusive=True)

    def on_polling_preset_tile_pressed(self, event: PollingPresetTile.Pressed) -> None:
        event.stop()
        low, high = self.task_manager.delay_choices[event.preset.preset_key][1]
        try:
            self.query_visible_one("#custom-delay-min-input", Input).value = str(low)
            self.query_visible_one("#custom-delay-max-input", Input).value = str(high)
        except Exception:
            return
        self.refresh_polling_preset_tiles()

    def on_credential_action_tile_pressed(self, event: CredentialActionTile.Pressed) -> None:
        event.stop()
        if self.selected_account_mode() != STEAM_BROWSER_MODE:
            self.push_screen(PACredentialsModal())
            self.call_after_refresh(self.refresh_pa_credentials_controls)
            return

        self.run_worker(
            self.prepare_steam_browser_profile(),
            name="prepare-steam-profile",
            group="actions",
            exclusive=True,
        )

    def on_steam_setup_tile_pressed(self, event: SteamSetupTile.Pressed) -> None:
        self.on_credential_action_tile_pressed(event)

    async def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "account-mode-select":
            await self.apply_account_mode_selection(event.value)
            self.refresh_credentials_summary()
            return

        if event.select.id != "delay-select":
            return
        self.apply_delay_choice(event.value)

    async def apply_account_mode_selection(self, account_mode: object) -> None:
        try:
            normalized_mode = str(account_mode)
            if normalized_mode == self.task_manager.account_mode:
                return
            await self.task_manager.change_account_mode(normalized_mode)
        except ValueError:
            self.set_status("Select a valid login method.", "warning")
            return

        if normalized_mode == PA_CREDENTIALS_MODE:
            _state, _detail, _level, email, password = self.pa_credential_state()
            if email and password:
                self.api_handler.email = email
                self.api_handler.password = password

        self.sync_mode_switches(False)
        self.set_status(f"Login method set to {self.task_manager.account_mode_label()}.")
        self.refresh_settings_summary()
        self.refresh_live_widgets()

    async def on_monitor_mode_tile_pressed(self, event: MonitorModeTile.Pressed) -> None:
        event.stop()
        await self.apply_purchase_mode(event.tile.mode_key == "buy")

    def on_monitor_scope_tile_pressed(self, event: MonitorScopeTile.Pressed) -> None:
        event.stop()
        scope_key = event.tile.scope_key
        if scope_key == "boxes":
            current = self.task_manager.include_outfit_boxes
            setter = self.task_manager.set_include_outfit_boxes
            scope_name = "Outfit boxes"
        else:
            current = self.task_manager.include_outfit_pieces
            setter = self.task_manager.set_include_outfit_pieces
            scope_name = "Outfit pieces"

        try:
            enabled = setter(not current)
        except ValueError:
            self.set_status("Choose at least one outfit scan category.", "warning")
            self.refresh_monitor_summary()
            return

        state = "enabled" if enabled else "disabled"
        timing = " It will apply on the next scan." if self.task_manager.checker_enabled else ""
        self.set_status(
            f"{scope_name} {state}. Active scope: {self.task_manager.scan_scope_label()}.{timing}",
            "info",
        )
        self.refresh_monitor_summary()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {
            "spend-cap-input",
            "custom-delay-min-input",
            "custom-delay-max-input",
            "purchase-delay-min-input",
            "purchase-delay-max-input",
        }:
            event.stop()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in {"custom-delay-min-input", "custom-delay-max-input"}:
            self.refresh_polling_preset_tiles()
        elif event.input.id in {"purchase-delay-min-input", "purchase-delay-max-input"}:
            return
        elif event.input.id == "spend-cap-input":
            return
        elif event.input.id in {"email-input", "password-input"}:
            self.refresh_pa_credentials_controls()

    def apply_delay_choice(self, delay: object) -> None:
        delay = str(delay)
        if delay == "custom":
            if delay == self.task_manager.delay:
                return

            self.task_manager.set_custom_delay_choice()
            self.set_status(f"Polling set to {self.polling_status_detail()}.", "info")
            self.refresh_settings_summary()
            self.refresh_live_widgets()
            return

        if delay not in self.task_manager.delay_choices or delay == self.task_manager.delay:
            return

        self.task_manager.set_delay_choice(delay)
        self.set_status(f"Polling set to {self.polling_status_detail()}.", "info")
        self.refresh_settings_summary()
        self.refresh_live_widgets()

    def apply_custom_delay_from_inputs(self, log_status: bool = True) -> bool:
        try:
            min_input = self.query_visible_one("#custom-delay-min-input", Input)
            max_input = self.query_visible_one("#custom-delay-max-input", Input)
        except Exception:
            return False

        try:
            self.task_manager.set_custom_delay_range(min_input.value.strip(), max_input.value.strip())
        except (TypeError, ValueError):
            self.set_status(
                "Custom polling range must use positive seconds with min less than or equal to max.",
                "warning",
            )
            return False

        if log_status:
            self.set_status(f"Polling set to {self.polling_status_detail()}.", "info")
        self.refresh_settings_summary()
        self.refresh_live_widgets()
        return True

    def save_polling_settings(self) -> bool:
        if not self.apply_custom_delay_from_inputs(log_status=False):
            return False

        self.set_status(f"Polling settings saved: {highlight(self.polling_status_detail())}.", "success")
        return True

    def apply_purchase_delay_from_inputs(self, log_status: bool = True) -> bool:
        try:
            min_input = self.query_visible_one("#purchase-delay-min-input", Input)
            max_input = self.query_visible_one("#purchase-delay-max-input", Input)
        except Exception:
            return False

        try:
            self.task_manager.set_purchase_delay_range(min_input.value.strip(), max_input.value.strip())
        except (TypeError, ValueError):
            self.set_status(
                "Buy delay must use non-negative seconds with min less than or equal to max.",
                "warning",
            )
            return False

        if log_status:
            self.set_status(f"Buy delay set to {self.task_manager.purchase_delay_range()}.", "info")
        self.refresh_live_widgets()
        return True

    def save_buy_delay_settings(self) -> bool:
        if not self.apply_purchase_delay_from_inputs(log_status=False):
            return False

        self.set_status(f"Buy delay saved: {highlight(self.task_manager.purchase_delay_range())}.", "success")
        return True

    async def apply_purchase_mode(self, enabled: bool, source_switch_id: str | None = None) -> None:
        if self._syncing_controls:
            return

        if enabled == self.task_manager.purchase_submission_enabled:
            return

        if not enabled:
            self.task_manager.set_purchase_submission_enabled(False)
            self.set_status(f"Mode set to {highlight('Watch only')}.", "info")
            self.sync_mode_switches(False, except_id=source_switch_id)
            self.refresh_settings_summary()
            self.refresh_live_widgets()
            return

        if self.task_manager.checker_enabled:
            if not self.api_handler.login_status:
                self.set_status(
                    "Login required before enabling buy mode. Login or refresh the marketplace session first.",
                    "warning",
                )
                self.sync_mode_switches(False)
                self.refresh_live_widgets()
                return

            self.push_screen(
                ConfirmBuyModeScreen(
                    account=self.session_account_label(),
                    scan_scope=self.task_manager.scan_scope_label(),
                    polling=f"{self.task_manager.current_delay_label()} ({self.task_manager.current_delay_range()})",
                    spend_cap=format_compact_silver(self.task_manager.max_spend),
                    buy_delay=self.task_manager.purchase_delay_range(),
                ),
                callback=self._handle_running_buy_mode_confirmation,
            )
            return

        if self.task_manager.single_item_test_checker_enabled:
            self.set_status("Single-item test monitor is running. Stop it before changing buy mode.", "warning")
            self.sync_mode_switches(False)
            self.refresh_live_widgets()
            return

        self.task_manager.set_purchase_submission_enabled(True)
        # Arming buy mode is a deliberate choice, not a fault — the orange mode name carries
        # the caution; the sentence itself stays on the dim noise floor.
        self.set_status(f"Mode set to {highlight_brand('Buy mode')}. Starting the monitor will ask for confirmation.", "info")
        self.sync_mode_switches(True, except_id=source_switch_id)
        self.refresh_settings_summary()
        self.refresh_live_widgets()

    def _handle_running_buy_mode_confirmation(self, confirmed: bool) -> None:
        self.task_manager.set_purchase_submission_enabled(bool(confirmed))
        if confirmed:
            self.set_status(f"{highlight_brand('Buy mode')} enabled for the running monitor.", "info")
        else:
            self.set_status("Buy mode canceled. Monitor remains watch only.", "info")
        self.sync_mode_switches(self.task_manager.purchase_submission_enabled)
        self.refresh_settings_summary()
        self.refresh_live_widgets()

    def sync_mode_switches(self, value: bool, except_id: str | None = None) -> None:
        # The buy/watch mode controls derive their state from the task manager; re-render them.
        self.refresh_monitor_summary()

    def apply_spend_cap_from_input(self, input_id: str) -> bool:
        try:
            spend_input = self.query_visible_one(f"#{input_id}", Input)
        except Exception:
            return False

        spend_value = spend_input.value.strip() or "0"
        try:
            spend_cap = int(spend_value)
            if spend_cap < 0:
                raise ValueError
        except ValueError:
            self.set_status("Spend cap must be 0 or a positive integer.", "warning")
            return False

        self.set_spend_cap(spend_cap)
        return True

    def set_spend_cap(self, spend_cap: int) -> None:
        self.task_manager.set_spend_cap(spend_cap)
        self.sync_spend_cap_inputs()
        self.set_status(f"Spend cap set to {format_compact_silver(self.task_manager.max_spend)}.", "info")
        self.refresh_settings_summary()
        self.refresh_live_widgets()

    def sync_spend_cap_inputs(self) -> None:
        value = str(self.task_manager.max_spend or 0)
        for input_id in ("spend-cap-input",):
            try:
                self.query_visible_one(f"#{input_id}", Input).value = value
            except Exception:
                pass

    def _debug_action_allowed(self) -> bool:
        if self.is_test_mode:
            return True

        self.set_status("Debug actions are only available in test mode.", "warning")
        return False

    async def add_test_log(self) -> None:
        if not self._debug_action_allowed():
            return

        message, level = random.choice(TEST_LOG_MESSAGES)
        self.task_manager.add_event(message, level)
        self.set_status("Synthetic event added.")
        await self.return_to_dashboard()

    async def toggle_test_session(self) -> None:
        if not self._debug_action_allowed():
            return

        if self.task_manager.single_item_test_checker_enabled:
            self.set_status("Stop the single-item test monitor before changing simulated session state.", "warning")
            await self.return_to_dashboard()
            return

        enabled = not self.is_simulated_session
        self.task_manager.set_simulated_session(enabled)
        if enabled:
            self.set_status(
                "Test session marked valid. Buy mode will use simulated purchase responses.",
                "success",
            )
        else:
            self.sync_mode_switches(False)
            self.set_status("Test session marked invalid. Buy mode returned to watch only.", "warning")
        self.refresh_modal_summaries()
        await self.return_to_dashboard()

    async def toggle_test_steam_auto_reauth(self) -> None:
        if not self._debug_action_allowed():
            return

        enabled = self.task_manager.debug_toggle_steam_auto_reauth()
        if enabled is None:
            self.set_status("Select Steam Account before toggling automatic re-authentication.", "warning")
        elif enabled:
            self.set_status("Steam automatic re-authentication debug override enabled.", "success")
        else:
            self.set_status("Steam automatic re-authentication debug override disabled.", "warning")
        self.refresh_modal_summaries()
        await self.return_to_dashboard()

    async def expire_test_session(self) -> None:
        if not self._debug_action_allowed():
            return

        if self.task_manager.debug_invalidate_marketplace_session():
            self.set_status("Test marketplace session cleared. Run Session Check or Reauth Check to test recovery.", "warning")
            self.refresh_modal_summaries()
        await self.return_to_dashboard()

    async def run_test_reauthentication_check(self) -> None:
        if not self._debug_action_allowed():
            return

        recovered = await self.task_manager.debug_run_reauthentication_check()
        if recovered:
            self.set_status("Test re-authentication check succeeded.")
        elif self.task_manager.uses_steam_browser_session():
            self.set_status("Steam Account refresh required after test re-authentication check.")
        else:
            self.set_status("Test re-authentication check failed.")
        self.refresh_modal_summaries()
        await self.return_to_dashboard()

    async def run_test_session_check(self) -> None:
        if not self._debug_action_allowed():
            return

        result = await self.task_manager.debug_run_session_check_now()
        if result:
            self.set_status("Session check complete: session valid or re-authenticated. See log.", "info")
        else:
            self.set_status("Session check: re-authentication required or failed. See log.", "warning")
        self.refresh_modal_summaries()
        await self.return_to_dashboard()

    async def reset_test_steam_setup_status(self) -> None:
        if not self._debug_action_allowed():
            return

        if self.task_manager.debug_clear_steam_initial_setup_status():
            self.set_status("Initial Steam setup status reset.", "warning")
            self.refresh_credentials_summary()
            self.refresh_settings_summary()
            self.refresh_live_widgets()
        else:
            self.set_status("Initial Steam setup status reset failed.", "warning")

    async def clear_test_browser_cookies(self) -> None:
        if not self._debug_action_allowed():
            return

        cleared = await self.task_manager.debug_clear_steam_browser_cookies()
        if cleared:
            self.set_status("Browser cookies cleared from the Steam profile.", "warning")
        else:
            self.set_status("Browser cookie clear failed.", "warning")

    async def clear_test_cookies_keep_steam(self) -> None:
        if not self._debug_action_allowed():
            return

        cleared = await self.task_manager.debug_clear_market_cookies_keep_steam_login()
        if cleared:
            self.set_status("Cleared non-Steam cookies; kept Steam login. Run Reauth Check to test.", "warning")
        else:
            self.set_status("Cookie clear skipped (Steam Account mode only) or failed; see log.", "warning")

    async def start_single_item_test_monitor(self, allow_purchase: bool = False) -> None:
        if not self._debug_action_allowed():
            return

        if self.task_manager.single_item_test_checker_enabled:
            self.set_status("Single-item test monitor already running; no additional task started.", "info")
            await self.return_to_dashboard()
            return

        if self.task_manager.checker_enabled:
            self.set_status("Stop the normal monitor before starting the single-item test monitor.", "warning")
            await self.return_to_dashboard()
            return

        if allow_purchase:
            if self.is_simulated_session:
                self.set_status(
                    "Disable the simulated test session before starting the live single-item buy test.",
                    "warning",
                )
                await self.return_to_dashboard()
                return

            if not self.api_handler.login_status:
                self.set_status(
                    "Login required before starting the single-item buy test. Refresh the marketplace session first.",
                    "warning",
                )
                await self.return_to_dashboard()
                return

            self.push_screen(
                ConfirmBuyModeScreen(
                    account=self.session_account_label(),
                    polling=f"{self.task_manager.current_delay_label()} ({self.task_manager.current_delay_range()})",
                    spend_cap=format_compact_silver(self.task_manager.max_spend),
                    buy_delay=self.task_manager.purchase_delay_range(),
                ),
                callback=self._handle_single_item_test_buy_confirmation,
            )
            return

        await self._start_single_item_test_monitor_now(allow_purchase=False)

    def _handle_single_item_test_buy_confirmation(self, confirmed: bool) -> None:
        if not confirmed:
            self.set_status("Single-item buy test canceled.", "info")
            return
        self.run_worker(
            self._start_single_item_test_monitor_now(allow_purchase=True),
            name="start-single-item-buy-test",
            group="actions",
            exclusive=True,
        )

    async def start_live_buy_error_probe(self) -> None:
        if not self._debug_action_allowed():
            return

        if self.is_simulated_session:
            self.set_status("Disable the simulated test session before sending the live buy error probe.", "warning")
            await self.return_to_dashboard()
            return

        if not self.api_handler.login_status:
            self.set_status("Login required before sending the live buy error probe. Refresh the marketplace session first.", "warning")
            await self.return_to_dashboard()
            return

        target = LIVE_BUY_ERROR_TEST_TARGET
        self.push_screen(
            ConfirmLiveTestBuyScreen(
                item_id=target["main_key"],
                price=format_compact_silver(int(target["max_buy_price"])),
                account=self.session_account_label(),
                buy_delay=self.task_manager.purchase_delay_range(),
            ),
            callback=self._handle_live_buy_error_probe_confirmation,
        )

    def _handle_live_buy_error_probe_confirmation(self, confirmed: bool) -> None:
        if not confirmed:
            self.set_status("Live buy error probe canceled.", "info")
            return
        self.run_worker(
            self._run_live_buy_error_probe_now(),
            name="live-buy-error-probe",
            group="actions",
            exclusive=True,
        )

    async def _run_live_buy_error_probe_now(self) -> None:
        target = LIVE_BUY_ERROR_TEST_TARGET
        self.set_status(
            f"Submitting live buy error probe for item {target['main_key']} at {format_compact_silver(int(target['max_buy_price']))}.",
            "warning",
        )
        submitted = await self.task_manager.debug_run_live_buy_error_probe()
        if submitted:
            self.set_status("Live buy error probe completed. Check Core logs for the marketplace response.", "warning")
        else:
            self.set_status("Live buy error probe did not run. Check Core logs for details.", "warning")
        await self.return_to_dashboard()

    async def _start_single_item_test_monitor_now(self, allow_purchase: bool = False) -> None:
        item_name = SINGLE_ITEM_TEST_TARGET["name"]
        started = await self.task_manager.start_single_item_test_checker(allow_purchase=allow_purchase)
        if started:
            if allow_purchase:
                self.set_status(
                    f"Single-item buy test started for {item_name}. Public detection uses the normal buy pipeline.",
                    "warning",
                )
            else:
                self.set_status(
                    f"Single-item test monitor started for {item_name}. Public scan only; live buy calls are disabled.",
                    "warning",
                )
        elif self.task_manager.single_item_test_checker_enabled:
            self.set_status("Single-item test monitor already running; no additional task started.", "info")
        else:
            self.set_status("Single-item test monitor did not start.", "warning")
        await self.return_to_dashboard()

    async def stop_single_item_test_monitor(self) -> None:
        if not self._debug_action_allowed():
            return

        was_running = await self.task_manager.stop_single_item_test_checker()
        if was_running:
            self.set_status("Single-item test monitor stopped.", "info")
        else:
            self.set_status("Single-item test monitor already stopped.", "info")
        await self.return_to_dashboard()

    async def fake_outfit_detection(self) -> None:
        if not self._debug_action_allowed():
            return

        await self.task_manager.debug_fake_outfit_detection()
        self.set_status("Fake detection processed through watch-only path.")
        await self.return_to_dashboard()

    async def fake_multi_outfit_detection(self) -> None:
        if not self._debug_action_allowed():
            return

        await self.task_manager.debug_fake_multi_outfit_detection()
        self.set_status("Fake multi-listing detection processed.")
        await self.return_to_dashboard()

    async def fake_buy_success(self) -> None:
        if not self._debug_action_allowed():
            return

        await self.task_manager.debug_simulate_purchase_success()
        self.set_status("Fake detection and purchase recorded.")
        await self.return_to_dashboard()

    async def fake_bundled_buy_success(self) -> None:
        if not self._debug_action_allowed():
            return

        self.set_status("Fake bundled buy list running: 8 outfits.")
        await self.return_to_dashboard()
        await self.task_manager.debug_simulate_bundled_purchase_success(
            progress_callback=self.refresh_live_widgets
        )
        self.refresh_live_widgets()
        self.set_status("Fake bundled buy list recorded: 8 outfits.")

    async def prepare_steam_browser_profile(self) -> None:
        try:
            account_mode = self.selected_account_mode()
        except ValueError:
            self.set_status("Select Steam Account before running setup.", "warning")
            return

        if account_mode != STEAM_BROWSER_MODE:
            self.set_status("Select Steam Account before running setup.", "warning")
            return

        self.set_status("Opening initial Steam browser setup.")
        prepared = await self.task_manager.prepare_steam_browser_profile(allow_inactive_mode=True)
        if prepared:
            self.set_status("Initial Steam setup saved. Refresh Session can now use the market login.")
        else:
            self.set_status("Initial Steam setup did not complete.")
        self.refresh_credentials_summary()
        self.refresh_settings_summary()
        self.refresh_live_widgets()

    async def return_to_dashboard(self) -> None:
        if self.current_view != "dashboard":
            await self.show_view("dashboard")
            return
        self.refresh_live_widgets()

    async def save_pa_credential_inputs(self) -> bool:
        email = self.query_visible_one("#email-input", Input).value.strip()
        password = self.query_visible_one("#password-input", Input).value
        _saved_state, _saved_detail, _saved_level, saved_email, saved_password = self.pa_credential_state()
        if not (email and password):
            self.refresh_pa_credentials_controls()
            return False
        if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            self.set_pa_credentials_warning("Enter a valid email address.")
            return False
        saved_email_matches = bool(saved_email and saved_email.strip().lower() == email.strip().lower())

        previous_email = self.api_handler.email
        session_identity_changed = bool(
            self.api_handler.login_status
            and (not previous_email or previous_email.strip().lower() != email.strip().lower())
        )
        self.api_handler.email = email
        self.api_handler.password = password
        try:
            if password == saved_password and saved_email_matches:
                save_credentials(email)
            else:
                save_credentials(email, password)
        except CredentialStoreError as exc:
            self.set_pa_credentials_warning(f"Unable to save credentials: {exc}")
            return False

        mode_changed = await self.task_manager.change_account_mode(PA_CREDENTIALS_MODE)
        if session_identity_changed and not mode_changed:
            await self.task_manager.reset_authentication_context("Credentials changed")
        self.sync_mode_switches(False)
        self.query_visible_one("#password-input", Input).value = ""
        self.set_status(f"Credentials saved: {highlight(self.task_manager.account_mode_label())}.", "success")
        self.refresh_credentials_summary()
        self.refresh_settings_summary()
        self.refresh_live_widgets()
        return True

    async def clear_saved_credentials(self) -> None:
        try:
            clear_credentials()
        except CredentialStoreError as exc:
            self.task_manager.add_event(f"Unable to clear credentials: {exc}", "error")
            self.set_status("Unable to clear saved credentials.")
            return

        self.api_handler.email = None
        self.api_handler.password = None
        for input_id in ("email-input", "password-input"):
            try:
                self.query_visible_one(f"#{input_id}", Input).value = ""
            except Exception:
                pass
        self.set_status("Saved credentials cleared.", "info")
        self.refresh_credentials_summary()
        self.refresh_settings_summary()
        self.refresh_live_widgets()

    async def clear_saved_session(self) -> None:
        cleared_now = await self.task_manager.reset_authentication_context("Manual session reset")
        self.sync_mode_switches(False)
        if cleared_now:
            self.set_status("Saved marketplace session cleared. Refresh Session to log in again.", "warning")
            self.set_settings_maintenance_status("Saved marketplace session cleared. Refresh Session to log in again.")
        else:
            self.set_status("Session reset queued until the current purchase chain finishes.", "warning")
            self.set_settings_maintenance_status("Session reset queued until the current purchase chain finishes.")
        self.refresh_settings_summary()
        self.refresh_live_widgets()

    def set_settings_maintenance_status(self, message: str) -> None:
        try:
            self.query_one("#settings-status", Static).update(message)
        except Exception:
            pass

    def set_settings_update_status(self, message: str) -> None:
        try:
            self.query_one("#settings-status", Static).update(message)
        except Exception:
            pass

    async def check_for_updates_from_settings(self) -> None:
        self.set_settings_update_status("Checking for updates...")
        result = await self.task_manager.check_for_update(manual=True)
        if result is None or result.status == "error":
            message = "Could not check for updates. Check your connection and try again."
            self.set_settings_update_status(message)
            self.set_status("Update check failed.")
        elif result.update_available:
            self.set_settings_update_status(
                f"Update available: v{result.latest_version} (you have {APP_VERSION}). "
                f"Download it from {RELEASES_URL}"
            )
            self.set_status(f"Update available: v{result.latest_version}.")
        else:
            message = f"You are on the latest version (v{result.current_version})."
            self.set_settings_update_status(message)
            self.set_status(message)
        self.refresh_settings_summary()

    def toggle_update_startup_check(self) -> None:
        enabled = self.task_manager.set_update_check_on_startup(
            not self.task_manager.update_check_on_startup
        )
        state = "on" if enabled else "off"
        self.set_settings_update_status(f"Startup update check turned {state}.")
        self.set_status(f"Startup update check turned {state}.", "info")
        self.refresh_settings_summary()

    async def startup_update_check(self) -> None:
        result = await self.task_manager.check_for_update(manual=False)
        if result is not None:
            self.refresh_settings_summary()

    async def clear_browser_cookies_from_settings(self) -> None:
        self.set_settings_maintenance_status("Clearing browser cookies...")
        cleared = await self.task_manager.clear_browser_session_cookies()
        if cleared:
            message = "Browser cookies cleared. Refresh Session to log in again."
            self.set_status(message, "warning")
        else:
            message = "Browser cookie clear failed. See the event log for details."
            self.set_status("Browser cookie clear failed.", "warning")
        self.set_settings_maintenance_status(message)
        self.refresh_settings_summary()
        self.refresh_credentials_summary()
        self.refresh_live_widgets()

    def save_browser_cache_limit_from_settings(self) -> None:
        try:
            cache_input = self.query_one("#settings-cache-threshold-input", Input)
            value = cache_input.value
            cache_input.blur()
            threshold = self.task_manager.set_browser_cache_cleanup_threshold_mb(value)
        except ValueError as exc:
            message = str(exc)
            self.set_settings_maintenance_status(message)
            self.set_status(message, "warning")
            return
        except Exception:
            message = "Browser cache cleanup limit is not available."
            self.set_settings_maintenance_status(message)
            self.set_status(message, "warning")
            return

        label = self.task_manager.browser_cache_cleanup_threshold_label()
        try:
            cache_input.value = str(threshold)
        except Exception:
            pass
        message = f"Browser cache cleanup limit saved: {highlight(label)}."
        self.set_settings_maintenance_status(message)
        self.set_status(message, "success")
        self.refresh_settings_summary()

    async def clean_browser_cache_from_settings(self) -> None:
        self.set_settings_maintenance_status("Cleaning disposable browser cache...")
        result = await self.task_manager.clean_browser_cache_now()
        if result is None:
            message = "Browser cache cleanup failed. See the event log for details."
            self.set_status("Browser cache cleanup failed.", "warning")
        else:
            removed_bytes = result["removed_bytes"]
            if removed_bytes:
                message = f"Cleaned {format_storage_size(removed_bytes)} of disposable browser cache."
                self.set_status(message, "success")
            else:
                message = "No disposable browser cache found to clean."
                self.set_status(message, "info")
            if result["failed_paths"]:
                message += f" {result['failed_paths']} cache path(s) could not be removed."
        self.set_settings_maintenance_status(message)
        self.refresh_settings_summary()

    def reset_steam_setup_from_settings(self) -> None:
        if self.task_manager.reset_steam_initial_setup_status():
            message = "Steam initial setup reset to incomplete. Run setup again from Credentials."
            self.set_status(message, "warning")
        else:
            message = "Steam setup reset failed."
            self.set_status(message, "warning")
        self.set_settings_maintenance_status(message)
        self.refresh_settings_summary()
        self.refresh_credentials_summary()
        self.refresh_live_widgets()

    async def save_settings(self) -> None:
        try:
            account_mode = self.query_visible_one("#account-mode-select", Select).value
        except Exception:
            self.set_status("Settings are not available.", "warning")
            return

        try:
            normalized_mode = self.task_manager.set_account_mode(str(account_mode))
        except ValueError:
            self.set_status("Select a valid session mode.", "warning")
            return

        if normalized_mode == STEAM_BROWSER_MODE and self.task_manager.purchase_submission_enabled:
            self.task_manager.set_purchase_submission_enabled(False)
            self.sync_mode_switches(False)

        self.set_status(f"Settings saved: {highlight(self.task_manager.account_mode_label())}.", "success")
        self.refresh_settings_summary()
        self.refresh_live_widgets()

    async def login_refresh(self) -> None:
        self.set_status("Fetching session status...")
        await self.task_manager.login()
        self.set_status("Login check complete.")
        self.refresh_live_widgets()

    async def start_monitor(self) -> None:
        if self.task_manager.single_item_test_checker_enabled:
            self.set_status("Single-item test monitor is running. Stop it before starting the normal monitor.", "warning")
            self.refresh_live_widgets()
            return

        if self.task_manager.checker_enabled:
            mode = "buy mode" if self.task_manager.purchase_submission_enabled else "watch-only mode"
            self.set_status(f"Monitor already running in {mode}; no additional monitor task started.", "info")
            self.refresh_live_widgets()
            return

        if not self.task_manager.has_scan_scope():
            self.set_status("Choose at least one outfit scan category before starting the monitor.", "warning")
            self.refresh_live_widgets()
            return

        if self.task_manager.purchase_submission_enabled and not self.api_handler.login_status:
            if self.task_manager.uses_steam_browser_session():
                message = "Steam Account refresh required before starting buy mode. Refresh Session first."
                self.set_status(message)
                self.task_manager.add_event(message, "warning")
                self.refresh_live_widgets()
                return

            message = (
                "Login required before starting buy mode. "
                "Login or refresh the marketplace session before starting the monitor."
            )
            self.set_status(message)
            self.task_manager.add_event(message, "warning")
            self.refresh_live_widgets()
            return

        if self.task_manager.purchase_submission_enabled:
            self.push_screen(
                ConfirmBuyModeScreen(
                    account=self.session_account_label(),
                    scan_scope=self.task_manager.scan_scope_label(),
                    polling=f"{self.task_manager.current_delay_label()} ({self.task_manager.current_delay_range()})",
                    spend_cap=format_compact_silver(self.task_manager.max_spend),
                    buy_delay=self.task_manager.purchase_delay_range(),
                ),
                callback=self._handle_buy_mode_confirmation,
            )
            return

        await self._start_monitor_now()

    def _handle_buy_mode_confirmation(self, confirmed: bool) -> None:
        if not confirmed:
            self.set_status("Buy mode start canceled.", "info")
            return
        self.run_worker(self._start_monitor_now(), name="start-buy-mode", group="actions", exclusive=True)

    async def _start_monitor_now(self) -> None:
        mode = "buy mode" if self.task_manager.purchase_submission_enabled else "watch-only mode"
        started = await self.task_manager.start_checker()
        if started:
            # start_checker logs the notable "Monitor started" event; status bar only here.
            scope = self.task_manager.scan_scope_label().lower()
            self.set_status(f"Monitor started in {mode} — {scope}.")
            self.close_dashboard_modals()
        elif self.task_manager.single_item_test_checker_enabled:
            self.set_status("Single-item test monitor is running. Stop it before starting the normal monitor.", "warning")
        elif self.task_manager.checker_enabled:
            self.set_status(f"Monitor already running in {mode}; no additional monitor task started.", "info")
        elif not self.task_manager.has_scan_scope():
            self.set_status("Choose at least one outfit scan category before starting the monitor.", "warning")
        else:
            self.set_status(f"Monitor did not start in {mode}.", "warning")
        await self.show_view("dashboard")

    async def refresh_wallet(self) -> None:
        self.set_status("Loading marketplace inventory...")
        try:
            response = await self.api_handler.get_mp_inventory()
            silver_balance = marketplace_silver_balance(response)
        except Exception as exc:
            self.task_manager.add_event(f"Inventory lookup failed: {exc}", "error")
            self.set_status("Inventory lookup failed.")
            try:
                self.query_one("#wallet-output", Static).update(str(exc))
            except Exception:
                pass
            return

        summary = Table.grid(padding=(0, 2))
        summary.add_column(style="bold")
        summary.add_column()
        summary.add_row("Silver", format_compact_silver(silver_balance) if silver_balance is not None else "Not found")
        summary.add_row("Value Pack", "Active" if response.get("useValuePackage") else "Inactive")
        if response.get("totalWeight") is not None and response.get("maxWeight") is not None:
            summary.add_row("Weight", f"{response.get('totalWeight')}/{response.get('maxWeight')}")

        self.query_one("#wallet-output", Static).update(Group(summary, JSON.from_data(response)))
        if silver_balance is not None:
            self.set_status(f"Inventory loaded: {highlight_silver(format_compact_silver(silver_balance))}.", "success")
        else:
            self.set_status("Inventory loaded.", "success")

    def action_show_dashboard(self) -> None:
        self.run_worker(self.show_view("dashboard"), name="show-dashboard", group="navigation", exclusive=True)

    async def action_toggle_monitor(self) -> None:
        if self.current_view == "dashboard" and not isinstance(self.focused, Input):
            await self.toggle_monitor_from_dashboard()

    async def action_quit_app(self) -> None:
        await self.task_manager.stop_checker()
        await self.task_manager.stop_single_item_test_checker()
        await self.task_manager.stop_login_status_checker()
        await self.task_manager.flush_stats_writes()
        self.api_handler.save_session()
        self.exit()
