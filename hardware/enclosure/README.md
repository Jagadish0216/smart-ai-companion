# Smart AI Companion — final down-firing CAD revision

OpenSCAD **2021.01**, millimetres. The **174 × 114 × 66 mm** base target is
achieved with a **160 × 102 mm** top and R16 rounded taper. The actual speaker
reservation is **78 × 78 × 40 mm**, horizontal, cone down and magnet up. The
side speaker grille is removed. The 66 mm base is 24 mm (26.7%) shorter than
the rejected 90 mm target described in the request. The on-disk starting model
was actually 65 mm, with a smaller 75 × 62 × 38 mm speaker reservation; that
baseline is preserved in `history/before_downfiring_revision.zip`.

The existing head, rear cover, bezel, two camera inserts and camera carrier are
preserved byte-for-byte. Every part was also re-exported, and their exact
oriented facets compared before retaining the original bytes. The head/neck
local geometry remains fixed; the assembled head translates up 1 mm to meet
the new base. Its 10° tilt and all TFT geometry remain unchanged.

## Dimensions and architecture

| Feature | Final model |
|---|---|
| Head body | 138 W × 112 H × 68 D mm, 10° backward tilt |
| Base bottom / top / height | 174 × 114 / 160 × 102 / 66 mm |
| Assembled width / depth | 174 / 114 mm, excluding attached cables |
| Height without / with intended feet | 191.149 / 199.949 mm |
| Main walls / floor / divider | 2.6 / 3 / 3 mm |
| Head-to-base minimum gap | 3.043 mm |
| Speaker reference | 78 × 78 × 40 mm; X=-77..1, Y=-39..39, Z=13.5..53.5 |
| Speaker carrier | 86 × 86 × 3 mm, Z=8..11, Ø54 central opening |
| Foam gasket | 2.5 mm, on frame contact surfaces only |
| Cone-to-grille static envelope clearance | 10.5 mm above the 3 mm floor |
| Bottom grille | 58 × 58 mm active bounds; 11 rounded 3 mm slots, 2.5 mm webs |
| Feet / desk clearance | Four 10 mm rubber feet in Ø11.5 × 1.2 mm recesses; 8.8 mm exposed |
| Foot centres | X=±68, Y=±42 mm |
| Pi PCB / mounting | 85 × 56 mm; standard 58 × 49 mm hole pattern; 7 mm standoffs |
| Pi allocated bay | Nominal 62 × 94 × 60 mm bounding region, following tapered shell and port notches |
| Cooler / free air above it | 70 × 50 × 22 mm / 26.4 mm to roof underside |
| Amplifier mounting panel | 32 × 28 mm, vertical, left/front; component keep-out 24 × 8 × 20 mm |
| Neck / bend region | 28 × 20 mm throat; full section clear for 8 mm below roof |
| Central service reserve | 11 × 23 × 35 mm (8.855 cm³), plus open surrounding routes |
| LED | One Ø5.3 hole, front centre, Z=27 mm |
| Microphone retrofit | Ø27 usable pocket, Ø26 board envelope; top acoustic holes preserved |
| Optional Camera Module 3 | 32 × 32 × 16 mm reservation; blank/lens inserts and carrier preserved |
| Tactile actuator | Ø11.5 stem in existing Ø12.3 hole; Ø15 capture flange; 10.8 mm overall length |

The accepted head in the actual repository contained a rectangular mic retainer
and a 12.3 mm panel-button hole. Removable **round_microphone_holder** and
**tactile_button_holder + button_actuator** adapt those interfaces without a
head reprint. The new microphone holder sits below the original retainer on
foam and ties, with a straight acoustic channel to the same top holes. It must
be removed before withdrawing the camera insert. The camera path itself is
unchanged. The tactile holder has a 10 × 10 mm universal shim pocket; the
6 × 6 × 5 mm switch model is a fit reference, not a measurement of the user's
unmeasured switch. See the fit-test guide before printing the full set.

