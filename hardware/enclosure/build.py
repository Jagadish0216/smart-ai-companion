"""Repeatable OpenSCAD 2021.01 exports, previews and mesh checks. No third-party dependencies.
Run from any directory: python hardware/enclosure/build.py
"""
from pathlib import Path
import argparse
import json
import re
import shutil
import struct
import subprocess
import time
import hashlib
import zipfile
from collections import defaultdict

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
PARTS = {
    "head_shell": ("head_shell.scad", []),
    "head_rear_cover": ("head_rear_cover.scad", []),
    "front_bezel": ("front_bezel.scad", []),
    "base_shell": ("base_shell.scad", []),
    "base_bottom_cover": ("base_bottom_cover.scad", []),
    "camera_blank_insert": ("camera_insert.scad", ["CAMERA_LENS=false"]),
    "camera_lens_insert": ("camera_insert.scad", ["CAMERA_LENS=true"]),
    "universal_speaker_mount": ("speaker_mount.scad", []),
    "camera_carrier": ("camera_insert.scad", ["CAMERA_CARRIER=true"]),
    "round_microphone_holder": ("head_adapters.scad", ['ADAPTER="microphone"']),
    "button_actuator": ("head_adapters.scad", ['ADAPTER="button_actuator"']),
    "tactile_button_holder": ("head_adapters.scad", ['ADAPTER="button_holder"']),
}
FROZEN_PARTS = {"head_shell", "head_rear_cover", "front_bezel", "camera_blank_insert", "camera_lens_insert", "camera_carrier"}

def preserve_frozen_export(name):
    """Re-export first; keep original bytes only after exact oriented-facet equality."""
    if name not in FROZEN_PARTS:
        return
    path=OUT/(name+'.stl')
    with zipfile.ZipFile(ROOT/'history/before_downfiring_revision.zip') as z:
        original=z.read('output/'+name+'.stl')
    current=path.read_bytes()
    def facets(data):
        result=[]
        for offset in range(84,len(data),50):
            values=struct.unpack_from('<12fH',data,offset)
            vertices=tuple(tuple(values[j:j+3]) for j in (3,6,9))
            result.append(min(vertices,vertices[1:]+vertices[:1],vertices[2:]+vertices[:2]))
        return sorted(result)
    if facets(original)!=facets(current):
        raise ValueError('Frozen head geometry changed: '+name)
    path.write_bytes(original)
    print(name+': exact oriented facets preserved; original STL bytes retained',flush=True)

def run(exe, name, args, allow_empty=False):
    start = time.monotonic()
    command = [exe, "--hardwarnings"] + [str(v) for v in args]
    p = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log = p.stdout + p.stderr
    (OUT / (name + ".log")).write_text(log, encoding="utf-8")
    empty = "Current top level object is empty" in log
    bad = bool(re.search(r"WARNING:|ERROR:|not a valid|not closed|Can't open", log, re.I))
    if bad or (p.returncode and not (allow_empty and empty)):
        raise RuntimeError(f"{name} failed ({p.returncode}):\n{log}")
    print(f"{name}: {'EMPTY / clear' if empty else 'OK'} ({time.monotonic()-start:.1f} s)", flush=True)
    return empty

