# Final validation — 2026-10-15

OpenSCAD 2021.01 was actually executed with `--hardwarnings` for every production
STL, coupon, collision check and render. This is a CAD-validated revision;
physical hardware measurements and printed assembly qualification remain open.
No failed geometric check is waived or presented as a pass.

## Final dimensions and airflow

Base **174 × 114 × 66 mm**, top **160 × 102 mm**, R16. The 66 mm target succeeds.

Head **138 × 112 × 68 mm**, 10° tilt. Mesh-transformed assembled size is
**174.000 × 114.000 × 191.149 mm** without feet and
**174.000 × 114.000 × 199.949 mm** with intended feet.
The total is computed from actual exported vertices, not hard-coded as a target.

Pi intake **854.826 mm²**, rear exhaust **688.915 mm²**, ratio
**0.806**. Exhaust is 19.41% smaller than intake.
Both meet 700/600 mm² minima. SVG projections integrate actual unblocked
through-openings, including capsule ends and protected lands; connector and
speaker openings are excluded. This is geometric area, not measured airflow.

The 70 × 50 × 22 mm cooler envelope ends at Z=36.6, with 26.4 mm nominal
clear air above. This bounding envelope is a reservation for a **Pi-5-compatible
open cooler**, not a solid rectangular part guaranteed to mate with all onboard
connectors. The selected cooler must provide GPIO/header/connector relief and
its intended mounts; remove it first for Pi screws if it blocks their drivers.
Do not run bundles across its fan. Pi mounting bay follows the taper; its
rectangular nominal bounds are not a claim that every point of that box is empty.

## Required validation table

PASS below applies to the stated CAD reservations. Unmeasured hardware is not
silently promoted to physically confirmed fit.

| # | Requirement | Result | Evidence / scope |
|---:|---|---|---|
| 1 | TFT fits | PASS (CAD) | `head_components`: 98 × 56.34 PCB, 14.18 depth; frozen head |
| 2 | TFT mounting holes align | PASS (CAD) | `head_components`: 92 × 49.5 centres; unchanged oriented facets |
| 3 | TFT screws reachable | PASS (CAD) | `tft_drivers`: Four Ø6 driver paths; rear cover removed |
| 4 | ESP32 fits | PASS (CAD) | `head_components`: 51.5 × 28.5 PCB and existing jumper/header space |
| 5 | Round microphone fits | PASS (CAD) | `head_adapters`: Ø26 board / Ø27 pocket; physical board to measure |
| 6 | Camera Module 3 fits | PASS (CAD) | `camera_carrier`: 32 × 32 × 16 reservation; unchanged carrier placed 5 mm lower |
| 7 | Tactile switch and actuator fit | PASS (CAD) | `head_adapters`: 6 × 6 × 5 reference; universal shim holder; 0.8 motion check |
| 8 | Neck wiring space | PASS (CAD) | `neck_bend`: 28 × 20 throat plus 8 mm clear bend region |
| 9 | Speaker 78 × 78 × 40 fits | PASS (CAD) | `base_components`: Unreduced cuboid, X=-77..1, Y=-39..39, Z=13.5..53.5 |
| 10 | Speaker frame clears walls | PASS (CAD) | `base_components`: Actual tapered shell versus full reservation |
| 11 | Speaker / magnet clear Pi | PASS (CAD) | `speaker_separation`: Whole speaker envelope separate from Pi/cooler |
| 12 | Speaker clears amplifier | PASS (CAD) | `speaker_separation`: Vertical front-left panel and terminal reservation |
| 13 | Cone acoustic path clear | PASS (CAD) | `acoustic_path`: Ø50 clear cylinder above floor through Ø54 carrier |
| 14 | Grille cannot contact static cone envelope | PASS (CAD) | `acoustic_path`: 10.5 mm gap; actual excursion still to verify |
| 15 | Carrier installs and removes | PASS (CAD) | `speaker_removal`: Five upward removal positions on withdrawn tray |
| 16 | Pi fits | PASS (CAD) | `base_components`: 85 × 56 board; 7 mm standoffs; standard M2.5 pattern |
| 17 | Pi removable | PASS (CAD) | `tray_removal`: Nine withdrawal positions plus continuous conservative sweep; Pi driver check |
| 18 | 70 × 50 × 22 cooler reservation fits | PASS (CAD) | `base_components`: Enclosure fit only; choose Pi-5-compatible hardware |
| 19 | Useful air above cooler | PASS (CAD) | `cooler_air`: 26.4 mm clear to roof |
| 20 | Pi intake open | PASS (CAD) | `base_components`: 854.826 mm² through-area, 7 mm under-board route |
| 21 | Rear exhaust open | PASS (CAD) | `cooler_air`: 688.915 mm² through-area, independent speaker grille |
| 22 | USB plugs fit | PASS (CAD) | `plugs`: Two 16 × 18 mm moulding sections |
| 23 | Ethernet plug fits | PASS (CAD) | `plugs`: 18 × 18 mm moulding section |
| 24 | USB-C plug fits | PASS (CAD) | `plugs`: 14 × 9 mm moulding section |
| 25 | Both micro-HDMI plugs fit | PASS (CAD) | `plugs`: Two 11 × 10 mm moulding sections |
| 26 | Bottom cover fits | PASS (CAD) | `base_mates`: Actual exported tray/shell mating surfaces |
| 27 | Feet clear speaker grille | PASS (CAD) | `feet`: Four Ø11.5 recesses at X±68/Y±42 |
| 28 | Feet clear Pi intake | PASS (CAD) | `feet`: 8.8 mm exposed foot height; screw heads also clear |
| 29 | Screw bosses clear hardware | PASS (CAD) | `base_components`: Head/base component, carrier, plug and tool checks |
| 30 | Static stability model | PASS (CAD) | `stability`: 34.40 mm minimum COM margin in 16384 assumed-mass cases |

