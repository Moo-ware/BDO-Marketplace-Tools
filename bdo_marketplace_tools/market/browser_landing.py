"""Local landing page for a retained authentication browser worker."""

import asyncio
import inspect
from dataclasses import dataclass, replace
from datetime import datetime
from html import escape


CAPTURE_WAITING = "waiting"
CAPTURE_WORKING = "working"
CAPTURE_CAPTURED = "captured"
CAPTURE_FAILED = "failed"
CAPTURE_CLEARED = "cleared"

VALIDATION_IDLE = "idle"
VALIDATION_WORKING = "working"
VALIDATION_VALID = "valid"
VALIDATION_FAILED = "failed"


@dataclass(frozen=True)
class BrowserLandingState:
    """Non-sensitive status rendered into the worker's local parked page."""

    account_label: str
    capture_state: str = CAPTURE_WAITING
    validation_state: str = VALIDATION_IDLE
    last_refresh: str | None = None


def initial_landing_state(account_label):
    return BrowserLandingState(account_label=str(account_label or "Marketplace Account"))


def landing_auth_started(state):
    return replace(
        state,
        capture_state=CAPTURE_WORKING,
        validation_state=VALIDATION_IDLE,
    )


def landing_cookie_capture_finished(state, *, captured):
    if captured:
        return replace(
            state,
            capture_state=CAPTURE_CAPTURED,
            validation_state=VALIDATION_WORKING,
        )
    return replace(
        state,
        capture_state=CAPTURE_FAILED,
        validation_state=VALIDATION_IDLE,
        last_refresh=_landing_timestamp(),
    )


def landing_validation_finished(state, *, valid, checked_at=None):
    return replace(
        state,
        validation_state=VALIDATION_VALID if valid else VALIDATION_FAILED,
        last_refresh=str(checked_at or _landing_timestamp()),
    )


def landing_cookies_cleared(state):
    return replace(
        state,
        capture_state=CAPTURE_CLEARED,
        validation_state=VALIDATION_IDLE,
        last_refresh=None,
    )


def _landing_timestamp(now=None):
    moment = now or datetime.now().astimezone()
    return moment.strftime("%b %d, %Y at %I:%M:%S %p")


# Each component reduces to a short chip label plus a tone. The longer "what is happening,
# and what to do about it" copy lives once in _headline_for instead of being repeated per
# component, which keeps the chips glanceable.
_CAPTURE_PRESENTATION = {
    CAPTURE_WAITING: ("Waiting", "neutral"),
    CAPTURE_WORKING: ("Signing in", "working"),
    CAPTURE_CAPTURED: ("Captured", "success"),
    CAPTURE_FAILED: ("Not captured", "error"),
    CAPTURE_CLEARED: ("Cleared", "warning"),
}

_VALIDATION_PRESENTATION = {
    VALIDATION_IDLE: ("Not checked", "neutral"),
    VALIDATION_WORKING: ("Checking", "working"),
    VALIDATION_VALID: ("Valid", "success"),
    VALIDATION_FAILED: ("Not valid", "error"),
}


def _headline_for(state):
    """Overall summary line, plus the tone for the roll-up indicator."""
    if state.validation_state == VALIDATION_VALID:
        return (
            "Session ready",
            "The worker is standing by for the next refresh.",
            "success",
        )
    if state.validation_state == VALIDATION_WORKING:
        return (
            "Checking your session",
            "The cookie was captured. The app is confirming it with the marketplace.",
            "working",
        )
    if state.capture_state == CAPTURE_FAILED or state.validation_state == VALIDATION_FAILED:
        return (
            "Session needs attention",
            "Open the app and use Refresh Session to try again.",
            "error",
        )
    if state.capture_state == CAPTURE_CLEARED:
        return (
            "Browser cookies cleared",
            "A new authentication refresh is needed before the app can trade.",
            "warning",
        )
    if state.capture_state == CAPTURE_WORKING:
        return (
            "Signing in",
            "The browser is completing the account sign-in flow.",
            "working",
        )
    return (
        "Worker ready",
        "This window will be reused the next time authentication is needed.",
        "neutral",
    )


