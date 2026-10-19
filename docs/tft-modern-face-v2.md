# TFT V2.2 modern minimalist face

Branch: `tft-modern-face-v2`, based on `tft-audio-level-v1`.
Prepared and physically accepted on 2026-10-19. Finalized for the feature branch;
no merge into main is part of this work.

## Physical acceptance

The owner confirmed the following on 2026-10-19 using the actual Pi/ESP32/TFT:

- READY animation passed.
- THINKING animation passed.
- SPEAKING reacted smoothly to actual audio LEVEL.
- Audio was clear without stuttering.
- QUERY, MODE and captions passed.
- The interaction returned to READY without flicker.

These are owner-reported physical results, separate from the automated checks
below. Finalization makes no changes to the accepted firmware appearance.

## Implementation

Black background, soft cyan rounded eyes, closer eye spacing, restrained whole-eye
gaze and rounded blinks. READY uses a shallow smile. THINKING uses three small
traveling beads and slow upward gaze, with no speaking motion. SPEAKING uses a
continuous compact amplitude shape instead of four hollow mouth categories.
LISTENING retains the microphone meter and subtle eye response. All eleven states
and their readable default messages remain available.

Local mouth composition removes its visible erase stage when sprite allocation
succeeds. Eye, text and mouth caches avoid unchanged redraws. Active expressions
target 50 ms; LEVEL keeps its independent 100 ms quarter-distance filter and
1200 ms freshness. Total sprite pixel storage is 77,382 bytes, allocated once,
with independent direct-render fallbacks. Hardware SPI transfers determine actual
frame rate. The owner accepted the observed animation and return-to-READY
behavior; the report does not establish measured allocation headroom or
allocation-failure fallback behavior.

## Files

- `hardware/esp32/companion_display/companion_display.ino`: V2.2 startup banner only.
- `hardware/esp32/companion_display/companion_face.cpp`: expressions, continuous
  mouth, buffered mouth/thinking composition, conditional QUERY header.
- `hardware/esp32/companion_display/companion_face.h`: sprite and rendering caches.
- `hardware/esp32/companion_display/companion_theme.h`: palette, eye geometry, timing.
- `hardware/esp32/companion_display/display_config.h`: face placement and bounds.
- `hardware/esp32/companion_display/README.md`: updated behavior, memory and bench checks.
- `docs/tft-modern-face-v2-preview.png`: illustrative state contact sheet; font
  rendering is approximate, not a TFT capture or firmware framebuffer capture.
- This implementation/validation note.

## Compatibility and validation

No Pi-side changes. Inspected the serial provider, state management command,
voice/display tests and Phase B integration notes. The complete `.ino` command
handler and bounded serial loop are unchanged. STATE, QUERY, MODE, TEXT, LEVEL,
CLEAR commands, limits, acknowledgements and malformed-line recovery retain
their implementation. State reset and query/mode/caption lifecycle functions
are unchanged. READY clears interaction metadata and amplitude; TEXT updates
leave the face animation and supplied MODE intact.

Pi regression command run in the existing Windows virtual environment:

```bash
python manage.py test assistant.tests_audio_level assistant.tests_display assistant.tests_capture_vad assistant.tests_hands_free assistant.tests_alsa_stream assistant.tests_voice_loop assistant.tests_voice_stream assistant.tests_alsa_audio --noinput
```

Finalization recheck: 340 tests in 7.874 seconds, 339 passed, one skipped,
no failures/errors.
The skip is the existing real nonblocking pipe test requiring Linux. Coverage
includes exact serial writes, amplitude conversion, keepalive/reset, query/mode
lifecycle, captions, capture/playback integration and nonfatal display failures.
These tests mock hardware; they do not execute the firmware parser on an ESP32.

Arduino CLI compile target: `esp32:esp32:esp32doit-devkit-v1`, installed ESP32 core
3.3.12 and installed TFT_eSPI. Installed `User_Setup.h` SHA-256 matches the
repository setup (ILI9488, existing pins/rotation, 10 MHz). No library setup was
modified. Initial sandbox compile failed to launch the ESP32 compiler with a
Windows DLL relocation error; the compile succeeded outside the sandbox.
Final revision compile passed: 317,008 bytes flash (24%) and 23,708 bytes static
RAM (7%), leaving 303,972 bytes before runtime allocations. This excludes the
77,382 sprite pixel bytes allocated during startup. Codex did not upload firmware;
the owner subsequently confirmed the physical acceptance recorded above.
Compiled source copies match the final source after excluding Arduino's added
`#line` directive. A compatibility comparison confirmed the entire serial parser
and loop are unchanged, as are 15 lifecycle/text/parser helpers; STATE only
switches its filter initialization to the independent LEVEL clock constant.

`git diff --check` passes. The layout illustration was visually inspected for
face/text separation and readability. This does not establish hardware success.
The unrelated ZIP, audit log, responsive recheck directory and review diff that
were already untracked were preserved.

## Raspberry Pi repeat-validation commands

Transfer/review these firmware changes using the existing workflow, compile and
upload V2.2 to the ESP32 using the verified TFT_eSPI setup. Preserve Pi-local
configuration/data and the existing Phase B audio integration. With the Pi's
existing virtual environment and display configuration, run the regression
command above (the real pipe test should run on Linux), then run separately:

```bash
python manage.py companion_display_state READY
python scripts/voice_loop.py --text "Explain how a Raspberry Pi works."
python scripts/voice_loop.py
python scripts/voice_loop.py --hands-free
```

The state command confirms a write, not a device acknowledgment. Use the live
microphone/VAD configuration for the third command. Hands-free requires the
already configured wake-word and ALSA support. Close other serial monitors while
the Pi owns the UART. Do not run multiple voice loops simultaneously.

Observe QUERY and actual route MODE during THINKING, clean bead motion, MODE
and paginated captions during SPEAKING, microphone and speaker level response,
quiet periods settling smoothly, and final READY clearing the prior interaction.
Interrupt capture/playback and confirm cleanup and usable READY/ERROR recovery.

For controlled LEVEL and malformed-command acceptance use the manual serial
sequence in the firmware README. Repeat levels within 1200 ms; compare 0, 15,
45, 80 and 100, then stop updates to check fallback. Check all eleven states,
long/wide captions, CLEAR QUERY/MODE/TEXT, oversized lines and subsequent valid
commands. Leave READY running for a minute to inspect blinks and subtle gaze.
Also check text changes leave no mouth/eye artifacts, listening bars clear on
exit, repeated THINKING resets animate correctly, and ERROR/PROTECTIVE accents
clear on READY. Confirm startup reports no sprite allocation warnings, or assess
the documented direct-render fallback if allocation fails.

Core physical Pi -> UART -> ESP32 -> TFT animation, audio continuity,
QUERY/MODE/captions and flicker-free return to READY passed the owner-reported
acceptance above. The extended checks are retained as a repeat-validation
procedure; the report does not claim every malformed-input or fallback scenario
was physically exercised.
