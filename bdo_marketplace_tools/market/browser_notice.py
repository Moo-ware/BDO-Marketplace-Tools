"""Cosmetic browser notices used during browser-auth flows.

Patchright runs ``add_init_script`` in an isolated world, while ``page.evaluate``
runs in the page's main world. Keep each emitted JavaScript script fully
self-contained; do not share JS helpers between the setup script and main-world
state flips.
"""

# A purely cosmetic notice injected into the visible browser on EVERY auth pop-up (login / reauth,
# Steam or PA). It dims + lightly blurs the whole page behind a frosted "Setting up your session"
# card, making it obvious not to touch the page while the automation drives the login. The dim is a
# `pointer-events:none` scrim, so it is a visual signal only and never blocks the automation's (or the
# user's) clicks. add_init_script runs it at document start on every navigation (re-showing it each
# page), and everything is `pointer-events:none`. The card flips to a red "manual action required"
# state -- and the scrim lifts so the page is usable -- via _set_setup_notice_warning. NOTE: Patchright
# runs add_init_script in an isolated world, so the flip must manipulate the shared DOM directly from
# the main world; it cannot call a function defined here, which is why no window global is exposed.

_NOTICE_IDS_SCRIPT = r"""  const ID = '__bdo_setup_notice__';
  const SCRIM_ID = '__bdo_setup_scrim__';"""
_NOTICE_IDS_WITH_STYLE_SCRIPT = _NOTICE_IDS_SCRIPT + "\n  const STYLE_ID = '__bdo_setup_notice_style__';"
_NOTICE_FONT_STYLE = "\"font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif\""
_NOTICE_KEYFRAMES_SCRIPT = r"""      '@keyframes __bdoSetupSpin{to{transform:rotate(360deg)}}' +
      '@keyframes __bdoSetupGlow{0%,100%{box-shadow:0 0 0 1px rgba(255,145,60,.4),0 0 20px rgba(255,145,60,.16),0 16px 44px rgba(0,0,0,.55)}50%{box-shadow:0 0 0 1px rgba(255,145,60,.72),0 0 42px rgba(255,145,60,.4),0 16px 44px rgba(0,0,0,.55)}}' +
      '@keyframes __bdoSetupGlowRed{0%,100%{box-shadow:0 0 0 1px rgba(255,95,85,.5),0 0 22px rgba(255,95,85,.2),0 16px 44px rgba(0,0,0,.55)}50%{box-shadow:0 0 0 1px rgba(255,95,85,.85),0 0 46px rgba(255,95,85,.46),0 16px 44px rgba(0,0,0,.55)}}';"""
_NOTICE_CREATE_STYLE_SCRIPT = r"""    const st = document.createElement('style');
    st.id = STYLE_ID;
    st.textContent =
__NOTICE_KEYFRAMES__
    (document.head || document.documentElement).appendChild(st);""".replace(
    "__NOTICE_KEYFRAMES__",
    _NOTICE_KEYFRAMES_SCRIPT,
)
_NOTICE_ENSURE_STYLE_SCRIPT = r"""  const ensureStyle = () => {
    if (document.getElementById(STYLE_ID)) return;
__NOTICE_CREATE_STYLE__
  };""".replace("__NOTICE_CREATE_STYLE__", _NOTICE_CREATE_STYLE_SCRIPT)
_NOTICE_INLINE_STYLE_SCRIPT = r"""  if (!document.getElementById(STYLE_ID)) {
__NOTICE_CREATE_STYLE__
  }""".replace("__NOTICE_CREATE_STYLE__", _NOTICE_CREATE_STYLE_SCRIPT)
_NOTICE_SCRIM_STYLE_ITEMS = r"""'position:fixed','inset:0','z-index:2147483646','pointer-events:none',
        'background:rgba(8,9,14,0.5)','backdrop-filter:blur(3px)','-webkit-backdrop-filter:blur(3px)'"""
_NOTICE_CARD_BASE_STYLE_ITEMS = r"""'z-index:2147483647','pointer-events:none','box-sizing:border-box',
        'border-radius:16px','backdrop-filter:blur(14px)','-webkit-backdrop-filter:blur(14px)',
        __NOTICE_FONT__""".replace("__NOTICE_FONT__", _NOTICE_FONT_STYLE)


