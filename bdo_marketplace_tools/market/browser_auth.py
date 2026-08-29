import asyncio
import inspect
from pathlib import Path
from urllib.parse import urlparse

from bdo_marketplace_tools.market import browser_cookies as _browser_cookies
from bdo_marketplace_tools.market import browser_dialogs as _browser_dialogs
from bdo_marketplace_tools.market import browser_notice as _browser_notice
from bdo_marketplace_tools.market.api_handler import TRADE_URL
from bdo_marketplace_tools.storage.paths import STEAM_MARKET_PROFILE_PATH


AUTH_START_URL = f"{TRADE_URL}/"
BDO_SITE_BOOTSTRAP_URL = "https://www.naeu.playblackdesert.com/en-US"
STEAM_PROFILE_PREP_START_URL = BDO_SITE_BOOTSTRAP_URL
STEAM_PROFILE_PREP_LOGIN_URL = "https://store.steampowered.com/login"
STEAM_STORE_URL = "https://store.steampowered.com/"
STEAM_COMMUNITY_URL = "https://steamcommunity.com/"
OTP_ROUTE_MARKERS = (
    "/en-us/Member/Login/CheckOtp",
    "/en-us/Member/Login/LoginOtpAuth",
    "/en-us/Member/Signin/Otp",
    "/en-us/Member/SignIn/OTPAuthenticate",
)
OAUTH_CALLBACK_PATH = "/Pearlabyss/Oauth2CallBack"
DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS = 900
DEFAULT_STEAM_PROFILE_SETUP_TIMEOUT_SECONDS = 900
STEAM_BROWSER_CHANNEL = "chrome"
COOKIEBOT_REQUIRED_CONSENT_SELECTORS = (
    "#CybotCookiebotDialogBodyButtonDecline",
    "button:has-text('Only Accept Required')",
    "button:has-text('Accept Necessary')",
    "button:has-text('Accept Required')",
)
COOKIEBOT_DIALOG_SELECTOR = "#CybotCookiebotDialog"
COOKIE_CONSENT_SAVED = "saved"
COOKIE_CONSENT_MANUAL = "manual"
COOKIE_CONSENT_NOT_FOUND = "not_found"
COOKIE_CONSENT_SKIPPED = "skipped"
PA_STEAM_LOGIN_SELECTORS = (
    "#btnSteam",
    "button[data-type='steam']",
    "button:has-text('Log in with Steam')",
)
PA_EMAIL_SELECTORS = (
    "#_email",
    "input[name='_email']",
    "input[inputmode='email']",
)
PA_PASSWORD_SELECTORS = (
    "#_password",
    "input[name='_password']",
    "input[type='password']",
)
PA_LOGIN_BUTTON_SELECTORS = (
    "#btnLogin",
    "button.js-btnLastLoginCheck",
    "button[data-type='original']",
    "button:has-text('Log in')",
)
STEAM_CONFIRM_LOGIN_SELECTORS = (
    "#imageLogin",
    "input[type='submit'][value='Sign In']",
    "input.btn_green_white_innerfade",
)
STEAM_AUTO_LOGIN_CLICK_TIMEOUT_MS = 1000
STEAM_AUTO_LOGIN_MISSING_NOTICE_SECONDS = 5
PA_AUTO_LOGIN_FILL_TIMEOUT_MS = 1000
PA_AUTO_LOGIN_CLICK_TIMEOUT_MS = 1000
PA_AUTO_LOGIN_MISSING_NOTICE_SECONDS = 5
PA_AUTO_LOGIN_RETRY_GRACE_SECONDS = 2.0
PA_AUTO_LOGIN_MAX_TECHNICAL_RETRIES = 2
PA_LOGIN_FORM_CHECK_TIMEOUT_MS = 150
PA_CONSENT_BUTTON_READY_TIMEOUT_MS = 250
BROWSER_AUTH_POLL_SECONDS = 0.25
BROWSER_PAGE_CLOSE_TIMEOUT_SECONDS = 0.5
BROWSER_CONTEXT_CLOSE_TIMEOUT_SECONDS = 2
STEAM_PROFILE_SETUP_POLL_SECONDS = 0.5
STEAM_AUTO_LOGIN_DISABLED = "disabled"
STEAM_AUTO_LOGIN_CLICKED = "clicked"
STEAM_AUTO_LOGIN_WAITING = "waiting"
STEAM_AUTO_LOGIN_MANUAL_NEEDED = "manual_needed"
STEAM_AUTO_LOGIN_SKIPPED = "skipped"
PA_AUTO_LOGIN_DISABLED = "disabled"
PA_AUTO_LOGIN_SUBMITTED = "submitted"
PA_AUTO_LOGIN_WAITING = "waiting"
PA_AUTO_LOGIN_MANUAL_NEEDED = "manual_needed"
PA_AUTO_LOGIN_SKIPPED = "skipped"
STEAM_LOGGED_IN_SELECTORS = (
    "#account_pulldown",
    ".user_avatar",
)
PA_CREDENTIALS_LOGIN_KEY = "pa_credentials_login"
PA_LOGIN_PROCESS_PATH = "/en-us/member/login/loginprocess"

# Keep these aliases in browser_auth so existing imports and test monkeypatch paths stay stable
# while browser cookie/session filtering rules live in browser_cookies.py.
MARKET_COOKIE_URLS = _browser_cookies.MARKET_COOKIE_URLS
MARKET_COOKIE_HOSTS = _browser_cookies.MARKET_COOKIE_HOSTS
STEAM_PROFILE_COOKIE_URLS = _browser_cookies.STEAM_PROFILE_COOKIE_URLS
MARKET_SESSION_COOKIE_NAMES = _browser_cookies.MARKET_SESSION_COOKIE_NAMES
STEAM_LOGIN_COOKIE_NAMES = _browser_cookies.STEAM_LOGIN_COOKIE_NAMES
STEAM_AUTH_COOKIE_DOMAIN_SUFFIXES = _browser_cookies.STEAM_AUTH_COOKIE_DOMAIN_SUFFIXES
_is_steam_auth_cookie = _browser_cookies._is_steam_auth_cookie
_has_steam_login_cookie = _browser_cookies._has_steam_login_cookie
_is_pa_account_cookie = _browser_cookies._is_pa_account_cookie
_is_cookie_consent_cookie = _browser_cookies._is_cookie_consent_cookie
pa_login_session_cookie_targets = _browser_cookies.pa_login_session_cookie_targets
filter_market_cookies = _browser_cookies.filter_market_cookies
_domain_applies_to_market = _browser_cookies._domain_applies_to_market
_has_market_session_cookie = _browser_cookies._has_market_session_cookie
_market_session_cookie_values = _browser_cookies._market_session_cookie_values
_has_fresh_market_session_cookie = _browser_cookies._has_fresh_market_session_cookie
_market_cookie_capture_ready = _browser_cookies._market_cookie_capture_ready

