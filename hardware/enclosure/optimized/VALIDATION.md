# Cost-optimized validation — 2026-10-16

62 mm was tested first. All 19 executed OpenSCAD CGAL collision/service checks pass.
All 12 freshly exported STLs pass nonzero-file, positive-volume, watertight,
consistent-winding and exactly-one-connected-surface-shell checks.
Nine head-related STLs were freshly exported, compared by exact oriented facets,
then original bytes retained. Their SHA-256 hashes match Option A.

Base: **174 × 114 × 62 mm**, top **160 × 102 mm**. Head: **138 × 112 × 68 mm**, 10°.
The entire frozen head/neck moves down 4 mm with the roof; its geometry does not change.
Speaker: unchanged **78 × 78 × 40 mm**, horizontal, cone down, lower-left.
Static grille gap **9.000 mm**; nominal desk gap **8.8 mm**.
Cooler: **70 × 50 × 22 mm**, minimum upper free air **22.400 mm**.
Intake **854.826 mm²** / exhaust **688.915 mm²** (speaker grille excluded).

## Requirement checks

PASS (CAD) refers to the modeled envelopes, not measured hardware. The two
structural rows are a dimensional/design review, not structural certification.

| # | Requirement | Result | Executed evidence / scope |
|---:|---|---|---|
| 1 | TFT fits | PASS (CAD) | head_components: 98 × 56.34 PCB, 14.18 depth; frozen head |
| 2 | TFT mounting holes align | PASS (CAD) | head_components: 92 × 49.5 centres; unchanged oriented facets |
| 3 | TFT screws reachable | PASS (CAD) | tft_drivers: Four Ø6 driver paths; rear cover removed |
| 4 | ESP32 fits | PASS (CAD) | head_components: 51.5 × 28.5 PCB and existing jumper/header space |
| 5 | Round microphone fits | PASS (CAD) | head_adapters: Ø26 board / Ø27 pocket; physical board to measure |
| 6 | Camera Module 3 fits | PASS (CAD) | camera_carrier: 32 × 32 × 16 reservation; unchanged carrier placed 5 mm lower |
| 7 | Tactile switch and actuator fit | PASS (CAD) | head_adapters: 6 × 6 × 5 reference; universal shim holder; 0.8 motion check |
| 8 | Neck wiring space | PASS (CAD) | neck_bend: 28 × 20 throat plus 8 mm clear bend region |
| 9 | Speaker 78 × 78 × 40 fits | PASS (CAD) | base_components: Full 78 × 78 × 40 envelope: X=-77..1, Y=-39..39, Z=10.8..50.8; unchanged speaker, lowered 2.7 mm. |
| 10 | Speaker frame clears walls | PASS (CAD) | base_components: Actual tapered shell versus full reservation |
| 11 | Speaker / magnet clear Pi | PASS (CAD) | speaker_separation: Whole speaker envelope separate from Pi/cooler |
| 12 | Speaker clears amplifier | PASS (CAD) | speaker_separation: Vertical front-left panel and terminal reservation |
| 13 | Cone acoustic path clear | PASS (CAD) | acoustic_path: Ø50 clear cylinder above floor through Ø54 carrier |
| 14 | Grille cannot contact static cone envelope | PASS (CAD) | acoustic_path: 9.00 mm above 1.8 mm grille; ≥7 mm requirement. |
| 15 | Carrier installs and removes | PASS (CAD) | speaker_removal: Five upward withdrawal positions; tray removed first; all carrier screws accessible after speaker removal. |
| 16 | Pi fits | PASS (CAD) | base_components: 85 × 56 board; 7 mm standoffs; standard M2.5 pattern |
| 17 | Pi removable | PASS (CAD) | tray_removal: Nine withdrawal positions plus continuous conservative sweep; Pi driver check |
| 18 | 70 × 50 × 22 cooler reservation fits | PASS (CAD) | base_components: Enclosure fit only; choose Pi-5-compatible hardware |
| 19 | Useful air above cooler | PASS (CAD) | cooler_air: 22.40 mm minimum above cooler to the local 3 mm roof reinforcement; broad 1.8 mm roof gives 23.6 mm. |
| 20 | Pi intake open | PASS (CAD) | base_components: 854.826 mm² actual through-area; independent of speaker grille; no rib crosses intake. |
| 21 | Rear exhaust open | PASS (CAD) | cooler_air: 688.915 mm² actual through-area; no major cooling passage blocked. |
| 22 | USB plugs fit | PASS (CAD) | plugs: Two 16 × 18 mm moulding sections |
| 23 | Ethernet plug fits | PASS (CAD) | plugs: 18 × 18 mm moulding section |
| 24 | USB-C plug fits | PASS (CAD) | plugs: 14 × 9 mm moulding section |
| 25 | Both micro-HDMI plugs fit | PASS (CAD) | plugs: Two 11 × 10 mm moulding sections |
| 26 | Bottom cover fits | PASS (CAD) | base_mates: Actual exported tray/shell mating surfaces |
| 27 | Feet clear speaker grille | PASS (CAD) | feet: Four Ø11.5 recesses at X±68/Y±42 |
| 28 | Feet clear Pi intake | PASS (CAD) | feet: 8.8 mm exposed foot height; screw heads also clear |
| 29 | Screw bosses clear hardware | PASS (CAD) | base_components: Head/base component, carrier, plug and tool checks |
| 30 | Static stability model | PASS (CAD) | stability: 34.113 mm margin / 20.809° ideal tip angle, 16384 mass cases. |
| 31 | Thin walls structurally reasonable | PASS (design review) | Dimensions + mesh + geometry review: 1.8 mm skins; 3 mm neck land; sparse ribs; 3 mm foot/pilot lands; retained 7 mm base screw engagement. No FEA or printed load test. |
| 32 | Speaker mount has no obvious flex/rattle issue | PASS (design review) | Connected frame + four supports + gasket: 86 mm outer frame, 2.2 mm webs/ring, 2.8 mm screw pads, four braced posts and flush countersinks. Continuous 2.5 mm gasket on available lands and ties; physical flex/audio test still required. |