The black 118 × 78 mm bezel geometry remains unchanged. For the requested
full black front, mask and finish the existing front-facing shell surface in
matte black; the body and sides remain white/off-white. The presentation shows
this paint finish on the same mesh. No larger bezel or head redesign is needed.

## Access, cooling and acoustics

Pi USB/Ethernet face rear through the 65 × 31 mm grouped cutter; USB-C and
dual micro-HDMI face right through the 52 × 25 mm group. Cuts reach through the
entire taper/corner. Actual plug bodies are collision-tested, not only sockets.
Reference maximum plug sections are USB 16 × 18, Ethernet 18 × 18, USB-C
14 × 9 and micro-HDMI 11 × 10 mm. Confirm actual cable mouldings on the coupons.

The loaded tray withdraws downward without removing the head. Disconnect all
external cables and support its service loops first. The Pi and microSD become
accessible on the withdrawn tray. The speaker lifts off its gasket, then its
carrier unscrews; no exact speaker hole pattern is assumed. Eight elongated
23 × 3.4 mm slots accept zip ties or suitable measured fasteners. A front carrier
relief clears the vertical amplifier panel. There are no bars across the cone.

Desk air enters the right bottom intake, passes the 7 mm under-board space and
open board edges, and exits the high rear vents. A partial divider separates
the speaker from this route, with a 9 mm roof gap, open ends and a 12 × 8 mm
cable passage. The 26.4 mm region above the cooler must stay free of wires.
Measured intake/exhaust values are recorded in `output/ventilation_current.json`
and `VALIDATION.md`; speaker grille area is excluded from Pi ventilation.

## Build and deliverables

```powershell
python hardware/enclosure/build.py --skip-previews
python hardware/enclosure/check_clearances.py
python hardware/enclosure/measure_ventilation.py
python hardware/enclosure/build_fit_tests.py
python hardware/enclosure/render_final.py
python hardware/enclosure/final_report.py
python hardware/enclosure/package_enclosure.py
```

Use `C:\Program Files\OpenSCAD\openscad.exe`; this machine's PATH `.com`
shim fails with a Windows DLL relocation error. No CAD framework was changed.
`companion_enclosure.scad` retains the assembly, exploded, cutaway, component,
service and print-layout modes. Select a single PART for printing; never print
the assembled reference geometry. `presentation.scad` renders real production
meshes with simplified hardware shapes, not manufacturer STEP models or concept art.

Final packages in `output/`:

- `enclosure-final-project.zip`: source, instructions, final meshes, previews and evidence.
- `enclosure-final-print-shop.zip`: current production STLs and printing/assembly guide only.
- `enclosure-final-fit-tests.zip`: eight small coupons with instructions and validation.

Obsolete bars, button coupons and previous packages are historical, excluded
from the final production ZIP. The original unrelated `hardware/enclosure.zip`
outside this task's directory remains untouched; use the new named packages.

## Physical qualification still required

CAD PASS means the stated reservations and exported meshes pass geometric
checks. It does not measure the user's speaker, prove cone travel or gasket
compression, identify the round microphone, establish switch travel, certify
fan performance, or prove tipping resistance under cable pulls. Measure the
actual speaker frame/depth, then print the speaker and other fit coupons first.
The real speaker must fit wholly within 78 × 78 × 40 mm; if it does not, stop
and revise the affected parts, never scale the speaker envelope down.

Verify microphone diameter/port orientation and retention; tactile switch body,
height and travel; camera lens alignment/FOV; actual cable plugs; fastener/pilot
fit; speaker terminal/wire exits and cone excursion. Test assembled cooling,
vibration, feedback and stability with the actual hardware. `VALIDATION.md`
distinguishes model results from these unperformed physical checks.

All changes are inside `hardware/enclosure/`. No application code, firmware,
GPIO assignments, commit or push are part of this work.
