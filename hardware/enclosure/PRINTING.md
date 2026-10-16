# FDM printing guide

Before the full print, use `output/fit_tests/fit-tests-package.zip` and follow
`output/fit_tests/README.md`. It contains eight low-material tests for the TFT,
both Pi port groups, button, camera inserts and removable-cover fit. Use the
same material and printer compensation intended for the final enclosure.

Exported STL coordinates already put each piece in its recommended orientation
with its lowest face at Z=0. Units are **mm**; use **100% scale**. Parts fit
individually on a nominal 220 x 220 mm bed. If generating the optional print-layout
preview, treat it as a catalogue larger than one bed, not a single print job.

Default material: **PETG**, 0.20 mm layers, 0.4 mm nozzle, **3 perimeters**,
**20% infill** (15% acceptable on nonstructural pieces), 5 top/bottom layers.
Use local 5-perimeter modifiers around neck joints and screw bosses if the
slicer supports them. The bottom panel and bezel are largely solid at these
skin settings. Calibrate flow and hole compensation for the actual printer.
PLA can be used for fit trials, but PETG is preferred near the Pi for the final
prototype. No untested material-temperature limit is claimed.

| STL | Quantity | Orientation already exported | Supports / bridging | Infill / walls |
|---|---:|---|---|---|
| `head_shell.stl` | 1 | Front face down; rear opening upward; integral neck extends to one side | **Local supports required** under projecting neck, microphone-pocket ledges and camera rails. Inspect bezel tower ceilings (8.6 mm span) and horizontal button/neck holes. Use painted/tree supports reachable from rear; keep supports out of blind pilots. Do not fill the whole cavity with dense support. | 20% / 3, local 5 around neck and bosses |
| `head_rear_cover.stl` | 1 | Exterior rear face down, ESP32 shelf upward | No support; short tie slots through panel. | 15-20% / 3 |
| `base_shell.stl` | 1 | Top roof down, bottom opening upward | Normally no support. Boss ramps grow from walls at approximately 45 degrees. Grouped port notches are open at the upper print edge, so no 65 mm bridge. Grille/vent bridges span only 3-4 mm; LED opening may need a small local support if the printer cannot bridge 5.3 mm. | 20% / 3 |
| `base_bottom_cover.stl` | 1 | Outer bottom down; standoffs and divider upward | No support. Foot recess roofs bridge 11.5 mm: use a calibrated bridge profile; add **local support only inside foot pockets** if needed. Tie tunnels bridge 3.4 mm. Keep narrow divider straight with slow outer walls. | 20% / 3 |
| `front_bezel.stl` | 1 | Visible front face down, posts upward | No support; large LCD opening remains open to bed. **Black PETG** recommended, smooth plate for visible face. | 15-20% / 3 |
| `camera_blank_insert.stl` | 1 initially | Visible disc down, ears up | Local supports under the two outer ears; clean them so they seat flat. The front surface is closed. Black or body colour. | 20% / 3 |
| `camera_lens_insert.stl` | 1 alternate | Same as blank | Same ear supports; keep the 8 mm lens opening clear. **Use instead of blank**, not simultaneously. | 20% / 3 |
| `universal_speaker_mount.stl` | 1 | Flat on bed | No support. Check 3.4 mm slots remain open and the slot webs and the front amplifier-panel relief print cleanly. | 20% / 3 |
| `round_microphone_holder.stl` | 1 | Flat annular face on bed | No support; keep the pickup opening and tie ears clear. | 20% / 3 |
| `button_actuator.stl` | 1 | Outer button face down; flange and stem upward | Local flange overhang is 1.75 mm; inspect bridging. | 100% / 3 |
| `tactile_button_holder.stl` | 1 | Flat back plate down, two rails upward | No support. Fit the actual switch with shims. | 20% / 3 |
| `camera_carrier.stl` | 0 or 1 | Flat on bed | No support. Optional universal backing for an installed camera, with a ribbon notch. | 20% / 3 |

The camera ears and head overhangs are intentional small support regions. This
design is **not** advertised as entirely support-free. Inspect the sliced tool
paths, particularly the neck attachment and microphone pocket, before printing.
Use a 5-8 mm brim on the head or base if PETG adhesion requires it; trim it fully
before measuring fits. Deburr all cable-contact edges and verify the throat with
the chosen ribbon/connector bundle.

## Fasteners and fit calibration

Default exports use **2.7 mm M3 / 2.2 mm M2.5 pilot holes** for suitable
plastic-threading screws. They are not 3.4/2.9 mm clearance holes. Cover, frame
and mating-part through-holes use **3.4 mm M3 clearance**. PCB holes are part of
the purchased boards. Use screws intended for plastic or carefully tap pilots;
do not force a large screw into a brittle print.

For an insert version, set `fastener_mode="insert"` and regenerate **all mating
parts**. The starting bores are **4.2 mm M3 / 3.6 mm M2.5** for inserts about
**4.6 / 4.0 mm OD and 4 mm long**. Confirm the actual insert maker's recommended
bore and tune the parameter; no universal heat-set insert diameter exists.
Install only the short 4 mm inserts at the entrance to the available blind bores.
Clearance dimensions remain unchanged. **The four TFT bosses always retain
2.7 mm pilots and use plastic-threading screws**, even in insert mode: their
6.4 mm outside diameter must clear the full glass outline and is too narrow
for the specified heat-set inserts. Default supplied STL files do not contain
the enlarged insert bores at the other joints.

Global fit allowances in `dimensions.scad` are per-side **0.30 mm precision**,
**0.50 mm normal removable fit**, **0.70 mm service clearance**; plug groups are
designed around **2 mm boundary allowance**. Screw and acoustic holes have their
own explicit dimensions. A printer-dependent elephant-foot correction of
roughly 0.2 mm may be appropriate, especially for the front bezel and cover lips.
Do not compensate a poor first layer by scaling the whole assembly.

After printing, check panel lips and the camera plug by hand, clean pilots,
verify screw length/depth, and dry-fit every board before applying power.
Do not use support-removal force against thin camera ears or the LCD glass.

## Final down-firing package notes

Only the twelve named production STLs listed above belong in the print-shop
package. Do not print historical speaker bars or the old panel-button coupon.
The camera blank and lens inserts are alternatives, and the camera carrier is
optional. The production bottom tray includes the vertical amplifier panel,
54 mm partial divider and central rib, so its overall print height is 54 mm.
The speaker carrier is 86 × 86 × 3 mm; its M3 heads must be countersunk flush.
The bottom speaker slots are 3 mm wide with 2.5 mm webs; do not block them with
supports, paint, foam or feet. Four purchased rubber feet are 10 mm tall and
seat 1.2 mm, leaving 8.8 mm clearance; do not print rigid substitutes in place.

Walls are nominally 2.6 mm, the floor/roof 3 mm, the divider 3 mm and the central
rib 3 mm wide × 3 mm tall above the floor. The base has a continuous rounded
174 × 114 to 160 × 102 taper. No unsupported giant acoustic hole is present.

Use the fit coupons first. Small pilot holes and universal adhesive/tie mounts
require the shop's calibration and actual hardware measurements. Review supports
for the original head and camera ears. Production geometry has been checked for
closed surfaces and collisions, but a slicer support/toolpath review and physical
fit, heat and stability tests are required before treating it as qualified.

Finish the original front-facing head surface matte black to join the black bezel into a full black front; mask the microphone and camera openings. Keep the body sides/back white or off-white. This is a surface finish only; the frozen head STL is unchanged.
