APP_TITLE = "Marketplace Tools"

COLOR_BRAND = "rgb(255,145,60)"
COLOR_SUCCESS = "rgb(126,184,138)"
COLOR_WARNING = "rgb(201,180,88)"
COLOR_CAUTION = "rgb(201,138,80)"
COLOR_ERROR = "rgb(209,106,106)"
COLOR_INFO = "rgb(232,229,220)"
COLOR_TEXT_MUTED = "rgb(170,170,170)"
COLOR_STEAM = "rgb(19,100,151)"
COLOR_GOLD = "rgb(218,177,86)"

EVENT_LEVEL_COLORS = {
    "info": COLOR_INFO,
    "success": COLOR_SUCCESS,
    "warning": COLOR_WARNING,
    "error": COLOR_ERROR,
}

# Routine (non-notable) info lines form the log's dim noise floor so notable
# lines stand out without hiding anything.
COLOR_EVENT_ROUTINE = "rgb(111,111,111)"


def highlight(value):
    """Bold-white emphasis for key values (counts, item ids) inside an event line."""
    return f"[bold {COLOR_INFO}]{value}[/bold {COLOR_INFO}]"


def highlight_silver(value):
    """Gold emphasis for silver amounts, matching the dashboard spent display."""
    return f"[bold {COLOR_GOLD}]{value}[/bold {COLOR_GOLD}]"


def highlight_brand(value):
    """Brand-orange emphasis for non-silver mode labels and action names."""
    return f"[bold {COLOR_BRAND}]{value}[/bold {COLOR_BRAND}]"


def mask_email(email):
    if not email or "@" not in email:
        return "Not set"

    name, domain = email.split("@", 1)
    if len(name) <= 2:
        masked_name = name[0] + "*"
    else:
        masked_name = name[:2] + "*" * max(2, len(name) - 2)
    return f"{masked_name}@{domain}"


def format_compact_number(value):
    value = int(value or 0)
    sign = "-" if value < 0 else ""
    value = abs(value)
    for suffix, size in (("T", 1_000_000_000_000), ("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if value >= size:
            formatted = f"{value / size:.1f}".rstrip("0").rstrip(".")
            return f"{sign}{formatted}{suffix}"
    return f"{sign}{value}"


def format_compact_silver(value):
    if value is None:
        return "No cap"
    return f"{format_compact_number(value)} silver"


def format_percent(numerator, denominator):
    if denominator <= 0:
        return "0%"
    return f"{(numerator / denominator) * 100:.0f}%"


def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"
