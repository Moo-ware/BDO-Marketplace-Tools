"""Block-character trend charts for the Stats view.

Pure functions from ``stats_db.load_trends`` snapshots to Rich renderables —
no plotting dependency, so the charts inherit the app palette and render as
plain text inside Textual Statics.
"""

from datetime import date
from math import ceil, floor, log10

from rich.text import Text

from bdo_marketplace_tools.ui.display import COLOR_BRAND, COLOR_TEXT_MUTED


CHART_HEIGHT = 9
WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Eighth blocks give sub-row resolution for the daily columns.
_PARTIAL_BLOCKS = " ▁▂▃▄▅▆▇█"
_HEAT_SHADES = ("░░", "▒▒", "▓▓", "██")
_HEAT_STYLES = ("#6f4d2d", "#a86731", "#d27d32", COLOR_BRAND)

# Dark neutral keeps detected context quiet while purchased orange owns the signal.
STYLE_DETECTED = "#3f3f3f"
STYLE_PURCHASED = COLOR_BRAND
STYLE_AXIS = "#5a5a5a"
STYLE_LABEL = "#8f8f8f"
STYLE_MONITORED = "#6f9a78"
STYLE_OFFLINE = "#a05555"
STYLE_EMPTY = "#3a3a3a"


def daily_activity_column_layout(day_count):
    # Wide columns for the 7-day view, compact ones so 30 days still fit at 120 cols.
    if day_count <= 7:
        return 4, 2
    return 2, 1


def daily_chart_label_width(max_value, height=CHART_HEIGHT):
    """Y-axis label width used by ``daily_activity_chart`` column layout.

    Derived from the rounded-up axis scale, not the raw max: 9 detections render
    against a scale of 10, so anything mapping mouse x-positions back to columns
    (the hover tooltip) must use this same width.
    """
    scale, _tick_rows = _daily_chart_scale(max_value, height)
    return max(1, len(str(scale)))


def daily_activity_chart(daily, height=CHART_HEIGHT):
    """Columns of detections per day with the purchased share overlaid in brand orange.

    ``daily`` is the oldest-first ``load_trends()["daily"]`` list. A ``▔`` strip under
    the axis marks days the monitor actually scanned, so quiet days and offline days
    read differently.
    """
    chart = Text()
    if not daily:
        chart.append("No history recorded yet.", style=COLOR_TEXT_MUTED)
        return chart

    bar_width, gap = daily_activity_column_layout(len(daily))
    max_detected = max(row["detected"] for row in daily)
    scale, tick_rows = _daily_chart_scale(max_detected, height)
    label_width = daily_chart_label_width(max_detected, height)
    cell = bar_width + gap

    for row_from_top in range(height):
        row_from_bottom = height - 1 - row_from_top
        row_value = tick_rows.get(row_from_top)
        if row_value is not None:
            chart.append(f"{row_value:>{label_width}} ", style=STYLE_LABEL)
            chart.append("┤", style=STYLE_AXIS)
        else:
            chart.append(" " * (label_width + 1))
            chart.append("│", style=STYLE_AXIS)
        chart.append(" ")

        for row in daily:
            detected_eighths = round(row["detected"] / scale * height * 8)
            purchased_eighths = round(min(row["purchased"], row["detected"]) / scale * height * 8)
            filled = min(8, max(0, detected_eighths - row_from_bottom * 8))
            purchased_fill = min(filled, max(0, purchased_eighths - row_from_bottom * 8))
            _append_detection_purchase_cell(chart, filled, purchased_fill, bar_width)
            chart.append(" " * gap)
        _append_daily_side_legend(chart, row_from_top)
        chart.append("\n")

    chart.append(" " * (label_width + 1))
    chart.append("┼" + "─" * (len(daily) * cell), style=STYLE_AXIS)
    chart.append("\n")

    chart.append(_coverage_strip(daily, label_width + 3, bar_width, gap))
    chart.append("\n")

    chart.append(_day_axis_labels(daily, label_width, bar_width, gap))

    if max_detected == 0:
        chart.append("\n\n")
        chart.append(
            "No outfits in this window yet — the chart fills in while the monitor runs.",
            style=COLOR_TEXT_MUTED,
        )
    return chart


def _daily_chart_scale(max_value, height, target_ticks=5):
    max_value = max(0, int(max_value or 0))
    if max_value <= 0:
        return 1, {}

    if max_value <= target_ticks:
        scale = max_value
        step = 1
    else:
        raw_step = max_value / target_ticks
        magnitude = 10 ** floor(log10(raw_step))
        normalized = raw_step / magnitude
        if normalized <= 1:
            nice = 1
        elif normalized <= 2:
            nice = 2
        elif normalized <= 2.5:
            nice = 2.5
        elif normalized <= 5:
            nice = 5
        else:
            nice = 10
        step = max(1, ceil(nice * magnitude))
        scale = int(ceil(max_value / step) * step)

    tick_rows = {}
    for tick in range(scale, 0, -step):
        row = round(height - (tick / scale * height))
        row = min(height - 1, max(0, row))
        tick_rows.setdefault(row, tick)
    return scale, tick_rows


