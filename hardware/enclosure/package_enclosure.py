"""Three final ZIPs with explicit current-file manifests; no obsolete production parts."""
import hashlib,json,zipfile,shutil
from build import ROOT,OUT,PARTS,mesh_report
from build_fit_tests import FIT_OUT,TESTS
from release_inventory import write_inventory

def archive(path,files,prefix=""):
    with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as z:
        for source,name in files:z.write(source,prefix+name)
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        assert len(z.namelist())==len(set(z.namelist()))
    return dict(file=path.name,bytes=path.stat().st_size,files=len(files),sha256=hashlib.sha256(path.read_bytes()).hexdigest())

def main():
    checks=json.loads((OUT/"clearance_validation.json").read_text())
    assert len(checks)==19 and all(v=="clear" for v in checks.values())
    assert "Current top level object is empty" in (OUT/"ventilation_preservation.log").read_text()
    meshes=[mesh_report(OUT/(n+".stl")) for n in PARTS]
    assert meshes==json.loads((OUT/"mesh_validation.json").read_text())
    fits=[mesh_report(FIT_OUT/(n+".stl"),k) for n,k in TESTS.items()]
    assert fits==json.loads((FIT_OUT/"mesh_validation.json").read_text())
    for name,digest in json.loads((FIT_OUT/"source_hashes.json").read_text()).items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    req=json.loads((OUT/"requirements_validation.json").read_text())
    assert len(req)==30 and all(r['result']=='PASS (CAD)' for r in req)
    guide=OUT/"PRINT_SHOP_README.txt"
    guide.write_text("FINAL DOWN-FIRING REVISION: 174 x 114 x 66 mm base.\n"
        "Print at 100% millimetre scale. PETG, 0.20 mm layers, 0.4 mm nozzle, 3 walls, 15-20% infill.\n"
        "First confirm the separate fit-test coupons with the actual hardware.\n"
        "Print one each of the enclosed STLs, except camera choices: use blank OR lens insert,\n"
        "and print camera_carrier only when a camera is fitted. Follow PRINTING.md for supports.\n"
        "No old speaker bars or panel-switch parts belong to this revision.\n"
        "The real speaker must fit the 78 x 78 x 40 mm reference; do not scale parts to force fit.\n"
        "Four separate 10 mm rubber feet give 8.8 mm installed desk clearance.\n"
        "CAD validation is complete; physical fit, thermal/acoustic and stability tests remain necessary.\n",encoding="utf-8")
    print(json.dumps(write_inventory(),indent=2))
    production=[(OUT/(n+".stl"),n+".stl") for n in PARTS]+[(ROOT/"PRINTING.md","PRINTING.md"),(guide,guide.name)]
    fitfiles=[(FIT_OUT/(n+".stl"),n+".stl") for n in TESTS]
    fitfiles += [(FIT_OUT/(n+".log"),n+".log") for n in TESTS]
    fitfiles += [(FIT_OUT/n,n) for n in ["README.md","mesh_validation.json","source_hashes.json","fit_tests_preview.png","version.log","fit_tests_preview.log","SHA256SUMS.txt"]]
    reports=[archive(OUT/"enclosure-final-print-shop.zip",production),archive(OUT/"enclosure-final-fit-tests.zip",fitfiles)]
    shutil.copyfile(OUT/"enclosure-final-fit-tests.zip",FIT_OUT/"fit-tests-package.zip")
    files=[p for pattern in ["*.scad","*.py","*.md"] for p in ROOT.glob(pattern)]
    files += [OUT/(n+".stl") for n in PARTS]
    files += list(OUT.glob("final_*.png"))+list(OUT.glob("clearance_*.log"))
    files += [OUT/n for n in ["mesh_validation.json","clearance_validation.json","requirements_validation.json",
      "final_dimensions_and_stability.json","revision_baseline.json","changed_files.json","git_final.txt",
      "ventilation_current.json","ventilation_current_intake.svg","ventilation_current_exhaust.svg",
      "ventilation_current_intake.log","ventilation_current_exhaust.log","ventilation_preservation.log",
      "dimensions.echo","dimension_checks.log","version.log","PRINT_SHOP_README.txt"]]
    files += [OUT/(n+".log") for n in PARTS]
    files += [OUT/(n+".log") for n in ["final_front_left","final_front_right","final_side","final_rear","final_bottom","final_cutaway","final_base_internal","final_exploded"]]
    files += [OUT/(n+"_projection.svg") for n in ["front","side","top"]]
    files += [a for a,_ in fitfiles]+list(FIT_OUT.glob("*.log"))
    files += list((ROOT/"references").glob("*"))
    files += [ROOT/"history/before_downfiring_revision.zip",ROOT/"history/tray_removal_discrete.log"]
    files=sorted(set(files))
    for path in files:
        assert path.is_file(),"Required package file missing: "+str(path)
    manifest="".join(hashlib.sha256(p.read_bytes()).hexdigest()+"  "+p.relative_to(ROOT).as_posix()+"\n" for p in files)
    (OUT/"SHA256SUMS.txt").write_text(manifest,encoding="utf-8")
    files.append(OUT/"SHA256SUMS.txt")
    reports.append(archive(OUT/"enclosure-final-project.zip",[(p,p.relative_to(ROOT).as_posix()) for p in files],"enclosure-final/"))
    (OUT/"package_validation.json").write_text(json.dumps(reports,indent=2)+"\n")
    print(json.dumps(reports,indent=2))

if __name__=="__main__":main()
