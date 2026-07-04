from rich.table import Table
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from bdo_marketplace_tools.storage.app_settings import ACCOUNT_MODE_LABELS
from bdo_marketplace_tools.ui.styles import MODAL_CSS
from bdo_marketplace_tools.ui.widgets import ModalAction, MonitorModeTile, PollingPresetTile, SteamSetupTile


class DashboardModalScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close_modal", "Close", show=False)]
    CSS = MODAL_CSS
    # Don't auto-focus the first field; inputs highlight only when the user clicks into them.
    AUTO_FOCUS = None

    def close_modal(self) -> None:
        self.dismiss(None)

    def action_close_modal(self) -> None:
        self.close_modal()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.button.blur()
        if event.button.id in {"close-modal", "cancel-modal"}:
            event.stop()
            self.close_modal()



class ConfirmBuyModeScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]
    CSS = DashboardModalScreen.CSS
    AUTO_FOCUS = None

    def __init__(self, account: str, polling: str, spend_cap: str, buy_delay: str) -> None:
        super().__init__()
        self.account = account
        self.polling = polling
        self.spend_cap = spend_cap
        self.buy_delay = buy_delay

    def compose(self) -> ComposeResult:
        review = Table.grid(padding=(0, 2))
        review.add_column(style="bold #6f6f6f", no_wrap=True)
        review.add_column()
        review.add_row("Account", self.account)
        review.add_row("Mode", "Buy mode")
        review.add_row("Polling", self.polling)
        review.add_row("Spend cap", self.spend_cap)
        review.add_row("Buy delay", self.buy_delay)
        with Vertical(id="confirm-dialog", classes="modal-card") as dialog:
            dialog.border_title = "Confirm Buy Mode"
            dialog.border_subtitle = "esc"
            yield Static("The app will start buying with these settings:", classes="modal-note")
            yield Static(review)
            with Horizontal(id="confirm-actions", classes="modal-actions"):
                yield ModalAction("▶ Start Buy Mode", "confirm-start")
                yield Static("", classes="modal-actions-spacer")
                yield ModalAction("Cancel", "confirm-cancel")

    def on_modal_action_pressed(self, event: ModalAction.Pressed) -> None:
        self.dismiss(event.action.action_id == "confirm-start")

    def action_cancel(self) -> None:
        self.dismiss(False)


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
            yield Static("This sends one real marketplace buy request through the normal purchase pipeline:", classes="modal-note")
            yield Static(review)
            with Horizontal(id="confirm-actions", classes="modal-actions"):
                yield ModalAction("Submit Test Buy", "confirm-live-test-buy")
                yield Static("", classes="modal-actions-spacer")
                yield ModalAction("Cancel", "cancel-live-test-buy")

    def on_modal_action_pressed(self, event: ModalAction.Pressed) -> None:
        self.dismiss(event.action.action_id == "confirm-live-test-buy")

    def action_cancel(self) -> None:
        self.dismiss(False)


class MonitorModal(DashboardModalScreen):
    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-card") as dialog:
            dialog.border_title = "Monitor Mode"
            dialog.border_subtitle = "esc"
            yield Static(
                "Buy mode requires an online marketplace session. If the session is offline, refresh Session from the dashboard before starting buy mode.",
                classes="modal-note",
            )
            with Horizontal(id="monitor-mode-options", classes="modal-summary-row"):
                yield MonitorModeTile("watch", "Watch only")
                yield MonitorModeTile("buy", "Buy mode")
            with Horizontal(classes="modal-actions"):
                yield Static("", classes="modal-actions-spacer")
                yield Button("Close", id="close-modal", classes="btn-quiet")


class SpendCapModal(DashboardModalScreen):
    def compose(self) -> ComposeResult:
        app = self.app
        with Vertical(classes="modal-card") as dialog:
            dialog.border_title = "Spend Cap"
            dialog.border_subtitle = "esc"
            with Horizontal(id="spend-summary", classes="modal-summary-row"):
                yield Static(id="spend-cap-tile", classes="modal-info-tile modal-info-muted")
                yield Static(id="spend-session-tile", classes="modal-info-tile modal-info-muted")
            with Horizontal(classes="modal-sentence"):
                yield Static("Stop buying after")
                yield Input(
                    value=str(app.task_manager.max_spend or 0),
                    type="integer",
                    placeholder="0 for no cap",
                    id="spend-cap-input",
                    select_on_focus=False,
                )
                yield Static("silver", classes="sentence-dim")
                yield Static("· 0 = no cap", classes="sentence-dim")
            with Horizontal(classes="modal-actions"):
                yield Button("Save", id="save-spend-cap")
                yield Static("", classes="modal-actions-spacer")
                yield Button("Close", id="close-modal", classes="btn-quiet")


class PollingModal(DashboardModalScreen):
    def compose(self) -> ComposeResult:
        app = self.app
        low, high = app.task_manager.current_delay_bounds()
        with Vertical(classes="modal-card") as dialog:
            dialog.border_title = "Polling"
            dialog.border_subtitle = "esc"
            yield Static("Presets", classes="modal-section-title")
            yield Static(
                "Polling controls how often the app checks the marketplace for new listings. Slower polling is calmer; faster polling checks more often.",
                classes="modal-note",
            )
            with Horizontal(id="polling-recommendations", classes="modal-summary-row"):
                yield PollingPresetTile("1", "Fast")
                yield PollingPresetTile("2", "Balanced")
                yield PollingPresetTile("3", "Slow")
            with Horizontal(classes="modal-sentence"):
                yield Static("Custom:", classes="sentence-dim")
                yield Static("every")
                yield Input(
                    value=str(low), type="integer", placeholder="s", id="custom-delay-min-input", select_on_focus=False
                )
                yield Static("to")
                yield Input(
                    value=str(high), type="integer", placeholder="s", id="custom-delay-max-input", select_on_focus=False
                )
                yield Static("seconds")
            with Horizontal(classes="modal-actions"):
                yield Button("Save", id="save-polling")
                yield Static("", classes="modal-actions-spacer")
                yield Button("Close", id="close-modal", classes="btn-quiet")


