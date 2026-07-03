import os
from pathlib import Path


APP_DIR_NAME = "bdo-marketplace-tools"
# Set BDO_DATA_DIR to keep data anywhere you like (portable installs, tests, power users).
DATA_DIR_ENV_VAR = "BDO_DATA_DIR"


def default_data_dir():
    """Resolve the per-user data directory, independent of where the app code lives.

    Precedence: ``BDO_DATA_DIR`` override → Windows ``%LOCALAPPDATA%`` → XDG/home fallback.
    Local (not roaming) app data is used on purpose: the session cookies and persistent
    browser profiles stored here are machine-bound and can be large, so they should not roam.
    """
    override = os.environ.get(DATA_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_DIR_NAME / "data"

    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share"
    return base / APP_DIR_NAME / "data"


DATA_DIR = default_data_dir()

APP_SETTINGS_PATH = DATA_DIR / "app_settings.json"

SESSION_COOKIE_PATH = DATA_DIR / "session.json"

LOCAL_STATS_PATH = DATA_DIR / "local_stats.json"

BROWSER_PROFILES_DIR = DATA_DIR / "browser_profiles"
PA_MARKET_PROFILE_PATH = BROWSER_PROFILES_DIR / "pa-market"
STEAM_MARKET_PROFILE_PATH = BROWSER_PROFILES_DIR / "steam-market"
