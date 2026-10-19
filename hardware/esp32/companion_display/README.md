# Smart AI Companion Display Firmware V2.2

Minimal cyan light-shape eyes on black, a shallow idle smile, an independent
three-dot thinking animation, and a continuous audio-driven speech shape.
QUERY, MODE, TEXT and LEVEL retain their existing protocol and lifecycle.

Built on the Phase B audio-level branch. The Pi already supplies microphone and
streaming ALSA playback LEVEL updates on supported paths; no Pi changes are
required for this redesign. Physical acceptance was confirmed by the owner on
2026-10-19: READY and THINKING animations, smooth SPEAKING response to actual
audio LEVEL, clear audio without stuttering, QUERY/MODE/captions, and return to
READY without flicker all passed. The accepted appearance is unchanged.

The original proof sketch in hardware/esp32/display_test is unchanged.

## Verified hardware

- ESP32 DevKit / ESP-WROOM-32
- MSP3520 3.5-inch SPI TFT
- ILI9488 display driver
- 480 × 320 landscape orientation
- TFT_eSPI by Bodmer
- Known-stable SPI clock: **10 MHz**

### Wiring

| TFT pin | ESP32 connection |
|---|---|
| VCC | VIN / 5V |
| GND | GND |
| CS | GPIO 27 |
| RESET | GPIO 25 |
| DC / RS | GPIO 26 |
| SDI / MOSI | GPIO 23 |
| SCK | GPIO 18 |
| LED | 3V3 |

TFT SDO/MISO is not required for display-only writes and may remain
disconnected. The checked-in TFT_eSPI setup defines the conventional ESP32
MISO pin as GPIO 19, but this firmware does not depend on display readback.

The touchscreen pins must remain disconnected for this milestone. Touch input
is not initialized or read. The TFT LED pin is wired directly to 3.3 V, so the
firmware does not attempt software backlight control.

## Required Arduino environment

1. Install Arduino IDE.
2. Install the Espressif ESP32 board package using Boards Manager.
3. Install **TFT_eSPI by Bodmer** using Library Manager.
4. Select **DOIT ESP32 DEVKIT V1**, or the compatible ESP32 DevKit board that
   was used for the verified hardware test.
5. Apply the repository's TFT_eSPI configuration before compiling.

No additional libraries are required by Display Firmware V2.2.

## Why TFT_eSPI needs a library setup file

TFT_eSPI compiles its driver and pin configuration as part of the library.
Macros in a sketch-local header do not reliably configure those separately
compiled library sources in Arduino IDE. The known-good setup therefore lives
in this repository at:

```text
tft_espi/User_Setup.h
```

It must be copied to the installed TFT_eSPI library as `User_Setup.h` before
compilation. The checked-in setup selects ILI9488, the verified GPIO mapping,
the minimal required fonts, disabled touch, and the stable 10 MHz SPI clock.
Twenty MHz may be evaluated later, but it is intentionally not enabled in V2.2.

### Apply the setup with PowerShell

From this `companion_display` directory, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\apply_tft_espi_setup.ps1
```

The default installed library location is:

```text
%USERPROFILE%\Documents\Arduino\libraries\TFT_eSPI
```

To use another installed library directory:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\apply_tft_espi_setup.ps1 `
  -TftEsPiPath 'D:\Arduino\libraries\TFT_eSPI'
