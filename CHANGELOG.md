# Changelog

All notable released changes for `bdo-marketplace-tools` are documented here.

## Unreleased

No released changes yet.

## 1.3.0-beta - 2026-07-04

### Added

- Reworked the app shell from the old left sidebar into a top-tab layout with Dashboard, Logs, Inventory, Stats, and Settings access, plus a bottom status strip for current mode and spend cap.
- Added a full Logs tab and moved the dashboard event log into a compact Activity tail. Logs now use one unified event stream with All, Notable, and Alerts filters, a 100-event buffer, coalesced repeats, unread alerts, run dividers, and calmer importance-based coloring.
- Added SQLite-backed stats history in `stats.db` with lifetime totals, timestamped detection/purchase events, per-day scan coverage, schema versioning/migration, and 30-day Stats trend charts for daily activity, busiest days, and listing hours.
- Added dashboard cockpit controls: compact chips, a large Start/Stop tile, running-state session stats, mascot/status polish, small animations, and a clearer distinction between idle and real fault states.
- Added hover tooltips and visual polish to Stats charts, including exact daily values, rounded y-axis labels, monitored/offline day coverage, heatmap gradients, and calmer detected/purchased colors.
- Added test-mode-only live buy diagnostics, including a confirmed live buy error probe and isolated `stats.test.db` chart-history writes for visual testing.

### Changed

- Modernized modals, App Settings, Inventory actions, and the top bar into the current flat text-action/chip design language.
- Removed the persisted Stats range preference and made Daily Activity a permanent 30-day centerpiece, with secondary Busiest Days and Listing Hours charts below it.
- Moved current-run stats out of the footer and into the dashboard welcome strip while keeping the global status bar focused on mode and cap.
- Removed the old repo-local data migration path. Runtime data now reads only from the current per-user data directory or `BDO_DATA_DIR`; legacy in-repo `data/` is left untouched and ignored.
- Updated the single-item test-buy target price to `92500` silver.
- Settings schema is now `9` for the updated settings/log/stat preference shape.

### Fixed

- Moved stats database writes for detections, purchases, lifetime totals, and scan coverage onto serialized/background workers so stats persistence cannot delay monitor purchase attempts.
- Recorded outfit detection history as availability episode starts instead of repeated scan sightings, with duplicate suppression, missing-scan grace, and retry-on-write-failure behavior.
- Routed test-mode stats to `stats.test.db` and guarded against accidental writes to production `stats.db`.
- Fixed chart load failures so they surface in the Stats page instead of failing silently.
- Fixed browser-auth notice mojibake so injected setup overlays show normal punctuation.
- Fixed purchase `resultCode=-16` so not-enough-silver failures are named clearly in the event log.
- Hid page scrollbars while preserving scrolling, hid test controls on Stats so charts get full width, and removed the old Blank Browser HAR diagnostic.

### Maintenance

- Extracted browser-auth cookie, dialog, notice, and Playwright-import helpers into focused modules while preserving compatibility aliases.
- Extracted the event-log engine into `services/event_log.py` and moved Textual CSS into `ui.styles`.
- Removed dead diagnostics and unused UI code after the browser-auth and top-tab rewrites.

## 1.2.1-beta - 2026-06-22

### Added

- Added configurable cleanup for disposable Chrome browser-profile cache before user-initiated session refresh/setup, preserving cookies and browser storage used for login persistence. App Settings now shows storage usage, saves the auto-clean limit, and offers manual cleanup.
- Added an unread indicator to the dashboard event-log Core/UI tabs when events arrive on the channel that is not currently visible.
- Added clearer in-browser authentication overlays, including working/manual-action states, page dimming during automation, Steam "Remember me" guidance, and a dedicated saved-credentials-rejected state.
- Added retries upon login failure not caused by wrong password/CAPTCHA

### Changed