def _notice_icon_chip(icon_expression, background, *, size="36px", radius="10px", extra_style="flex:none;"):
    return (
        "'<div style=\"width:" + size + ";height:" + size + ";border-radius:" + radius + ";background:" + background
        + ";display:flex;align-items:center;justify-content:center;" + extra_style + "\">' + "
        + icon_expression
        + " + '</div>'"
    )


SETUP_NOTICE_SCRIPT = r"""
(() => {
  // The optional persistent PA worker parks on about:blank between auth cycles.
  // The worker renders its own full-page status landing there; this auth notice
  // runs again when that page later navigates to a real login or market document.
  if (window.location.href === 'about:blank') return;
__NOTICE_IDS__
  const SPIN_ICON = '<span style="box-sizing:border-box;width:18px;height:18px;border-radius:50%;' +
    'border:2px solid rgba(255,145,60,0.3);border-top-color:#ff913c;' +
    'animation:__bdoSetupSpin 0.7s linear infinite;display:inline-block;"></span>';
__NOTICE_ENSURE_STYLE__
  const showSetup = () => {
    if (!document.body) return;
    ensureStyle();
    let scrim = document.getElementById(SCRIM_ID);
    if (!scrim) {
      scrim = document.createElement('div');
      scrim.id = SCRIM_ID;
      scrim.setAttribute('style', [
        __NOTICE_SCRIM_STYLE_ITEMS__,
        'opacity:0','transition:opacity .35s ease'
      ].join(';'));
      document.body.appendChild(scrim);
      requestAnimationFrame(() => { scrim.style.opacity = '1'; });
    }
    let card = document.getElementById(ID);
    if (!card) {
      card = document.createElement('div');
      card.id = ID;
      card.setAttribute('style', [
        'position:fixed','top:20px','left:50%','transform:translateX(-50%)',
        __NOTICE_CARD_BASE_STYLE_ITEMS__,
        'max-width:440px','width:calc(100% - 32px)','padding:15px 18px',
        'border:1px solid rgba(255,145,60,0.4)','background:rgba(17,17,21,0.66)','color:#e6e3dc',
        'animation:__bdoSetupGlow 2s ease-in-out infinite',
        'opacity:0','transition:opacity .4s ease'
      ].join(';'));
      document.body.appendChild(card);
      requestAnimationFrame(() => { card.style.opacity = '1'; });
    }
    card.innerHTML =
      '<div style="display:flex;align-items:center;gap:13px;text-align:left;">' +
        __NOTICE_SETUP_ICON_CHIP__ +
        '<div>' +
          '<div style="font-size:15px;font-weight:600;color:#ffb37a;letter-spacing:.2px;">' +
            'Setting up your session' +
          '</div>' +
          '<div style="font-size:12.5px;font-weight:400;color:#e6e3dc;opacity:.85;margin-top:2px;line-height:1.45;">' +
            'The app is signing you in - please don\'t click anything. It finishes on its own.' +
          '</div>' +
        '</div>' +
      '</div>';
  };
  if (document.body) showSetup();
  else document.addEventListener('DOMContentLoaded', showSetup);
})();
""".replace("__NOTICE_IDS__", _NOTICE_IDS_WITH_STYLE_SCRIPT).replace(
    "__NOTICE_ENSURE_STYLE__",
    _NOTICE_ENSURE_STYLE_SCRIPT,
).replace(
    "__NOTICE_SCRIM_STYLE_ITEMS__",
    _NOTICE_SCRIM_STYLE_ITEMS,
).replace(
    "__NOTICE_CARD_BASE_STYLE_ITEMS__",
    _NOTICE_CARD_BASE_STYLE_ITEMS,
).replace(
    "__NOTICE_SETUP_ICON_CHIP__",
    _notice_icon_chip("SPIN_ICON", "rgba(255,145,60,0.18)"),
)

SETUP_NOTICE_CAPTCHA_MESSAGE = "Please complete verification in this window to continue."
SETUP_NOTICE_MANUAL_LOGIN_MESSAGE = "Please log in manually in this window to continue."
SETUP_NOTICE_INVALID_CREDENTIALS_MESSAGE = "Saved email/password were rejected. Update credentials in the app, then refresh again."
SETUP_NOTICE_STEAM_LOGIN_MESSAGE = 'Log in to Steam, and check "Remember me" so you stay signed in.'


