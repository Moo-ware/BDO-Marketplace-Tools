"""In-memory event log behind the Activity tail and the Logs tab.

One unified stream with two-axis importance — this module is the single home of
the classification policy:

- ``level`` picks the color: info, success, warning, error.
- ``notable`` picks the brightness. Routine info AND routine success render as the
  dim gray noise floor; notable lines keep full-strength color. Green is earned by
  notability, not by level. Errors are always notable; warnings never dim but only
  reach the Activity tail when a call site flags them notable.
- ``divider`` (a short label such as "monitor started · buy mode") makes the Logs
  view render the record as a dim full-width rule marking a run boundary, while
  the Activity tail still shows the normal colored line.
- Identical consecutive events coalesce into one record with a ``×N`` repeat
  counter carrying the latest timestamp, so error/retry storms cannot flood the
  buffer and flush real events out of the tail.

Rendering happens at read time: ``rendered_for_filter`` produces Rich-markup lines
for the UI, while ``plain_events`` strips markup for tests and programmatic use.
"""

from collections import deque
from datetime import datetime

from rich.text import Text

from bdo_marketplace_tools.ui.display import COLOR_EVENT_ROUTINE, EVENT_LEVEL_COLORS

EVENT_LOG_LIMIT = 100
DIVIDER_RULE_WIDTH = 64
DIVIDER_RULE_STYLE = "#3a3a3a"


class EventLog:
    def __init__(self, limit=EVENT_LOG_LIMIT):
        self.records = deque(maxlen=limit)
        self.unseen_alerts = False

    def add(self, message, level="info", notable=False, divider=None):
        # Errors are always notable; everything else opts in at the call site.
        notable = bool(notable) or level == "error"

        message = str(message)
        last = self.records[-1] if self.records else None
        if last is not None and last["message"] == message and last["level"] == level:
            last["count"] += 1
            last["timestamp"] = datetime.now().strftime("%H:%M:%S")
            last["notable"] = last["notable"] or notable
        else:
            self.records.append(
                {
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "message": message,
                    "level": level,
                    "notable": notable,
                    "count": 1,
                    "divider": divider,
                }
            )

        if level in {"warning", "error"}:
            self.unseen_alerts = True

    @staticmethod
    def render_event(record):
        style = EVENT_LEVEL_COLORS.get(record["level"], EVENT_LEVEL_COLORS["info"])
        if not record["notable"] and record["level"] in {"info", "success"}:
            style = COLOR_EVENT_ROUTINE
        line = f"[dim]{record['timestamp']}[/dim] [{style}]{record['message']}[/{style}]"
        if record["count"] > 1:
            line = f"{line} [dim]×{record['count']}[/dim]"
        return line

    @staticmethod
    def render_divider(record):
        label = f"── {record['divider']} · {record['timestamp']} "
        rule = label + "─" * max(0, DIVIDER_RULE_WIDTH - len(label))
        return f"[{DIVIDER_RULE_STYLE}]{rule}[/{DIVIDER_RULE_STYLE}]"

    @staticmethod
    def plain_text(record):
        # Messages may carry Rich markup for the UI; strip it for programmatic use.
        try:
            message = Text.from_markup(record["message"]).plain
        except Exception:
            message = record["message"]
        line = f"{record['timestamp']} {message}"
        if record["count"] > 1:
            line = f"{line} ×{record['count']}"
        return line

    @property
    def plain_events(self):
        return tuple(self.plain_text(record) for record in self.records)

    def rendered_for_filter(self, log_filter="all", dividers=False):
        log_filter = str(log_filter or "all").strip().lower()
        if log_filter == "notable":
            records = [record for record in self.records if record["notable"]]
        elif log_filter == "alerts":
            records = [record for record in self.records if record["level"] in {"warning", "error"}]
        else:
            records = list(self.records)
        return tuple(
            self.render_divider(record) if dividers and record.get("divider") else self.render_event(record)
            for record in records
        )

    def has_unseen_alerts(self):
        return self.unseen_alerts

    def mark_alerts_seen(self):
        self.unseen_alerts = False
