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

TEST_LOG_MESSAGES = [
    ("Synthetic scan completed: no outfits detected.", "info"),
    ("Synthetic outfit detected in premium category.", "success"),
    ("Synthetic purchase skipped: test spend cap reached.", "warning"),
    ("Synthetic session refresh warning for layout testing.", "warning"),
    ("Synthetic marketplace response error for log sizing.", "error"),
    ("Synthetic purchase request succeeded for one outfit.", "success"),
]