def mesh_report(path, expected_shells=1):
    data = path.read_bytes()
    if len(data) < 84:
        raise ValueError(f"Empty/truncated STL: {path}")
    count = struct.unpack_from("<I", data, 80)[0]
    if len(data) == 84 + 50 * count:
        triangles = [struct.unpack_from("<12fH", data, 84+i*50)[3:12] for i in range(count)]
    else:
        numbers = re.findall(rb"vertex\s+([-+\d.eE]+)\s+([-+\d.eE]+)\s+([-+\d.eE]+)", data)
        vertices = [tuple(float(v) for v in p) for p in numbers]
        triangles = [sum(vertices[i:i+3], ()) for i in range(0,len(vertices),3)]
        count = len(triangles)
    if not count:
        raise ValueError(f"No triangles: {path}")
    ids = {}
    xyz = []
    edges = defaultdict(list)
    volume = 0.0
    parent = list(range(count))
    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n
    for i,t in enumerate(triangles):
        points = [tuple(t[j:j+3]) for j in (0,3,6)]
        inds = []
        for p in points:
            # Exact binary-STL vertices: welding at 1e-5 mm falsely collapses tiny
            # but distinct CGAL chamfer/ramp triangles and changes the topology.
            key = p
            if key not in ids:
                ids[key] = len(ids)
                xyz.append(p)
            inds.append(ids[key])
        if len(set(inds)) != 3:
            raise ValueError(f"Degenerate facet in {path.name}")
        a,b,c = points
        volume += (a[0]*(b[1]*c[2]-b[2]*c[1]) + a[1]*(b[2]*c[0]-b[0]*c[2]) + a[2]*(b[0]*c[1]-b[1]*c[0]))/6
        for j in range(3):
            u,v = inds[j], inds[(j+1)%3]
            edges[tuple(sorted((u,v)))].append((i, u < v))
    bad_edges = sum(len(v) != 2 for v in edges.values())
    inconsistent = sum(len(v)==2 and v[0][1]==v[1][1] for v in edges.values())
    for incident in edges.values():
        if len(incident) == 2:
            a,b = find(incident[0][0]), find(incident[1][0])
            parent[a] = b
    shells = len({find(i) for i in range(count)})
    bounds = [[min(p[d] for p in xyz) for d in range(3)], [max(p[d] for p in xyz) for d in range(3)]]
    result = dict(file=path.name,bytes=len(data),triangles=count,
                  bounds_mm=[[round(v,4) for v in p] for p in bounds],
                  size_mm=[round(bounds[1][d]-bounds[0][d],4) for d in range(3)],
                  signed_volume_mm3=round(volume,2),boundary_or_nonmanifold_edges=bad_edges,
                  inconsistent_edges=inconsistent,connected_surface_shells=shells)
    if bad_edges or inconsistent or volume <= 0 or shells != expected_shells:
        raise ValueError(f"Mesh check failed: {result}")
    return result

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--openscad", default=r"C:\Program Files\OpenSCAD\openscad.exe")
    ap.add_argument("--only", nargs="*", choices=list(PARTS))
    ap.add_argument("--skip-stl", action="store_true")
    ap.add_argument("--skip-previews", action="store_true")
    args = ap.parse_args()
    exe = args.openscad if Path(args.openscad).is_file() else shutil.which("openscad")
    if not exe:
        raise SystemExit("OpenSCAD not found")
    OUT.mkdir(exist_ok=True)
    run(exe,"version",["--version"])
    run(exe,"source_parse",["-o",OUT/"source_parse.csg",ROOT/"companion_enclosure.scad"])
    selected = args.only or list(PARTS)
    if not args.skip_stl:
        for name in selected:
            source,defines = PARTS[name]
            cmd = ["--export-format", "binstl", "-o", OUT/(name+".stl")]
            for define in defines:
                cmd.extend(["-D",define])
            run(exe,name,cmd+[ROOT/source])
            preserve_frozen_export(name)
    report = [mesh_report(OUT/(name+".stl")) for name in PARTS if (OUT/(name+".stl")).exists()]
    (OUT/"mesh_validation.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(f"Validated {len(report)} closed, consistently oriented, single-shell meshes",flush=True)
    if not args.skip_previews:
        for mode in ["assembly","rear","exploded","cutaway","print_layout","components","head_service","base_service"]:
            camera = "0,0,90,72,0,30,450"
            if mode == "rear": camera = "0,0,90,72,0,210,450"
            if mode == "print_layout": camera = "0,0,0,25,0,0,650"
            if mode == "head_service": camera = "0,0,0,12,0,0,450"
            if mode == "base_service": camera = "0,0,0,30,0,20,350"
            source_mode = "assembly" if mode=="rear" else mode
            run(exe,mode+"_preview",["-o",OUT/(mode+"_preview.png"),"--imgsize=1600,1000",
                "--viewall","--autocenter","--projection=o","--colorscheme=Tomorrow",
                "--camera="+camera,"-D","MESH_PREVIEW=true","-D",f'MODE="{source_mode}"',ROOT/"companion_enclosure.scad"])
        for view in ["front","side","top"]:
            run(exe,view+"_projection",["-o",OUT/(view+"_projection.svg"),
                "-D",f'VIEW="{view}"',ROOT/"projections.scad"])

if __name__ == "__main__":
    main()