def _coverage_strip(daily, indent, bar_width, gap):
    strip = Text(" " * indent)
    for row in daily:
        style = STYLE_MONITORED if row["scans"] > 0 else STYLE_OFFLINE
        strip.append("▔" * bar_width, style=style)
        strip.append(" " * gap)
    return strip


def _append_detection_purchase_cell(chart, detected_fill, purchased_fill, width):
    detected_fill = min(8, max(0, detected_fill))
    purchased_fill = min(detected_fill, max(0, purchased_fill))
    if detected_fill <= 0:
        chart.append(" " * width)
        return
    if purchased_fill <= 0:
        chart.append(_PARTIAL_BLOCKS[detected_fill] * width, style=STYLE_DETECTED)
        return
    if purchased_fill >= detected_fill:
        chart.append(_PARTIAL_BLOCKS[detected_fill] * width, style=STYLE_PURCHASED)
        return

    chart.append(
        _PARTIAL_BLOCKS[purchased_fill] * width,
        style=f"{STYLE_PURCHASED} on {STYLE_DETECTED}",
    )


def _append_daily_side_legend(chart, row_from_top):
    entries = (
        ("█", "detected", STYLE_DETECTED),
        ("█", "purchased", STYLE_PURCHASED),
        ("▔", "monitored", STYLE_MONITORED),
        ("▔", "offline", STYLE_OFFLINE),
    )
    if row_from_top % 2 != 0:
        return
    entry_index = row_from_top // 2
    if entry_index >= len(entries):
        return
    symbol, label, style = entries[entry_index]
    chart.append("  ")
    chart.append(symbol, style=style)
    chart.append(f" {label}", style=STYLE_LABEL)


def _day_axis_labels(daily, label_width, bar_width, gap):
    cell = bar_width + gap
    indent = label_width + 3
    # Pad the right edge so a month label on the final day isn't clipped.
    buffer = [" "] * (indent + len(daily) * cell + 6)
    occupied = [False] * len(buffer)

    def place(index, label, center):
        start = indent + index * cell
        if center:
            start += max(0, (bar_width - len(label)) // 2)
        if any(occupied[p] for p in range(max(0, start - 1), min(len(occupied), start + len(label) + 1))):
            return
        for offset, char in enumerate(label):
            position = start + offset
            if 0 <= position < len(buffer):
                buffer[position] = char
                occupied[position] = True

    if bar_width >= 3:
        # 7-day view: one weekday name centered under each bar.
        for index, row in enumerate(daily):
            day = row["day"]
            label = WEEKDAY_LABELS[day.weekday()] if isinstance(day, date) else str(day)
            place(index, label, center=True)
        return Text("".join(buffer).rstrip(), style=STYLE_LABEL)

    # 30-day view: month markers win, day-number ticks fill the gaps.
    shown_month = None
    for index, row in enumerate(daily):
        day = row["day"]
        if not isinstance(day, date):
            continue
        if index == 0 or day.month != shown_month:
            shown_month = day.month
            place(index, f"{day.strftime('%b')} {day.day}", center=False)
    for index, row in enumerate(daily):
        if index % 5 != 0:
            continue
        day = row["day"]
        place(index, str(day.day) if isinstance(day, date) else str(day), center=False)
    return Text("".join(buffer).rstrip(), style=STYLE_LABEL)


def weekday_chart(weekday, track_width=20):
    """Horizontal Mon–Sun bars of detected outfits over the selected window."""
    detected = weekday["detected"]
    scale = max(1, max(detected))
    chart = Text()
    for index, label in enumerate(WEEKDAY_LABELS):
        value = detected[index]
        filled = round(value / scale * track_width) if value else 0
        chart.append(f"{label}  ", style=STYLE_LABEL)
        if filled:
            chart.append("█" * filled, style=STYLE_PURCHASED)
        chart.append("░" * (track_width - filled), style=STYLE_EMPTY)
        chart.append(f"  {value}", style="#d8d3c8" if value else STYLE_LABEL)
        if index < len(WEEKDAY_LABELS) - 1:
            chart.append("\n")
    return chart


def hour_heatmap_chart(hourly):
    """Weekday-by-hour heatmap of detections: shading shows when outfits get listed."""
    max_value = max((value for row in hourly for value in row), default=0)
    chart = Text()

    chart.append("     " + "".join(f"{hour:<8}" for hour in (0, 4, 8, 12, 16, 20)).rstrip(), style=STYLE_LABEL)
    chart.append("\n")

    for weekday in range(7):
        chart.append(f"{WEEKDAY_LABELS[weekday]}  ", style=STYLE_LABEL)
        for hour in range(24):
            value = hourly[weekday][hour]
            if value <= 0 or max_value <= 0:
                chart.append("· ", style=STYLE_EMPTY)
                continue
            shade_index = min(3, int(value / max_value * 4))
            chart.append(_HEAT_SHADES[shade_index], style=_HEAT_STYLES[shade_index])
        chart.append("\n")

    chart.append("\n")
    chart.append("less ", style=STYLE_LABEL)
    chart.append("· ", style=STYLE_EMPTY)
    for index, shade in enumerate(_HEAT_SHADES):
        chart.append(shade, style=_HEAT_STYLES[index])
        if index < len(_HEAT_SHADES) - 1:
            chart.append(" ")
    chart.append(" more", style=STYLE_LABEL)
    return chart
