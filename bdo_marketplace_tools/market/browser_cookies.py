from urllib.parse import urlparse

from bdo_marketplace_tools.market.api_handler import GAME_TRADE_URL, TRADE_URL


MARKET_COOKIE_URLS = (
    f"{TRADE_URL}/",
    f"{GAME_TRADE_URL}/",
)
MARKET_COOKIE_HOSTS = tuple(urlparse(url).hostname for url in MARKET_COOKIE_URLS)
STEAM_PROFILE_COOKIE_URLS = (
    "https://store.steampowered.com/",
    "https://steamcommunity.com/",
)
MARKET_SESSION_COOKIE_NAMES = {
    "TradeAuth_Session",
}
STEAM_LOGIN_COOKIE_NAMES = {
    "steamLoginSecure",
}
# The full Steam web session lives across these domains (steamLoginSecure, sessionid,
# steamMachineAuth, ...), and keeping all of them avoids repeated Steam logins in re-auth tests.
STEAM_AUTH_COOKIE_DOMAIN_SUFFIXES = (
    "steampowered.com",
    "steamcommunity.com",
    "steam-chat.com",
)


def _is_steam_auth_cookie(cookie):
    if not isinstance(cookie, dict):
        return False
    domain = (cookie.get("domain") or "").lstrip(".").lower()
    if not domain:
        return False
    return any(
        domain == suffix or domain.endswith("." + suffix)
        for suffix in STEAM_AUTH_COOKIE_DOMAIN_SUFFIXES
    )


def _has_steam_login_cookie(cookies):
    for cookie in cookies or []:
        if not isinstance(cookie, dict):
            continue
        if cookie.get("name") in STEAM_LOGIN_COOKIE_NAMES:
            return True
    return False


def filter_market_cookies(cookies):
    filtered = []
    for cookie in cookies or []:
        if not isinstance(cookie, dict):
            continue

        domain = cookie.get("domain") or ""
        if not domain or not _domain_applies_to_market(domain):
            continue

        filtered.append(
            {
                "name": cookie.get("name"),
                "value": cookie.get("value"),
                "domain": domain,
                "path": cookie.get("path") or "/",
                "secure": bool(cookie.get("secure")),
                "expires": cookie.get("expires"),
            }
        )
    return filtered


def _domain_applies_to_market(domain):
    normalized = domain.lstrip(".").lower()
    for host in MARKET_COOKIE_HOSTS:
        if host == normalized or normalized == "naeu.playblackdesert.com":
            return True
    return False


def _has_market_session_cookie(cookies):
    return any(cookie.get("name") in MARKET_SESSION_COOKIE_NAMES for cookie in cookies or [])


def _market_session_cookie_values(cookies):
    values = set()
    for cookie in cookies or []:
        if cookie.get("name") in MARKET_SESSION_COOKIE_NAMES:
            value = cookie.get("value")
            if value:
                values.add(value)
    return values


def _has_fresh_market_session_cookie(cookies, baseline_session_values):
    for cookie in cookies or []:
        if cookie.get("name") in MARKET_SESSION_COOKIE_NAMES:
            value = cookie.get("value")
            if value and value not in baseline_session_values:
                return True
    return False


def _market_cookie_capture_ready(cookies, baseline_session_values, callback_seen, market_active, auth_flow_seen):
    if not _has_market_session_cookie(cookies):
        return False

    # Fast path: an auth flow happened and a *new* marketplace session cookie was issued. This
    # fires the moment login completes (the cookie is set on the OAuth callback) instead of
    # waiting for the heavy market page to load, and it can never trip on a stale pre-login
    # cookie because the value must differ from the pre-login baseline.
    if (auth_flow_seen or callback_seen) and _has_fresh_market_session_cookie(cookies, baseline_session_values):
        return True

    # Saved browser session was still valid: the market loaded with no auth detour, so the
    # existing session cookie is the live one. An expired profile would have redirected to the
    # login page first (auth_flow_seen), so this never captures a stale session.
    if market_active and not auth_flow_seen:
        return True

    return False
