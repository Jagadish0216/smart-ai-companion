# Smart AI Companion Display Firmware V2.1

A consistent cyan companion with tall Concept-3 eyes above an edge-to-edge
subtitle/context band. Dark pupils and white highlights retain the visual
identity. Eyelids, eased gaze, mouth, and restrained color give each state its
personality.

Existing Raspberry Pi STATE/TEXT captions and V2 LEVEL/QUERY/MODE command
semantics remain supported. **Actual Pi microphone/playback LEVEL integration
is not implemented here.** LEVEL remains manually testable over serial and
will be connected to Pi audio in the next integration phase. No Python,
audio, AI-generation, Wi-Fi, MQTT, touch, sensor, LED, or wiring changes are
part of this firmware.

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

No additional libraries are required by Display Firmware V2.1.

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
Twenty MHz may be evaluated later, but it is intentionally not enabled in V2.1.

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
Smart AI Companion Display Firmware V2.1
Display: 480 x 320
State: BOOTING
```

The dimensions are printed using `tft.width()` and `tft.height()` after display
initialization and rotation.

## V2.1 layout and character

All states use the same underlying 66 x 86 px rounded eyes, radius 18,
centered at (150,100) and (330,100). Pupil radius is 7, with a radius-2 white
highlight. Eased horizontal/vertical gaze stays within about +/-6 and +/-5 px.
The eyes' full shape never compresses during a blink.

An upper/lower eyelid mask covers the eye instead: the upper lid contributes
78% of closure, the lower 22%. The deterministic coverage sequence is
0, 35, 80, 96, 80, 35, 0 percent in 36 ms time slots. Full reopening targets
216 ms; completion is 252 ms. Rendering skips elapsed frames rather than
queuing a slow blink. Perceived pacing still needs real 10 MHz TFT acceptance.
Primary blink gaps are 4.8, 6.4, 5.7, 7.2, and 5.3 seconds, with an occasional
double blink every fifth primary cycle and a 180 ms second-blink gap. OFFLINE
and ECO add 1.6 and 2.4 seconds to normal blink gaps.

The face occupies the upper 220 pixels. The interaction band is
(0,220,480,100), with a single subtle separator at y=220 and no outer border.
Main text is in (24,250,432,62), inset 2 px, with at most two lines.
The installed TFT_eSPI FreeSans12pt7b proportional font is used at native size:
capital A is 18 px tall, between the previous built-in typography sizes.
Its 17 px maximum ascent and 6 px descender fit baselines 20 and 49 inside
the local text region. Two-line baseline spacing is 29 px; single-line
baseline is 37. Unusually wide text falls back to existing font 2; very long
queries can end in a display-only ellipsis. Wrapping uses measured widths,
preserves words when they fit, and splits an oversized token only as needed.
No new font file or dependency is added.

MODE is small muted font-2 text, right aligned at (456,228), visible only
during THINKING/SPEAKING. It has no outline and never overlays main text.
A small three-dot processing sequence occupies (24,228,64,18), with 350 ms
steps, for THINKING/CONNECTING and the initial boot interval. Steady 18 x 2 px
amber/red accents occupy that header area for PROTECTIVE/ERROR.

The seven listening bars retain their V2 profile, dimensions, LEVEL filter,
and stale behavior: x=320,336,352,368,384,400,416; width 8; bottom y=298;
minimum height 4 and maximum heights 18,26,32,38,32,26,18. The listening
text uses only the left 260 px of the content region, so caption/status
refreshes do not wipe the live meter.

## Local composition and selective replacement

Two 16-bit TFT_eSprite buffers are attempted once at startup:

| Buffer | Dimensions | Pixel allocation including TFT_eSPI's extra pixel |
|---|---|---|
| Reusable eye | 84 x 104 | 17,474 bytes |
| Interaction text | 432 x 62 | 53,570 bytes |
| Combined | Two local regions | 71,044 bytes, excluding object/allocator overhead |

There is one eye sprite, reused for both eyes. It composes the final
background, tall eye, pupil, highlight, and eyelid masks, then pushes the
result. Eye caches compare color, pupil position, and lid coverage: still
frames push nothing. There is no full-screen sprite or permanent pair of
eye buffers.

The text sprite composes background and the latest two-line text before one
local push. In LISTENING only its left 260 px are pushed. Entering LISTENING
clears the old caption's right-hand footprint locally and repaints the bars;
leaving it replaces the complete content region with the new text frame.

Each allocation is independent. Failure prints one concise startup warning
and uses a bounded direct-render fallback for that component. It does not
reboot, retry allocations in the loop, or continually allocate/free memory.
Fallback can show more local erase/repaint flicker than sprite rendering.

STATE no longer performs any drawing or blanket face clear. It resets the
same interaction data/LEVEL lifecycle, resets blink scheduling, and marks
text/expression for an update. Component caches and eased gaze survive:
only changed eye frames, mouth shape, mode text, and processing/accent
regions are replaced. Mouth clears are immediately followed by the new
mouth in the same function; verified V2 four-category mouth dimensions
remain unchanged.

MODE changes touch only their 120 x 18 header field. Processing/accent updates
touch only the 64 x 18 header field. The interaction band and separator are
drawn once at initialization. Only initialization calls fillScreen.
No face activity particles, brows, or recurring ERROR color animation are
drawn.

Normal active/LEVEL updates target 100 ms, quiet idle 180 ms, ECO 250 ms.
Brief blink frames use their shorter interval without accelerating the
100 ms LEVEL smoothing filter. Timing is millis-driven and wrap-safe;
SPI transfer and glyph drawing can lower actual frame rate. Sprites remove
the visible background-clear stage, but pushes are still sequential SPI
writes, not atomic screen swaps or a guarantee of zero tearing.

## State behavior

| State | Face / animation | Interaction content |
|---|---|---|
| BOOTING | Tall eyes revealed by smooth upper-lid opening over about 900 ms; smile afterward | Starting...; brief lower-band processing dots |
| SETUP | Friendly same-base eyes, slight upper lid, smile | Ready for setup |
| CONNECTING | Same tall eyes, slight upward gaze | Connecting...; lower-band processing dots |
| READY | Calm smile, long holds, 4.8 s gaze targets with horizontal/vertical easing, eyelid blinks | Ready |
| LISTENING | Tall attentive eyes; slight lid/gaze response to level without resizing the eye | Listening...; preserved seven-bar amplitude meter |
| THINKING | Center/up-right/hold/center/up-left/hold/center gaze in seven 500 ms phases; no face decoration | QUERY or Thinking...; small processing dots; supplied MODE |
| SPEAKING | Tall eyes, subtle slow gaze/blinks, preserved closed/small/medium/wide mouth categories | TEXT captions; supplied MODE; no large state label |
| OFFLINE | Friendly READY-like expression, dim cyan, 6.2 s gaze targets and slower blinks | Local mode |
| ECO | Dim cyan, relaxed 10 px upper/2 px lower lids, smile, 7.2 s gaze targets | Eco mode |
| PROTECTIVE | 18 px upper/4 px lower lids, mildly lowered pupils, small concerned mouth | Protecting resources; steady small amber accent |
| ERROR | 22 px upper/6 px lower lids, mildly lowered pupils, small concerned mouth | Let's try again; steady small red accent |

The meter is an amplitude visualization, not a frequency waveform. No
network readings, progress percentages, temperatures, or other system data
are invented.


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
the bars toward their minimum. The speaking mouth uses four smoothed-level
ranges: below 12, 12–31, 32–61, and 62–100.

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
smooth gaze, asymmetrical eyelid coverage, and an occasional double blink. Check that transitions
from ERROR/PROTECTIVE replace prior eyelid/mouth details and steady accents, from THINKING stop the lower-band processing dots,
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
