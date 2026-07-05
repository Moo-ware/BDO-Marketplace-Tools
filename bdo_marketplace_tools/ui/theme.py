from bdo_marketplace_tools.ui.display import COLOR_CAUTION, COLOR_ERROR, COLOR_INFO, COLOR_SUCCESS, COLOR_WARNING

from bdo_marketplace_tools.ui.display import COLOR_GOLD, COLOR_STEAM

DEFAULT_THEME = "ansi-dark"
STATUS_STYLES = {
    "success": f"bold {COLOR_SUCCESS}",
    "warning": f"bold {COLOR_WARNING}",
    "orange": f"bold {COLOR_CAUTION}",
    "error": f"bold {COLOR_ERROR}",
    "gold": f"bold {COLOR_GOLD}",
    "info": f"bold {COLOR_INFO}",
    "steam": f"bold {COLOR_STEAM}",
    "idle": "#8f8f8f",
}

STATUS_DOT = "●"
IDLE_DOT = "○"


def _parse_rgb(style):
    inner = style[style.index("(") + 1 : style.index(")")]
    return tuple(int(part) for part in inner.split(","))


# Success-rate color is a continuous ramp rather than hard buckets, so the hue climbs
# smoothly with the rate — the 70s already read green-leaning instead of flat yellow.
# Anchors: 0% red -> 30% orange -> 55% yellow -> 80%+ green.
_RATE_SPECTRUM_STOPS = (
    (0.00, _parse_rgb(COLOR_ERROR)),
    (0.30, _parse_rgb(COLOR_CAUTION)),
    (0.55, _parse_rgb(COLOR_WARNING)),
    (0.80, _parse_rgb(COLOR_SUCCESS)),
    (1.00, _parse_rgb(COLOR_SUCCESS)),
)


def rate_spectrum_style(ratio):
    """Bold rgb style interpolated across the red->green rate ramp for ratio in [0, 1]."""
    ratio = max(0.0, min(1.0, ratio))
    for (p0, c0), (p1, c1) in zip(_RATE_SPECTRUM_STOPS, _RATE_SPECTRUM_STOPS[1:]):
        if ratio <= p1:
            t = 0.0 if p1 == p0 else (ratio - p0) / (p1 - p0)
            r, g, b = (round(a + (b_ - a) * t) for a, b_ in zip(c0, c1))
            return f"bold rgb({r},{g},{b})"
    r, g, b = _RATE_SPECTRUM_STOPS[-1][1]
    return f"bold rgb({r},{g},{b})"

BANNER_ART = r"""
██████╗ ██████╗  ██████╗                                             ███████████
██╔══██╗██╔══██╗██╔═══██╗                                        █████████████████
██████╔╝██║  ██║██║   ██║                                      ███████     ███████
██╔══██╗██║  ██║██║   ██║                                     ██████   █   ███████
██████╔╝██████╔╝╚██████╔╝                                    █████████   █████████
╚═════╝ ╚═════╝  ╚═════╝                                     █████████████████████
███╗   ███╗ █████╗ ██████╗ ██╗  ██╗███████╗████████╗        ████  █████████  ████
████╗ ████║██╔══██╗██╔══██╗██║ ██╔╝██╔════╝╚══██╔══╝        █████████████████████
██╔████╔██║███████║██████╔╝█████╔╝ █████╗     ██║            ███████   █████████
██║╚██╔╝██║██╔══██║██╔══██╗██╔═██╗ ██╔══╝     ██║            ███████████████████
██║ ╚═╝ ██║██║  ██║██║  ██║██║  ██╗███████╗   ██║             ████████████████
╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝   ╚═╝                ███████████
""".strip("\n")

# Same art with the mascot's eyes closed (blink frame) — the eyes are the two gaps in the
# "████  █████████  ████" row. Line widths must match BANNER_ART exactly so the blink never
# shifts layout.
BANNER_BLINK_ART = r"""
██████╗ ██████╗  ██████╗                                             ███████████
██╔══██╗██╔══██╗██╔═══██╗                                        █████████████████
██████╔╝██║  ██║██║   ██║                                      ███████     ███████
██╔══██╗██║  ██║██║   ██║                                     ██████   █   ███████
██████╔╝██████╔╝╚██████╔╝                                    █████████   █████████
╚═════╝ ╚═════╝  ╚═════╝                                     █████████████████████
███╗   ███╗ █████╗ ██████╗ ██╗  ██╗███████╗████████╗        ████▄▄█████████▄▄████
████╗ ████║██╔══██╗██╔══██╗██║ ██╔╝██╔════╝╚══██╔══╝        █████████████████████
██╔████╔██║███████║██████╔╝█████╔╝ █████╗     ██║            ███████   █████████
██║╚██╔╝██║██╔══██║██╔══██╗██╔═██╗ ██╔══╝     ██║            ███████████████████
██║ ╚═╝ ██║██║  ██║██║  ██║██║  ██╗███████╗   ██║             ████████████████
╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝   ╚═╝                ███████████
""".strip("\n")

# Rotating footer lines while the monitor runs. Index 0 is the default; the pool is
# cosmetic and safe to edit freely.
RUNNING_QUIPS = (
    "watching the marketplace",
    "eyes on the listings…",
    "nothing gets past me!",
    "refreshing so you don't have to",
    "waiting for someone to blink first",
    "outfits won't hide forever…",
)

# The sleeping "Zzz" trail overlaid on the dozing mascot (idle). Each entry is a
# (row, col, char) cell in the banner grid, ordered low->high so the z's rise toward
# the mascot's head; the animation reveals them one at a time then resets.
MASCOT_ZZZ_TRAIL = ((2, 56, "z"), (1, 59, "z"), (0, 62, "Z"))

TEST_LOG_MESSAGES = [
    ("Synthetic scan completed: no outfits detected.", "info"),
    ("Synthetic outfit detected in premium category.", "success"),
    ("Synthetic purchase skipped: test spend cap reached.", "warning"),
    ("Synthetic session refresh warning for layout testing.", "warning"),
    ("Synthetic marketplace response error for log sizing.", "error"),
    ("Synthetic purchase request succeeded for one outfit.", "success"),
]