- Modernized App Settings into a tighter action-card layout with update/storage controls, a focused danger zone, slimmer settings buttons, shared status text, and less redundant configuration summary content.
- Reworked the Stats page by removing the redundant refresh action, keeping live session stat tiles, and showing lifetime stats as compact reference values.
- The auth "please wait" notice now appears on every embedded login/reauth popup with context-aware messages for setup, manual action, rejected credentials, and timeout reasons.
- Settings schema is now `8` for the browser-cache cleanup preferences.

### Fixed

- Fixed Pearl Abyss saved-credential browser login retry behavior so verification or invalid-credential dialogs stop automation immediately, failed refill attempts do not burn retry budget, and technical retries are bounded.
- Blocked Pearl Abyss browser refresh when saved PA credentials are missing, with the Session modal marking credentials as required and disabling refresh until credentials are saved.
- Trimmed Steam browser-auth status noise so repeated click attempts and cookie capture steps do not spam duplicate user-facing events.
- Fixed test-mode `Expire Session` so it clears the saved/request marketplace session while preserving monitor and buy-mode state for recovery testing.
- Prevented manual App Settings update checks from writing duplicate UI event-log rows.

### Docs

- Added ignored developer-local operation and timing reference notes for auth, session, monitor, UI controls, and runtime delay ownership.

## 1.2.0-beta - 2026-06-20

### Added

- Added notify-only update checks on startup and from App Settings. The app now logs its running version, can compare against `APP_VERSION` on the GitHub `main` branch, shows an update-available indicator when a newer version exists, and lets users disable startup checks while keeping manual checks available.
- Added verification-challenge detection for visible reCAPTCHA, hCaptcha, and Cloudflare Turnstile widgets during browser re-authentication. The app now pauses automatic clicks or credential submission, keeps the browser open, and logs a clear user action message when a CAPTCHA-style challenge needs manual completion.
- Added a first-time setup notice inside the login browser while the app is handling slower one-time cookie-consent and Steam-login automation.
- Added normal-mode App Settings maintenance actions for clearing the current session, clearing browser cookies, and resetting Steam setup, plus visible result feedback.
- Added test-mode controls for repeated reauth diagnostics: `Clear (Keep Steam)` preserves Steam web-session cookies while clearing marketplace/PA/Cookiebot state, and `Run Session Check` runs one production session-check iteration on demand.

### Changed

- Moved runtime data out of the repo into the per-user data directory (`%LOCALAPPDATA%\bdo-marketplace-tools\data` by default, overrideable with `BDO_DATA_DIR`). On first launch, existing in-repo `data` is copied best-effort to the new location without overwriting existing per-user data.
- Reworked and decluttered App Settings into focused About, Updates, Account, Configuration, and maintenance areas, with live account/session status and a compact summary of dashboard-controlled settings.
- Settings schema is now `7` to support the new update-notification preferences.
- Steam Account reauth now stays on the fast path after first-time setup by treating cookie-consent preparation as browser-cookie state that is cleared only when cookies or setup are reset.

### Fixed

- Fixed auth browser shutdown and cookie capture so fresh marketplace session cookies are captured from the OAuth callback response, stale persistent-profile cookies cannot trigger early success, and still-loading marketplace pages cannot block close.
- Fixed Steam auto-login reliability by retrying Pearl Abyss Steam and Steam OpenID confirmation clicks when a click does not advance the page, instead of marking a dead click as complete.
- Fixed Steam buy-mode continuity so periodic expiry handling, inline purchase recovery, and manual refresh failure all pause through the shared session-refresh path and arm auto-resume consistently.
- Fixed pending buy-mode auto-resume state so explicit auth resets and account-mode changes cannot revive an older app-paused buy mode later.
- Fixed test-mode forced expiry so it is read by the real session-check path and cleared after recovery, making idle reauth testing match production orchestration more closely.

## 1.1.0-beta - 2026-06-19

### Changed

- Replaced Pearl Abyss direct password login requests with the app-owned visible browser session flow. Saved PA credentials can now auto-fill in the browser, and validated marketplace cookies are imported after browser login completes.
- Steam Account refreshes now use completed Steam Initial Setup as the normal auto-click gate. After setup, manual refresh, purchase reauth, and scheduled Steam session recovery can click the usual Pearl Abyss Steam and Steam OpenID continuation buttons when the prepared browser profile allows it.

