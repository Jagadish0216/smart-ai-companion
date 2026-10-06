# Smart AI Companion Display Firmware V1

This directory is the source of truth for the Smart AI Companion's standalone
ESP32 display firmware. V1 renders the companion face, short state text, and
non-blocking animations on the verified 3.5-inch TFT. Serial at 115200 baud is
the temporary test control plane.

This firmware intentionally contains no Wi-Fi, MQTT, Raspberry Pi integration,
audio, buttons, LEDs, sensors, or touchscreen support.

The original hardware proof sketch remains in
`hardware/esp32/display_test/display_test.ino` and is not replaced by this
firmware.

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

No additional libraries are required by Display Firmware V1.

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
Twenty MHz may be evaluated later, but it is intentionally not enabled in V1.

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
Smart AI Companion Display Firmware V1
Display: 480 x 320
State: BOOTING
```

The dimensions are printed using `tft.width()` and `tft.height()` after display
initialization and rotation.

## Display architecture

- `companion_display.ino` owns startup, the fixed-size serial line parser, and
  optional demo sequencing.
- `display_config.h` centralizes screen and logical-region geometry.
- `companion_theme.h` centralizes the RGB888-to-RGB565 palette, face sizing,
  and animation timing.
- `companion_face.h/.cpp` own state, expressions, text, and animation.
- `tft_espi/User_Setup.h` is only the TFT_eSPI driver/pin build configuration.

The firmware performs one full-screen clear during initialization. State
transitions and animation frames redraw only the eye, mouth, status, or small
activity regions that changed. It allocates no full-screen framebuffer, uses
no dynamic `String` objects in the animation loop, and uses `millis()` rather
than blocking delays.

## Serial commands

Send one newline-terminated command at a time:

```text
STATE BOOTING
STATE SETUP
STATE CONNECTING
STATE READY
STATE LISTENING
STATE THINKING
STATE SPEAKING
STATE OFFLINE
STATE ECO
STATE PROTECTIVE
STATE ERROR
TEXT Wi-Fi unavailable
TEXT Hello there!
CLEAR TEXT
HELP
```

A successful state command prints `STATE: <STATE>`. A successful text command
prints the text actually displayed. Status text longer than 40 characters is
safely truncated and reported with `WARN: text truncated`. Oversized serial
lines are discarded until the next newline. Unknown commands and states return
concise `ERR:` messages without changing the display state.

The status rule is deterministic:

- every `STATE` command resets the status to that state's default;
- `TEXT` overrides the status for the current state;
- `CLEAR TEXT` restores the current state's default;
- the next `STATE` command replaces any prior custom text.

## State behavior

| State | Display behavior |
|---|---|
| BOOTING | Non-blocking cyan eye-opening sequence; remains BOOTING afterward; `Starting...` |
| SETUP | Friendly neutral eyes and subtle smile; `Setup Wi-Fi` |
| CONNECTING | Attentive cyan eyes and animated three-dot connection indicator |
| READY | Friendly eyes, calm blink intervals, and subtle pupil movement; green status dot |
| LISTENING | Slightly wider eyes and a restrained cyan pulse indicator |
| THINKING | Controlled pupil movement and a small three-dot activity indicator |
| SPEAKING | Stable cyan eyes and a simple 140 ms mouth animation; no phoneme sync |
| OFFLINE | Calm, dimmer sleepy cyan eyes and a small amber status dot |
| ECO | Relaxed, reduced-intensity cyan expression and small green status dot |
| PROTECTIVE | Controlled concerned expression and small amber status dot |
| ERROR | Concerned cyan expression and small red status dot; no red screen |

Semantic green, amber, and red colors are confined to small status indicators.
The companion face remains cyan in normal, constrained, and error states.

## Demo mode

Normal firmware never cycles states automatically:

```cpp
#define COMPANION_DISPLAY_DEMO_MODE 0
```

For a supervised bench demonstration only, change the value to `1`, compile,
and upload again. Demo transitions are non-blocking and occur every four
seconds. Restore the value to `0` for normal serial-controlled testing.

## Troubleshooting

- A backlight-only or grey/white screen previously proved to be caused by
  faulty or poor jumper-wire connections. Check every power, ground, CS, DC,
  reset, MOSI, and SCK connection before changing firmware.
- Confirm that the installed TFT_eSPI `User_Setup.h` exactly matches the
  repository file. A sketch-local macro file is not sufficient in Arduino IDE.
- Confirm ILI9488, rotation 1, and the 10 MHz SPI frequency remain selected.
- Confirm TFT LED is connected to 3.3 V and VCC to VIN/5V.
- If upload fails, verify the selected ESP32 board and serial port before
  altering display pins.

Touch, MQTT, Wi-Fi, Raspberry Pi communication, audio, buttons, LEDs, and
sensors are intentionally reserved for later integration layers. The next
acceptance step for this subsystem is manual verification of every display
state on the real ESP32 and MSP3520.