# Keep these aliases in browser_auth so existing imports and test monkeypatch paths stay stable
# while the browser dialog listener/classification implementation lives in browser_dialogs.py.
AUTH_DIALOG_VERIFICATION_REQUIRED = _browser_dialogs.AUTH_DIALOG_VERIFICATION_REQUIRED
AUTH_DIALOG_INVALID_CREDENTIALS = _browser_dialogs.AUTH_DIALOG_INVALID_CREDENTIALS
AUTH_DIALOG_MANUAL_ATTENTION = _browser_dialogs.AUTH_DIALOG_MANUAL_ATTENTION
AUTH_DIALOG_VERIFICATION_MARKERS = _browser_dialogs.AUTH_DIALOG_VERIFICATION_MARKERS
AUTH_DIALOG_INVALID_CREDENTIAL_MARKERS = _browser_dialogs.AUTH_DIALOG_INVALID_CREDENTIAL_MARKERS
_maybe_await = _browser_dialogs._maybe_await
_new_auth_dialog_state = _browser_dialogs._new_auth_dialog_state
_reset_auth_dialog_state = _browser_dialogs._reset_auth_dialog_state
_prune_auth_dialog_pages = _browser_dialogs._prune_auth_dialog_pages
_drain_auth_dialog_tasks = _browser_dialogs._drain_auth_dialog_tasks
_sanitize_dialog_message = _browser_dialogs._sanitize_dialog_message
_classify_auth_dialog_message = _browser_dialogs._classify_auth_dialog_message
_auth_dialog_status_message = _browser_dialogs._auth_dialog_status_message
_record_auth_dialog = _browser_dialogs._record_auth_dialog
_accept_or_dismiss_dialog = _browser_dialogs._accept_or_dismiss_dialog
_handle_auth_dialog = _browser_dialogs._handle_auth_dialog
_install_auth_dialog_page_handler = _browser_dialogs._install_auth_dialog_page_handler
_install_auth_dialog_handlers = _browser_dialogs._install_auth_dialog_handlers
_maybe_emit_auth_dialog_manual_attention = _browser_dialogs._maybe_emit_auth_dialog_manual_attention

# Keep these aliases in browser_auth so existing imports and test monkeypatch paths stay stable
# while the cosmetic browser-overlay implementation lives in browser_notice.py.
SETUP_NOTICE_SCRIPT = _browser_notice.SETUP_NOTICE_SCRIPT
SETUP_NOTICE_CAPTCHA_MESSAGE = _browser_notice.SETUP_NOTICE_CAPTCHA_MESSAGE
SETUP_NOTICE_MANUAL_LOGIN_MESSAGE = _browser_notice.SETUP_NOTICE_MANUAL_LOGIN_MESSAGE
SETUP_NOTICE_INVALID_CREDENTIALS_MESSAGE = _browser_notice.SETUP_NOTICE_INVALID_CREDENTIALS_MESSAGE
SETUP_NOTICE_STEAM_LOGIN_MESSAGE = _browser_notice.SETUP_NOTICE_STEAM_LOGIN_MESSAGE
SETUP_NOTICE_WARN_SCRIPT = _browser_notice.SETUP_NOTICE_WARN_SCRIPT
SETUP_NOTICE_CREDENTIALS_SCRIPT = _browser_notice.SETUP_NOTICE_CREDENTIALS_SCRIPT
STEAM_REMEMBER_ME_GUIDE_SCRIPT = _browser_notice.STEAM_REMEMBER_ME_GUIDE_SCRIPT
_inject_setup_notice = _browser_notice._inject_setup_notice
_set_setup_notice_warning = _browser_notice._set_setup_notice_warning
_set_setup_notice_credentials_rejected = _browser_notice._set_setup_notice_credentials_rejected
_show_steam_remember_me_guide = _browser_notice._show_steam_remember_me_guide


def _auth_dialog_notice_message(category):
    if category == AUTH_DIALOG_VERIFICATION_REQUIRED:
        return SETUP_NOTICE_CAPTCHA_MESSAGE
    if category == AUTH_DIALOG_INVALID_CREDENTIALS:
        return SETUP_NOTICE_INVALID_CREDENTIALS_MESSAGE
    return SETUP_NOTICE_MANUAL_LOGIN_MESSAGE


class BrowserAuthError(RuntimeError):
    pass


class BrowserAuthUnavailable(BrowserAuthError):
    pass


def _import_async_playwright(unavailable_message):
    try:
        from patchright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional runtime
        raise BrowserAuthUnavailable(unavailable_message) from exc
    return async_playwright


async def acquire_market_cookies(
    status_callback=None,
    *,
    timeout_seconds=DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS,
    profile_path=STEAM_MARKET_PROFILE_PATH,
    start_url=AUTH_START_URL,
    bootstrap_url=None,
    auto_steam_login=False,
    auto_pa_login=False,
    pa_email=None,
    pa_password=None,
    handle_pa_cookie_consent=False,
    pa_cookie_consent_callback=None,
    clear_cookies_before_auth=False,
    clear_market_session_before_auth=False,
    account_label="Steam Account",
    announce_opening=True,
):
    async_playwright = _import_async_playwright(
        "Patchright is not installed. Install requirements, then run `patchright install chromium`."
    )

    profile_path = Path(profile_path)
    profile_path.mkdir(parents=True, exist_ok=True)
    # Callers that already announced the refresh (the PA flow) pass announce_opening=False
    # so one refresh doesn't produce two near-identical "opening browser" lines.
    if announce_opening:
        opening_message = f"Opening {account_label} browser in Chrome. Complete login manually."
        if auto_steam_login:
            opening_message = f"Opening {account_label} browser in Chrome for automatic re-authentication."
        elif auto_pa_login:
            opening_message = f"Opening {account_label} browser in Chrome. Saved credentials will be submitted."
        await _emit_status(status_callback, opening_message, "info")

    context = None
    try:
        async with async_playwright() as playwright:
            context = await _launch_persistent_chrome_context(playwright, profile_path)
            try:
                return await _acquire_market_cookies_in_context(
                    context,
                    status_callback,
                    page=None,
                    timeout_seconds=timeout_seconds,
                    start_url=start_url,
                    bootstrap_url=bootstrap_url,
                    auto_steam_login=auto_steam_login,
                    auto_pa_login=auto_pa_login,
                    pa_email=pa_email,
                    pa_password=pa_password,
                    handle_pa_cookie_consent=handle_pa_cookie_consent,
                    pa_cookie_consent_callback=pa_cookie_consent_callback,
                    clear_cookies_before_auth=clear_cookies_before_auth,
                    clear_market_session_before_auth=clear_market_session_before_auth,
                    account_label=account_label,
                )
            finally:
                await _close_browser_context(context, status_callback)
                context = None
    except BrowserAuthError:
        raise
    except Exception as exc:
        raise BrowserAuthError(_browser_launch_error_message(exc)) from exc
    finally:
        if context is not None:
            await _close_browser_context(context, status_callback)