async def _inject_setup_notice(context):
    # Best-effort: never let a cosmetic notice break the auth flow if the runtime lacks the API.
    add_init_script = getattr(context, "add_init_script", None)
    if not callable(add_init_script):
        return
    try:
        await add_init_script(SETUP_NOTICE_SCRIPT)
    except Exception:
        pass


# Self-contained main-world flip to the red "manual action required" state: it lifts the dim/blur
# scrim (the page is now the user's to act on) and recolors the box. It manipulates the shared DOM
# directly (find-or-create the box by id) rather than calling into SETUP_NOTICE_SCRIPT, which runs in
# Patchright's isolated world and is therefore unreachable from page.evaluate.
SETUP_NOTICE_WARN_SCRIPT = r"""
(message) => {
__NOTICE_IDS__
  if (!document.body) return;
__NOTICE_INLINE_STYLE__
  const scrim = document.getElementById(SCRIM_ID);
  if (scrim) scrim.remove();
  let card = document.getElementById(ID);
  if (!card) {
    card = document.createElement('div');
    card.id = ID;
    document.body.appendChild(card);
  }
  card.setAttribute('style', [
    'position:fixed','top:20px','left:50%','transform:translateX(-50%)',
    __NOTICE_CARD_BASE_STYLE_ITEMS__,
    'max-width:440px','width:calc(100% - 32px)','padding:15px 18px',
    'border:1px solid rgba(255,95,85,0.45)','background:rgba(22,14,14,0.68)','color:#ecdfdc',
    'animation:__bdoSetupGlowRed 1.6s ease-in-out infinite','opacity:1'
  ].join(';'));
  var ICON = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#ff6b5e" ' +
    'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="display:block;">' +
    '<path d="M12 9v4"/><path d="M12 17h.01"/>' +
    '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>';
  card.innerHTML =
    '<div style="display:flex;align-items:center;gap:13px;text-align:left;">' +
      __NOTICE_WARNING_ICON_CHIP__ +
      '<div>' +
        '<div style="font-size:15px;font-weight:600;color:#ff8a7e;letter-spacing:.2px;">Manual action required</div>' +
        '<div style="font-size:12.5px;font-weight:400;color:#ecdfdc;opacity:.9;margin-top:2px;line-height:1.45;">' +
        (message || 'Action is needed in this window to continue.') +
        '</div>' +
      '</div>' +
    '</div>';
}
""".replace("__NOTICE_IDS__", _NOTICE_IDS_WITH_STYLE_SCRIPT).replace(
    "__NOTICE_INLINE_STYLE__",
    _NOTICE_INLINE_STYLE_SCRIPT,
).replace(
    "__NOTICE_CARD_BASE_STYLE_ITEMS__",
    _NOTICE_CARD_BASE_STYLE_ITEMS,
).replace(
    "__NOTICE_WARNING_ICON_CHIP__",
    _notice_icon_chip("ICON", "rgba(255,95,85,0.18)"),
)


async def _set_setup_notice_warning(page, message):
    # Flip the in-page notice to its "manual action required" state. Best-effort and never raises; the
    # notice is cosmetic and must not affect the auth flow. Runs in the main world (see
    # SETUP_NOTICE_WARN_SCRIPT) so it works regardless of the isolated world add_init_script uses.
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return
    try:
        await evaluate(SETUP_NOTICE_WARN_SCRIPT, message)
    except Exception:
        pass


