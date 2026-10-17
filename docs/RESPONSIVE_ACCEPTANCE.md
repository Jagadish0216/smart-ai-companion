# Responsive acceptance and fix pass

Branch: `dashboard-premium-v1`. Base commit: `b9133cc` (the accepted premium dashboard). All fixes are uncommitted. No product redesign, backend/schema change, deployment, commit or push.

## Findings and corrections

1. The global `body` was constrained to `100vh` with hidden overflow, while `.content-area` and Studio/log panels owned nested scroll regions. Regular pages now use the document scrollport, with bottom padding for the fixed navigation and safe area. Logs and Studio no longer create competing vertical scroll regions.
2. Assistant combined a viewport-sized wrapper, `height:100%` main area, a fixed 535px empty state, desktop header offsets, and fixed mobile header/nav calculations. On the live Pi at 1366×768, the composer extended below the screen. Header, tools and composer now occupy normal flex flow; every flexible ancestor has an explicit zero minimum height. Only the bounded transcript (or empty-state region when needed) scrolls internally.
3. Header content and status labels can wrap. The inherited mobile rule that hid status text was removed. More/history panels fit the available viewport, with their own bounded content scrolling.
4. The jump control could consume transcript height or cover the final response. It now sits in the existing tools row, using a compact mobile label, with no transcript overlap or change to the scrollport height. Its interaction no longer depends on transform-based absolute centering.
5. Grid tracks and flex children now allow shrinking. Labels, status values, conversation titles, document names, network identifiers and long resource strings wrap. The companion presence scales within its existing composition.
6. Memory's absolute upload/authentication block became part of the flowing heading row. Its list/detail view stacks at narrower widths. Device readiness rows stack when needed, with telemetry still reachable below.
7. Settings has a named, keyboard-focusable horizontal table scroller with readable minimum column widths. Long cell text wraps; the table cannot widen the document.
8. Logs use a two-column mobile metadata layout followed by component/message lines. Filters continue to work without replacing that grid layout. Studio controls wrap and use the page scrollport; the phone preview/status spacing is contained.
9. VisualViewport provides only the available height. CSS accounts for bottom navigation exactly once. Short-height rules collapse the decorative empty-state presence; resizing follows the latest message only when the reader was already following it.

## Validation environments

**Live Pi baseline:** `http://smart-ai-companion.local:8000` was reachable and audited with Playwright. No write requests were permitted. The deployed, previously accepted build flagged 77 of 120 audit cases; these include old scroll-owner policies as well as actual clipping, occlusion and keyboard-height failures. Its empty Assistant clipping was captured at 1366×768 and 375×667. Exact per-route issues are in `screenshots/responsive/live-pi-baseline/responsive-validation.json`.

**Fixed candidate:** isolated local previews at `127.0.0.1:8004` (normal mode) and `127.0.0.1:8005` (setup mode), using migrated temporary copies of the normal database. The original database and Pi were not migrated, seeded, reconfigured or deployed. A temporary local staff account and long configuration/log rows exercised populated layouts. Browser-only conversation/network/text fixtures were never saved to the product database. Application POST/DELETE requests were blocked; streaming and voice used controlled in-browser responses.

The fixed candidate passed **120 / 120** primary route/viewport cases and **80 / 80** supplementary cases: 40 long-text cases, 30 local staff-control cases, and 10 setup-mode cases. The live Pi is still running its old build; the candidate fixes require a later deployment after review.

| Viewport (100% zoom) | Live deployed baseline | Local fixed candidate |
| --- | --- | --- |
| 1440 × 960 | 4 / 12 flagged | 12 / 12 pass |
| 1366 × 768 | 6 / 12 flagged | 12 / 12 pass |
| 1280 × 720 | 6 / 12 flagged | 12 / 12 pass |
| 1024 × 768 | 7 / 12 flagged | 12 / 12 pass |
| 768 × 1024 | 4 / 12 flagged | 12 / 12 pass |
| 430 × 932 | 10 / 12 flagged | 12 / 12 pass |
| 430 × 844 | 10 / 12 flagged | 12 / 12 pass |
| 390 × 844 | 10 / 12 flagged | 12 / 12 pass |
| 375 × 667 | 10 / 12 flagged | 12 / 12 pass |
| 360 × 800 | 10 / 12 flagged | 12 / 12 pass |

Primary routes/states: `/`, `/assistant/?new=1`, `/assistant/` with a controlled 24-message conversation, `/conversations/`, `/knowledge/`, `/device/`, `/resource-manager/`, `/network/`, `/logs/`, `/settings/`, `/studio/`, and `/setup/` under its access rules. Normal mode correctly returns 404 for setup; isolated setup mode returns 200 at every viewport.

The audit now checks horizontal overflow, vertical clipping, sibling text/control collisions, hidden status labels, root versus nested scroll ownership, occluded/unreachable controls, fixed-navigation coverage, table scroll strategy and column readability, long text, More/history panel bounds, and usable conversation height. Decorative contour/face overlap is excluded.

