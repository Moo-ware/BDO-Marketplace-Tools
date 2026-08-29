from bdo_marketplace_tools.ui.display import COLOR_BRAND, COLOR_CAUTION, COLOR_ERROR, COLOR_TEXT_MUTED


APP_CSS = """
    Screen {
        background: #101010;
    }

    #shell {
        height: 1fr;
    }

    #topbar {
        dock: top;
        height: 2;
        background: #171717;
        border-bottom: solid __COLOR_BRAND__;
        padding: 0 2;
    }

    #brand {
        width: auto;
        color: __COLOR_BRAND__;
        text-style: bold;
        margin-right: 0;
        content-align: left middle;
    }

    #tabs {
        width: auto;
        height: 1;
    }

    .nav-tab {
        width: auto;
        height: 1;
        margin: 0 2;
        color: __COLOR_TEXT_MUTED__;
        content-align: center middle;
    }

    .nav-tab:hover {
        color: __COLOR_BRAND__;
    }

    .nav-tab-active {
        color: __COLOR_BRAND__;
        text-style: bold;
    }

    #tab-settings {
        width: auto;
        height: 1;
        margin: 0;
        color: __COLOR_TEXT_MUTED__;
        content-align: center middle;
    }

    #tab-settings:hover {
        color: __COLOR_BRAND__;
    }

    #tab-settings.nav-tab-active {
        color: __COLOR_BRAND__;
        text-style: bold;
    }

    #topbar-spacer {
        width: 1fr;
    }

    #topbar-brand-divider,
    #topbar-settings-divider {
        width: auto;
        margin-left: 2;
        color: #3a3a3a;
        content-align: center middle;
    }

    #header-session {
        width: auto;
        margin-left: 2;
        content-align: right middle;
    }

    #build-info {
        width: auto;
        margin-left: 2;
        color: __COLOR_TEXT_MUTED__;
        text-style: dim;
        content-align: right middle;
    }

    #main {
        height: 1fr;
        padding: 0 2;
    }

    #welcome-card {
        height: auto;
        border: round #3a3a3a;
        padding: 0 1;
        margin: 0 0 1 0;
    }

    #greeting-row {
        height: 1;
        margin-top: 1;
        layers: base badges;
    }

    #buy-badges {
        /* Auto-width overlay docked to the right of the chatter's own row. Because it is
           auto-width (not full-width) on a higher layer, it only covers the empty right
           cells — never the centered chatter — so buys never shift the chatter. */
        width: auto;
        dock: right;
        layer: badges;
        color: __COLOR_BRAND__;
    }

    #banner {
        height: 12;
        color: __COLOR_BRAND__;
        text-style: bold;
        content-align: center middle;
        overflow: hidden;
    }

    #welcome-greeting {
        width: 100%;
        height: 1;
        layer: base;
        color: __COLOR_TEXT_MUTED__;
        content-align: center middle;
        text-align: center;
    }

    #welcome-footer {
        height: 2;
        color: __COLOR_TEXT_MUTED__;
        content-align: center middle;
        border-top: solid #2b2b2b;
    }

    #welcome-card.-compact #greeting-row {
        display: none;
    }

    #welcome-card.-compact #welcome-footer {
        height: 1;
        border-top: none;
    }

    #statusbar {
        dock: bottom;
        height: 1;
        background: #101010;
        padding: 0 1;
    }

    #status-keys {
        width: 1fr;
        color: __COLOR_TEXT_MUTED__;
        content-align: left middle;
    }

    #status-state {
        width: auto;
        color: __COLOR_TEXT_MUTED__;
        content-align: right middle;
    }

    .panel {
        border: round #3a3a3a;
        padding: 1;
        margin-bottom: 1;
    }

    #settings-identity {
        height: 1;
        margin: 0 1 1 1;
    }

    .action-card-line-tight {
        width: 1fr;
        height: 1;
        content-align: left middle;
    }

    #settings-cache-threshold-input {
        width: 8;
        height: 1;
        margin-top: 1;
        margin-right: 1;
        border: none;
        background: #101010;
        color: #d8d3c8;
        padding: 0 1;
    }

    #settings-cache-threshold-input:focus {
        border: none;
        background: #241c12;
        color: #d8d3c8;
    }

    #settings-cache-threshold-input > .input--cursor {
        background: transparent;
        color: __COLOR_BRAND__;
        text-style: underline;
    }

    #settings-cache-threshold-input > .input--selection {
        background: #3a2c18;
    }

    #settings-cache-threshold-input > .input--placeholder {
        color: __COLOR_TEXT_MUTED__;
    }

    .row {
        height: auto;
        margin-bottom: 1;
    }

    .row > Label {
        width: 18;
        text-style: bold;
    }

    #dashboard-panel {
        height: auto;
        margin-bottom: 0;
    }

    #dashboard-deck {
        height: 7;
    }

    #dashboard-tiles {
        width: auto;
        height: 7;
    }

    .dashboard-tile-row {
        width: auto;
        height: 3;
        padding-left: 1;
    }

    #dashboard-primary-tiles {
        margin-bottom: 1;
    }

    .dashboard-tile {
        width: 23;
        height: 3;
        min-width: 13;
        margin: 0 1 1 0;
        padding: 0 1;
        content-align: left middle;
    }

    .tile-clickable {
        background: #262626;
        color: #d8d3c8;
    }

    .tile-clickable:hover {
        background: #333231;
    }

    .tile-clickable:focus {
        background: #333231;
    }

    #monitor-toggle {
        width: 1fr;
        min-width: 14;
        height: 7;
        margin: 0 1 0 1;
        content-align: center middle;
    }

    .toggle-start {
        background: #33230f;
        color: __COLOR_BRAND__;
    }

    .toggle-start:hover {
        background: #46301a;
    }

    .toggle-start:focus {
        background: #46301a;
    }

    .toggle-stop {
        background: #2e1a1a;
        color: __COLOR_ERROR__;
    }

    .toggle-stop:hover {
        background: #3d2222;
    }

    .toggle-stop:focus {
        background: #3d2222;
    }

    #activity-header {
        height: 1;
        margin-top: 1;
    }

    #activity-title {
        width: auto;
        color: #8f8f8f;
        text-style: bold;
    }

    #activity-hint {
        width: 1fr;
        text-align: right;
        color: #5a5a5a;
    }

    #activity-tail {
        /* Initial height; App.activity_tail_lines() overrides it at runtime, stepping the
           tail down before the mascot is hidden on short windows. */
        height: 4;
    }

    #event-log {
        height: 1fr;
        min-height: 6;
        background: transparent;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
        scrollbar-color: #343434;
        scrollbar-color-hover: #4a4a4a;
        scrollbar-color-active: #5f5f5f;
        scrollbar-background: #111111;
        scrollbar-background-hover: #111111;
        scrollbar-background-active: #111111;
        scrollbar-corner-color: #111111;
    }

    #event-log-toolbar {
        height: 1;
        margin-top: 1;
        margin-bottom: 0;
    }

    #event-log-title {
        width: 1fr;
        content-align: left middle;
    }

    #event-log-rule {
        height: 1;
        margin-bottom: 1;
        border-top: solid #262626;
    }

    .log-filter-separator {
        width: auto;
        margin: 0 1;
        content-align: center middle;
        color: #777777;
    }

    .log-filter-option {
        width: auto;
        height: 1;
        content-align: center middle;
        color: __COLOR_TEXT_MUTED__;
        background: transparent;
    }

    .log-filter-option:hover {
        color: __COLOR_BRAND__;
    }

    .log-filter-selected {
        color: __COLOR_BRAND__;
        text-style: bold;
    }

    #wallet-actions {
        height: auto;
        margin-bottom: 1;
    }

    .wip-note {
        border: round #3a3a3a;
        border-title-color: __COLOR_BRAND__;
        color: __COLOR_TEXT_MUTED__;
        padding: 0 1;
        margin: 1 0 1 0;
    }

    .modal-action-tile {
        width: auto;
        min-width: 6;
        height: 1;
        margin-right: 2;
        padding: 0 1;
        content-align: center middle;
        border: none;
        text-style: bold;
        color: __COLOR_BRAND__;
        background: transparent;
    }

    .modal-action-tile:hover {
        border: none;
        color: #ffc389;
        background: transparent;
    }

    .modal-action-destructive {
        border: none;
        color: __COLOR_ERROR__;
    }

    .modal-action-destructive:hover {
        border: none;
        color: #f2c0c0;
        background: transparent;
    }

    .action-card .modal-action-tile {
        margin-top: 1;
    }

    .action-card {
        height: auto;
        border: round #3a3a3a;
        border-title-color: #d8d3c8;
        border-title-style: bold;
        padding: 0 1;
        margin-bottom: 1;
    }

    .action-card-info {
        width: 1fr;
        height: 3;
        content-align: left middle;
    }

    .action-card-line {
        width: 1fr;
        height: 1;
        content-align: left middle;
        margin-bottom: 1;
    }

    .action-card-spacer {
        width: 1fr;
        height: 1;
    }

    #settings-about-card, #settings-storage-card, #settings-danger-card {
        padding: 1 1;
    }

    .danger-card {
        border: round __COLOR_ERROR__;
        border-title-color: __COLOR_ERROR__;
    }

    .cache-controls-row, .danger-actions-row {
        height: 3;
        align: left middle;
    }

    .cache-inline-label {
        width: auto;
        height: 3;
        content-align: center middle;
        color: __COLOR_TEXT_MUTED__;
        margin-right: 1;
    }

    #settings-status {
        color: __COLOR_TEXT_MUTED__;
        min-height: 1;
        margin-top: 0;
        margin-bottom: 1;
    }

    .stats-chip-row {
        height: 3;
        margin-top: 1;
        margin-bottom: 1;
    }

    .stats-chip {
        width: 1fr;
        height: 3;
        min-width: 12;
        margin-right: 1;
        padding: 0 1;
        background: #1c1b1b;
        content-align: left middle;
    }

    .stats-chart-title {
        width: auto;
        height: 1;
        color: #8f8f8f;
        text-style: bold;
        margin-bottom: 1;
    }

    .stats-chart {
        width: auto;
        height: auto;
        margin-bottom: 1;
    }

    .stats-chart-column {
        width: auto;
        height: auto;
    }

    #stats-col-weekday {
        width: 31;
        margin-right: 6;
    }

    #stats-col-hours {
        width: 1fr;
    }

    #stats-trends-title {
        margin-top: 1;
    }

    #stats-chart-row {
        height: auto;
        margin-top: 1;
    }

    #content {
        height: 1fr;
        overflow-y: auto;
        scrollbar-visibility: hidden;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 0;
        scrollbar-color: #343434;
        scrollbar-color-hover: #4a4a4a;
        scrollbar-color-active: #5f5f5f;
        scrollbar-background: #101010;
        scrollbar-background-hover: #101010;
        scrollbar-background-active: #101010;
        scrollbar-corner-color: #101010;
    }

    Input, Select {
        width: 60;
    }

    Button {
        margin-right: 1;
    }
    """.replace("__COLOR_BRAND__", COLOR_BRAND).replace("__COLOR_TEXT_MUTED__", COLOR_TEXT_MUTED).replace("__COLOR_ERROR__", COLOR_ERROR).replace("__COLOR_CAUTION__", COLOR_CAUTION)


