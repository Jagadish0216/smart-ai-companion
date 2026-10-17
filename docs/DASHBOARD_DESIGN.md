# Dashboard design

Approved source: https://www.figma.com/design/Eq0gX01PplpM303siRTDyV?node-id=2-138

## Scope and visual philosophy

The existing Django application implements the approved Stillpoint composition: an 88px rail, graphite surfaces, restrained aqua accents, open typography, and the physical companion's minimal face. Home prioritizes conversation; Device keeps telemetry secondary. No frontend framework, routing rewrite, hardware or firmware changes were introduced.

Primary references: Home `2:138`, Assistant empty `2:194`, Assistant conversation `2:239`, Memory `2:293`, Device `2:383`, mobile Assistant `2:438`. Companion variants, desktop/mobile composers, history drawer and More menu were inspected through the connected Figma integration. Runtime data replaces example Figma content.

Secondary pages share tokens, navigation, surfaces, typography and focus behavior: Conversations, Resource Manager, Network, Logs, Settings, Companion Studio. Wi-Fi Setup retains its separate provisioning shell with the coordinated palette.

## Tokens and architecture

| Token | Dark value |
| --- | --- |
| Base | `#101315` |
| Surface | `#191E21` |
| Inset | `#0C1012` |
| Primary text | `#EDF1EF` |
| Secondary text | `#A5AFB4` |
| Accent | `#76E2D1` |
| Border | `#30383C` |
| Success / warning / error | `#89CBA4` / `#E6BD76` / `#F08C8C` |

Spacing uses 8, 16, 24, 32, 48 and 64px steps; composer radius is 20px, primary actions 28px, and presence radius scales from 52px at 320px width. Focus uses a 2px aqua outline with 3px offset. The existing light-mode preference remains functional with a coordinated accessible light palette.

- `static/css/design-system.css`: tokens, fonts, shared controls, motion/accessibility.
- `static/css/app-shell.css`: desktop rail, More dialog and mobile navigation.
- `static/css/dashboard.css`: Home, Device, Memory and secondary page refinements.
- `static/css/assistant.css`: extracted legacy chat layout plus approved presentation.
- `templates/components/presence.html`: reusable CSS face and local Figma contour assets.
- `static/js/app-shell.js`: navigation variants, viewport keyboard handling, local-mode copy.
- `static/js/companion-ui.js`: small presentation-only companion state controller.
- `static/js/assistant-ui.js`: safe text formatting and real response source metadata.
- `static/js/dashboard-ui.js`: existing metrics/service API presentation.
- `static/js/knowledge.js`: existing upload/action contracts, searchable list and contextual details.

The assistant's networking remains in its Django template to retain server-configured request and stream-idle timeout constants and existing regression tests. Studio keeps its existing expression/customization controller, with reduced-motion support.

## Companion and operational states

IDLE, LISTENING, THINKING, SPEAKING, LOCAL MODE and ERROR use the approved restrained geometry. Processing maps to thinking; alert maps to error. Local mode preserves the calm face. Speaking is tied to actual playback, not text streaming; streamed text displays Responding. There is no synthetic microphone waveform or fabricated audio envelope. Reduced motion disables decorative transitions and animations.

Home's indexed count comes from the document database. AI readiness checks the existing service-health endpoint when local AI is configured; mock configuration is identified as development. Internet state remains separate from inference readiness. OFFLINE is presented as Local mode without asserting that inference is ready.

Device uses existing metrics. Combined audio telemetry cannot independently prove microphone or speaker health, so these show Not configured or Status unavailable. Unknown display and connection states are never promoted to Ready. Failures replace telemetry with unavailable copy. Metrics retain a five-second cadence with overlapping requests prevented; no new recurring service-health polling was added.

## Assistant, Memory and accessibility

Streaming retains its POST endpoint, CSRF, AbortController, request timeout, idle timeout and interrupted-response handling. Scrolling follows only while the reader is near the bottom; Jump to latest restores following. Interrupt cancels the browser request or stops playback. Server inference cancellation depends on the existing backend's disconnect behavior.

Voice uses the existing endpoint's transcription, generated response and WAV payload without resubmitting the transcription. Browser permission and playback restrictions are handled with visible errors. Text remains available when speech fails. History uses real stored conversations, supports new chat and active selection, and returns focus on Escape. Historical source metadata is unavailable in the current history API; sources are displayed for current responses when supplied.