### Fixed

- Closed the visible auth browser as soon as a fresh marketplace session cookie is captured from the OAuth callback response, avoiding slow waits for the marketplace page to finish loading and preventing stale profile cookies from being mistaken for a new login.
- Buy mode now resumes automatically after a successful session refresh when the app paused it because of an expired session. User-disabled buy mode is still never re-enabled silently.
- Pearl Abyss browser recovery failures now pause buy mode from structured auth-failure signals, preventing repeated monitor-triggered reauthentication attempts during an expired session.
- Fresh Pearl Abyss browser profiles are seeded from the Black Desert homepage before first marketplace auth, then marked prepared so later refreshes skip the warmup.
- Steam browser profiles now remember one-time Pearl Abyss login-page Cookiebot preparation, so later Steam refreshes skip that DOM probe unless setup or browser cookies are reset.
- Test-mode purchase-expiry reauth checks now force the browser recovery path for both Pearl Abyss and Steam instead of short-circuiting when locally saved cookies still appear valid.

## 1.0.0-beta - 2026-06-04

### Added

- Steam compatibility reached its first release-ready milestone. Steam Account is now a second session acquisition path using the existing marketplace monitor and purchase pipeline after importing validated Central Market cookies from a visible app-owned Patchright Chrome profile.
- Steam now supports persistent browser profiles, first-run browser setup, required-only cookie consent handling, manual Steam login/profile preparation, manual Steam/Pearl Abyss marketplace login, OAuth-return cookie capture, `/Home/AppSessionRefresh` validation, and existing session persistence through `APIHandler.save_session()`.
- Steam reauthentication is integrated with the existing session recovery model. Startup still requires a manual refresh when the saved Steam session is expired or unknown, but after one validated Steam refresh in the current app run the visible browser flow can auto-click normal Pearl Abyss and Steam continuation buttons when available.
- Test mode includes Steam diagnostics for auto-reauth toggling, synthetic session expiry, reauth checks, resetting Steam setup state, and clearing app-owned browser cookies without printing cookie values.

### Changed

- Tightened session safety around Steam support: account-mode changes stop the monitor, clear old marketplace session state, mark the app offline, reset the current-run Steam auto-reauth gate, and defer resets until active purchase chains finish when needed.
- Steam-mode expired buy responses now run the shared browser refresh path and retry the purchase batch once after validation succeeds instead of attempting Pearl Abyss email/password login.
- The Textual UI exposes Steam Account setup and refresh workflows from the Credentials and Session modals, keeps login-method labels consistent, persists selected account mode and Steam setup state, and separates Core versus UI event logs for clearer troubleshooting.
- Supporting reliability work includes cookie-authoritative browser capture without arbitrary market-page stability waits, saved-session startup gating, persistent UI preferences, focused Steam/browser/session tests, and local documentation updates for Steam auth and release workflow.

### Security

- OTP and CAPTCHA remain manual-only, with sanitized status messages and no OTP, Steam credentials, or Steam cookies imported into the marketplace API session.

## 0.1.16-beta - 2026-06-04

### Added

- Steam Initial Setup now opens Steam's official login page and closes automatically after observing a logged-in Steam browser profile without calling Steam APIs or storing Steam credentials.
- Test mode now includes controls to reset Steam Initial Setup status and clear app-owned browser profile cookies without printing cookie values in the event log.

### Fixed

- Steam Initial Setup now closes promptly when no Cookiebot consent banner is present instead of waiting through slow missing-button timeouts.

### Docs

- Added a README version badge and clarified that README edits and badge updates are reserved for explicit user requests or the dedicated release thread.

## 0.1.15-beta - 2026-06-04

### Added

