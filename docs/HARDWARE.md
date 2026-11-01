# Hardware and manufacturing evidence

The completed build is owner-reported; no hardware was available for this audit.
The documented core is Raspberry Pi 5 (8 GB), ESP32 DevKit, MSP3520 3.5-inch
ILI9488 SPI TFT, I2S microphone, MAX98357A amplifier, speaker and physical button.

- [Bill of materials](../hardware/enclosure/BOM.md)
- [Wiring](../hardware/enclosure/WIRING.md)
- [Assembly](../hardware/enclosure/ASSEMBLY.md)
- [Printing](../hardware/enclosure/PRINTING.md)
- [Option A CAD and validation](../hardware/enclosure/README.md)
- [Cost-optimized Option B](../hardware/enclosure/optimized/README.md)
- [Display firmware, wiring and TFT_eSPI setup](../hardware/esp32/companion_display/README.md)
- [Optional MQTT firmware](../firmware/esp32_mqtt_companion)

The camera is optional mechanical provision, not a verified vision feature.
The BOM also labels future purchases and optional electronics; do not infer
that every reserved part was installed. The final printed variant needs owner
confirmation before recommending a specific print package as the built enclosure.

Editable OpenSCAD, build scripts, twelve-part manufacturing sets, fit tests,
clearance reports and historical source are retained. Equal head meshes across
Options A/B are intentional frozen geometry. Packaging scripts reference output
inventories/hashes; deleting logs, JSON or STL merely by extension would remove
engineering evidence or break reproducibility checks.

Regeneration starts with `python hardware/enclosure/build.py --skip-previews`
using OpenSCAD; optimized build/report/package commands are in its README.
Run in a disposable copy, because generation replaces output files. ZIP package
links in older CAD documentation may name locally generated ignored packages;
they are not promises of uploaded GitHub release assets. Checked-in STL files
remain available. CAD validation is geometric evidence, not slicer/material or
physical fit certification. The cleanup parsed a CAD assembly without rebuilding
the entire manufacturing distribution; see [verification](REPOSITORY_CLEANUP_VERIFICATION.md).