async def _acquire_market_cookies_in_context(
    context,
    status_callback=None,
    *,
    page=None,
    timeout_seconds=DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS,
    start_url=AUTH_START_URL,
    bootstrap_url=None,
    auto_steam_login=False,
    auto_pa_login=False,
    pa_email=None,
    pa_password=None,
    handle_pa_cookie_consent=False,
    pa_cookie_consent_callback=None,
    clear_cookies_before_auth=False,
    clear_market_session_before_auth=False,
    account_label="Steam Account",
    auth_dialog_state=None,
    inject_setup_notice=True,
):
    """Run one marketplace authentication cycle in a caller-owned browser context.

    The disposable public wrapper and the optional persistent PA worker share this function so
    authentication behavior cannot drift. Context creation, parking, and shutdown remain the
    caller's responsibility.
    """
    if clear_cookies_before_auth:
        # Account-identity changes mark the PA profile unprepared. Clear its
        # cookies in the same context that will authenticate so there is no
        # second Chrome launch and no stale baseline session to accept.
        await context.clear_cookies()
    elif clear_market_session_before_auth:
        # Test/recovery callers can invalidate only the marketplace web session while retaining
        # the PA or Steam account login and consent state. The next market navigation must then
        # traverse the session-issuance redirect instead of accepting the warm market cookie.
        for cookie_name in sorted(MARKET_SESSION_COOKIE_NAMES):
            await context.clear_cookies(name=cookie_name)
        await _emit_status(
            status_callback,
            "Browser marketplace session cleared; account-login cookies were preserved for re-authentication.",
            "info",
        )

    page = page if page is not None and not _page_is_closed(page) else None
    if page is None:
        pages = [candidate for candidate in getattr(context, "pages", []) or [] if not _page_is_closed(candidate)]
        page = pages[0] if pages else await context.new_page()

    if auth_dialog_state is None:
        auth_dialog_state = _new_auth_dialog_state()
    else:
        _reset_auth_dialog_state(auth_dialog_state)
    _install_auth_dialog_handlers(context, auth_dialog_state)

    try:
        # add_init_script persists for the lifetime of a context. Disposable callers install it
        # for their one cycle; a retained worker installs it once when the context is created.
        if inject_setup_notice:
            await _inject_setup_notice(context)

        if bootstrap_url:
            await _bootstrap_browser_profile(page, status_callback, bootstrap_url, account_label)
        try:
            await page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            await _emit_status(status_callback, f"Waiting for {account_label} login in the browser.", "info")

        return await _wait_for_market_cookies(
            context,
            status_callback,
            timeout_seconds,
            auto_steam_login=auto_steam_login,
            auto_pa_login=auto_pa_login,
            pa_email=pa_email,
            pa_password=pa_password,
            handle_pa_cookie_consent=handle_pa_cookie_consent,
            pa_cookie_consent_callback=pa_cookie_consent_callback,
            account_label=account_label,
            auth_dialog_state=auth_dialog_state,
        )
    finally:
        await _drain_auth_dialog_tasks(auth_dialog_state)