Every active-conversation case guarantees overflow and programmatically checks that `scrollTop=0` exposes the first message and `scrollTop=scrollHeight` exposes the final message above the composer. The final message is also checked against the jump/history controls and navigation. Wide code lines and unbroken user/link text are included. Focused input is checked before/after reducing mobile viewport height to 420px. Screenshots were visually inspected, including the short desktop and short-phone Assistant layouts.

Interaction fixtures passed: streaming preserves reading position; Jump to latest is visible/clickable and reveals current output; Interrupt restores input; Shift+Enter creates a newline; microphone enters Listening; the existing voice response and sources render; playback enters Speaking and interruption returns to Idle. History Escape restores focus. Required input/voice/network/Knowledge contracts remain intact.

Final browser runtime errors: **0**. Failed static assets: **0**. Failed candidate route/viewport cases: **none**. All recorded viewport scales are 1.

## Django checks

- `venv/Scripts/python.exe manage.py check`: no issues (0 silenced).
- `venv/Scripts/python.exe manage.py test dashboard assistant.tests_streaming assistant.tests_voice system.tests_provisioning.SetupPortalTests --noinput`: **106 tests passed in 20.849 seconds**. The complete 228-test suite was not repeated.
- Backend files, models/migrations, hardware/enclosure, enclosure.zip and firmware have no diff.

## Screenshots and machine-readable results

Exactly 18 acceptance screenshots are under `docs/screenshots/responsive/local-fixed/`:

- `home-1440x960.png`
- `assistant-empty-1440x960.png`
- `assistant-active-1440x960.png`
- `home-1366x768.png`
- `assistant-empty-1366x768.png`
- `assistant-active-1366x768.png`
- `memory-1366x768.png`
- `device-1366x768.png`
- `home-390x844.png`
- `assistant-empty-390x844.png`
- `assistant-active-390x844.png`
- `memory-390x844.png`
- `device-390x844.png`
- `logs-390x844.png`
- `settings-390x844.png`
- `studio-390x844.png`
- `assistant-empty-375x667.png`
- `assistant-active-375x667.png`

`responsive-validation.json` records every route, viewport, issue list, scroll measurements, keyboard simulation, supplementary case, interaction result and screenshot. `acceptance-matrix.csv` provides the same case outcomes in a compact table. Three failure screenshots remain under `live-pi-baseline/` as deployment-baseline evidence. Prior top-level screenshots/results remain historical outputs from the earlier dashboard task.

## Reproduction

The audit uses an installed Playwright module and Edge by default; set `PLAYWRIGHT_MODULE` to its package path if it is not on Node's module path. Run from the repository root.

Start `venv/Scripts/python.exe scripts/dashboard_responsive_preview.py --port 8004`. In another terminal start the same helper with `--setup --port 8005`. The helper binds only to loopback, backs up SQLite into a temporary directory, migrates/seeds only that copy, and prints the path to a temporary local staff cookie file. It cleans its temporary files on exit.

Set `DASHBOARD_BASE_URL=http://127.0.0.1:8004`, `DASHBOARD_SETUP_BASE_URL=http://127.0.0.1:8005`, and `DASHBOARD_STAFF_COOKIE_FILE` to the normal preview's printed cookie-file path, then run `node scripts/dashboard_browser_check.cjs`. Staff and active setup-mode fixtures are restricted to loopback hosts. Omit those optional variables for a public-page audit. Use `DASHBOARD_AUDIT_BASELINE=1` and `DASHBOARD_AUDIT_LABEL=live-pi-baseline` to record an existing deployment without claiming candidate acceptance. `DASHBOARD_AUDIT_FUNCTIONAL_ONLY=1` runs just interaction fixtures for diagnosis.

## Remaining limits

The current Pi was inspected but not updated. Physical phone keyboards were approximated with focused-input viewport-height reduction; on-device iOS/Android keyboard checks remain useful after deployment. Real microphone/speaker/TFT operation and live inference were not exercised by the layout fixtures. No operational readiness was fabricated in application code. No new visual direction, fonts, icons, dependencies or network polling was introduced.

`git diff --check`: clean. All changes remain uncommitted for review.

## Final working tree

```text
 M scripts/dashboard_browser_check.cjs
 M static/css/app-shell.css
 M static/css/assistant.css
 M static/css/dashboard.css
 M static/css/style.css
 M static/js/app-shell.js
 M templates/dashboard/assistant.html
 M templates/dashboard/knowledge.html
 M templates/dashboard/logs.html
 M templates/dashboard/settings.html
 M templates/dashboard/studio.html
?? docs/RESPONSIVE_ACCEPTANCE.md
?? docs/screenshots/responsive/
?? scripts/dashboard_responsive_preview.py
```

No files staged. NO COMMIT. NO PUSH.