MODAL_CSS = """
    DashboardModalScreen,
    ConfirmBuyModeScreen,
    MonitorModal,
    SpendCapModal,
    PollingModal,
    CredentialsModal,
    PACredentialsModal,
    SessionModal,
    SessionRefreshConfirmScreen {
        align: center middle;
        background: #101010 72%;
    }

    .modal-card {
        width: 68;
        max-width: 90%;
        height: auto;
        border: round #3a3a3a;
        border-title-color: __COLOR_BRAND__;
        border-title-style: bold;
        border-subtitle-color: #5f5f5f;
        background: #171717 96%;
        padding: 1 2 0 2;
    }

    .modal-heading {
        color: __COLOR_BRAND__;
        text-style: bold;
        margin-bottom: 1;
    }

    .modal-summary {
        border: round #3a3a3a;
        padding: 1;
        margin-bottom: 1;
    }

    .modal-note {
        color: #b8b2a8;
        margin-top: 1;
        margin-bottom: 1;
    }

    .modal-warning {
        color: #f0b45a;
        min-height: 1;
        margin-top: 1;
    }

    .modal-summary-row {
        height: 3;
        margin-bottom: 1;
    }

    .modal-section-title {
        color: __COLOR_BRAND__;
        text-style: bold;
        margin-top: 1;
        margin-bottom: 1;
    }

    .modal-info-tile {
        width: 1fr;
        min-width: 12;
        height: 3;
        margin-right: 1;
        padding: 0 1;
        background: #232323;
        content-align: left middle;
    }

    .modal-info-clickable:hover {
        background: #333231;
    }

    .modal-info-muted {
        color: #aaaaaa;
    }

    .preset-selected {
        background: #33230f;
    }

    .modal-info-wide {
        width: 2fr;
        min-width: 24;
    }

    .modal-row {
        height: auto;
        margin-bottom: 1;
        align: left middle;
    }

    .modal-row > Label {
        width: 18;
        text-style: bold;
        content-align: left middle;
    }

    .modal-actions {
        height: auto;
        border-top: solid #262626;
    }

    .modal-actions-spacer {
        width: 1fr;
        height: 1;
    }

    .modal-actions Button {
        border: none;
        height: 1;
        min-width: 6;
        padding: 0 1;
        margin-right: 2;
        content-align: center middle;
        text-style: bold;
        background: transparent;
        color: __COLOR_BRAND__;
    }

    .modal-actions Button:hover,
    .modal-actions Button:focus {
        border: none;
        background: transparent;
        color: #ffc389;
        text-style: bold;
    }

    .modal-actions Button.btn-danger {
        color: __COLOR_ERROR__;
    }

    .modal-actions Button.btn-danger:hover,
    .modal-actions Button.btn-danger:focus {
        background: transparent;
        color: #f2c0c0;
        text-style: bold;
    }

    .modal-actions Button.btn-quiet {
        color: #8f8f8f;
        text-style: none;
    }

    .modal-actions Button.btn-quiet:hover,
    .modal-actions Button.btn-quiet:focus {
        color: __COLOR_BRAND__;
        text-style: none;
    }

    .modal-actions Button:disabled {
        border: none;
        background: transparent;
        color: #5a5a5a;
        text-opacity: 60%;
    }

    .modal-action-tile {
        width: auto;
        min-width: 6;
        height: 1;
        margin-right: 2;
        padding: 0 1;
        content-align: center middle;
        border: none;
        text-style: bold;
        background: transparent;
        color: __COLOR_BRAND__;
    }

    .modal-action-tile:hover {
        border: none;
        background: transparent;
        color: #ffc389;
        text-style: bold;
    }

    #confirm-cancel, #cancel-refresh-session {
        border: none;
        background: transparent;
        color: #8f8f8f;
        text-style: none;
    }

    #confirm-cancel:hover, #cancel-refresh-session:hover {
        border: none;
        background: transparent;
        color: __COLOR_BRAND__;
    }

    .modal-card Input {
        border: none;
        background: #101010;
        color: #d8d3c8;
        width: 1fr;
        height: 1;
        padding: 0 1;
    }

    .modal-card Input:focus {
        border: none;
        background: #241c12;
        background-tint: transparent;
    }

    .modal-card Input > .input--cursor {
        background: transparent;
        color: __COLOR_BRAND__;
        text-style: underline;
    }

    .modal-card Input > .input--selection {
        background: #3a2c18;
    }

    .modal-card Input > .input--placeholder {
        color: #6f6f6f;
    }

    .modal-sentence {
        height: 3;
        align: left middle;
        margin-bottom: 1;
    }

    .modal-sentence Static, .modal-sentence Label {
        width: auto;
        height: 3;
        content-align: center middle;
        margin-right: 1;
        color: #d8d3c8;
    }

    .field-label {
        color: #8f8f8f;
        margin-top: 1;
    }

    .modal-sentence .sentence-dim {
        color: #6f6f6f;
    }

    .modal-sentence Input {
        width: 8;
        margin-top: 1;
        margin-right: 1;
    }

    .modal-sentence #spend-cap-input {
        width: 20;
    }

    .modal-card Select > SelectCurrent {
        border: none;
        background: #101010;
        color: #d8d3c8;
        padding: 0 1;
    }

    .modal-card Select:focus > SelectCurrent {
        border: none;
        background: #241c12;
        background-tint: transparent;
    }

    .modal-card Select > SelectOverlay {
        border: round #3a3a3a;
        background: #171717;
        color: #d8d3c8;
    }

    .modal-card Select > SelectOverlay > .option-list--option-highlighted {
        background: #33230f;
        color: __COLOR_BRAND__;
    }

    """.replace("__COLOR_BRAND__", COLOR_BRAND).replace("__COLOR_ERROR__", COLOR_ERROR)