```

The script verifies both setup files, compares their SHA-256 hashes, creates a
timestamped backup beside the installed `User_Setup.h` before replacement,
copies the repository version, and verifies the result. Re-running it when the
files already match makes no change. It never deletes the backup or any other
file.

### Manual setup alternative

1. Close Arduino IDE if it is compiling.
2. Find the installed TFT_eSPI library directory.
3. Back up its existing `User_Setup.h`.
4. Copy `tft_espi/User_Setup.h` from this directory over the installed
   TFT_eSPI `User_Setup.h`.
5. Reopen Arduino IDE and compile the sketch.

## Compile and upload

1. Open `companion_display.ino` in Arduino IDE. Arduino IDE should open the
   other `.h` and `.cpp` files as tabs in the same sketch.
2. Select the compatible ESP32 DevKit board and the correct serial port.
3. Confirm the repository `User_Setup.h` has been applied to TFT_eSPI.
4. Click **Verify**.
5. Click **Upload**.
6. Open Serial Monitor at **115200 baud**.
7. Set the Serial Monitor line ending to **Newline** or **Both NL & CR**.

Expected startup output resembles:

```text
Smart AI Companion Display Firmware V2.2
Display: 480 x 320
State: BOOTING
```

The dimensions are printed using `tft.width()` and `tft.height()` after display
initialization and rotation.

## V2.2 layout and rendering

[State layout preview](../../../docs/tft-modern-face-v2-preview.png) (illustration,
not a hardware capture; owner-reported hardware acceptance is recorded above).

Eyes are solid 68 x 54 px rounded light shapes (20 px maximum radius), centered
at (166,108) and (314,108). The entire eye moves with eased gaze; pupils and
highlights are removed. Blinks close into rounded slits with the existing
elapsed-time blink sequence and relaxed scheduling. READY gaze moves only
about +/-2 px horizontally and +/-1 px vertically over long holds.

The mouth region is (186,157,108,48), separated from both eyes and captions.
READY has a shallow smile; LISTENING has a quiet line and subtly attentive eyes.
THINKING has slow upward/side gaze (1.4 s holds) and three small beads cycling
every 240 ms in a 1.44 s period. It never uses the speaking mouth. SPEAKING
maps the smoothed amplitude continuously to a solid rounded shape from
38 x 4 to 62 x 16 px, with a small silence gate. There are no abrupt mouth
categories or hollow mechanical outlines. ERROR/PROTECTIVE have narrowed eyes,
a quiet line, readable default text and a steady red/amber header accent.
OFFLINE/ECO retain dim, relaxed expressions; all eleven states remain supported.

The interaction band is (0,220,480,100), black with one subtle separator.
The existing proportional FreeSans12pt7b captions, measured two-line wrapping,
font-2 fallback, display ellipsis, 40-byte TEXT and 80-byte QUERY limits remain.
Main text uses (24,250,432,62). MODE remains right aligned at (456,228).
THINKING labels the content field QUERY when displaying a supplied query; processing beads sit on the face,
so the header does not duplicate their motion. CONNECTING/BOOTING retain small
header dots. LISTENING reserves the right side for the existing seven-bar
amplitude meter, with caption text confined to the left 260 px.

Three independent 16-bit sprites are attempted once at startup:

| Buffer | Dimensions | Pixel bytes including extra pixel |
|---|---|---|
| Reusable eye | 84 x 80 | 13,442 |
| Interaction text | 432 x 62 | 53,570 |
| Mouth / processing | 108 x 48 | 10,370 |
| Total | Local regions only | 77,382 |

This adds 6,338 pixel bytes over V2.1, excluding object/allocator overhead.
Allocation failure falls back independently to bounded direct rendering and
prints one startup warning. No per-frame allocations or full-screen sprites.
Changed eyes are composed once and pushed to both positions; changed mouth
geometry and processing beads are composed locally before pushing. Still
components send no pixels. TEXT changes replace only the caption region;
STATE marks dirty data without clearing the face. Only initialization uses
fillScreen. Text/mode/query/reset semantics and the listening meter are intact.

Active expression frames target 50 ms, idle 180 ms, ECO 250 ms, blinks 36 ms.
The LEVEL filter keeps its separate 100 ms clock and quarter-distance easing;
faster facial frames do not accelerate amplitude smoothing. Times are
millis-driven and wrap-safe. Serial work stays bounded to 64 bytes per loop.
Actual frame rate is limited by 10 MHz SPI transfers; target intervals are not
hardware timing guarantees. Sprite pushes reduce local clear/repaint flicker
but remain sequential transfers. Direct fallback can flicker more.

## Serial protocol

115200 baud, one ASCII command per newline; CRLF is accepted. Commands and
modes are uppercase; existing state names also retain case-insensitive
matching. Leading/trailing spaces and tabs are trimmed. Text/query internal
whitespace collapses to single spaces.

| Command | Effect / acknowledgement |
|---|---|
| STATE BOOTING / SETUP / CONNECTING / READY / LISTENING / THINKING / SPEAKING / OFFLINE / ECO / PROTECTIVE / ERROR | Select state, reset primary text and level; STATE: <name> |
| TEXT <text> | Override primary content for this state; TEXT: <stored text> |
| CLEAR TEXT | Restore state's default content (or QUERY in THINKING); TEXT: <default text> |
| QUERY <text> | Store recognized query as display-only data; QUERY: OK |
| CLEAR QUERY | Clear stored query; QUERY: cleared |
| MODE LOCAL / KNOWLEDGE / WEB | Set only the supplied display mode label; MODE: <name> |
| CLEAR MODE | Remove mode label; MODE: cleared |
| LEVEL <0-100> | Supply a decimal integer amplitude; LEVEL: <value> |
| HELP | Print supported commands |

TEXT stores at most 40 bytes, QUERY at most 80, each plus a terminator.
Overlong accepted text/query payloads return WARN: text truncated or
WARN: query truncated before their acknowledgement. Empty TEXT/QUERY is
rejected and leaves the previous content intact. No query is echoed in its
acknowledgement. QUERY/MODE never affect AI logic.

LEVEL accepts digits only, including leading zeros, in the inclusive range
0–100. Signs, fractions, embedded whitespace, suffixes, and empty values are
malformed. Values outside the range are **rejected**, not clamped by the
protocol. Accumulation is saturated internally to prevent integer overflow;
malformed/rejected input leaves the last valid level unchanged.

The serial line buffer remains 96 bytes: up to 95 bytes plus terminator.
An 80-byte query plus its command prefix fits. Longer lines are discarded
through the next newline and report ERR: command too long, without processing
a valid prefix. NUL/control/non-ASCII bytes similarly discard the line.
Unknown commands, states, and modes return concise ERR messages and do not
alter display state. Input work is bounded to 64 bytes per loop iteration,
so sustained serial input does not monopolize the animation loop.

## LEVEL semantics and fallback

The latest valid LEVEL remains fresh for **1200 ms**. Send levels about 8–10
times per second for a continuously reactive manual test. Fixed-point easing
moves one quarter of the remaining difference per 100 ms level update, independently of eyelid frames, smoothing
both the meter and mouth. A valid zero brings the mouth toward closed and
the bars toward their minimum. The speaking mouth uses continuous smoothed
amplitude, with a silence gate at levels 0–5 and gradual growth above that range.

When no level is fresh, LISTENING eases toward a small autonomous pulse;
SPEAKING eases toward a restrained deterministic multi-shape rhythm. These
fallback animations are decorative, not fabricated measurements. A stale high
input cannot hold the UI at high amplitude. Every STATE clears the previous
level/freshness and starts its fallback; subsequent LEVEL commands drive that
state. LEVEL supplied in an inactive state is stored but has no level display,
and the next STATE clears it.

## Interaction lifecycle and backward compatibility

Every STATE, including a repeated same-state command, resets TEXT to the new
state's default and resets LEVEL. Therefore send STATE SPEAKING **before**
TEXT, as in the existing Pi caption path. Later TEXT updates do not reset
mouth animation or erase the mode label. Duplicate text is coalesced.

- LISTENING begins a new interaction and clears the previous query, mode,
  caption, and level.
- QUERY received during this interaction survives STATE THINKING and STATE
  SPEAKING. THINKING shows it unless TEXT explicitly overrides the primary
  content. CLEAR TEXT restores the query; CLEAR QUERY restores Thinking...
  when there is no custom TEXT.
- MODE survives THINKING and SPEAKING and is visible there, showing only a supplied LOCAL,
  KNOWLEDGE, or WEB label. No supplied mode means no mode label.
- READY, ECO, PROTECTIVE, ERROR, OFFLINE, BOOTING, SETUP, and CONNECTING clear
  query/mode and restore their default band content. A later new interaction cannot
  inherit this metadata.
- CLEAR QUERY and CLEAR MODE work immediately in any state. Neither changes
  STATE or the current caption.

Legacy STATE/TEXT/CLEAR TEXT/HELP senders need no change. With only legacy
commands, there is no mode label or recognized-query preview, and the meter
and mouth use the documented stale-level fallback. This task makes no changes
to Raspberry Pi Python code, caption pagination, audio, or generation.

## Manual serial acceptance

Open Serial Monitor at 115200 baud with Newline or Both NL & CR.
Send each line separately, allowing its expression to be observed:

```text
STATE READY
STATE LISTENING
LEVEL 10
LEVEL 35
LEVEL 70
LEVEL 30
LEVEL 0
QUERY What is a Raspberry Pi?
STATE THINKING
MODE LOCAL
STATE SPEAKING
TEXT A Raspberry Pi is a small computer.
LEVEL 15
LEVEL 45
LEVEL 80
LEVEL 35
LEVEL 5
TEXT It is useful for learning and projects.
STATE READY
```

For levels, send repeated updates within 1200 ms to see sustained amplitude.
Stop sending LEVEL and verify the smooth fallback after the timeout; observe
that LEVEL 100 cannot leave the meter or mouth stuck high. Send LEVEL 0
repeatedly and verify a closed/near-closed mouth.

Also exercise:

```text
STATE THINKING
QUERY   Whitespace trimmed query
MODE KNOWLEDGE
MODE WEB
CLEAR MODE
CLEAR QUERY
TEXT Temporary caption
CLEAR TEXT
LEVEL malformed
LEVEL -1
LEVEL 101
LEVEL 10.5
LEVEL 20 extra
LEVEL 99999999999999999999999
QUERY
MODE LOCAL_RAG
STATE UNKNOWN
HELP
```

Verify errors do not change state/metadata; valid input still works afterward.
Send QUERY followed by 81–89 printable characters: expect a truncation
warning and an 80-byte stored limit. Send a line longer than 95 bytes, then
STATE READY on the next line: expect one oversized-line error and recovery,
not a partially executed command. Test TEXT with 40 wide characters and short
two-line captions; inspect readability and absence of stale pixels.

Visit all eleven states. Leave READY running for at least 60 seconds to see
smooth gaze, rounded blink closure, and an occasional double blink. Check that transitions
from ERROR/PROTECTIVE replace prior eyelid/mouth details and steady accents, from THINKING stop the facial processing dots and clear the QUERY header,
and from LISTENING erase bars. Check that several TEXT updates during SPEAKING
preserve the face/mouth animation and mode label, and final READY restores Ready.
These visual/hardware checks require the actual ESP32/TFT.

## Demo and troubleshooting

COMPANION_DISPLAY_DEMO_MODE remains 0. Enabling it for a supervised bench
demo cycles states every four seconds using millis; restore 0 for production.
It does not fabricate MODE/QUERY/LEVEL data.

If the screen is blank/grey, inspect the verified wiring first. Keep ILI9488,
rotation 1, existing pins, and 10 MHz. Confirm the installed TFT_eSPI
User_Setup.h matches the checked-in configuration.

If a startup message reports a sprite allocation failure, direct rendering
remains functional but local flicker can be more noticeable. Sprite allocation
headroom and blink/transition pacing must be checked on the actual ESP32.
The firmware is not uploaded automatically.