async def prepare_steam_browser_profile(
    status_callback=None,
    *,
    profile_path=STEAM_MARKET_PROFILE_PATH,
    start_url=STEAM_PROFILE_PREP_START_URL,
    steam_login_url=STEAM_PROFILE_PREP_LOGIN_URL,
    timeout_seconds=DEFAULT_STEAM_PROFILE_SETUP_TIMEOUT_SECONDS,
):
    async_playwright = _import_async_playwright(
        "Patchright is not installed. Install requirements before opening the Steam setup browser."
    )

    profile_path = Path(profile_path)
    profile_path.mkdir(parents=True, exist_ok=True)
    await _emit_status(
        status_callback,
        "Starting initial Steam browser setup in Chrome.",
        "info",
    )

    context = None
    try:
        async with async_playwright() as playwright:
            context = await _launch_persistent_chrome_context(playwright, profile_path)
            page = context.pages[0] if context.pages else await context.new_page()
            # "Setup in progress" notice while the BDO site + cookie consent load; it flips to the
            # Steam login reminder (see _wait_for_steam_profile_login) once the Steam login page is up.
            await _inject_setup_notice(context)
            try:
                await page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
                consent_result = await _accept_required_cookie_consent_if_available(page, status_callback)
            except Exception:
                await _emit_status(status_callback, "Steam setup browser is open. Continue setup manually.", "warning")
                consent_result = COOKIE_CONSENT_MANUAL

            if consent_result == COOKIE_CONSENT_MANUAL:
                await _wait_for_cookie_consent_manual_completion(page, context)

            await _emit_status(
                status_callback,
                "Complete the Steam login in the browser; the window closes by itself once login is detected.",
                "info",
            )
            try:
                await page.goto(steam_login_url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                await _emit_status(status_callback, "Waiting for Steam login in the browser.", "info")
            await _wait_for_steam_profile_login(context, status_callback, timeout_seconds)
    except BrowserAuthError:
        raise
    except Exception as exc:
        raise BrowserAuthError(_browser_launch_error_message(exc)) from exc
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass


async def clear_steam_browser_profile_cookies(
    *,
    profile_path=STEAM_MARKET_PROFILE_PATH,
):
    async_playwright = _import_async_playwright(
        "Patchright is not installed. Install requirements before clearing browser cookies."
    )

    profile_path = Path(profile_path)
    profile_path.mkdir(parents=True, exist_ok=True)

    context = None
    try:
        async with async_playwright() as playwright:
            context = await _launch_persistent_chrome_context(playwright, profile_path)
            if not context.pages:
                await context.new_page()
            cookies = await context.cookies()
            await context.clear_cookies()
            return len(cookies)
    except BrowserAuthError:
        raise
    except Exception as exc:
        raise BrowserAuthError(_browser_launch_error_message(exc)) from exc
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass


async def clear_market_cookies_keep_steam_login(
    *,
    profile_path=STEAM_MARKET_PROFILE_PATH,
):
    """Clear every cookie in the profile except the Steam web session cookies.

    Test tooling: leaves the user logged into Steam while wiping the marketplace session, the
    Pearl Abyss session, and the Cookiebot consent cookie, so the next re-auth runs the full
    cookie-box + Steam-button flow without a Steam re-login. Returns the number of cookies cleared.
    """
    async_playwright = _import_async_playwright(
        "Patchright is not installed. Install requirements before clearing browser cookies."
    )

    profile_path = Path(profile_path)
    profile_path.mkdir(parents=True, exist_ok=True)

    context = None
    try:
        async with async_playwright() as playwright:
            context = await _launch_persistent_chrome_context(playwright, profile_path)
            if not context.pages:
                await context.new_page()
            all_cookies = await context.cookies()
            kept = [cookie for cookie in all_cookies if _is_steam_auth_cookie(cookie)]
            await context.clear_cookies()
            if kept:
                await context.add_cookies(kept)
            return len(all_cookies) - len(kept)
    except BrowserAuthError:
        raise
    except Exception as exc:
        raise BrowserAuthError(_browser_launch_error_message(exc)) from exc
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass


async def _clear_pa_login_session_cookies_in_context(context):
    """Clear PA account-login and marketplace-session cookies in an owned context.

    Cookie-consent records and unrelated profile state are deliberately retained. Each cookie is
    removed by its exact identity so a shared parent domain does not require wiping and restoring
    consent or other unrelated cookies. Returns only a count; cookie values never leave the browser
    helper.
    """
    all_cookies = await context.cookies()
    targets = pa_login_session_cookie_targets(all_cookies)
    cleared_identities = set()
    for cookie in targets:
        name = cookie.get("name")
        domain = cookie.get("domain")
        path = cookie.get("path") or "/"
        if not name or not domain:
            continue
        identity = (name, domain, path)
        if identity in cleared_identities:
            continue
        await context.clear_cookies(name=name, domain=domain, path=path)
        cleared_identities.add(identity)
    return len(cleared_identities)


async def _wait_for_market_cookies(
    context,
    status_callback,
    timeout_seconds,
    *,
    auto_steam_login=False,
    auto_pa_login=False,
    pa_email=None,
    pa_password=None,
    handle_pa_cookie_consent=False,
    pa_cookie_consent_callback=None,
    account_label="Steam Account",
    auth_dialog_state=None,
):
    deadline = asyncio.get_running_loop().time() + float(timeout_seconds)
    callback_seen = False
    auth_flow_seen = False
    emitted_states = set()
    auto_login_state = _new_steam_auto_login_state()
    pa_auto_login_state = _new_pa_auto_login_state()
    auth_dialog_state = auth_dialog_state or _new_auth_dialog_state()
    _install_auth_dialog_handlers(context, auth_dialog_state)
    pa_cookie_consent_state = _new_pa_cookie_consent_state()
    pa_cookie_consent_completed = not handle_pa_cookie_consent
    pa_credentials_auto_stopped = False

    # Snapshot any marketplace session cookie value already in the persistent profile so a
    # fresh login is recognized by a *changed* TradeAuth_Session value, not its mere presence.
    # This is what lets capture close as soon as login completes without waiting for the market
    # page, while never closing on a stale pre-login cookie left over in the profile.
    baseline_session_values = _market_session_cookie_values(
        filter_market_cookies(await context.cookies(list(MARKET_COOKIE_URLS)))
    )

    # The marketplace session cookie the app needs (TradeAuth_Session) is set on the
    # /Pearlabyss/Oauth2CallBack 302. That redirect never becomes page.url, so URL polling can't
    # observe it, and the poll loop's own context.cookies() read blocks for several seconds while
    # the market page navigates after login. So we read cookies the instant the callback *response*
    # arrives and race that capture against the poll loop (below), closing at login success instead
    # of after the market page loads. (__RequestVerificationToken lands later, at the market page,
    # but the app's authenticated calls do not require it.)
    captured_at_callback = []
    callback_done = asyncio.Event()
    callback_read_tasks = set()
    async def _read_market_cookies_after_callback():
        try:
            cookies = filter_market_cookies(await context.cookies(list(MARKET_COOKIE_URLS)))
            if _has_fresh_market_session_cookie(cookies, baseline_session_values):
                captured_at_callback.append(cookies)
                callback_done.set()
        except Exception:
            pass

    def _on_request(request):
        if _is_pa_login_process_request(request):
            _record_pa_login_process_submit(pa_auto_login_state)

    def _on_response(response):
        _state, is_callback = _classify_url(getattr(response, "url", ""))
        if is_callback:
            task = asyncio.ensure_future(_read_market_cookies_after_callback())
            callback_read_tasks.add(task)
            task.add_done_callback(callback_read_tasks.discard)

    context_on = getattr(context, "on", None)
    attached_listeners = []
    if callable(context_on):
        for event_name, listener in (("request", _on_request), ("response", _on_response)):
            try:
                context_on(event_name, listener)
            except Exception:
                continue
            attached_listeners.append((event_name, listener))

    async def _poll_loop():
        nonlocal callback_seen, auth_flow_seen, pa_credentials_auto_stopped
        nonlocal pa_cookie_consent_completed
        # Track whether automatic login gave up and handed off to the user, so the timeout message can
        # say the login was never finished manually instead of giving a generic "no cookies" error.
        manual_login_seen = False
        while asyncio.get_running_loop().time() < deadline:
            now = asyncio.get_running_loop().time()
            pages = [page for page in context.pages if not _page_is_closed(page)]
            if not pages:
                raise BrowserAuthError("Browser closed before a marketplace session could be captured.")

            market_active = False
            for page in pages:
                _install_auth_dialog_page_handler(page, auth_dialog_state)
                state, is_callback = _classify_url(getattr(page, "url", ""))
                if is_callback:
                    callback_seen = True
                if state in {"pa", "steam", "otp"}:
                    auth_flow_seen = True
                if state == "market":
                    market_active = True
                if state and state not in emitted_states:
                    emitted_states.add(state)
                    message, level = _status_for_state(state)
                    await _emit_status(status_callback, message, level)

                if await _maybe_emit_auth_dialog_manual_attention(auth_dialog_state, status_callback):
                    pa_credentials_auto_stopped = True
                    manual_login_seen = True
                    record = auth_dialog_state.get("manual_attention") or {}
                    if record.get("category") == AUTH_DIALOG_INVALID_CREDENTIALS:
                        await _set_setup_notice_credentials_rejected(page)
                    else:
                        await _set_setup_notice_warning(page, _auth_dialog_notice_message(record.get("category")))

                pa_cookie_consent_result = COOKIE_CONSENT_SKIPPED
                if not pa_cookie_consent_completed:
                    pa_cookie_consent_result = await _maybe_prepare_pa_cookie_consent(
                        page,
                        state,
                        tracking=pa_cookie_consent_state,
                        status_callback=status_callback,
                    )
                    if pa_cookie_consent_result == COOKIE_CONSENT_SAVED:
                        pa_cookie_consent_completed = True
                        await _emit_callback(pa_cookie_consent_callback, True)

                auto_login_result = STEAM_AUTO_LOGIN_SKIPPED
                manual_attention_pending = bool(auth_dialog_state.get("manual_attention"))
                if not manual_attention_pending and _should_attempt_steam_auto_login(state, auth_flow_seen, callback_seen):
                    if pa_cookie_consent_result == COOKIE_CONSENT_MANUAL:
                        auto_login_result = STEAM_AUTO_LOGIN_WAITING
                    else:
                        auto_login_result = await _maybe_run_steam_auto_login(
                            page,
                            state,
                            enabled=auto_steam_login,
                            tracking=auto_login_state,
                            status_callback=status_callback,
                            now=now,
                        )
                if auto_login_result in {STEAM_AUTO_LOGIN_CLICKED, STEAM_AUTO_LOGIN_MANUAL_NEEDED}:
                    auth_flow_seen = True
                if auto_login_result == STEAM_AUTO_LOGIN_MANUAL_NEEDED:
                    manual_login_seen = True
                    await _set_setup_notice_warning(page, SETUP_NOTICE_MANUAL_LOGIN_MESSAGE)
                pa_login_result = PA_AUTO_LOGIN_SKIPPED
                if not pa_credentials_auto_stopped:
                    pa_login_result = await _maybe_run_pa_credentials_login(
                        page,
                        state,
                        enabled=auto_pa_login,
                        email=pa_email,
                        password=pa_password,
                        tracking=pa_auto_login_state,
                        dialog_state=auth_dialog_state,
                        status_callback=status_callback,
                        now=now,
                    )
                if pa_login_result in {PA_AUTO_LOGIN_SUBMITTED, PA_AUTO_LOGIN_MANUAL_NEEDED}:
                    auth_flow_seen = True
                if pa_login_result == PA_AUTO_LOGIN_MANUAL_NEEDED:
                    pa_credentials_auto_stopped = True
                    manual_login_seen = True
                    await _set_setup_notice_warning(page, SETUP_NOTICE_MANUAL_LOGIN_MESSAGE)

            cookies = filter_market_cookies(await context.cookies(list(MARKET_COOKIE_URLS)))
            if _market_cookie_capture_ready(cookies, baseline_session_values, callback_seen, market_active, auth_flow_seen):
                return cookies

            await asyncio.sleep(BROWSER_AUTH_POLL_SECONDS)

        if manual_login_seen:
            raise BrowserAuthError(
                f"{account_label} browser session timed out before login was completed. Automatic login "
                "could not finish, and the login was not completed manually in the browser window in time. "
                "Refresh the session and finish the login when the window opens."
            )
        raise BrowserAuthError(
            f"{account_label} browser session timed out before the marketplace session was captured. "
            "The login did not complete in time — refresh the session to try again."
        )

    # Run the polling loop and the OAuth-callback capture concurrently and finish on whichever
    # lands first. The callback capture can complete while the poll loop is parked in a slow,
    # navigation-blocked context.cookies() read, so it is watched independently here rather than
    # from inside the loop.
    poll_task = None
    callback_task = None
    try:
        poll_task = asyncio.ensure_future(_poll_loop())
        callback_task = asyncio.ensure_future(callback_done.wait())
        await asyncio.wait({poll_task, callback_task}, return_when=asyncio.FIRST_COMPLETED)

        if captured_at_callback:
            await _emit_status(
                status_callback,
                "Session cookies captured; validating.",
                "info",
            )
            return captured_at_callback[0]

        return poll_task.result()
    finally:
        tasks = [
            task
            for task in (
                poll_task,
                callback_task,
                *tuple(callback_read_tasks),
            )
            if task is not None
        ]
        # Detach first so no new callback-cookie task can be scheduled while the current task
        # snapshot is being canceled and awaited.
        for event_name, listener in attached_listeners:
            _remove_event_listener(context, event_name, listener)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def _remove_event_listener(emitter, event_name, listener):
    remove_listener = getattr(emitter, "remove_listener", None)
    if not callable(remove_listener):
        return
    try:
        remove_listener(event_name, listener)
    except Exception:
        pass


async def _close_browser_context(context, status_callback=None):
    pages = [page for page in getattr(context, "pages", []) or [] if not _page_is_closed(page)]
    if pages:
        await asyncio.gather(*[_close_page_quickly(page) for page in pages], return_exceptions=True)

    try:
        await asyncio.wait_for(context.close(), timeout=BROWSER_CONTEXT_CLOSE_TIMEOUT_SECONDS)
    except Exception:
        await _emit_status(
            status_callback,
            "Browser cookies were captured, but Chrome did not close cleanly. Close it manually if it remains open.",
            "warning",
        )
        return False
    return True


async def _close_page_quickly(page):
    close_page = getattr(page, "close", None)
    if close_page is None:
        return
    try:
        close_result = close_page(run_before_unload=False)
    except TypeError:
        close_result = close_page()
    except Exception:
        return

    if inspect.isawaitable(close_result):
        try:
            await asyncio.wait_for(close_result, timeout=BROWSER_PAGE_CLOSE_TIMEOUT_SECONDS)
        except Exception:
            pass


async def _bootstrap_browser_profile(page, status_callback, bootstrap_url, account_label):
    try:
        await _emit_status(
            status_callback,
            f"Preparing {account_label} browser profile from the Black Desert site.",
            "info",
        )
        await page.goto(bootstrap_url, wait_until="domcontentloaded", timeout=60000)
        await _accept_required_cookie_consent_if_available(
            page,
            status_callback,
            profile_label=f"{account_label} browser profile",
        )
    except Exception:
        await _emit_status(
            status_callback,
            f"{account_label} browser profile preparation was skipped; continuing to marketplace login.",
            "warning",
        )


async def _launch_persistent_chrome_context(playwright, profile_path):
    return await playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_path),
        channel=STEAM_BROWSER_CHANNEL,
        headless=False,
        viewport={"width": 1280, "height": 900},
    )


