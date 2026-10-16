# Assembly and servicing

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
5. Fit the camera lens or blank insert from the rear with two M3 × 8 screws,
   before fitting the microphone adapter, which crosses its insertion path.
   If installing Camera Module 3, use the preserved carrier with foam and ties
   around the rails' solid outer spines, leaving the lens and ribbon clear.
   Set the carrier centre at head Y=-25.5: 5 mm lower along the existing slots
   than the previous position. Keep the camera lens aligned with the insert.
   Check the Ø8 aperture for vignetting. The blank gives a closed finished face.
6. Place the round microphone into the Ø27 holder, pickup toward the top grille.
   Use a thin foam annulus, leaving its central acoustic aperture open. Fit
   approximately 1.9 mm foam between the new holder and the lower edge of the
   existing retainer. Secure the holder's two slotted ears with ties around the
   existing retainer walls through its rear relief; do not trust adhesive alone.
   The adapter datum is head Y=-41.5; the board face is Y=-39.5. Verify a straight
   unobstructed path to the existing central top hole. Route leads to the left.
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
otherwise cross the insert's original rearward path. To remove the TFT, first
remove the optional camera carrier from the rails, then clear the TFT wires
and remove its four screws with a ≤6 mm driver. Lift and bring the
left edge rearward first, progressively tilting to 60°, as in the checked path.
Remove the small tactile holder/actuator first if it obstructs real wiring.
Do not force the glass or ribbon. Physical cable routing is not simulated.
