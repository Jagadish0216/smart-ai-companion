# TFT V2.1 Phase B: audio LEVEL integration

Implemented on `tft-audio-level-v1`, based on accepted main commit
`160aa8583d0f2222c1add1cc3857bc79c03c233b`. Prepared for Raspberry Pi validation
on 2026-10-19. Linux and physical hardware acceptance remain pending.

## Display API and meter

`CompanionDisplay.set_level(level: int) -> bool` is optional, so older subclasses
remain instantiable. Only Python integers 0–100 are accepted; booleans, floats,
strings, None and out-of-range values raise `ValueError` before any
serial write. Serial sends exactly `LEVEL <value>\n`; True means bytes sent,
not acknowledgment. NoOpDisplay inherits validation and returns False.
Providers without the optional method are harmless in the voice loop.

`AudioLevelMeter` accepts normalized RMS, applies sensitivity gain 4, clamps to
0–100, and smooths with time-based attack 40 ms / release 180 ms. Invalid or
nonfinite RMS is treated as silence. Constructor parameters allow deterministic
tests and sensitivity adjustment without adding environment flags. Normal
updates are limited to 80 ms intervals (12.5 Hz) using monotonic time. Identical
levels are coalesced, with an 800 ms keepalive because firmware freshness expires
after 1200 ms. Silence settles to integer zero; reset bypasses throttling.

## Audio integration

- Normal VAD capture reuses its existing signed S16_LE mono or ALSA S32_LE left
  channel RMS. Only post-calibration chunks reach the amplitude callback.
  Warm-up/calibration never supply their measured noise as speech activity.
- Hands-free capture reuses exactly the RMS passed to its endpoint detector,
  after warm-up/calibration. Every recorded frame is still written before
  endpoint decisions; admission, thresholds, wake lifecycle and timing budgets
  are unchanged.
- Streaming ALSA playback observes successfully accepted bytes from the existing
  persistent player. These are the already prepared, gain-processed S32_LE
  stereo samples, including startup/inter-chunk silence. RMS includes both signed
  channels. Partial writes preserve alignment in the observer, and file contents,
  write order, progress callbacks and captions are preserved.
- Playback measures 80 ms PCM windows and keeps bounded amplitude summaries
  (at most 256) on an estimated monotonic playback timeline. Existing writer,
  backpressure, queue-wait and drain checks service due summaries; late checks
  skip expired windows. There is no added playback pacing, audio process,
  microphone reader, thread, or unbounded queue. Disabled displays avoid the
  additional playback RMS work.
- Display and amplitude callback exceptions are best effort. Every display state
  helper sends zero after STATE, including THINKING, READY and ERROR. The meter
  resets after capture and before playback; cancellation/error cleanup finishes
  workers before sending the final state/zero. QUERY, MODE and TEXT ordering
  remains covered by lifecycle tests.

The streaming worker now catches `concurrent.futures.TimeoutError` explicitly.
On Python 3.10 it differs from built-in TimeoutError; normal 50 ms worker waits
must not terminate playback. This compatibility issue appeared when running
the previously Unix-only mocked playback fixtures on Windows.

## Limits

Fixed-duration capture records directly into a WAV and has no live amplitude
observer. Pulse playback and the older nonstreaming playback helpers retain
their existing behavior without live speaker metering. Injected audio files
bypass live microphone metering but retain speaker metering on streaming ALSA.
Wake-word standby capture is unchanged and is not metered.

Pipe acceptance is not a hardware playback timestamp. Meter windows follow a
nominal 48 kHz timeline, rebasing after estimated underruns. ALSA/device buffering,
startup, scheduling, blocked writes, and driver drift can produce visible lead
or lag. Writer checks normally service at roughly 80 ms; final drain checks can
be 100 ms apart. Host attack/release and the unchanged firmware filter add visual
smoothing. Display callbacks run synchronously; an unsuccessful serial write
can briefly delay the audio thread up to the existing configured 50 ms write
timeout before the transport backs off. Driver open/close behavior and audible
continuity still require Pi testing; no hard real-time guarantee is claimed.
Serial failure exceptions are isolated from capture/playback. No sample-accurate
synchronization is claimed. Under extreme buffering,
furthest amplitude summaries are coalesced to keep memory bounded.

Zero is sent at lifecycle boundaries, but unsupported paths and long periods
without observations can still enter the firmware's existing decorative stale
fallback after 1.2 seconds. No firmware was changed. During live supported audio,
keepalives maintain measured-level freshness, including silence.