async def _wait_for_cookie_consent_manual_completion(page, context):
    while True:
        pages = [open_page for open_page in context.pages if not _page_is_closed(open_page)]
        if not pages:
            raise BrowserAuthError("Initial Steam setup was closed before the browser profile was prepared.")
        if _page_is_closed(page):
            raise BrowserAuthError("Initial Steam setup page was closed before the browser profile was prepared.")
        if await _cookiebot_dialog_hidden(page):
            return
        await asyncio.sleep(STEAM_PROFILE_SETUP_POLL_SECONDS)


async def _wait_for_steam_profile_login(context, status_callback, timeout_seconds):
    deadline = asyncio.get_running_loop().time() + float(timeout_seconds)
    while asyncio.get_running_loop().time() < deadline:
        pages = [page for page in context.pages if not _page_is_closed(page)]
        if not pages:
            raise BrowserAuthError("Steam setup browser closed before Steam login was detected.")

        if _has_steam_login_cookie(await context.cookies(list(STEAM_PROFILE_COOKIE_URLS))):
            await _emit_status(status_callback, "Steam login detected in the browser profile.", "info")
            return

        for page in pages:
            if await _steam_store_logged_in_dom_ready(page):
                await _emit_status(status_callback, "Steam login detected in the browser profile.", "info")
                return

        # Not logged in yet: flip the in-page notice to remind the user to log in and tick
        # "Remember me" so the Steam session persists and re-auth can stay automatic.
        for page in pages:
            await _set_setup_notice_warning(page, SETUP_NOTICE_STEAM_LOGIN_MESSAGE)
            await _show_steam_remember_me_guide(page)

        await asyncio.sleep(STEAM_PROFILE_SETUP_POLL_SECONDS)

    raise BrowserAuthError("Steam setup timed out before Steam login was detected.")


