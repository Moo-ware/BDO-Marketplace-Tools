from textual.binding import Binding
from textual.message import Message
from textual.widgets import Static

from bdo_marketplace_tools.ui.charts import (
    CHART_HEIGHT,
    daily_activity_chart,
    daily_activity_column_layout,
    daily_chart_label_width,
)


class DailyActivityChart(Static):
    def __init__(self) -> None:
        super().__init__("", id="stats-chart-daily", classes="stats-chart")
        self._daily = []
        self._column_start = 0
        self._column_width = 1
        self._bar_width = 1

    def update_daily(self, daily, height=CHART_HEIGHT) -> None:
        self._daily = list(daily or [])
        self._set_geometry(height)
        self.tooltip = None
        self.update(daily_activity_chart(self._daily, height=height))

    def clear_daily(self) -> None:
        self._daily = []
        self.tooltip = None

    def tooltip_for_day(self, index):
        if index is None or index < 0 or index >= len(self._daily):
            return None
        row = self._daily[index]
        day = row.get("day")
        day_label = day.strftime("%b %d, %Y (%a)") if hasattr(day, "strftime") else str(day)
        detected = int(row.get("detected") or 0)
        purchased = int(row.get("purchased") or 0)
        scans = int(row.get("scans") or 0)
        rate = f"{round(purchased / detected * 100)}%" if detected else "0%"
        return (
            f"{day_label}\n"
            f"Detected: {detected}\n"
            f"Purchased: {purchased}\n"
            f"Scans: {scans}\n"
            f"Purchase rate: {rate}"
        )

    def on_mouse_move(self, event) -> None:
        self.tooltip = self.tooltip_for_day(self._day_index_for_x(event.x))

    def on_leave(self) -> None:
        self.tooltip = None

    def _set_geometry(self, height=CHART_HEIGHT) -> None:
        if not self._daily:
            self._column_start = 0
            self._column_width = 1
            self._bar_width = 1
            return
        bar_width, gap = daily_activity_column_layout(len(self._daily))
        max_detected = max(int(row.get("detected") or 0) for row in self._daily)
        # Must match daily_activity_chart's layout: the label width comes from the
        # rounded axis scale (9 -> "10"), not the raw max.
        self._column_start = daily_chart_label_width(max_detected, height) + 3
        self._column_width = bar_width + gap
        self._bar_width = bar_width

    def _day_index_for_x(self, x):
        relative_x = int(x) - self._column_start
        if relative_x < 0 or not self._daily:
            return None
        index = relative_x // self._column_width
        if index < 0 or index >= len(self._daily):
            return None
        if relative_x % self._column_width >= self._bar_width:
            return None
        return index


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

