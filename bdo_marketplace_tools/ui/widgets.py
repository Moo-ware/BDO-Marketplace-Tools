from textual.binding import Binding
from textual.message import Message
from textual.widgets import Static


class ModalAction(Static):
    class Pressed(Message):
        def __init__(self, action: "ModalAction") -> None:
            super().__init__()
            self.action = action

    def __init__(self, label: str, action_id: str, *, extra_classes: str = "") -> None:
        classes = "modal-action-tile"
        if extra_classes:
            classes = f"{classes} {extra_classes}"
        super().__init__(label, id=action_id, classes=classes)
        self.action_id = action_id

    def on_click(self) -> None:
        self.post_message(self.Pressed(self))


class LogFilterOption(Static):
    class Pressed(Message):
        def __init__(self, option: "LogFilterOption") -> None:
            super().__init__()
            self.option = option

    def __init__(self, mode: str, label: str) -> None:
        super().__init__(label, id=f"log-filter-{mode}", classes="log-filter-option")
        self.mode = mode

    def on_click(self) -> None:
        self.post_message(self.Pressed(self))


class NavTab(Static):
    class Pressed(Message):
        def __init__(self, tab: "NavTab") -> None:
            super().__init__()
            self.tab = tab

    def __init__(self, key: str, label: str) -> None:
        super().__init__(label, id=f"tab-{key}", classes="nav-tab")
        self.key = key

    def on_click(self) -> None:
        self.post_message(self.Pressed(self))



class DashboardTile(Static, can_focus=True):
    BINDINGS = [
        Binding("enter", "press", "Press", show=False),
        Binding("space", "press", "Press", show=False),
    ]

    class Pressed(Message):
        def __init__(self, tile: "DashboardTile") -> None:
            super().__init__()
            self.tile = tile

    def __init__(self, tile_key: str, title: str, interactive: bool = True) -> None:
        tile_class = "tile-clickable" if interactive else "tile-muted"
        super().__init__("", id=f"tile-{tile_key}", classes=f"dashboard-tile {tile_class}")
        self.tile_key = tile_key
        self.interactive = interactive
        self.border_title = title

    def allow_focus(self) -> bool:
        return self.interactive

    def focus_on_click(self) -> bool:
        return False

    def action_press(self) -> None:
        if not self.interactive:
            return
        self.post_message(self.Pressed(self))

    def on_click(self) -> None:
        self.action_press()
        self.blur()


class MonitorToggleTile(Static, can_focus=True):
    BINDINGS = [
        Binding("enter", "press", "Press", show=False),
    ]

    class Pressed(Message):
        def __init__(self, tile: "MonitorToggleTile") -> None:
            super().__init__()
            self.tile = tile

    def __init__(self) -> None:
        super().__init__("", id="monitor-toggle", classes="toggle-start")

    def focus_on_click(self) -> bool:
        return False

    def action_press(self) -> None:
        self.post_message(self.Pressed(self))

    def on_click(self) -> None:
        self.action_press()
        self.blur()


class MonitorModeTile(Static):
    class Pressed(Message):
        def __init__(self, tile: "MonitorModeTile") -> None:
            super().__init__()
            self.tile = tile

    def __init__(self, mode_key: str, title: str) -> None:
        super().__init__("", id=f"monitor-mode-{mode_key}", classes="modal-info-tile modal-info-clickable")
        self.mode_key = mode_key
        self.border_title = title

    def on_click(self) -> None:
        self.post_message(self.Pressed(self))


class PollingPresetTile(Static):
    class Pressed(Message):
        def __init__(self, preset: "PollingPresetTile") -> None:
            super().__init__()
            self.preset = preset

    def __init__(self, preset_key: str, title: str) -> None:
        super().__init__("", id=f"polling-preset-{preset_key}", classes="modal-info-tile modal-info-clickable")
        self.preset_key = preset_key
        self.border_title = title

    def on_click(self) -> None:
        self.post_message(self.Pressed(self))


class CredentialActionTile(Static):
    class Pressed(Message):
        def __init__(self, tile: "CredentialActionTile") -> None:
            super().__init__()
            self.tile = tile

    def __init__(self) -> None:
        super().__init__("", id="credential-action-tile", classes="modal-info-tile modal-info-muted modal-info-wide")

    def on_click(self) -> None:
        if "modal-info-clickable" not in self.classes:
            return
        self.post_message(self.Pressed(self))


SteamSetupTile = CredentialActionTile