async def _steam_store_logged_in_dom_ready(page):
    if urlparse(getattr(page, "url", "") or "").hostname != "store.steampowered.com":
        return False
    for selector in STEAM_LOGGED_IN_SELECTORS:
        try:
            if await page.locator(selector).first.is_visible(timeout=500):
                return True
        except Exception:
            continue
    return False


async def _accept_required_cookie_consent_if_available(page, status_callback=None, *, profile_label="Steam browser profile"):
    for selector in COOKIEBOT_REQUIRED_CONSENT_SELECTORS:
        try:
            await page.locator(selector).first.click(timeout=PA_CONSENT_BUTTON_READY_TIMEOUT_MS)
        except Exception:
            continue

        await _emit_status(status_callback, f"Required cookie consent saved in the {profile_label}.", "info")
        return COOKIE_CONSENT_SAVED
    return COOKIE_CONSENT_NOT_FOUND


def _new_steam_auto_login_state():
    return {
        "clicked": set(),
        "missing_started_at": {},
        "missing_reported": set(),
    }


def _new_pa_auto_login_state():
    return {
        "pending": {},
        "attempts": {},
        "network_submit_count": {},
        "network_count_at_click": {},
        "technical_retries": {},
        "missing_started_at": {},
        "missing_reported": set(),
        "manual_reported": set(),
    }


def _new_pa_cookie_consent_state():
    return {
        "checked": set(),
    }


def _is_pa_login_process_url(url):
    parsed = urlparse(str(url or ""))
    return parsed.path.lower() == PA_LOGIN_PROCESS_PATH


def _is_pa_login_process_request(request):
    method = getattr(request, "method", "")
    if callable(method):
        try:
            method = method()
        except Exception:
            method = ""
    if str(method or "").upper() != "POST":
        return False
    return _is_pa_login_process_url(getattr(request, "url", ""))


def _record_pa_login_process_submit(tracking):
    key = PA_CREDENTIALS_LOGIN_KEY
    tracking["network_submit_count"][key] = tracking["network_submit_count"].get(key, 0) + 1


def _should_attempt_steam_auto_login(state, auth_flow_seen, callback_seen):
    if state == "market" and (auth_flow_seen or callback_seen):
        return False
    return True


async def _maybe_prepare_pa_cookie_consent(
    page,
    state,
    *,
    tracking,
    status_callback=None,
):
    targets = _pa_cookie_consent_targets(page, state)
    if not targets:
        return COOKIE_CONSENT_SKIPPED

    result = COOKIE_CONSENT_SKIPPED
    for scope in targets:
        scope_result = await _maybe_prepare_pa_cookie_consent_target(
            scope,
            tracking=tracking,
            status_callback=status_callback,
        )
        if scope_result in {COOKIE_CONSENT_SAVED, COOKIE_CONSENT_MANUAL}:
            return scope_result
        if scope_result == COOKIE_CONSENT_NOT_FOUND:
            result = COOKIE_CONSENT_NOT_FOUND

    return result


async def _maybe_prepare_pa_cookie_consent_target(scope, *, tracking, status_callback=None):
    key = (id(scope), "pa_cookie_consent", getattr(scope, "url", ""))
    if key in tracking["checked"]:
        return COOKIE_CONSENT_SKIPPED

    consent_result = await _accept_required_cookie_consent_if_available(
        scope,
        status_callback,
        profile_label="Pearl Abyss login page",
    )
    if consent_result == COOKIE_CONSENT_SAVED:
        tracking["checked"].add(key)
        return COOKIE_CONSENT_SAVED
    if consent_result == COOKIE_CONSENT_MANUAL:
        return COOKIE_CONSENT_MANUAL

    return COOKIE_CONSENT_NOT_FOUND


async def _maybe_run_steam_auto_login(
    page,
    state,
    *,
    enabled,
    tracking,
    status_callback=None,
    now=None,
    missing_notice_seconds=STEAM_AUTO_LOGIN_MISSING_NOTICE_SECONDS,
):
    if not enabled:
        return STEAM_AUTO_LOGIN_DISABLED

    targets = _steam_auto_login_targets(page, state)
    if not targets:
        return STEAM_AUTO_LOGIN_SKIPPED

    now = asyncio.get_running_loop().time() if now is None else now
    result = STEAM_AUTO_LOGIN_SKIPPED
    for scope, config_state in targets:
        scope_result = await _maybe_run_steam_auto_login_target(
            scope,
            config_state,
            tracking=tracking,
            status_callback=status_callback,
            now=now,
            missing_notice_seconds=missing_notice_seconds,
        )
        if scope_result in {STEAM_AUTO_LOGIN_CLICKED, STEAM_AUTO_LOGIN_MANUAL_NEEDED}:
            return scope_result
        if scope_result == STEAM_AUTO_LOGIN_WAITING:
            result = STEAM_AUTO_LOGIN_WAITING

    return result


async def _maybe_run_steam_auto_login_target(
    scope,
    state,
    *,
    tracking,
    status_callback,
    now,
    missing_notice_seconds,
):
    config = _steam_auto_login_config(state)
    if config is None:
        return STEAM_AUTO_LOGIN_SKIPPED

    selector_key, selectors, clicked_message, missing_message = config
    key = (id(scope), selector_key, getattr(scope, "url", ""))

    if await _click_first_available_selector(scope, selectors, timeout=STEAM_AUTO_LOGIN_CLICK_TIMEOUT_MS):
        first_click = selector_key not in tracking["clicked"]
        tracking["clicked"].add(selector_key)
        tracking["missing_started_at"].pop(key, None)
        if first_click:
            await _emit_status(status_callback, clicked_message, "info")
        return STEAM_AUTO_LOGIN_CLICKED

    started_at = tracking["missing_started_at"].setdefault(key, now)
    if now - started_at >= missing_notice_seconds and key not in tracking["missing_reported"]:
        tracking["missing_reported"].add(key)
        await _emit_status(status_callback, missing_message, "warning")
        return STEAM_AUTO_LOGIN_MANUAL_NEEDED

    return STEAM_AUTO_LOGIN_WAITING


async def _maybe_run_pa_credentials_login(
    page,
    state,
    *,
    enabled,
    email,
    password,
    tracking,
    dialog_state=None,
    status_callback=None,
    now=None,
    missing_notice_seconds=PA_AUTO_LOGIN_MISSING_NOTICE_SECONDS,
):
    if not enabled or not email or not password:
        return PA_AUTO_LOGIN_DISABLED

    if dialog_state is not None and await _maybe_emit_auth_dialog_manual_attention(dialog_state, status_callback):
        return PA_AUTO_LOGIN_MANUAL_NEEDED

    targets = _pa_credentials_login_targets(page, state)
    if not targets:
        return PA_AUTO_LOGIN_SKIPPED

    now = asyncio.get_running_loop().time() if now is None else now
    result = PA_AUTO_LOGIN_SKIPPED
    for scope in targets:
        scope_result = await _maybe_run_pa_credentials_login_target(
            scope,
            email,
            password,
            tracking=tracking,
            dialog_state=dialog_state,
            status_callback=status_callback,
            now=now,
            missing_notice_seconds=missing_notice_seconds,
        )
        if scope_result in {PA_AUTO_LOGIN_SUBMITTED, PA_AUTO_LOGIN_MANUAL_NEEDED}:
            return scope_result
        if scope_result == PA_AUTO_LOGIN_WAITING:
            result = PA_AUTO_LOGIN_WAITING

    return result


