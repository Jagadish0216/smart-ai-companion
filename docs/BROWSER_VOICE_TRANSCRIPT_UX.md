# Browser voice transcript drafts

Implemented on `voice-transcript-ux`; changes remain uncommitted. No Pi deployment or physical microphone/audio acceptance has been performed.

## Changed files

- `assistant/views.py`: shared browser conversion/STT helper, STT-only and TTS-only endpoints, timing instrumentation.
- `assistant/urls.py`: additive API routes.
- `templates/dashboard/assistant.html`: draft and voice-origin state, explicit statuses, post-stream playback and cancellation.
- `static/css/assistant.css`: one visibility condition so the draft indication appears in the existing idle header.
- `assistant/tests_browser_voice.py`: focused API, CSRF, validation and persistence tests.
- `scripts/browser_voice_check.cjs`: focused mocked browser regression tests.
- `scripts/dashboard_browser_check.cjs`: updated existing voice fixture for the new draft flow.
- This report and generated screenshots/results under `docs/screenshots/voice-transcript/` and `docs/screenshots/responsive/voice-transcript/`.

Pre-existing untracked `docs/live-pi-responsive-audit.log` and `docs/screenshots/responsive/live-pi-functional-recheck/` were retained. Final `git diff --check` passed. Hardware/enclosure, firmware, standalone voice code, model configuration, RAG and schema files have no diff.

## Cause and implementation

The browser previously posted recordings to a combined STT → inference → TTS endpoint and waited for all stages before displaying anything. The browser now requests STT alone and places the exact returned transcription into `#chat-input`. Recording displays Listening; STT displays Transcribing…; the draft returns to idle readiness with “Voice transcript — review or send.” The textarea is enabled and focused with the cursor at the end. No message, conversation ID, inference or TTS request is created during dictation.

The draft can be edited, deleted or submitted. Recording with a nonempty textarea is refused with a message, preserving the text. Clearing the draft allows recording again. New Chat and selecting another conversation clear the current draft and its voice-origin flag. Long drafts use the existing bounded textarea scrolling; the established composer layout remains intact.

Send uses the existing `sendMessage()` NDJSON streaming flow, including conversation IDs, sources, History, Jump to latest, request/idle timeouts and Interrupt. Edited voice drafts retain their voice origin; clearing the draft resets it. Ordinary typed messages never request speech. The single completed response is sent to a TTS-only endpoint after streaming completes. There is no second inference and no extra database message.

## API

Both new endpoints are POST-only and explicitly CSRF protected.

| Endpoint | Input | Success output | Work performed |
| --- | --- | --- | --- |
| `/api/assistant/voice/transcribe-only/` | Multipart `audio` | `{"transcription":"exact Whisper output"}` | Existing upload validation, browser MIME handling, FFmpeg and configured STT only |
| `/api/assistant/voice/synthesize/` | JSON `{"text":"completed response"}` | `{"audio_base64":"WAV bytes encoded as base64"}` | Existing configured TTS provider only; Piper when configured |

Audio remains limited to 5 MiB. Missing/empty/oversized uploads fail with 400; existing conversion/provider error behavior is retained. Temporary conversion files are cleaned up. Synthesis accepts nonblank strings up to 12,000 characters, rejects invalid input with 400, and reports unavailable speech with 503. Successful responses use `Cache-Control: no-store`.

The legacy `/api/assistant/voice/transcribe/` retains its complete STT/inference/TTS response contract and uses the same private conversion/STT helper. Its existing API tests pass. No models, migration files, model routing, RAG, settings, hardware paths or standalone voice-loop code changed.

TTS failures, rejected browser playback and synthesis cancellation leave the displayed response intact. Interrupt stops active playback and prevents late audio from starting. Browser cancellation does not guarantee cancellation of server-side Whisper/Piper work already executing; existing provider timeouts still bound that work. Responses over the speech text limit retain their full text and report audio unavailable.

Microphone permission and browser secure-context requirements remain unchanged. The presentation tests mock microphone capture and audio playback; they do not establish that microphone access works over the Pi's HTTP hostname or certify physical audio quality.

## Timing

No speech or response text is included in the new timing messages.

- Browser console `upload_conversion_stt_ms`: STT request round trip, from upload initiation to parsed transcript.
- STT Network response `Server-Timing`: `conversion` covers server-side upload saving and FFmpeg; `whisper` covers provider acquisition and transcription. Network upload precedes these measurements and is visible in browser request timing.
- Browser console `ttft_ms`: Send to first streamed text delta. Existing stream metadata also retains backend `ttft_ms`.
- Browser console `tts_request_ms`: post-stream synthesis round trip. Synthesis response `Server-Timing: tts;dur=...` reports server synthesis duration.
- Server INFO messages expose the same conversion/Whisper/TTS durations when INFO logging is enabled.

