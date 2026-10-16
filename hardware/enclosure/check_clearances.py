"""CGAL intersections of exported parts with component/plug/tool reservations."""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from build import ROOT, OUT, run

ap = argparse.ArgumentParser()
ap.add_argument("--openscad",default=r"C:\Program Files\OpenSCAD\openscad.exe")
all_checks = ["head_components","base_components","plugs","cooler_air","head_mates","base_mates","tft_drivers","camera_carrier","service_volume","tft_removal","camera_removal","speaker_separation","acoustic_path","feet","tray_removal","speaker_removal","head_adapters","neck_bend","pi_driver"]
ap.add_argument("--checks",nargs="+",choices=all_checks)
ap.add_argument("--jobs",type=int,default=3)
args = ap.parse_args()
run(args.openscad,"dimension_checks",["-o",OUT/"dimensions.echo","-D",'CHECK="assertions"',ROOT/"clearance_checks.scad"])
report_path = OUT/"clearance_validation.json"
results = json.loads(report_path.read_text()) if args.checks and report_path.exists() else {}
def check_one(check):
    target = OUT/("clearance_"+check+".stl")
    if target.exists():
        target.unlink()  # Only our named, generated diagnostic; stale collisions must not survive.
    empty = run(args.openscad,"clearance_"+check,["--export-format","binstl","-o",target,"-D",f'CHECK="{check}"',ROOT/"clearance_checks.scad"],allow_empty=True)
    return check, "clear" if empty else "INTERFERENCE: inspect clearance_"+check+".stl"
with ThreadPoolExecutor(max_workers=args.jobs) as pool:
    for future in as_completed([pool.submit(check_one,c) for c in args.checks or all_checks]):
        check,result=future.result()
        results[check]=result
        report_path.write_text(json.dumps(results,indent=2)+"\n",encoding="utf-8")
        print(check+": "+result,flush=True)
report_path.write_text(json.dumps(results,indent=2)+"\n",encoding="utf-8")
if any(v != "clear" for v in results.values()):
    raise SystemExit("Interference found; inspect and correct before release")
