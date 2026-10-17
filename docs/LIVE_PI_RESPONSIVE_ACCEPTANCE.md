# Live Pi responsive acceptance

**ACCEPTED** for responsive layout and browser presentation at http://smart-ai-companion.local:8000.

- Route/viewport cases: **120/120 passed**, zero layout failures.
- Supplementary long-content layout cases: **40/40 passed**.
- Streaming, scrolling, input and voice-presentation checks: **8/8 passed**.
- Application JavaScript exceptions: **0**. Failed static HTTP responses: **0**. Additional request-failure tracing observed browser-cancelled `home-DeviceIcon.svg` loads (`net::ERR_ABORTED`) while the shell replaces navigation icon URLs. The SVG returns HTTP 200 and is successfully loaded elsewhere; no missing static assets were found. Raw traces are retained in `asset-network-recheck.json` and `asset-diagnosis.json`.
- Deployed commit: `3732d94a61147b310472c20d4991bea563279bb5`, branch `dashboard-premium-v1`, confirmed by the user from the Pi. Five served CSS/shell-JavaScript files independently match the committed source. SSH access was unavailable.

## Coverage

All twelve route/states at all ten viewport sizes: 1440x960, 1366x768, 1280x720, 1024x768, 768x1024, 430x932, 430x844, 390x844, 375x667, 360x800.

Routes: `/`, `/assistant/?new=1`, `/assistant/`, `/conversations/`, `/knowledge/`, `/device/`, `/resource-manager/`, `/network/`, `/logs/`, `/settings/`, `/studio/`, `/setup/`. Setup correctly returns 404 outside setup mode at all ten sizes. Production access modes were not changed; local-only staff and active-provisioning fixtures were not repeated against production.

No detected meaningful text/control overlap, horizontal document overflow, clipped page content or navigation-covered final content. Normal pages use document scrolling. Memory, Device readiness, Logs, Settings tables and Studio controls pass mobile geometry checks. Browser scale is 1 (100%).

## Assistant

A controlled 24-message fixture is rendered using actual live Pi HTML/CSS. First and final messages are reachable at every viewport, transcript height remains usable, and the composer stays above mobile navigation without covering the final response. History fits and restores focus. 1366x768, 375x667 and 390x844 pass. Focused-input keyboard-height simulation passes at 420px viewport height.

Streaming preserves a scrolled-up reading position; Jump to latest exposes current output; interruption restores input; Shift+Enter preserves multiline input. Mocked voice enters Listening, renders the response and returns to Idle on interruption.

The original interaction harness failed when assigning `getUserMedia` because `navigator.mediaDevices` is unavailable on this HTTP origin. This was a test-harness exception, not an application exception. The unchanged script was re-executed in memory with a browser-only `mediaDevices` mock and all eight checks passed. Both original and recheck JSON are retained. Real microphone capture, native phone keyboards and physical audio hardware are not certified by these presentation tests.

## Screenshots and evidence

- [Assistant active 1366x768](screenshots/responsive/live-pi-acceptance/assistant-active-1366x768.png)
- [Assistant active 375x667](screenshots/responsive/live-pi-acceptance/assistant-active-375x667.png)
- [Home 390x844](screenshots/responsive/live-pi-acceptance/home-390x844.png)

18 screenshots retained. Per-case results: `screenshots/responsive/live-pi-acceptance/responsive-validation.json` and `acceptance-matrix.csv`. Functional recheck: `screenshots/responsive/live-pi-functional-recheck/responsive-validation.json`. Deployment fingerprints: `deployment-verification.json`. Additional static-network check: `asset-network-recheck.json`.

No source files changed. No production data writes, migrations, commits or pushes performed. Only local audit evidence was generated.
