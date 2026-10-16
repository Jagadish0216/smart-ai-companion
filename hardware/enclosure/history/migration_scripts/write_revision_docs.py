from pathlib import Path
R=Path(__file__).resolve().parent
(R/'README.md').write_text('''# Smart AI Companion — final down-firing CAD revision

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
| Bottom grille | 58 × 47 mm active bounds; 9 rounded 3 mm slots, 2.5 mm webs |
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

The black 118 × 78 mm bezel and its white surrounding head face are unchanged.
Painting the surrounding face black is optional; making it a larger printed
bezel would violate the frozen-head requirement.

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
the speaker from this route, with a 5 mm roof gap, open ends and a 12 × 8 mm
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

Use `C:\\Program Files\\OpenSCAD\\openscad.exe`; this machine's PATH `.com`
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
''',encoding='utf-8')
(R/'ASSEMBLY.md').write_text('''# Assembly and servicing

Disconnect power and all external cables before assembly or service. World
front is negative Y; right is positive X. Print the fit coupons and verify
actual hardware before ordering all parts. Use PETG, insulating foam and ties.

1. Deburr the prints and test pilots. Bolt the empty head/neck to the empty base
   roof with four M3 × 10 screws from below (3 mm roof, 7 mm engagement). Do this
   before the loaded tray obstructs the neck screws. The head geometry is unchanged.
2. Insert the TFT **from the rear**, header on the right in front view. Seat the
   PCB at 7.7 mm depth on the 92 × 49.5 mm bosses. The glass has 0.5 mm front
   clearance. Use four M3 × 6 plastic-threading screws with insulating washers
   as necessary; never clamp the glass against the face.
3. Seat the black bezel in its recess, securing its four posts with M3 × 8
   screws through the rear-access tower caps. Do not pry against the display.
4. Fix the 30-pin ESP32 to the removed rear-cover shelf with approximately
   3 mm nonconductive foam pads and two ties. Maintain its header/jumper space.
   Its programming USB remains accessible with the rear cover removed.
5. Place the round microphone into the Ø27 holder, pickup toward the top grille.
   Use a thin foam annulus, leaving its central acoustic aperture open. Fit
   approximately 1.9 mm foam between the new holder and the lower edge of the
   existing retainer. Secure the holder's two slotted ears with ties around the
   existing retainer walls through its rear relief; do not trust adhesive alone.
   The adapter datum is head Y=-41.5; the board face is Y=-39.5. Verify a straight
   unobstructed path to the existing central top hole. Route leads to the left.
6. Fit the camera lens or blank insert from the rear with two M3 × 8 screws.
   If installing Camera Module 3, use the preserved carrier with foam and ties
   around the rails' solid outer spines, leaving the lens and ribbon clear.
   Check the Ø8 aperture for vignetting. The blank gives a closed finished face.
7. Insert the tactile actuator **from inside**: the Ø11.5 stem passes the existing
   Ø12.3 hole and the Ø15 flange stays inside. Fit the real switch in the holder's
   10 × 10 × 1 mm recess, using shims/foam and a tie through the two slots. Seat
   its two side rails against the inside right wall using removable high-strength
   mounting tape. The nominal holder starts at head X=52; its rails end at X=66,
   leaving 0.4 mm to the wall for tape. Adjust switch/shim height so it returns
   freely and actuates before reaching its permitted travel. The model checks
   up to 0.8 mm actuator motion; do not impose that travel on a shorter-travel switch.
8. Route head wiring and optional camera ribbon through the unchanged 28 × 20
   neck throat. The 8 mm clear region below the roof lets wiring turn toward
   the central/right corridor. Keep the camera ribbon loosely curved.
9. On the removed base tray, install the Pi using four M2.5 × 6 screws in the
   7 mm standoffs. Its USB/Ethernet edge faces rear and power/HDMI edge faces right.
10. Fit the selected open cooler, at most 70 × 50 × 22 mm in the reserved location.
    Keep all 26.4 mm of upper free air clear. Check the selected cooler's mounting
    hardware against the Pi itself; the reservation is not a universal cooler mount.
11. Tape/tie the MAX98357A to the **vertical** 32 × 28 mm left/front panel, components
    and green terminal facing front. The board sits 0.5 mm ahead of the panel;
    maximum modeled component projection is 8.5 mm ahead of it. Wire the terminal
    while the tray is outside. This panel is clear of the cone and Pi airflow.
12. Attach the 86 mm speaker carrier to its four tray bosses with M3 countersunk
    screws, nominally M3 × 8. Heads must sit flush; select length giving adequate
    engagement without breaking the floor. Add the **2.5 mm foam gasket** only on
    the frame contact lands. Place the 78 × 78 × 40 speaker cone down, magnet up.
    Retain its metal-frame edges with ties through the elongated slots, clear
    of the moving cone and terminals. Do not assume a particular hole spacing.
13. Fit the optional single LED at the front Ø5.3 hole with an insulating collar.
    Connect wiring according to WIRING.md. Keep the neck and cooler clear and
    leave 60–80 mm tray service loops. Neither speaker terminal connects to ground.
14. Support the open assembly, verify polarity and test the existing display,
    audio and controls. Button/LED/UART software remains a later integration step.
15. Close the head rear cover with four M3 × 8 screws, checking the ESP32 jumpers.
16. Lift the loaded tray into the shell through the bottom. Guide Pi sockets into
    the open-bottom port groups. Fit four M3 × 8 cover screws without trapping wires.
17. Fit four nominal Ø11 × 10 mm rubber feet in the Ø11.5 × 1.2 pockets. Resulting
    desk clearance is 8.8 mm. Verify actual compressed foot height on the desk.

## Service order

Remove external cables and power. Support the tray, undo its four bottom screws,
lower it, then disconnect head harnesses. The head stays installed. Pi microSD,
Pi screws, cooler, amp and speaker are accessible. Release speaker ties, lift
the speaker, then undo its carrier screws. This is intentionally a sequential
removal; screws underneath the frame are not accessible with the speaker fitted.

Remove the rear head cover for head service. Remove the microphone adapter and
camera/carrier before withdrawing the camera insert. The new round board would
otherwise cross the insert's original rearward path. To remove the TFT, clear
its wires and remove its four screws with a ≤6 mm driver. Lift and bring the
left edge rearward first, progressively tilting to 60°, as in the checked path.
Remove the small tactile holder/actuator first if it obstructs real wiring.
Do not force the glass or ribbon. Physical cable routing is not simulated.
''',encoding='utf-8')
(R/'BOM.md').write_text('''# Bill of materials

| Item | Quantity | Mechanical provision |
|---|---:|---|
| Raspberry Pi 5, 8 GB | 1 | Right/rear tray; M2.5 mounting |
| ESP32 DOIT-style 30-pin DevKit / ESP-WROOM-32 | 1 | Existing universal rear-cover shelf |
| Actual red 3.5-inch TFT SPI 480×320 V1.0, ILI9488 configuration | 1 | Preserved rear-installation bosses and bezel |
| Actual round I2S microphone breakout | 1 | New Ø27 universal holder; measure board and port |
| MAX98357A I2S amplifier | 1 | Vertical 32×28 mounting panel |
| Actual S.R.C. Hi-Fi 4-ohm speaker | 1 | 78×78×40 reference, down-firing |
| Owned small tactile momentary switch | 1 | Shim/foam holder and printed actuator |
| Raspberry Pi Camera Module 3 and Pi 5 compatible ribbon | 0 or 1 | Preserved 32×32×16 reservation |
| Standard 5 mm LED and 220–330 ohm resistor | 0 or 1 | Future implementation, one front location |
| Open Pi heatsink/fan cooler | 1 future purchase | Up to 70×50×22 reservation |
| Jumper/service wiring and insulation | As needed | Existing intended harness, WIRING.md |
| Rubber feet, nominal Ø11×10 mm | 4 | Ø11.5×1.2 recesses; 8.8 mm nominal desk gap |
| Nonconductive foam/gasket | As needed | 2.5 mm speaker gasket; microphone/ESP32 isolation |
| Small zip ties / removable mounting tape | As needed | Universal board and speaker retention |
| M3×10 plastic-compatible neck screws | 4 | Four roof-to-neck joints |
| M3×6 TFT screws | 4 | Existing narrow TFT pilots; no heat-set inserts |
| M3×8 screws | 14 | Bezel 4, rear cover 4, base cover 4, camera insert 2 |
| M3 countersunk screws, nominal length 8 mm | 4 | Speaker carrier, verify actual engagement |
| M2.5×6 screws | 4 | Pi standoffs |
| Insulating washers / shims | As needed | Protect PCB traces and tune tactile switch |

Twelve production STLs are supplied. Print either camera blank or lens insert;
the camera carrier is optional. No speaker retainer bars are used. All purchased
mounting supplies are assembly hardware, not extra electronics. No relay,
temperature sensor, motor, servo, battery, extra ESP32/display or RGB strip is added.
''',encoding='utf-8')
fit=R/'output/fit_tests/README.md'
fit.write_text('''# Print these fit coupons before the complete enclosure

Units mm; 100% scale. Use the final printer/material and its calibrated hole
compensation. These are actual production surfaces, not independent guesses.

| Coupon | Test |
|---|---|
| TFT_FIT_TEST | Real PCB, glass, 92×49.5 holes, active offset and screw seating |
| PI_USB_ETHERNET_PORT_TEST | Pi datum mounts and actual rear taper/corner; plug both USB and Ethernet fully |
| PI_POWER_HDMI_PORT_TEST | Pi datum mounts and actual side opening; insert power and both HDMI cables together |
| BUTTON_TACTILE_TEST | Three pieces: real wall crop, actuator and holder; shim the actual switch and test return/travel |
| CAMERA_INSERT_FIT_TEST | Three pieces: wall crop, blank and lens insert; inspect lens/FOV with real camera |
| COVER_CLEARANCE_TEST | Two mating head-cover corner pieces; calibrate lip and screw pilots |
| SPEAKER_FIT_TEST | Actual 86×86×3 carrier plus corner gauges; fit the real frame, gasket, ties and terminal exits |
| MICROPHONE_FIT_TEST | Actual Ø27 holder; fit board, foam, pickup alignment and lead exit |

The speaker coupon's inside gauge is **79×79 mm**, allowing 0.5 mm per side
around the required **78×78 mm** frame. Gauge posts rise **42.5 mm above the
carrier's top**: put in the intended **2.5 mm gasket**, then verify the complete
speaker is no more than **40 mm above that seating surface**. The speaker must
also fit wholly inside the 78×78×40 CAD reservation; the extra 0.5 mm is fit
allowance, not permission to silently enlarge the hardware envelope. Check
cone projection/excursion, magnet, basket, terminals and wire bends separately.
The Ø54 opening, front amplifier relief, slots and countersinks match production.
No complete base is needed for this test. Posts are gauges, not production mounts.

The tactical switch reference is 6×6×5 mm only because the actual small switch
has not been measured. The pocket is 10×10 mm for shims; adjust height so normal
actuation precedes the switch's specified travel limit. The actuator must return
freely. Do not print/use the obsolete BUTTON_12MM_TEST as a panel-switch choice.

Camera and button coupons intentionally contain three disconnected printable
pieces, and the cover coupon two. The others have one connected shell. Separate
them in the slicer if convenient. Port coupons reproduce sloped walls and may
need local supports; do not fill their openings. Speaker gauge uprights benefit
from a brim. The coupons test fit, not thermal/acoustic/load performance.
''',encoding='utf-8')