- Added Steam Account browser-session authentication as a second session acquisition path, using a visible app-owned Patchright Chrome profile, manual Steam/Pearl Abyss login, Central Market cookie capture, `/Home/AppSessionRefresh` validation, and existing session persistence.
- Added Steam Initial Setup from the Credentials modal so the persistent browser profile can visit the main Black Desert site, accept required-only cookie consent when available, save setup state, and close automatically on successful setup.
- Added current-run gated Steam automatic re-authentication after a validated manual Steam refresh, including visible auto-click support for the Pearl Abyss `Log in with Steam` button and Steam OpenID `Sign In` button.
- Added test-mode Steam controls for toggling automatic reauth, expiring the current session without stopping the monitor, and running the same reauth path used after an expired buy response.
- Added persistent UI preferences in `data/app_settings.json` for polling range, buy delay, spend cap, watch/buy mode, and the selected event-log view.
- Added a saved-session last-known-valid flag so startup only calls the session-refresh endpoint when saved cookies were previously validated and a cookie jar exists.
- Added separate Core and UI dashboard event-log streams, each with its own rolling 20-entry history, while retaining the combined compatibility stream.
- Added package-level runtime/version metadata and App Settings display for app version, release channel, settings schema, launch mode, and session-control summaries.

### Changed

- Reworked the project into the `bdo_marketplace_tools` package layout with separated market, services, storage, and UI modules, and moved private runtime state under ignored `data/` paths.
- Split the Textual UI into app, modal, widget, theme, and display modules, with dashboard actions centered around modal workflows instead of sidebar action buttons.
- Reworked the dashboard into compact workflow tiles for Monitor/Session, Spent/Credentials, Polling/Buy Delay, plus read-only Success Rate and Runtime tiles.
- Reworked dashboard tile states with semantic colors, status dots, centered tile content, dimmed read-only tiles, hover/focus states for interactive tiles, and a percentage color spectrum for Success Rate.
- Moved account/session mode selection into the Credentials modal and renamed login modes to `Pearl Abyss Account` and `Steam Account`.
- Reworked the Credentials modal into a login-method hub: Pearl Abyss mode opens a child email/password modal with inline validation, Steam mode uses the Steam Initial Setup tile, and switching modes preserves saved PA credentials.
- Changed Credentials dashboard/status presentation so saved PA credentials show as `PA Account` in gold, Steam shows in Steam blue, and Clear PA Account uses red destructive styling only in PA mode.
- Reworked the Session modal so the Session dashboard tile opens a modal first, Refresh Session asks for confirmation, PA mode shows a credentials guard without duplicating the account email, Steam mode shows Initial Setup status, and the refresh button no longer opens pre-highlighted.
- Standardized the dashboard Session tile description to `Authenticated` when online and `Refresh required` when offline for both Pearl Abyss and Steam login modes.
- Changed account-mode switching, credential changes, and manual session clears to stop or queue monitor shutdown, clear old marketplace cookies, mark the session offline, and reset Steam auto-reauth state.
- Changed Steam auth to be cookie-authoritative: browser cookie polling is local, Central Market cookies are captured as soon as the OAuth callback or market return has usable cookies, and final success remains server validation through `/Home/AppSessionRefresh`.
- Changed Steam-mode expired sessions to prompt browser refresh or run visible browser reauth instead of attempting Pearl Abyss email/password relogin.
- Changed buy-time expired-session handling so Steam mode runs the shared browser refresh flow and retries the same purchase batch once after validation succeeds.
- Changed startup session behavior to skip unnecessary session-refresh API calls when no known-valid saved session exists.
- Changed polling, buy-delay, and spend-cap modal inputs back to preview-only behavior until Save; invalid values keep the modal open and preserve the last saved setting.
- Reworked the Polling modal to explain scan frequency, remove the dropdown, show Fast/Balanced/Slow preset tiles, auto-fill custom min/max inputs from presets, and auto-highlight preset-equivalent custom ranges.
- Reworked the Buy Delay modal and buy-mode confirmations so users can configure and review the delay between purchase attempts separately from polling frequency.
- Renamed the Marketplace Wallet screen to Marketplace Inventory and kept it as a WIP inventory/silver/Value Pack diagnostic view.
- Reworked Stats, Marketplace Inventory, and App Settings utility actions to use the same rounded action-tile style while removing filled rectangular backgrounds from page-level actions.
- Reworked App Settings into app metadata and Session Debug sections with a clear saved-session action that preserves credentials.
- Replaced Textual's default header/menu with a custom title/clock header, removed the default top-left menu and clock expansion behavior, removed beta labels from the title/brand, and moved the exact build label to the lower-left sidebar.
- Reworked the dashboard event log with a compact Core/UI selector, neutral event-log title, separate rolling 20-entry streams, and a slimmer dark scrollbar instead of Textual's default blue scrollbar.
- Updated `run.bat` to launch through Windows Terminal at a usable size when available, forward arguments, avoid pycache churn, and close cleanly on successful app exits.

