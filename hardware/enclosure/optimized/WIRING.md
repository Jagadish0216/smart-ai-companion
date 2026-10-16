# Intended harness — no software or pin changes

This records the user's intended wiring. Verify the markings on the actual
round microphone and each breakout before powering up. Route the TFT SPI wires
locally between the existing TFT and ESP32 inside the head.

| Raspberry Pi signal | Physical pin | Destination |
|---|---:|---|
| GPIO18 | 12 | Microphone SCK/BCLK and MAX98357A BCLK |
| GPIO19 | 35 | Microphone WS and MAX98357A LRC/LRCLK |
| GPIO20 | 38 | Microphone SD |
| GPIO21 | 40 | MAX98357A DIN |
| 3V3 | 1 or 17 | Microphone VDD |
| 5V | 2 or 4 | MAX98357A VIN |
| GND | Suitable ground pin | Common microphone, amplifier, Pi and ESP32 ground |

Microphone L/R follows the existing left-channel wiring (grounded), subject to
the actual module's marking. Do not infer its identity from the circular PCB.
MAX98357A **SPK+ and SPK- go only to the two speaker terminals**.
**Neither speaker output goes to ground.** Keep the speaker pair short and
twisted; strain-relieve it at the carrier edge, clear of the cone and grille.

| Internal serial/power harness | Destination |
|---|---|
| Pi GPIO14 / physical pin 8 TX | ESP32 RX0 / GPIO3 |
| ESP32 TX0 / GPIO1 | Pi GPIO15 / physical pin 10 RX |
| Pi 5V | ESP32 VIN |
| Pi GND | ESP32 GND |

This harness avoids an internal USB-A cable. It is a future integration plan;
this CAD revision does not enable UART or change application software.
Disconnect the ESP32 internal power/UART harness before programming it over USB.
Keep the Pi unpowered during this change. Turning the Pi off alone does not
disconnect the shared 5V wire; avoid back-powering it through the ESP32 USB supply.

One future LED: ESP32 GPIO32 → 220–330 ohm resistor → LED anode; LED cathode →
GND. The single tactile button retains its intended future idle/listen and
speaking/stop role. Its GPIO assignment is not changed or invented here.

Route head wiring through the preserved 28 × 20 mm neck throat. The full throat
continues through an 8 mm clear region below the base roof. Run the optional
camera ribbon in a loose loop, respecting the selected ribbon's bend limits.
Use a Pi 5 compatible camera cable. Keep bundles out of the 70 × 50 mm cooler
air column. Use the divider's 12 × 8 mm passage, open ends and top gap for
separate signal and speaker routes. Leave 60–80 mm of tray service slack,
supported in the central/front corridor, so the bottom tray can be lowered.