# Distinct state for "saved credentials were rejected". Unlike the captcha/OTP/Steam manual states,
# the user must NOT log in on this page -- they fix the saved email/password in the app and refresh.
# So this re-dims the page (recreating the scrim a prior warning flip may have removed) and shows a
# calm amber "fix it in the app / safe to close" card instead of the red "act here" warning. Runs in
# the main world via page.evaluate; cosmetic and pointer-events:none.
SETUP_NOTICE_CREDENTIALS_SCRIPT = r"""
() => {
__NOTICE_IDS__
  if (!document.body) return;
  let scrim = document.getElementById(SCRIM_ID);
  if (!scrim) {
    scrim = document.createElement('div');
    scrim.id = SCRIM_ID;
    scrim.setAttribute('style', [
        __NOTICE_SCRIM_STYLE_ITEMS__
      ].join(';'));
    document.body.appendChild(scrim);
  }
  let card = document.getElementById(ID);
  if (!card) {
    card = document.createElement('div');
    card.id = ID;
    document.body.appendChild(card);
  }
  card.setAttribute('style', [
    'position:fixed','top:50%','left:50%','transform:translate(-50%,-50%)',
    __NOTICE_CARD_BASE_STYLE_ITEMS__,
    'width:calc(100% - 48px)','max-width:400px','padding:16px 18px',
    'border:1px solid rgba(224,168,72,0.5)','background:rgba(20,16,12,0.74)',
    'color:#e6e0d4','text-align:center',
    'box-shadow:0 0 30px rgba(224,168,72,0.16),0 16px 44px rgba(0,0,0,0.5)','opacity:1'
  ].join(';'));
  var KEY = '<svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="#e8b65a" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:block;">' +
    '<circle cx="8" cy="16" r="3.2"/><path d="M10.3 13.7 19 5"/><path d="M16 8l2.6 2.6"/><path d="M4 4l16 16"/></svg>';
  var CHECK = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#7eb88a" ' +
    'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="display:block;">' +
    '<circle cx="12" cy="12" r="9"/><path d="m8.5 12 2.5 2.5 4.5-4.5"/></svg>';
  card.innerHTML =
    __NOTICE_CREDENTIALS_ICON_CHIP__ +
    '<div style="font-size:15px;font-weight:600;color:#f0c069;letter-spacing:.2px;">Wrong email or password</div>' +
    '<div style="font-size:12.5px;color:#e6e0d4;opacity:.88;margin-top:5px;line-height:1.5;">Update your saved login in the app, then refresh the session. You don\'t need to log in here.</div>' +
    '<div style="display:inline-flex;align-items:center;gap:6px;margin-top:11px;padding:5px 11px;border-radius:20px;background:rgba(126,184,138,0.14);">' +
      CHECK + '<span style="font-size:12px;font-weight:600;color:#8fce9c;">Safe to close this window</span>' +
    '</div>';
}
""".replace("__NOTICE_IDS__", _NOTICE_IDS_SCRIPT).replace(
    "__NOTICE_SCRIM_STYLE_ITEMS__",
    _NOTICE_SCRIM_STYLE_ITEMS,
).replace(
    "__NOTICE_CARD_BASE_STYLE_ITEMS__",
    _NOTICE_CARD_BASE_STYLE_ITEMS,
).replace(
    "__NOTICE_CREDENTIALS_ICON_CHIP__",
    _notice_icon_chip("KEY", "rgba(224,168,72,0.16)", size="38px", radius="11px", extra_style="margin:0 auto 9px;"),
)


async def _set_setup_notice_credentials_rejected(page):
    # Best-effort, main-world (see SETUP_NOTICE_CREDENTIALS_SCRIPT). Never raises; the notice is
    # cosmetic and must not affect the auth flow.
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return
    try:
        await evaluate(SETUP_NOTICE_CREDENTIALS_SCRIPT)
    except Exception:
        pass


