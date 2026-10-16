# Cost-optimized FDM quote and printing guide

Print all twelve supplied STL files for the quote. Camera blank/lens inserts are
alternatives in assembly; the camera carrier is optional hardware support.
Units **mm**, scale **100%**, **PLA / PLA+**, **0.4 mm nozzle**, **0.20 mm layers**,
**3 walls/perimeters**, **10% gyroid or grid infill**, approximately **4 top and
4 bottom layers**. Use the same profile for the preserved original quotation.
These settings are a quote baseline, not measured printer results. Thin walls
and 1.6 mm ribs need gap-fill/variable-line-width handling; inspect toolpaths.

Send model grams, support grams, total grams, print time, material and total
price for approval **before printing**. No authoritative slicer estimate was
available. Do not infer filament weight from the CAD solid-volume report.

All STLs are exported in the orientations below with their lowest face at Z=0.
Print pieces separately on a suitable bed; maximum footprint is 174 × 114 mm.

| STL | Orientation | Supports required | Support-sensitive areas |
|---|---|---|---|
| head_shell.stl | Front face down, rear opening up | **Yes, local** | Integral neck projection, microphone ledges, camera rails; inspect 8.6 mm tower ceilings and transverse holes. Paint reachable supports; keep blind pilots clear. |
| head_rear_cover.stl | Exterior rear face down, ESP32 shelf up | No | Short tie-slot bridges. |
| front_bezel.stl | Visible face down, posts up | No | Keep LCD opening clear; smooth plate preferred. |
| base_shell.stl | Roof down, bottom opening up | No, with qualified bridges | Wall/boss ramps, 3–4 mm vent bridges, Ø5.3 LED hole; local support at LED only if bridge test fails. |
| base_bottom_cover.stl | Exterior bottom down, posts/divider up | No, with qualified bridges | Ø11.5 foot-pocket roofs and 3.4 mm tie tunnels; local pocket supports only if bridge test fails. Slow thin divider walls. |
| universal_speaker_mount.stl | Flat frame face down, countersinks up | No | Diagonal webs and elongated slots; no supports in Ø54 aperture. |
| camera_blank_insert.stl | Visible disc down, ears up | **Yes, local** | Two outer attachment ears. |
| camera_lens_insert.stl | Visible disc down, ears up | **Yes, local** | Same ears; keep Ø8 lens opening clear. |
| camera_carrier.stl | Flat back down | No | Ribbon notch and tie features. |
| round_microphone_holder.stl | Flat annular face down | No | Pickup opening and tie ears. |
| button_actuator.stl | Outer button face down, stem up | No, with qualified overhang | 1.75 mm radial flange; inspect toolpath and add local support if necessary. |
| tactile_button_holder.stl | Flat back down, rails up | No | Small shim recess and tie slots. |

The head is frozen and retains its original support needs. Base parts use sparse
bed-grown ribs and wall ramps. No sliced support-volume reduction has been measured.
Preview all layers, especially thin slot webs, roof-rib starts, screw pilots and
foot bridges. A brim is optional for adhesion; remove it before checking fits.

White/off-white: head shell, rear cover, base shell, bottom cover and camera blank
if used. Black: front bezel; actuator and lens insert preferred. Hidden holders,
carrier and speaker frame: any inexpensive available colour. Finish only the
original front-facing head surface matte black to visually join the bezel;
mask microphone/camera openings and leave body sides/back white/off-white.

## Mechanical details and calibration

Base skins are 1.8 mm; bottom perimeter, foot/pilot lands and neck load land are
locally 3 mm. Speaker carrier is 2.2 mm with 2.8 mm screw pads; divider is 1.6 mm.
Do not scale globally or thin these features in the slicer. Base boss pilots retain
7 mm engagement; speaker posts provide up to 4.5 mm, above a 1.6 mm local floor.
Use suitable plastic-threading screws and inspect for cracks. Supplied exports
use 2.7 mm M3 and 2.2 mm M2.5 pilots; mating through-holes are 3.4/2.9 mm.
Do not install heat-set inserts into the default exports without regenerating
and qualifying the relevant interfaces. The frozen TFT bosses use plastic screws.

Support only required regions. Keep the acoustic grille, Pi intake, exhaust and
cable throat open. The speaker uses purchased rubber/foam and the four feet are
purchased 10 mm rubber feet, not printed permanent feet. Check actual fit, screw
length, gasket/foot compression, speaker excursion and PLA/PLA+ temperatures.
Original Option A fit coupons are available in its engineering backup; its old
base/speaker coupons do not qualify the changed Option B interfaces. Dry-fit the
optimized carrier and tray before committing to the full assembled product.
