<div align="center">

# Marketplace Tools

<p>
  <a href="https://www.python.org/downloads/">
    <img alt="Python 3.14+" src="https://img.shields.io/badge/Python-3.14%2B-blue">
  </a>
  <img alt="Version 1.3.0-beta" src="https://img.shields.io/badge/Version-1.3.0--beta-blueviolet">
  <img alt="Windows" src="https://img.shields.io/badge/Platform-Windows-0078D6">
  <img alt="MIT License" src="https://img.shields.io/badge/License-MIT-green">
</p>

<p>
  <img alt="Status: Testing" src="https://img.shields.io/badge/Status-Testing-yellow">
  <img alt="Launcher Supported" src="https://img.shields.io/badge/Launcher-Supported-brightgreen">
  <img alt="Steam Supported" src="https://img.shields.io/badge/Steam-Supported-brightgreen">
  <img alt="OTP WIP" src="https://img.shields.io/badge/OTP-WIP-yellow">
</p>

</div>

![Marketplace Tools dashboard](docs/assets/dashboard.png)


Python CLI app for monitoring the *Black Desert Online* marketplace through authenticated HTTP/API requests. It maintains a persistent marketplace session, continuously checks for outfit listings at a custom polling interval, handles long-running monitoring sessions with custom built re-authentication workflow, and executes buy-order requests as soon as matching items become available, in millieseconds.


## Features

### Core Capabilities

- Live interactive dashboard.
- Monitors BDO marketplace for outfit listings.
- Automated purchasing of outfits upon detection.
- Adjustable marketplace polling speed and delay between individual buy attempts.
- Custom silver spend cap per session, stopping future purchases if cap is met.
- Tracks current session's outfit detections, successful purchases, and silver spent. 
- Tracks lifetime silver spent, and successful purchases.
- Provides a unified Logs tab for actions, purchases, detections, session/auth outcomes, and errors.
- Provides a Stats tab with lifetime totals and SQLite-backed 30-day trend charts for daily activity, busiest days, and listing hours.
- Provides a marketplace inventory view for checking stored silver, Value Pack state, and marketplace inventory data (WIP).

### Technical Features

- Marketplace API integration for listing scans, wallet data, session refresh, authentication, and `BuyItem` purchase requests.
- Concurrent marketplace polling with isolated unauthenticated `requests.Session` clients for male and female outfit categories, preserving connection reuse without sharing authenticated state.
- Custom Huffman response decoder for packed marketplace payloads, optimized for repeated high-frequency scans.
- Async monitor orchestration around blocking HTTP calls using `asyncio.to_thread()`, randomized polling windows, capped retry backoff, task lifecycle guards, and crash-aware monitor state.
- Secure per-user runtime storage under `%LOCALAPPDATA%\bdo-marketplace-tools\data` by default, with `BDO_DATA_DIR` override support, JSON cookie storage, SQLite stats history, browser profiles, and OS keyring-backed password storage.
- Safety-gated purchase pipeline with explicit buy-mode confirmation, spend-cap enforcement, configurable per-item buy delay, session-expiration recovery, and one-time retry on expired marketplace sessions.
- Structured purchase result parsing that separates fulfilled purchases from pre-order placements, records actual execution prices, and maps known marketplace result codes into actionable event-log messages.
- Resilient network and response validation for timeouts, malformed JSON, unexpected API shapes, invalid listing rows, stale pricing, duplicate orders, and unavailable items.
- Textual-based terminal dashboard with top-tab navigation, live runtime metrics, modal control flows, inventory/status views, test-mode-only simulation controls, and headless UI workflow tests.
- Focused unit coverage for listing parsing, pricing conversion, spend caps, session refresh behavior, purchase accounting, runtime file initialization, stats history, and dashboard workflows.

## App Status

This app is currently undergoing a codebase rewrite. Features may be incomplete, unstable, or temporarily broken. Message me on discord if you encounter any errors.

## Supported Versions

Last verified compatibility: July 14, 2025.

Pearl Abyss launcher accounts are supported through saved email/password credentials.
Steam accounts are supported through a visible browser session: choose `Steam Account` in the Credentials dashboard modal, run Steam Initial Setup once, then use `Refresh Session` from the dashboard. Initial setup uses the app-owned browser profile to visit the main Black Desert site, handle required-only cookie consent when available, and open Steam's official login page so you can manually log into Steam. The app only observes local browser state, such as Steam login cookie names or the loaded Store account menu, then closes the browser when Steam login is detected. Steam credentials and OTP values are never stored by the app.

## Running the App

Install dependencies from the repository root:

```powershell
py -3 -m pip install -r requirements.txt
```

On Windows, start the app with:

```powershell
run.bat
```

Or run directly from the repository root:

```powershell
py -3 main.py
```

`run.bat` uses Windows Terminal when available so the Textual UI opens at a usable size. Set `BDO_DISABLE_WT=1` before launching to run in the current console instead.

Runtime data is stored outside the app folder by default at `%LOCALAPPDATA%\bdo-marketplace-tools\data`. Set `BDO_DATA_DIR` before launch to use a portable or custom data location.

## Disclaimer

This repository is provided as a proof of concept and educational purposes ONLY. I am *NOT* responsible for anything that happens to your account if you choose to use this.

## Known Issues

If your IP reputation is low, the official login flow may present a CAPTCHA. This project does not automate CAPTCHA challenges, but browser auth can detect visible challenges and keep the browser open so you can complete them manually.

Known problematic result codes:

- `resultCode=30`: identical order already exists. This has been observed with `resultMsg=eErrNoAlreadyReservationDay`.
- `resultCode=34`: item unavailable, already taken, or the request would create a duplicate pre-order.
- `resultCode=-14`: price mismatch. This can happen when PA decide to change max outfit prices. needs updating if that's the case.
- `resultCode=-16`: not enough silver for the order.
- `resultCode=2000`: marketplace login session expired upon buy attempt. The app attempts to refresh/re-authenticate and re buy the item.

Unknown purchase codes are reported as `resultCode {code}` in the event log so they can be documented after a new capture.

## Contact

For questions or bug reports, use the issue tracker or my Discord: `._.__.__._._.__._____.__._.___.`

## Planned Work

- Marketplace Inventory viewer
- Automated pre-orders
- More configurable marketplace categories