async def _maybe_run_pa_credentials_login_target(
    scope,
    email,
    password,
    *,
    tracking,
    dialog_state,
    status_callback,
    now,
    missing_notice_seconds,
):
    if not _scope_is_pa_credentials_login_target(scope):
        return PA_AUTO_LOGIN_SKIPPED

    key = PA_CREDENTIALS_LOGIN_KEY
    if dialog_state is not None and await _maybe_emit_auth_dialog_manual_attention(dialog_state, status_callback):
        return PA_AUTO_LOGIN_MANUAL_NEEDED

    pending_since = tracking["pending"].get(key)
    is_retry = pending_since is not None
    if is_retry:
        if now - pending_since < PA_AUTO_LOGIN_RETRY_GRACE_SECONDS:
            return PA_AUTO_LOGIN_WAITING
        if not await _pa_login_form_visible(scope):
            return PA_AUTO_LOGIN_WAITING
        if not await _pa_password_field_empty(scope):
            return PA_AUTO_LOGIN_WAITING

        retries_used = tracking["technical_retries"].get(key, 0)
        if retries_used >= PA_AUTO_LOGIN_MAX_TECHNICAL_RETRIES:
            if key not in tracking["manual_reported"]:
                tracking["manual_reported"].add(key)
                await _emit_status(
                    status_callback,
                    "Pearl Abyss login returned to the login page after saved credentials were submitted. Auto-login paused; complete login manually or update saved credentials.",
                    "warning",
                )
            return PA_AUTO_LOGIN_MANUAL_NEEDED

        network_seen = tracking["network_submit_count"].get(key, 0) > tracking["network_count_at_click"].get(key, 0)
        retry_number = retries_used + 1
        retry_message = (
            f"Pearl Abyss login did not submit cleanly; retrying saved credentials "
            f"({retry_number}/{PA_AUTO_LOGIN_MAX_TECHNICAL_RETRIES})."
        )
        if network_seen:
            retry_message = (
                f"Pearl Abyss login returned to the login page; retrying saved credentials "
                f"({retry_number}/{PA_AUTO_LOGIN_MAX_TECHNICAL_RETRIES})."
            )

    credentials_filled = await _fill_pa_credentials(scope, email, password)
    if (
        credentials_filled
        and _scope_is_pa_credentials_login_target(scope)
        and await _click_first_available_pa_selector(
            scope,
            PA_LOGIN_BUTTON_SELECTORS,
            timeout=PA_AUTO_LOGIN_CLICK_TIMEOUT_MS,
        )
    ):
        tracking["attempts"][key] = tracking["attempts"].get(key, 0) + 1
        # Count a technical retry only once a resubmit actually goes out, so a failed retry-fill
        # doesn't burn the retry budget and jump straight to manual hand-off.
        if is_retry:
            tracking["technical_retries"][key] = tracking["technical_retries"].get(key, 0) + 1
            await _emit_status(status_callback, retry_message, "warning")
        tracking["pending"][key] = now
        tracking["network_count_at_click"][key] = tracking["network_submit_count"].get(key, 0)
        tracking["missing_started_at"].pop(key, None)
        if tracking["attempts"][key] == 1:
            await _emit_status(status_callback, "Automatic Pearl Abyss login submitted saved credentials.", "info")
        return PA_AUTO_LOGIN_SUBMITTED

    started_at = tracking["missing_started_at"].setdefault(key, now)
    if now - started_at >= missing_notice_seconds and key not in tracking["missing_reported"]:
        tracking["missing_reported"].add(key)
        await _emit_status(
            status_callback,
            "Automatic Pearl Abyss login is waiting for manual input on the Pearl Abyss page.",
            "warning",
        )
        return PA_AUTO_LOGIN_MANUAL_NEEDED

    return PA_AUTO_LOGIN_WAITING


async def _fill_pa_credentials(scope, email, password):
    if not _scope_is_pa_credentials_login_target(scope):
        return False
    email_filled = await _fill_first_available_pa_selector(
        scope,
        PA_EMAIL_SELECTORS,
        str(email),
        timeout=PA_AUTO_LOGIN_FILL_TIMEOUT_MS,
    )
    if not email_filled or not _scope_is_pa_credentials_login_target(scope):
        return False
    return await _fill_first_available_pa_selector(
        scope,
        PA_PASSWORD_SELECTORS,
        str(password),
        timeout=PA_AUTO_LOGIN_FILL_TIMEOUT_MS,
    )


async def _bound_pa_element(scope, selector, timeout):
    if not _scope_is_pa_credentials_login_target(scope):
        return None
    try:
        element = await scope.locator(selector).first.element_handle(timeout=timeout)
    except Exception:
        return None
    if not _scope_is_pa_credentials_login_target(scope):
        return None
    return element


async def _fill_first_available_pa_selector(scope, selectors, value, timeout):
    for selector in selectors:
        element = await _bound_pa_element(scope, selector, timeout)
        if element is None:
            if not _scope_is_pa_credentials_login_target(scope):
                return False
            continue
        try:
            # ElementHandle stays bound to the document in which it was resolved. If
            # that document navigates after the origin check, this fails as detached
            # instead of re-resolving the secret-bearing action on the new page.
            await element.fill(value, timeout=timeout)
        except Exception:
            if not _scope_is_pa_credentials_login_target(scope):
                return False
            continue
        return _scope_is_pa_credentials_login_target(scope)
    return False


async def _click_first_available_pa_selector(scope, selectors, timeout):
    for selector in selectors:
        element = await _bound_pa_element(scope, selector, timeout)
        if element is None:
            if not _scope_is_pa_credentials_login_target(scope):
                return False
            continue
        try:
            # This polling loop owns navigation detection. Waiting for navigation
            # inside click() can raise after the browser already dispatched the
            # submit, which would lose the pending-attempt bookkeeping and allow
            # an immediate duplicate submit on the returned login page.
            await element.click(timeout=timeout, no_wait_after=True)
        except Exception:
            if not _scope_is_pa_credentials_login_target(scope):
                return False
            continue
        # A successful login click commonly navigates away before click() resolves.
        # The handle was bound on the verified PA document, so that navigation is
        # expected and must not erase the submitted/pending attempt bookkeeping.
        return True
    return False


