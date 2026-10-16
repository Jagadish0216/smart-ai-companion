# Smart Companion — independent cost-optimized Option B

Use `output/Smart_Companion_Cost_Optimized_Print_Package.zip` for quotations.
Accepted Option A remains unchanged one directory above; its immutable release
is `../output/archive/Smart_Companion_66mm_Original_Backup.zip`.

Only the base shell, bottom cover and speaker carrier change. Target: 174 × 114
× 62 mm base; frozen 138 × 112 × 68 mm head and neck; same large down-facing
78 × 78 × 40 mm speaker. Read VALIDATION.md and COST_OPTIMIZATION.md for results.

Reproduce using installed Python and OpenSCAD 2021.01, without external Python
dependencies:

1. `python build.py --skip-previews`
2. `python check_clearances.py --jobs 3`
3. `python measure_ventilation.py`
4. `python engineering_report.py`
5. `python render_final.py`
6. `python render_comparison.py`
7. `python package_optimized.py`

Keep the full project's Option A reference files alongside this directory: the
exporter compares every frozen head mesh to the archived original before retaining
its bytes. Engineering reporting recomputes both versions' mesh volumes and
mass-sensitivity stability. Package validation checks exact inventories, hashes,
PNG dimensions and ZIP CRCs. No slicer result is claimed.
