# Print these fit coupons before the complete enclosure

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

The tactile switch reference is 6×6×5 mm only because the actual small switch
has not been measured. The pocket is 10×10 mm for shims; adjust height so normal
actuation precedes the switch's specified travel limit. The actuator must return
freely. Do not print/use the obsolete BUTTON_12MM_TEST as a panel-switch choice.

Camera and button coupons intentionally contain three disconnected printable
pieces, and the cover coupon two. The others have one connected shell. Separate
them in the slicer if convenient. Port coupons reproduce sloped walls and may
need local supports; do not fill their openings. Speaker gauge uprights benefit
from a brim. The coupons test fit, not thermal/acoustic/load performance.