class BuyDelayModal(DashboardModalScreen):
    def compose(self) -> ComposeResult:
        app = self.app
        low, high = app.task_manager.purchase_delay_bounds
        with Vertical(classes="modal-card") as dialog:
            dialog.border_title = "Buy Delay"
            dialog.border_subtitle = "esc"
            with Horizontal(id="buy-delay-summary", classes="modal-summary-row"):
                yield Static(id="buy-delay-current-tile", classes="modal-info-tile modal-info-muted")
            yield Static(
                "When a scan finds multiple buyable items, this waits a random amount of time between each purchase attempt. It does not change how often the app scans.",
                classes="modal-note",
            )
            with Horizontal(classes="modal-sentence"):
                yield Static("Wait")
                yield Input(
                    value=app.format_delay_seconds(low),
                    type="number",
                    placeholder="s",
                    id="purchase-delay-min-input",
                    select_on_focus=False,
                )
                yield Static("to")
                yield Input(
                    value=app.format_delay_seconds(high),
                    type="number",
                    placeholder="s",
                    id="purchase-delay-max-input",
                    select_on_focus=False,
                )
                yield Static("seconds between buys")
            with Horizontal(classes="modal-actions"):
                yield Button("Save", id="save-buy-delay")
                yield Static("", classes="modal-actions-spacer")
                yield Button("Close", id="close-modal", classes="btn-quiet")


class CredentialsModal(DashboardModalScreen):
    def compose(self) -> ComposeResult:
        app = self.app
        mode_options = [(label, value) for value, label in ACCOUNT_MODE_LABELS.items()]
        with Vertical(classes="modal-card") as dialog:
            dialog.border_title = "Credentials"
            dialog.border_subtitle = "esc"
            yield Label("Login method")
            yield Select(
                mode_options,
                value=app.task_manager.account_mode,
                id="account-mode-select",
            )
            yield Static(id="credentials-mode-note", classes="modal-note")
            with Horizontal(id="credentials-summary", classes="modal-summary-row"):
                yield SteamSetupTile()
            with Horizontal(classes="modal-actions"):
                yield Button("Clear PA Account", id="clear-credentials", classes="btn-danger")
                yield Static("", classes="modal-actions-spacer")
                yield Button("Close", id="close-modal", classes="btn-quiet")


class PACredentialsModal(DashboardModalScreen):
    def compose(self) -> ComposeResult:
        app = self.app
        _, _, _, email, _ = app.pa_credential_state()
        with Vertical(classes="modal-card") as dialog:
            dialog.border_title = "Pearl Abyss Account"
            dialog.border_subtitle = "esc"
            yield Static(
                "Pearl Abyss Account uses your saved email and OS keyring password when the app refreshes the marketplace session.",
                classes="modal-note",
            )
            yield Label("Email", id="credentials-email-label", classes="field-label")
            yield Input(value=email or "", placeholder="account@example.com", id="email-input", select_on_focus=False)
            yield Label("Password", id="credentials-password-label", classes="field-label")
            yield Input(password=True, placeholder="Stored in OS keyring", id="password-input", select_on_focus=False)
            yield Static("", id="pa-credentials-warning", classes="modal-warning")
            with Horizontal(classes="modal-actions"):
                yield Button("Save", id="save-pa-credentials", disabled=True)
                yield Static("", classes="modal-actions-spacer")
                yield Button("Close", id="close-modal", classes="btn-quiet")


class SessionModal(DashboardModalScreen):
    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-card") as dialog:
            dialog.border_title = "Session"
            dialog.border_subtitle = "esc"
            with Horizontal(id="session-summary", classes="modal-summary-row"):
                yield Static(id="session-account-tile", classes="modal-info-tile modal-info-muted modal-info-wide")
            with Horizontal(id="session-credentials-row", classes="modal-summary-row"):
                yield Static(id="session-credentials-tile", classes="modal-info-tile modal-info-muted modal-info-wide")
            with Horizontal(classes="modal-actions"):
                refresh_button = Button("Refresh Session", id="refresh-session")
                refresh_button.can_focus = False
                yield refresh_button
                yield Static("", classes="modal-actions-spacer")
                close_button = Button("Close", id="close-modal", classes="btn-quiet")
                close_button.can_focus = False
                yield close_button


class SessionRefreshConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]
    CSS = DashboardModalScreen.CSS
    AUTO_FOCUS = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-card") as dialog:
            dialog.border_title = "Refresh Session"
            dialog.border_subtitle = "esc"
            yield Static("Refresh the marketplace session now?")
            with Horizontal(classes="modal-actions"):
                yield ModalAction("Refresh", "confirm-refresh-session")
                yield Static("", classes="modal-actions-spacer")
                yield ModalAction("Cancel", "cancel-refresh-session")

    def on_modal_action_pressed(self, event: ModalAction.Pressed) -> None:
        self.dismiss(event.action.action_id == "confirm-refresh-session")

    def action_cancel(self) -> None:
        self.dismiss(False)