## Changed files

Production:

- `assistant/display/base.py` — optional validated API.
- `assistant/display/serial_display.py` — exact serial command.
- `assistant/display/audio_level.py` — meter and safe callback helper (new).
- `assistant/voice/alsa_stream.py` — bounded PCM amplitude observation.
- `assistant/voice/hands_free.py` — reuse endpoint RMS.
- `scripts/voice_loop.py` — callbacks, lifecycle resets, worker timeout compatibility.

Tests:

- `assistant/tests_audio_level.py` (new).
- `assistant/tests_display.py`.
- `assistant/tests_capture_vad.py`.
- `assistant/tests_hands_free.py`.
- `assistant/tests_alsa_stream.py`.
- `assistant/tests_alsa_audio.py`.
- `assistant/tests_voice_loop.py`.
- `assistant/tests_wakeword.py`.

Test fixtures accept the optional callbacks. Mocked Unix attributes use
`create=True` on Windows; the real nonblocking-pipe test is explicitly skipped
where `os.set_blocking` is unavailable. This does not emulate ALSA on Windows.
This note and `docs/tft-audio-level-phase-b-tests.log` are additional review artifacts.

## Verification

Final Windows recheck on 2026-10-19, Python 3.10: **434 tests run in 19.378 seconds; 433 passed,
1 skipped, 0 failures, 0 errors**. `git diff --check` also passed. Two added
integration checks feed a disconnected SerialCompanionDisplay into microphone
VAD capture and PCM playback: captured/played bytes and playback progress remain
intact despite the failing UART write. QUERY, MODE, captions, cancellation and
existing voice lifecycle regression tests pass. No additional production fixes
were needed during this recheck.

```bash
python manage.py test assistant.tests_audio_level assistant.tests_display assistant.tests_capture_vad assistant.tests_hands_free assistant.tests_alsa_stream assistant.tests_voice_loop assistant.tests_voice_stream assistant.tests_alsa_audio assistant.tests_wakeword assistant.tests_voice --noinput
```

The skipped test is
`assistant.tests_alsa_stream.RealPipeTests.test_real_nonblocking_pipe_normal_eof_and_cancelled_stalled_reader`.
It must run on Linux. Windows tests use synthetic PCM, deterministic clocks and
mocked devices; these results do not validate microphones, speakers, UART or TFT.

An earlier expanded run had a Windows sandbox `WinError 5` on the wake-model
fixture's temporary-file rename, plus a player mock missing the new optional
argument. The mock was updated; the isolated rename test passed with TEMP/TMP
pointing to a writable task directory. The final full run used that same temp
directory, with no wake-model production code changes. The earlier core run was
338 tests: 337 passed, one platform skip.

## Raspberry Pi acceptance

Use the existing Pi virtual environment and accepted configuration, with these
reviewed changes transferred without replacing Pi-local knowledge or settings.
Run the complete test command above; the real pipe test should run on Linux.
Then run these separately:

```bash
python scripts/voice_loop.py
python scripts/voice_loop.py --text "Explain how a Raspberry Pi works."
python scripts/voice_loop.py --hands-free
```

The last command requires the existing wake-word/ALSA setup to be enabled.
Use the first command with the existing VAD configuration to check microphone
levels, quiet gaps and speech. In hands-free mode, verify one wake capture,
unconditional request recording, and the original endpoint behavior. Confirm
SPEAKING mouth activity follows loud/quiet PCM, and THINKING/READY return to
zero while QUERY, actual route MODE and streaming captions remain correct.
Interrupt capture and playback with Ctrl+C; verify final zero/error behavior
and child cleanup. After exiting the voice loop, inspect:

```bash
pgrep -x aplay
pgrep -x arecord
```

With no other audio sessions, neither should leave a child behind. Repeat with
the TFT disconnected to verify audio continues, then reconnect and check
best-effort recovery. Compare existing startup/starvation timing metrics and
subjective meter lead/lag. Record Linux results and actual TFT observations
before calling Phase B hardware-accepted.

Protected Windows artifacts and Pi-local knowledge paths were not modified,
staged, renamed or deleted. No `.env`, hardware pins, ALSA configuration, models,
RAG settings, performance settings or ESP32 firmware changed. Publication is
limited to one Phase B commit on `tft-audio-level-v1`; main is not modified,
pushed or merged. The unrelated untracked `Smart_Companion_B.zip` is excluded
along with the protected audit artifacts.