These are instrumentation points, not measured physical-Pi latency claims.

## Validation

- `python manage.py check`: passed.
- 43 relevant Django tests passed: new browser voice APIs, legacy voice API, streaming API/service/frontend checks and dashboard tests.
- 46 focused Playwright assertions passed, including exact textarea contents, visible STT/draft status, no automatic submission or conversation ID, editable submissions, single message pair, source rendering, rerecording protection, draft deletion, New Chat, typed-message behavior, streaming interruption, TTS failure, playback rejection, STT cancellation, synthesis cancellation and playback Interrupt.
- 120/120 responsive route/viewport cases, 40/40 supplementary layout cases and 10/10 updated comprehensive interaction checks passed. No JavaScript exceptions or failed static HTTP responses in that audit.
- Draft and active composer checks passed at 1366×768, 375×667 and 390×844, with 420px keyboard-height simulation for mobile draft and active states.
- Tests used a migrated temporary database copy and browser-controlled audio/API fixtures. The normal database and live Pi were not modified. No unrelated hardware acceptance suite ran.

Reproduce the Django tests:

```powershell
python manage.py check
python manage.py test assistant.tests_browser_voice assistant.tests_voice.VoiceAPITests assistant.tests_streaming.StreamingAPITests assistant.tests_streaming.StreamingServiceTests assistant.tests_streaming.StreamingFrontendStructureTests dashboard --noinput
```

For browser checks, start `python scripts/dashboard_responsive_preview.py --port 8004` in another terminal. With Playwright available (or `PLAYWRIGHT_MODULE` pointing at its installed module directory):

```powershell
node scripts/browser_voice_check.cjs
$env:DASHBOARD_BASE_URL='http://127.0.0.1:8004'
$env:DASHBOARD_AUDIT_LABEL='voice-transcript'
node scripts/dashboard_browser_check.cjs
```

Evidence: `docs/screenshots/voice-transcript/results.json`, draft screenshots at 1366×768 / 375×667 / 390×844, and `docs/screenshots/responsive/voice-transcript/responsive-validation.json` with the full matrix and 18 responsive screenshots.

## Pi deployment and manual acceptance

The local changes have **not** been committed or pushed. The following deployment commands become usable after the reviewed branch has been committed and published by the user. They are instructions only; they have not been executed.

```bash
ssh jagadish@smart-ai-companion.local
cd /home/jagadish/apps/smart-ai-companion
git status --short
# Stop if the Pi checkout has local changes; preserve/review them first.
git fetch origin voice-transcript-ux
git switch voice-transcript-ux
git pull --ff-only origin voice-transcript-ux
./venv/bin/python manage.py check
./venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart smart-ai-companion.service
sudo systemctl status --no-pager smart-ai-companion.service
git branch --show-current
git rev-parse HEAD
```

No new migrations or dependency installation are required for this change. This does not resolve unrelated pending migrations on an existing Pi database.

Hard-refresh the Assistant page in the browser with microphone permission available. Use the existing deployment URL `http://smart-ai-companion.local:8000/assistant/?new=1` where the browser already permits microphone access; otherwise use the deployment's established secure microphone-access setup.

1. Tap the microphone and speak **“What is Raspberry Pi?”**
2. Tap again to stop. Verify **Transcribing…** appears, without an AI request or conversation message.
3. Verify the transcript appears in the textarea **before any AI response**, the textarea has focus and Send is enabled.
4. Edit the transcript. Try recording again and verify the draft is preserved. Send or clear it before recording again.
5. Press Send. Verify one user message appears and the normal AI response streams once, with normal source/history behavior.
6. Verify speech starts only after the streamed response completes. If speech is unavailable, verify the full text remains visible with the audio-unavailable message.
7. Press Interrupt during playback. Also test interruption during STT, streaming and pending synthesis; verify no late transcript/audio appears after cancellation.
8. Repeat at 1366×768 with 100% browser zoom and on a mobile browser with its native keyboard. Check the textarea, transcript and final response remain reachable above navigation.
9. Send an ordinary typed question and verify it does not speak. Inspect Network `Server-Timing` and browser console timing entries to distinguish upload/conversion, Whisper, AI TTFT and TTS.

Physical acceptance remains pending this manual test.