# Cropped from ui/theme.py BANNER_ART: the mascot block alone, dedented to width 21. The
# blink frame is derived by swapping the eye row, so the two frames can never differ in
# width. The blink <pre> is stacked on the base frame and only fills in the eye gaps, which
# is why the base does not need to be hidden while it shows.
_MASCOT_EYES_OPEN = "████  █████████  ████"
_MASCOT_EYES_SHUT = "████▄▄█████████▄▄████"

_MASCOT_ART = """\
         ███████████
     █████████████████
   ███████     ███████
  ██████   █   ███████
 █████████   █████████
 █████████████████████
████  █████████  ████
█████████████████████
 ███████   █████████
 ███████████████████
  ████████████████
     ███████████"""

_MASCOT_BLINK_ART = _MASCOT_ART.replace(_MASCOT_EYES_OPEN, _MASCOT_EYES_SHUT)


def build_browser_landing_html(state):
    """Build a self-contained page with no remote resources or sensitive values."""
    capture_label, capture_tone = _CAPTURE_PRESENTATION.get(
        state.capture_state,
        _CAPTURE_PRESENTATION[CAPTURE_WAITING],
    )
    validation_label, validation_tone = _VALIDATION_PRESENTATION.get(
        state.validation_state,
        _VALIDATION_PRESENTATION[VALIDATION_IDLE],
    )
    headline, subheadline, overall_tone = _headline_for(state)
    account_label = escape(state.account_label)
    last_refresh = escape(state.last_refresh or "None in this worker yet")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
  <title>BDO Marketplace Tools - Browser Worker</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0a0b0e;
      --line: rgba(232, 229, 220, .085);
      --line-soft: rgba(232, 229, 220, .045);
      --ink: #e8e5dc;
      --ink-dim: #aaaaaa;
      --ink-faint: #6f6f6f;
      --brand: #ff913c;
      --success: #7eb88a;
      --warning: #c9b458;
      --error: #d16a6a;
      --mono: "Cascadia Mono", Consolas, "SFMono-Regular", ui-monospace, monospace;
      --sans: "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; }}
    body {{
      display: grid;
      place-items: center;
      min-height: 100vh;
      padding: 40px 24px;
      font-family: var(--sans);
      color: var(--ink);
      background:
        radial-gradient(ellipse 900px 400px at 50% -12%, rgba(255, 145, 60, .10), transparent 68%),
        var(--bg);
    }}
    .page {{ width: min(680px, 100%); }}

    /* ---- masthead ---------------------------------------------------- */
    .masthead {{
      display: flex; align-items: center; gap: 20px;
      padding-bottom: 26px; border-bottom: 1px solid var(--line);
    }}
    .mark {{ position: relative; flex: none; color: var(--brand); }}
    .mark pre {{
      margin: 0; font-family: var(--mono); font-size: 4.4px; line-height: 1;
      white-space: pre; letter-spacing: 0;
      text-shadow: 0 0 14px rgba(255, 145, 60, .35);
    }}
    .mark .blink {{ position: absolute; inset: 0; opacity: 0; animation: blink 6.4s infinite; }}
    .wordmark {{ flex: 1; min-width: 0; }}
    .product {{
      font-family: var(--mono); font-size: 12px; font-weight: 700;
      letter-spacing: .2em; text-transform: uppercase; color: var(--brand);
    }}
    .unit {{ margin-top: 5px; font-size: 12px; color: var(--ink-faint); }}
    .account {{ flex: none; text-align: right; max-width: 42%; }}
    .account-key {{
      display: block; font-family: var(--mono); font-size: 9.5px;
      letter-spacing: .16em; text-transform: uppercase; color: var(--ink-faint);
    }}
    .account-value {{
      display: block; margin-top: 5px; font-size: 13px; color: var(--ink-dim);
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }}

    /* ---- hero: the standing instruction, the reason this page exists --- */
    .hero {{ padding: 40px 0 38px; }}
    h1 {{
      margin: 0; font-family: var(--mono); font-weight: 600;
      font-size: clamp(22px, 4.5vw, 33px); line-height: 1.16; letter-spacing: -.025em;
      text-wrap: balance;
    }}
    .lede {{
      margin: 16px 0 0; max-width: 56ch; font-size: 15.5px; line-height: 1.6;
      color: var(--ink-dim); text-wrap: pretty;
    }}
    .lede b {{ color: var(--ink); font-weight: 600; }}
    .caveat {{
      margin: 11px 0 0; max-width: 56ch; font-size: 13px; line-height: 1.6;
      color: var(--ink-faint); text-wrap: pretty;
    }}

    /* Window glyph: minimize lit, close visibly discouraged. */
    .win {{
      width: 118px; margin-bottom: 24px;
      border: 1px solid var(--line); border-radius: 6px;
      background: rgba(232, 229, 220, .02);
      box-shadow: 0 14px 40px rgba(0, 0, 0, .45); overflow: hidden;
    }}
    .win-bar {{
      display: flex; justify-content: flex-end; gap: 10px; padding: 7px 9px;
      font-family: var(--mono); font-size: 9px; line-height: 1; color: #4a4a4a;
      border-bottom: 1px solid var(--line-soft); background: rgba(232, 229, 220, .02);
    }}
    .win-bar .min {{ color: var(--brand); text-shadow: 0 0 10px rgba(255, 145, 60, .85); }}
    .win-bar .close {{ color: var(--error); opacity: .62; }}
    .win-body {{ display: grid; gap: 6px; padding: 12px 10px 15px; }}
    .win-body i {{ height: 2px; border-radius: 1px; background: var(--line); }}
    .win-body i:nth-child(1) {{ width: 86%; }}
    .win-body i:nth-child(2) {{ width: 58%; }}
    .win-body i:nth-child(3) {{ width: 72%; }}

    /* ---- status roll-up + component chips ----------------------------- */
    .status {{
      padding: 20px 24px; border: 1px solid var(--line); border-radius: 6px;
    }}
    .status-head {{ display: flex; align-items: center; gap: 10px; }}
    .status-head h2 {{ margin: 0; font-size: 14.5px; font-weight: 600; letter-spacing: -.005em; }}
    .status-sub {{ margin: 8px 0 0; font-size: 13px; line-height: 1.6; color: var(--ink-faint); }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 17px; }}
    .chip {{
      display: inline-flex; align-items: center; gap: 10px;
      padding: 8px 14px; border: 1px solid var(--line); border-radius: 999px;
      background: rgba(232, 229, 220, .022);
    }}
    .chip-name {{ font-size: 12.5px; color: var(--ink-dim); }}
    .chip-state {{
      font-family: var(--mono); font-size: 10px; font-weight: 700;
      letter-spacing: .12em; text-transform: uppercase; color: var(--tone);
    }}
    .chip-sep {{ width: 1px; height: 12px; background: var(--line); }}
    .dot {{
      flex: none; display: block; width: 8px; height: 8px; border-radius: 50%;
      background: var(--tone); box-shadow: 0 0 10px var(--tone);
    }}

    [data-tone="neutral"] {{ --tone: #5f5f5f; }}
    [data-tone="working"] {{ --tone: var(--brand); }}
    [data-tone="success"] {{ --tone: var(--success); }}
    [data-tone="warning"] {{ --tone: var(--warning); }}
    [data-tone="error"]   {{ --tone: var(--error); }}
    [data-tone="neutral"] .dot {{ box-shadow: none; }}
    [data-tone="working"] .dot {{ animation: pulse 1.5s ease-in-out infinite; }}

    /* ---- meta -------------------------------------------------------- */
    .meta {{
      display: flex; justify-content: space-between; gap: 18px; flex-wrap: wrap;
      margin-top: 20px; font-family: var(--mono); font-size: 10.5px; color: #5c5c5c;
    }}
    .meta-key {{ letter-spacing: .13em; text-transform: uppercase; color: #444444; }}

    @keyframes pulse {{ 0%, 100% {{ opacity: .3; }} 50% {{ opacity: 1; }} }}
    @keyframes blink {{ 0%, 95.5%, 100% {{ opacity: 0; }} 96.6%, 98.2% {{ opacity: 1; }} }}
    @media (prefers-reduced-motion: reduce) {{
      .mark .blink {{ animation: none; }}
      [data-tone="working"] .dot {{ animation: none; }}
    }}
    @media (max-width: 620px) {{
      body {{ padding: 26px 16px; }}
      .masthead {{ flex-wrap: wrap; gap: 16px; }}
      .account {{ max-width: none; text-align: left; }}
      .hero {{ padding: 30px 0 26px; }}
      .status {{ padding: 18px; }}
    }}
  </style>
</head>
<body data-capture-state="{escape(state.capture_state)}" data-validation-state="{escape(state.validation_state)}">
  <main class="page">
    <header class="masthead">
      <div class="mark" aria-hidden="true"><pre>{_MASCOT_ART}</pre><pre class="blink">{_MASCOT_BLINK_ART}</pre></div>
      <div class="wordmark">
        <div class="product">BDO Marketplace Tools</div>
        <div class="unit">Authentication browser worker</div>
      </div>
      <div class="account">
        <span class="account-key">Account</span>
        <span class="account-value">{account_label}</span>
      </div>
    </header>

    <section class="hero">
      <div class="win" aria-hidden="true">
        <div class="win-bar">
          <span class="min">&#9472;</span><span>&#9723;</span><span class="close">&#10005;</span>
        </div>
        <div class="win-body"><i></i><i></i><i></i></div>
      </div>
      <h1>Please don&#8217;t close this window</h1>
      <p class="lede">You can <b>minimize it</b> and carry on playing. It just needs to stay open.</p>
      <p class="caveat">If you close it, the app stops refreshing your marketplace session automatically until you start the worker again from App Settings.</p>
    </section>

    <section class="status">
      <div class="status-head" data-tone="{overall_tone}">
        <span class="dot"></span>
        <h2>{escape(headline)}</h2>
      </div>
      <p class="status-sub">{escape(subheadline)}</p>
      <div class="chips">
        <span class="chip" data-tone="{capture_tone}">
          <span class="dot"></span>
          <span class="chip-name">Marketplace cookie</span>
          <span class="chip-sep"></span>
          <span class="chip-state">{escape(capture_label)}</span>
        </span>
        <span class="chip" data-tone="{validation_tone}">
          <span class="dot"></span>
          <span class="chip-name">Session validation</span>
          <span class="chip-sep"></span>
          <span class="chip-state">{escape(validation_label)}</span>
        </span>
      </div>
    </section>

    <footer class="meta">
      <span><span class="meta-key">Last refresh</span> &nbsp;{last_refresh}</span>
      <span>No credentials or cookie values appear on this page.</span>
    </footer>
  </main>
</body>
</html>"""


async def render_browser_landing(page, state, *, timeout_ms=5000):
    """Best-effort render; a cosmetic page must never fail authentication."""
    set_content = getattr(page, "set_content", None)
    if not callable(set_content):
        return False
    try:
        result = set_content(
            build_browser_landing_html(state),
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        if inspect.isawaitable(result):
            await asyncio.wait_for(result, timeout=max(0.001, timeout_ms / 1000))
    except asyncio.CancelledError:
        raise
    except Exception:
        return False
    return True