# Cosmetic guide that points at Steam's "Remember me" checkbox during the manual Steam-login step,
# nudging the user to tick it (so the Steam session persists and re-auth can stay automatic). Runs in
# the main world via page.evaluate; finds the checkbox by its visible "Remember me" label text + the
# role=checkbox attribute (Steam's class names are build-hashed and change between releases, so they
# are not reliable). pointer-events:none, so it never blocks the click; it repositions on a short
# interval and self-hides once the box is ticked or is not on the page.
STEAM_REMEMBER_ME_GUIDE_SCRIPT = r"""
() => {
  const RING_ID = '__bdo_remember_ring__';
  const CALLOUT_ID = '__bdo_remember_callout__';
  const ARROW_ID = '__bdo_remember_arrow__';
  const STYLE_ID = '__bdo_remember_style__';
  const BORDER = '1px solid rgba(255,145,60,0.42)';
  const BG = 'rgba(17,17,21,0.78)';
  if (!document.body) return;
  if (!document.getElementById(STYLE_ID)) {
    const st = document.createElement('style');
    st.id = STYLE_ID;
    st.textContent = '@keyframes __bdoRememberPulse{0%,100%{box-shadow:0 0 0 0 rgba(255,145,60,.5),0 0 14px rgba(255,145,60,.35)}50%{box-shadow:0 0 0 7px rgba(255,145,60,0),0 0 22px rgba(255,145,60,.55)}}';
    (document.head || document.documentElement).appendChild(st);
  }
  const findCheckbox = () => {
    const boxes = document.querySelectorAll('[role="checkbox"]');
    for (let i = 0; i < boxes.length; i++) {
      const box = boxes[i];
      let txt = '';
      const lblId = box.getAttribute('aria-labelledby');
      if (lblId) { const l = document.getElementById(lblId); if (l) txt = l.textContent || ''; }
      if (!txt && box.parentElement) txt = box.parentElement.textContent || '';
      if (/remember me/i.test(txt)) return box;
    }
    return null;
  };
  let ring = document.getElementById(RING_ID);
  if (!ring) {
    ring = document.createElement('div');
    ring.id = RING_ID;
    ring.setAttribute('style', 'position:fixed;z-index:2147483645;pointer-events:none;border:2px solid rgba(255,145,60,0.95);border-radius:5px;animation:__bdoRememberPulse 1.4s ease-in-out infinite;');
    document.body.appendChild(ring);
  }
  let callout = document.getElementById(CALLOUT_ID);
  if (!callout) {
    callout = document.createElement('div');
    callout.id = CALLOUT_ID;
    callout.setAttribute('style',
      'position:fixed;z-index:2147483645;pointer-events:none;max-width:230px;padding:8px 12px;' +
      'border-radius:10px;border:' + BORDER + ';background:' + BG + ';' +
      'backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);box-shadow:0 0 22px rgba(255,145,60,0.18);' +
      "font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;");
    callout.innerHTML =
      '<div id="' + ARROW_ID + '" style="position:absolute;width:10px;height:10px;background:' + BG + ';"></div>' +
      '<div style="font-size:12.5px;font-weight:600;color:#ffb37a;letter-spacing:.2px;">Tick this to stay signed in</div>' +
      '<div style="font-size:11px;color:#d7d4cd;opacity:.82;margin-top:1px;line-height:1.4;">so the app can re-login on its own</div>';
    document.body.appendChild(callout);
  }
  const arrow = document.getElementById(ARROW_ID);
  const hide = () => { ring.style.display = 'none'; callout.style.display = 'none'; };
  const place = () => {
    const cb = findCheckbox();
    if (!cb) { hide(); return; }
    if (cb.getAttribute('aria-checked') === 'true') {
      hide();
      if (window.__bdoRememberGuideInterval) { clearInterval(window.__bdoRememberGuideInterval); window.__bdoRememberGuideInterval = null; }
      return;
    }
    const r = cb.getBoundingClientRect();
    if (!r.width && !r.height) { hide(); return; }
    ring.style.display = 'block';
    callout.style.display = 'block';
    ring.style.left = (r.left - 4) + 'px';
    ring.style.top = (r.top - 4) + 'px';
    ring.style.width = (r.width + 8) + 'px';
    ring.style.height = (r.height + 8) + 'px';
    const cw = callout.offsetWidth || 210;
    const ch = callout.offsetHeight || 46;
    if (r.right + 16 + cw <= window.innerWidth) {
      callout.style.left = (r.right + 16) + 'px';
      callout.style.top = (r.top + r.height / 2 - ch / 2) + 'px';
      if (arrow) arrow.setAttribute('style', 'position:absolute;width:10px;height:10px;background:' + BG + ';left:-6px;top:50%;transform:translateY(-50%) rotate(45deg);border-left:' + BORDER + ';border-bottom:' + BORDER + ';');
    } else {
      callout.style.left = Math.max(8, r.left - 8) + 'px';
      callout.style.top = (r.bottom + 14) + 'px';
      if (arrow) arrow.setAttribute('style', 'position:absolute;width:10px;height:10px;background:' + BG + ';left:16px;top:-6px;transform:rotate(45deg);border-left:' + BORDER + ';border-top:' + BORDER + ';');
    }
  };
  const start = findCheckbox();
  const alreadyDone = start && start.getAttribute('aria-checked') === 'true';
  place();
  if (window.__bdoRememberGuideInterval) clearInterval(window.__bdoRememberGuideInterval);
  if (!alreadyDone) window.__bdoRememberGuideInterval = setInterval(place, 400);
}
"""


async def _show_steam_remember_me_guide(page):
    # Best-effort cosmetic highlight pointing at Steam's "Remember me" checkbox. pointer-events:none so
    # it never blocks the click; safe to call repeatedly from the Steam-login wait loop.
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return
    try:
        await evaluate(STEAM_REMEMBER_ME_GUIDE_SCRIPT)
    except Exception:
        pass