## Mesh verification

All production meshes have positive volume, zero boundary/nonmanifold edges,
consistent winding and exactly one connected surface shell. CGAL export logs
report simple solids. Tests do not replace a slicer check for print supports.

| Production STL | Print bounds, mm | Volume, mm³ | Shells |
|---|---|---:|---:|
| head_shell.stl | 138.0 × 123.8947 × 65.4 | 152801.98 | 1 |
| head_rear_cover.stl | 138.0 × 112.0 × 5.6 | 46993.1 | 1 |
| front_bezel.stl | 118.0 × 78.0 × 9.0 | 9893.09 | 1 |
| base_shell.stl | 174.0 × 114.0 × 63.0 | 118960.42 | 1 |
| base_bottom_cover.stl | 174.0 × 114.0 × 54.0 | 68483.08 | 1 |
| camera_blank_insert.stl | 47.0 × 25.0 × 10.8 | 2734.26 | 1 |
| camera_lens_insert.stl | 47.0 × 25.0 × 10.8 | 2612.81 | 1 |
| universal_speaker_mount.stl | 86.0 × 86.0 × 3.0 | 12881.25 | 1 |
| camera_carrier.stl | 50.0 × 20.0 × 2.4 | 2022.83 | 1 |
| round_microphone_holder.stl | 40.0 × 31.0 × 5.0 | 1588.18 | 1 |
| button_actuator.stl | 15.0 × 15.0 × 10.8 | 765.64 | 1 |
| tactile_button_holder.stl | 23.0 × 20.0 × 14.0 | 2451.3 | 1 |

Eight coupon files also pass the same checks: button and camera each have three
intentional pieces, cover has two, and the other five each have one. Production
meshes, fit meshes, final PNGs, source hashes and clearance logs are packaged.

## What was fixed and what remains unchanged

The six original head-related STLs are byte-identical. Fresh exports were compared
by exact oriented facets (allowing only triangle-list ordering), then original
bytes retained. SHA256 values are in `final_dimensions_and_stability.json`.
No change to the TFT offset, bosses, neck shape, head outline, rear cover or
camera parts was needed. The head translates up 1 mm as an assembled unit.

All eight final CAD PNGs are 1600 x 1200. The front-left, front-right and side
views were visually inspected: the 66 mm rounded tapered base remains below
the dominant head, the old side speaker grille is absent and the bottom grille
is hidden in normal desk views. The full black front is a paint finish on the
original shell face plus the original bezel. Cutaway details use actual mesh
crops, with simplified hardware references and transparent service/air reserves.

The repository's old microphone holder was rectangular and its button hole
was sized for a panel switch. New removable adapters accommodate a round board
and tactile switch. The first check found a 54.79 mm³ camera-carrier/round-mic
overlap; moving the same carrier 5 mm lower on its existing slotted rails fixed
it. Remove the round mic adapter before camera-insert withdrawal. The divider
was lowered to Z=54 for the full 8 mm neck bend region, leaving a 9 mm upper gap.
Base screw-boss ramps were clipped to the tapered exterior to eliminate visible
bumps. Final checks use these corrected meshes.

Nineteen named CGAL checks cover real exported parts and conservative component,
plug, driver, acoustic and cable volumes. Zero-volume seating contacts use a
0.02 mm numerical displacement. Motion checks sample nine tray, five carrier,
ten TFT and eight camera poses. A further tray check uses continuous conservative
vertical sweeps for hardware/upper structure and four low-lip positions. TFT and
camera removal remain discrete checks, without flexible-cable simulation. Remove
the optional camera carrier before TFT withdrawal. Slide the tray vertically while supported.

## Stability sensitivity and physical uncertainties

Printed mass 55–100% of solid mesh at assumed 1.25 g/cm3; TFT 60–110g, ESP32 8–16g, mic 2–6g, camera 4–10g, switch 1–5g, Pi 40–70g, cooler 10–60g, amp 2–10g; speaker 80–500g with COM x=-48..-28,y=-10..10,z=26..50mm. Camera fitted. Feet excluded from mass (conservative height).

The 16384 combinations give COM bounds [[-24.606, -7.595, 46.433], [6.269, 4.95, 90.495]] mm,
minimum margin **34.405 mm** inside the conservative foot-centre rectangle
(136 × 84 mm), and minimum ideal static tilt angle **21.10°**.
The 10° head tilt is included in every transformed mesh centroid. These are
assumed mass sensitivity results; real mass, rubber compression/friction, cable
pull and dynamic knock tests were not measured. No unconditional stability
certification is claimed.

Before the full print order, measure the actual speaker frame, depth, terminals
and cone excursion; print its 79 mm gauge / actual-carrier coupon with the
2.5 mm gasket. Static envelope-to-grille clearance is 10.5 mm, and nominal desk
gap is 8.8 mm. Confirm those after foam/foot compression. Measure microphone
diameter, pickup side and connector heights; switch body/height/travel; camera
lens/FOV/ribbon; plug mouldings; screw lengths and printer shrinkage. Test
temperature under sustained load, fan breathing, speech, feedback and rattling.
The actual maximum-size cooler and switch remain unspecified hardware choices.

## Source control

Work is restricted to `hardware/enclosure/`. `hardware/enclosure.zip` was already
untracked outside the permitted scope and was not touched. The enclosure folder
was also untracked at the start, so `git diff` does not show these new files.
See `output/git_final.txt` and `output/changed_files.json` for final status and
the baseline-to-final file inventory. No commit. No push.
