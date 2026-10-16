# Cost optimization — Option A versus Option B

Option A is preserved in `output/archive/Smart_Companion_66mm_Original_Backup.zip`.
Option B is isolated under `optimized/`. The first tested height, 62 mm, passes;
63/64 mm fallback candidates were therefore unnecessary. Head, neck, components,
base width/depth and top footprint are preserved. Only three STL geometries change.

## Measured solid CAD volumes

Totals include all twelve supplied STLs, including both alternative camera inserts.
These are closed-mesh volumes, NOT infill/support/toolpath estimates.

| Part | Original cm³ | Optimized cm³ | Saved cm³ | Saved % |
|---|---:|---:|---:|---:|
| head_shell.stl | 152.802 | 152.802 | 0.000 | 0.00% |
| head_rear_cover.stl | 46.993 | 46.993 | 0.000 | 0.00% |
| front_bezel.stl | 9.893 | 9.893 | 0.000 | 0.00% |
| base_shell.stl | 118.960 | 79.127 | 39.834 | 33.48% |
| base_bottom_cover.stl | 68.483 | 47.075 | 21.408 | 31.26% |
| camera_blank_insert.stl | 2.734 | 2.734 | 0.000 | 0.00% |
| camera_lens_insert.stl | 2.613 | 2.613 | 0.000 | 0.00% |
| universal_speaker_mount.stl | 12.881 | 7.299 | 5.583 | 43.34% |
| camera_carrier.stl | 2.023 | 2.023 | 0.000 | 0.00% |
| round_microphone_holder.stl | 1.588 | 1.588 | 0.000 | 0.00% |
| button_actuator.stl | 0.766 | 0.766 | 0.000 | 0.00% |
| tactile_button_holder.stl | 2.451 | 2.451 | 0.000 | 0.00% |
| **Total** | **422.188** | **355.363** | **66.825** | **15.83%** |

## Engineering changes

- Base shell: side skin 2.6 → 1.8 mm; broad roof 3 → 1.8 mm; retain 3 mm local
  neck load land and two 1.6 mm ribs to perimeter. Local 2.6 mm port-edge bands.
  Accepted tapered footprint and corner radius retained; height 66 → 62 mm.
- Bottom: panel 3 → 1.8 mm; 3 mm local perimeter, feet and screw lands. Sparse
  1.6 × 3 mm ribs around speaker and Pi mounts; no ribs over acoustic/vent openings.
  Pi position/orientation and 7 mm standoffs unchanged; no solid Pi platform.
- Speaker: unchanged 78 × 78 × 40 envelope moved down 2.7 mm. Carrier 86 × 86,
  3 → 2.2 mm nominal, local 2.8 mm pads; outer frame + Ø54 acoustic ring + four
  diagonal webs replace the broad plate. Existing slots and front amplifier relief
  retained. Four posts have 4.5 mm pilot depth above 1.6 mm closed local floors.
- Divider: 3 → 1.6 mm, top Z54 → Z50; two thin triangular gussets. The divider
  stays between speaker/Pi, with the existing cable notch and top service gap.
- Amplifier: solid 32 × 28 × 3 panel becomes 2.2 mm rails with a 17 × 20 mm
  central opening; zip-tie slots retained and no broad supporting shelf.
- Base bosses: diameter 9 → 8 mm, straight height 9 → 8 mm; original 7 mm screw
  pilot engagement and 12 mm wall ramps retained. No blind decorative mass.
- Supports: roof-down base uses existing wall ramps/open port notches. New ribs,
  feet lands and carrier frame print from the bed. No new support-heavy platforms.
  Frozen head/camera supports remain; actual support savings cannot be quantified
  without a slicer. LED/foot-pocket bridges require shop review.

## Slicer and price

No authoritative slicer estimate was available. PATH, Windows installed-program
registry, Program Files, Program Files (x86), LocalAppData/Programs and Downloads
were checked for PrusaSlicer, OrcaSlicer, CuraEngine, Bambu Studio and Slic3r.
No software was installed. Filament grams, support grams, print hours, price and
the ≤300 g target are unverified. Do not multiply CAD volume by density and call
it a sliced estimate. Quote both options with identical settings: PLA/PLA+, 0.4 mm,
0.20 mm layers, 3 walls, 10% gyroid/grid, 4 top/bottom layers, supports only as needed.

## Final comparison

| Attribute | Original | Optimized |
|---|---|---|
| Base width | 174 mm | 174 mm |
| Base depth | 114 mm | 114 mm |
| Base height | 66 mm | 62 mm |
| Head dimensions | 138 × 112 × 68 mm | Identical; nine STL hashes match |
| Speaker | S.R.C. 4 Ω, 78 × 78 × 40 mm | Same; cone down |
| CAD volume | 422.188 cm³ | 355.363 cm³ |
| Pi cooler free air | 26.4 mm | 22.400 mm |
| Speaker grille gap | 10.5 mm | 9.000 mm |
| Desk gap | 8.8 mm | 8.8 mm |
| Intake | 854.826 mm² | 854.826 mm² |
| Exhaust | 688.915 mm² | 688.915 mm² |
| Stability margin | 34.405 mm | 34.113 mm |
| Ideal tipping angle | 21.103° | 20.809° |
| Validation | Accepted CAD baseline | 19 collision checks + 12 meshes PASS; structural review PASS; physical tests pending |