Memory retains staff-only upload, drop, indexing, reindex, online-source sync and deletion, with original CSRF behavior. Search and filters operate on fetched records. Upload progress is actual transferred bytes, never a fictional indexing percentage. Configuration controls without save support are now informational/read-only instead of claiming simulated success.

All new text/source rendering uses DOM text nodes; links permit only HTTP(S). Keyboard focus, skip navigation, labeled controls, modal focus handling, live status copy and 44px composer touch targets are included. Below 600px navigation moves to the bottom. Assistant uses dynamic viewport height; keyboard-open mode collapses the large presence. Layout was checked at 390px and 430px. System fonts replace Google Fonts; precise typography differs from Figma where its fonts are unavailable locally.

## Offline and performance

No remote fonts, scripts, styles or image requests are required. The existing Lucide library is pinned locally at 0.468.0 with its license; exported Figma SVG assets are local. No new icon library, chart dependency, WebGL, video or continuous 60fps decoration. Original routes and backend APIs remain intact. The only backend file changed is `dashboard/views.py`, adding an indexed-document count and static Home metric labels.

## Verification and limitations

`venv/Scripts/python.exe manage.py check`: no issues (0 silenced).

`venv/Scripts/python.exe manage.py test dashboard assistant.tests_streaming assistant.tests_voice knowledge_base system.tests_provisioning --noinput`: **228 tests passed**, 20.579 seconds. Existing dashboard-only run: **11 passed**. Missing local virtualenv dependencies were installed at the already-declared versions: WhiteNoise 6.7.0 and pypdf 6.19.0; requirements were unchanged.

The original local SQLite database lacks the `system_networkprovisioningstate` table. Browser verification used a migrated copy, preserving the original database. Running the normal app against the original database still requires its pending migrations. No deployment configuration was changed.

Headless Edge rendered all ten requested routes with HTTP 200, using the existing records in the copied database. Screenshots in `docs/screenshots/` cover Home, empty and active Assistant, Memory, Device, and empty/active Assistant at 390px and 430px. Browser checks cover overflow, asset loading, JS errors, history focus, More dismissal, multiline input, search, 44px controls, and a controlled streaming fixture. Exact results are recorded in `browser-validation.json`.

Physical Raspberry Pi audio, TFT/ESP32 connectivity, live Ollama generation and real microphone capture were not validated on this Windows host. Browser fixtures verify UI behavior only. The simplified device face is not an enclosure render. No screenshot claims that unavailable services or hardware are ready.

All changes remain uncommitted. No branch switch, merge, commit or push. Hardware/enclosure, enclosure.zip and firmware remain untouched.

The browser suite is retained as `scripts/dashboard_browser_check.cjs`. Run it from the repository root with Playwright available, setting `DASHBOARD_BASE_URL` to a local isolated preview and optionally `PLAYWRIGHT_MODULE` to an installed Playwright module path. It never submits a real assistant request: streaming and voice behavior use explicit browser fixtures. Screenshots of conversations use existing records; no example conversations are seeded into the application. Voice fixture validation passed for recording, transcription, response, sources, playback state and interrupt. Real audio capture/playback remains unverified.

Final `git diff --check`: clean.

## Final source-control status

Branch: `dashboard-premium-v1`. No files staged. Protected hardware/firmware paths have no diff.

```text
 M dashboard/views.py
 M static/css/setup.css
 M static/css/style.css
 M static/js/companion.js
 M static/js/knowledge.js
 M templates/base.html
 M templates/dashboard/assistant.html
 M templates/dashboard/conversations.html
 M templates/dashboard/device.html
 M templates/dashboard/index.html
 M templates/dashboard/knowledge.html
 M templates/dashboard/settings.html
 M templates/dashboard/studio.html
?? .final-report.py
?? docs/DASHBOARD_DESIGN.md
?? docs/screenshots/
?? scripts/dashboard_browser_check.cjs
?? static/css/app-shell.css
?? static/css/assistant.css
?? static/css/dashboard.css
?? static/css/design-system.css
?? static/img/
?? static/js/app-shell.js
?? static/js/assistant-ui.js
?? static/js/companion-ui.js
?? static/js/dashboard-ui.js
?? static/vendor/
?? templates/components/navigation.html
?? templates/components/presence.html
```