async def _pa_login_form_visible(scope):
    email_visible = await _selector_visible(
        scope,
        PA_EMAIL_SELECTORS,
        timeout=PA_LOGIN_FORM_CHECK_TIMEOUT_MS,
    )
    if not email_visible:
        return False
    return await _selector_visible(
        scope,
        PA_PASSWORD_SELECTORS,
        timeout=PA_LOGIN_FORM_CHECK_TIMEOUT_MS,
    )


async def _pa_password_field_empty(scope):
    for selector in PA_PASSWORD_SELECTORS:
        try:
            value = await scope.locator(selector).first.input_value(timeout=PA_LOGIN_FORM_CHECK_TIMEOUT_MS)
        except Exception:
            continue
        return value == ""
    return False


def _pa_credentials_login_targets(page, _state):
    targets = []
    seen = set()

    def add_target(scope):
        if not _scope_is_pa_credentials_login_target(scope):
            return
        key = id(scope)
        if key in seen:
            return
        seen.add(key)
        targets.append(scope)

    # Reclassify live URLs here instead of trusting the page state captured near
    # the start of the poll. Consent/browser work can navigate to OTP meanwhile.
    add_target(page)
    for frame in getattr(page, "frames", []) or []:
        add_target(frame)

    return targets


def _pa_credentials_login_target_state(state):
    # Saved credentials must only be submitted to an origin explicitly classified
    # as Pearl Abyss. Unknown/loading pages are revisited on the next poll once
    # their real URL is available.
    if state == "pa":
        return "pa"
    return None


def _scope_is_pa_credentials_login_target(scope):
    try:
        url = getattr(scope, "url", "")
    except Exception:
        return False
    if not _is_canonical_pa_credentials_url(url):
        return False
    state, _is_callback = _classify_url(url)
    return _pa_credentials_login_target_state(state) == "pa"


def _is_canonical_pa_credentials_url(url):
    try:
        parsed = urlparse(str(url or ""))
    except (TypeError, ValueError):
        return False
    authority = parsed.netloc
    return (
        authority.isascii()
        and parsed.scheme.lower() == "https"
        and authority.lower()
        in {
            "account.pearlabyss.com",
            "account.pearlabyss.com:443",
        }
    )


def _steam_auto_login_targets(page, state):
    targets = []
    seen = set()

    def add_target(scope, target_state):
        if target_state not in {"pa", "steam"}:
            return
        key = (id(scope), target_state)
        if key in seen:
            return
        seen.add(key)
        targets.append((scope, target_state))

    add_target(page, _steam_auto_login_target_state(state))
    for frame in getattr(page, "frames", []) or []:
        frame_state, _is_callback = _classify_url(getattr(frame, "url", ""))
        add_target(frame, _steam_auto_login_target_state(frame_state))

    return targets


def _pa_cookie_consent_targets(page, state):
    targets = []
    seen = set()

    def add_target(scope, target_state):
        if target_state != "pa":
            return
        key = id(scope)
        if key in seen:
            return
        seen.add(key)
        targets.append(scope)

    add_target(page, _pa_cookie_consent_target_state(state))
    for frame in getattr(page, "frames", []) or []:
        frame_state, _is_callback = _classify_url(getattr(frame, "url", ""))
        add_target(frame, _pa_cookie_consent_target_state(frame_state))

    return targets


def _pa_cookie_consent_target_state(state):
    if state == "pa":
        return "pa"
    return None


def _steam_auto_login_target_state(state):
    if state in {"pa", "steam"}:
        return state
    if state is None:
        return "pa"
    return None


def _steam_auto_login_config(state):
    if state == "pa":
        return (
            "pa_steam_login",
            PA_STEAM_LOGIN_SELECTORS,
            "Steam re-auth submitted the Pearl Abyss Steam login.",
            "Automatic Steam re-auth is waiting for manual input on the Pearl Abyss page.",
        )
    if state == "steam":
        return (
            "steam_confirm_login",
            STEAM_CONFIRM_LOGIN_SELECTORS,
            "Steam re-auth confirmed the Steam sign-in.",
            "Automatic Steam re-auth is waiting for manual input on the Steam page.",
        )
    return None


async def _click_first_available_selector(page, selectors, timeout):
    for selector in selectors:
        try:
            await page.locator(selector).first.click(timeout=timeout)
            return True
        except Exception:
            continue
    return False


async def _selector_visible(page, selectors, timeout):
    for selector in selectors:
        try:
            if await page.locator(selector).first.is_visible(timeout=timeout):
                return True
        except Exception:
            continue
    return False


async def _fill_first_available_selector(page, selectors, value, timeout):
    for selector in selectors:
        try:
            await page.locator(selector).first.fill(value, timeout=timeout)
            return True
        except Exception:
            continue
    return False


async def _cookiebot_dialog_hidden(page):
    try:
        await page.locator(COOKIEBOT_DIALOG_SELECTOR).wait_for(state="hidden", timeout=3000)
        return True
    except Exception:
        return False


def _classify_url(url):
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    lower_path = path.lower()

    if host == "steamcommunity.com":
        return "steam", False

    if host == "account.pearlabyss.com":
        normalized_path = lower_path.rstrip("/") or "/"
        for marker in OTP_ROUTE_MARKERS:
            if normalized_path == (marker.lower().rstrip("/") or "/"):
                return "otp", False
        return "pa", False

    if host == "na-trade.naeu.playblackdesert.com":
        return "market", lower_path == OAUTH_CALLBACK_PATH.lower()

    return None, False


def _status_for_state(state):
    if state == "steam":
        return "Waiting for Steam confirmation in the browser.", "info"
    if state == "otp":
        return "OTP required. Complete verification in the browser.", "warning"
    if state == "pa":
        return "Waiting for Pearl Abyss authorization in the browser.", "info"
    return "Waiting for Steam/PA login in the browser.", "info"


def _browser_launch_error_message(exc):
    details = _single_line(str(exc))
    lower_details = details.lower()
    missing_browser = (
        "executable doesn't exist" in lower_details
        or "not found" in lower_details
        or "could not find" in lower_details
    )
    if missing_browser and "chrome" in lower_details and "ms-playwright" not in lower_details:
        return "Google Chrome is not available for Patchright. Install Chrome, then refresh the Steam session again."
    if "executable doesn't exist" in lower_details or "playwright was just installed or updated" in lower_details:
        return "Patchright Chromium is not installed. Run `py -m patchright install chromium`, then refresh the Steam session again."
    if not details:
        return "Browser authentication failed before marketplace cookies were captured."
    return f"Browser authentication failed before marketplace cookies were captured: {details}"


def _single_line(message):
    lines = [line.strip() for line in str(message or "").splitlines() if line.strip()]
    return " ".join(lines)


def _page_is_closed(page):
    try:
        return page.is_closed()
    except Exception:
        return True


async def _emit_status(callback, message, level="info"):
    if callback is None:
        return
    result = callback(message, level)
    if inspect.isawaitable(result):
        await result


async def _emit_callback(callback, *args):
    if callback is None:
        return
    result = callback(*args)
    if inspect.isawaitable(result):
        await result