### Fixed

- Fixed Steam browser-session capture failures caused by waiting for later market UI page stability instead of validating once usable Central Market cookies existed.
- Fixed Steam browser flows staying open after successful cookie capture by closing the visible browser after setup or session validation succeeds.
- Fixed mode hot-swapping so purchases are not made with stale cookies from the previous account mode; if a purchase chain is already active, reset is deferred until the chain finishes.
- Fixed startup state discrepancies where the UI could show Pearl Abyss mode while a saved Steam session was active by persisting and restoring the selected account mode.
- Fixed event-log over-noise by routing interface confirmations to the UI stream and keeping Core focused on monitor, session, API, detection, purchase, and critical failure outcomes.
- Fixed dashboard/sidebar readability issues while iterating on the compact Textual layout and modernized modal styling.
- Fixed monitor controls so Start is disabled while already running, Stop is disabled while already stopped, repeated starts do not create duplicate tasks, and successful start flows close modal stacks back to the dashboard.
- Fixed several Textual focus artifacts where modal buttons appeared pre-highlighted after opening or clicking.
- Fixed missing-credential session refresh attempts so they stay in UI status instead of producing backend event-log warnings.

### Docs

- Added and refreshed local agent documentation for Steam auth, API behavior, runtime files, technical priorities, startup session checks, event-log categories, and the new versioning/changelog workflow.
- Replaced the old active agent changelog flow with the tracked root `CHANGELOG.md` plus ignored `.changesets/` files for future pending implementation notes.
- Froze the detailed pre-release modernization history in `agent/LEGACY_CHANGELOG.md`.

### Tests

- Added focused tests for Steam mode branching, browser-cookie import validation, failed import rollback, Steam auto-reauth branching, OTP status-only handling, missing-button manual-attention status, startup auto-gate behavior, and purchase-expiry retry behavior.
- Added tests for app-settings persistence, saved-session startup gating, event-log Core/UI separation, event-log view persistence, 20-entry event buffers, and fresh `BackgroundTasks` instances restoring saved UI state.
- Added and updated Textual workflow tests for credentials, session refresh, modal saves, buy-mode blocking/confirmation, test-mode reauth controls, Inventory navigation, and App Settings behavior.

## 0.1.0-beta - 2026-06-04

### Added

- Established the Textual dashboard app for marketplace monitoring, session management, wallet/status views, settings, stats, and test-mode tools.
- Added marketplace API integration for outfit stock scans, session refresh, wallet data, and guarded `BuyItem` purchase requests.
- Added Steam browser-session support through a visible Patchright Chrome profile and Pearl Abyss credential support through OS keyring-backed password storage.
- Added structured local runtime storage under ignored `data/` files for app settings, session cookies, local stats, and browser profiles.
- Added the `bdo_marketplace_tools` package layout with separated market, service, storage, and UI modules.
- Added app/version metadata in `bdo_marketplace_tools/version.py` and mirrored it into `data/app_settings.json` for troubleshooting.

### Changed

- Replaced the old scattered runtime/module layout with a fresh-start `data/` runtime model and direct `bdo_marketplace_tools.*` imports.
- Kept the detailed pre-release modernization history archived in deprecated `agent/LEGACY_CHANGELOG.md` as a local legacy record.