## Mesh results

| STL | CAD cm³ | Closed / winding / shells |
|---|---:|---|
| head_shell.stl | 152.802 | PASS / PASS / 1 |
| head_rear_cover.stl | 46.993 | PASS / PASS / 1 |
| front_bezel.stl | 9.893 | PASS / PASS / 1 |
| base_shell.stl | 79.127 | PASS / PASS / 1 |
| base_bottom_cover.stl | 47.075 | PASS / PASS / 1 |
| camera_blank_insert.stl | 2.734 | PASS / PASS / 1 |
| camera_lens_insert.stl | 2.613 | PASS / PASS / 1 |
| universal_speaker_mount.stl | 7.299 | PASS / PASS / 1 |
| camera_carrier.stl | 2.023 | PASS / PASS / 1 |
| round_microphone_holder.stl | 1.588 | PASS / PASS / 1 |
| button_actuator.stl | 0.766 | PASS / PASS / 1 |
| tactile_button_holder.stl | 2.451 | PASS / PASS / 1 |

## Stability and limitations

Original: **34.405 mm / 21.103°**.
Optimized: **34.113 mm / 20.809°**.
Original 16384-case model: printed mass 55–100% of solid volume at 1.25 g/cm3, NOT a slicing estimate; TFT 60–110g, ESP32 8–16g, mic 2–6g, camera 4–10g, switch 1–5g, Pi 40–70g, cooler 10–60g, amp 2–10g; speaker 80–500g, x=-48..-28,y=-10..10,z=26..50 shifted with the speaker. Feet omitted from mass; 8.8 mm ground gap included. Camera lens/carrier installed; spare blank excluded from assembly.

This is a CAD/model result, not a physical tipping test. Removing low base mass
can offset the benefit of lowering the head; the calculated result governs.
Rubber compression, friction, wire pull, dynamic knocks and actual masses remain untested.

Collision checks use exported production meshes and the accepted conservative
hardware envelopes. Intended seating contacts use 0.02 mm numerical displacement.
Tray checks include continuous conservative sweeps; carrier, camera and TFT
motion use sampled positions. Flexible cables are not simulated. Keep the full
28 × 20 neck throat and 8 mm bend zone clear; only 0.2 mm separates the top of the
speaker envelope from that reserved bend zone. Actual bundles must stay inside it.

Measure real speaker terminals, frame, cone excursion and gasket compression;
test carrier rigidity and audio rattle. Confirm microphone diameter, switch travel,
camera FOV/ribbon, actual plug mouldings, fasteners, printer shrinkage and hole fit.
Use a Pi-5-compatible cooler; its reservation does not guarantee mating to every
connector or fan design. Perform sustained-load thermal tests, especially for PLA/PLA+.
No printed assembly, FEA, thermal simulation or physical load test was performed.

No authoritative slicer estimate was available. CAD volume is not sliced filament
mass; the 220–260 g ideal and ≤300 g target remain unverified. The shop must quote
all parts and supports before printing. Both camera inserts are supplied/quoted;
only one is installed. Head supports remain necessary as documented in PRINTING.md.

All evidence is in the full project ZIP. Original 66 mm source and production
files remain untouched. No commit. No push.
