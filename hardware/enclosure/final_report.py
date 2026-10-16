"""Final dimensional, mesh, stability-sensitivity and requirements evidence."""
from pathlib import Path
import hashlib,itertools,json,math,struct
from build import ROOT,OUT,PARTS,FROZEN_PARTS,mesh_report
from build_fit_tests import TESTS,FIT_OUT

def triangles(path):
    data=path.read_bytes()
    for off in range(84,len(data),50):
        t=struct.unpack_from('<12fH',data,off)
        yield [tuple(t[i:i+3]) for i in (3,6,9)]

def properties(path,transform):
    lo=[math.inf]*3;hi=[-math.inf]*3;vol=0;mom=[0,0,0]
    for face in triangles(path):
        pts=[transform(p) for p in face]
        a,b,c=pts
        v=(a[0]*(b[1]*c[2]-b[2]*c[1])+a[1]*(b[2]*c[0]-b[0]*c[2])+a[2]*(b[0]*c[1]-b[1]*c[0]))/6
        vol+=v
        for k in range(3):
            mom[k]+=v*sum(p[k] for p in pts)/4
            lo[k]=min(lo[k],*(p[k] for p in pts));hi[k]=max(hi[k],*(p[k] for p in pts))
    return dict(bounds=[lo,hi],volume_mm3=vol,centroid=[v/vol for v in mom])

def head(p):
    a=math.radians(-100);x,y,z=p
    return [x,-32+y*math.cos(a)-z*math.sin(a),136+y*math.sin(a)+z*math.cos(a)]

transforms={
 'head_shell':head,
 'head_rear_cover':lambda p:head([p[0],-p[1],68-p[2]]),
 'front_bezel':lambda p:head([p[0],p[1],p[2]+0.2]),
 'camera_lens_insert':lambda p:head([p[0],p[1]-36.5,p[2]+0.6]),
 'camera_carrier':lambda p:head([p[0],p[1]-25.5,p[2]+32]),
 'base_shell':lambda p:[p[0],-p[1],66-p[2]],
 'base_bottom_cover':lambda p:p,
 'universal_speaker_mount':lambda p:[p[0]-38,p[1],p[2]+8],
 'round_microphone_holder':lambda p:head([p[0],-41.5+p[2],44-p[1]]),
 'button_actuator':lambda p:head([70-p[2],27+p[1],41+p[0]]),
 'tactile_button_holder':lambda p:head([52+p[2],27+p[1],41-p[0]]),
}

def main():
    checks=json.loads((OUT/'clearance_validation.json').read_text())
    if len(checks)!=19 or any(v!='clear' for v in checks.values()):
        raise SystemExit('All 19 clearance checks must pass before final reporting')
    parts={n:properties(OUT/(n+'.stl'),t) for n,t in transforms.items()}
    bounds=[[min(v['bounds'][0][k] for v in parts.values()) for k in range(3)],
            [max(v['bounds'][1][k] for v in parts.values()) for k in range(3)]]
    size=[bounds[1][k]-bounds[0][k] for k in range(3)]
    frozen={}
    baseline=json.loads((OUT/'revision_baseline.json').read_text())
    for n in sorted(FROZEN_PARTS):
        key='output\\'+n+'.stl'
        original=baseline.get(key,baseline.get(key.replace('\\','/')))
        digest=hashlib.sha256((OUT/(n+'.stl')).read_bytes()).hexdigest()
        assert digest==original,n
        frozen[n]=digest
    # Assumed mass sensitivity, not measured assembled mass or tipping certification.
    groups=[]
    for names in [set(parts)-{'base_shell','base_bottom_cover','universal_speaker_mount'},
                  {'base_shell','base_bottom_cover','universal_speaker_mount'}]:
        volume=sum(parts[n]['volume_mm3'] for n in names)
        cog=[sum(parts[n]['volume_mm3']*parts[n]['centroid'][k] for n in names)/volume for k in range(3)]
        groups.append((volume*0.00125*0.55,volume*0.00125,cog))
    groups += [(60,110,head([0,12.5,10])),(8,16,head([-24,12,54])),
               (2,6,head([0,-38.7,44])),(4,10,head([0,-36.5,20])),
               (1,5,head([57,27,41])),(40,70,[52,10.5,15]),
               (10,60,[50,4,25.6]),(2,10,[-38,-47,17])]
    cogs=[]
    for weights in itertools.product(*[(g[0],g[1]) for g in groups]):
        for smass,sx,sy,sz in itertools.product([80,500],[-48,-28],[-10,10],[26,50]):
            total=sum(weights)+smass
            cogs.append([(sum(w*g[2][k] for w,g in zip(weights,groups))+smass*[sx,sy,sz][k])/total for k in range(3)])
    cog_bounds=[[min(p[k] for p in cogs) for k in range(3)], [max(p[k] for p in cogs) for k in range(3)]]
    margin=min(min(68-abs(p[0]),42-abs(p[1])) for p in cogs)
    tip_angle=min(math.degrees(math.atan(min(68-abs(p[0]),42-abs(p[1]))/(p[2]+8.8))) for p in cogs)
    assert margin>0
    vents=json.loads((OUT/'ventilation_current.json').read_text())
    intake=vents['intake']['through_area_mm2'];exhaust=vents['exhaust']['through_area_mm2']
    assert intake>=700 and exhaust>=600
    facts={
       'head_body_WHD_mm':[138,112,68], 'base_bottom_WD_mm':[174,114],
       'base_top_WD_mm':[160,102], 'base_height_mm':66,
       'assembled_mesh_bounds_mm':bounds,'assembled_WDH_without_feet_mm':size,
       'assembled_WDH_with_feet_mm':[size[0],size[1],size[2]+8.8],
       'speaker_envelope_mm':[78,78,40],'speaker_carrier_mm':[86,86,3],
       'speaker_opening_diameter_mm':54,'speaker_to_grille_mm':10.5,
       'grille_active_bounds_mm':[58,58],'desk_gap_mm':8.8,
       'cooler_reservation_mm':[70,50,22],'cooler_air_mm':26.4,
       'intake_mm2':intake,'exhaust_mm2':exhaust,'exhaust_intake_ratio':exhaust/intake,
       'frozen_head_sha256':frozen,
       'stability':{'status':'PASS within assumed mass sensitivity; physical test unperformed',
          'assumptions':'Printed mass 55–100% of solid mesh at assumed 1.25 g/cm3; TFT 60–110g, ESP32 8–16g, mic 2–6g, camera 4–10g, switch 1–5g, Pi 40–70g, cooler 10–60g, amp 2–10g; speaker 80–500g with COM x=-48..-28,y=-10..10,z=26..50mm. Camera fitted. Feet excluded from mass (conservative height).',
          'cases':len(cogs),'COM_bounds_mm':cog_bounds,'minimum_foot_centre_margin_mm':margin,
          'minimum_static_tilt_angle_deg':tip_angle},
       'printed_part_assembled_properties':parts,
    }
    (OUT/'final_dimensions_and_stability.json').write_text(json.dumps(facts,indent=2)+'\n')
    rows=[
      ('TFT fits','head_components','98 × 56.34 PCB, 14.18 depth; frozen head'),
      ('TFT mounting holes align','head_components','92 × 49.5 centres; unchanged oriented facets'),
      ('TFT screws reachable','tft_drivers','Four Ø6 driver paths; rear cover removed'),
      ('ESP32 fits','head_components','51.5 × 28.5 PCB and existing jumper/header space'),
      ('Round microphone fits','head_adapters','Ø26 board / Ø27 pocket; physical board to measure'),
      ('Camera Module 3 fits','camera_carrier','32 × 32 × 16 reservation; unchanged carrier placed 5 mm lower'),
      ('Tactile switch and actuator fit','head_adapters','6 × 6 × 5 reference; universal shim holder; 0.8 motion check'),
      ('Neck wiring space','neck_bend','28 × 20 throat plus 8 mm clear bend region'),
      ('Speaker 78 × 78 × 40 fits','base_components','Unreduced cuboid, X=-77..1, Y=-39..39, Z=13.5..53.5'),
      ('Speaker frame clears walls','base_components','Actual tapered shell versus full reservation'),
      ('Speaker / magnet clear Pi','speaker_separation','Whole speaker envelope separate from Pi/cooler'),
      ('Speaker clears amplifier','speaker_separation','Vertical front-left panel and terminal reservation'),
      ('Cone acoustic path clear','acoustic_path','Ø50 clear cylinder above floor through Ø54 carrier'),
      ('Grille cannot contact static cone envelope','acoustic_path','10.5 mm gap; actual excursion still to verify'),
      ('Carrier installs and removes','speaker_removal','Five upward removal positions on withdrawn tray'),
      ('Pi fits','base_components','85 × 56 board; 7 mm standoffs; standard M2.5 pattern'),
      ('Pi removable','tray_removal','Nine withdrawal positions plus continuous conservative sweep; Pi driver check'),
      ('70 × 50 × 22 cooler reservation fits','base_components','Enclosure fit only; choose Pi-5-compatible hardware'),
      ('Useful air above cooler','cooler_air','26.4 mm clear to roof'),
      ('Pi intake open','base_components',f'{intake:.3f} mm² through-area, 7 mm under-board route'),
      ('Rear exhaust open','cooler_air',f'{exhaust:.3f} mm² through-area, independent speaker grille'),
      ('USB plugs fit','plugs','Two 16 × 18 mm moulding sections'),
      ('Ethernet plug fits','plugs','18 × 18 mm moulding section'),
      ('USB-C plug fits','plugs','14 × 9 mm moulding section'),
      ('Both micro-HDMI plugs fit','plugs','Two 11 × 10 mm moulding sections'),
      ('Bottom cover fits','base_mates','Actual exported tray/shell mating surfaces'),
      ('Feet clear speaker grille','feet','Four Ø11.5 recesses at X±68/Y±42'),
      ('Feet clear Pi intake','feet','8.8 mm exposed foot height; screw heads also clear'),
      ('Screw bosses clear hardware','base_components','Head/base component, carrier, plug and tool checks'),
      ('Static stability model','stability',f'{margin:.2f} mm minimum COM margin in {len(cogs)} assumed-mass cases'),
    ]
    validation=[dict(number=i+1,check=a,result='PASS (CAD)',evidence=b,scope=c) for i,(a,b,c) in enumerate(rows)]
    (OUT/'requirements_validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    meshes=[mesh_report(OUT/(n+'.stl')) for n in PARTS]
    fits=[mesh_report(FIT_OUT/(n+'.stl'),k) for n,k in TESTS.items()]
    text='''# Final validation — 2026-10-15

OpenSCAD 2021.01 was actually executed with `--hardwarnings` for every production
STL, coupon, collision check and render. This is a CAD-validated revision;
physical hardware measurements and printed assembly qualification remain open.
No failed geometric check is waived or presented as a pass.

## Final dimensions and airflow

'''
    text+=f'Base **174 × 114 × 66 mm**, top **160 × 102 mm**, R16. The 66 mm target succeeds.\n\nHead **138 × 112 × 68 mm**, 10° tilt. Mesh-transformed assembled size is\n**{size[0]:.3f} × {size[1]:.3f} × {size[2]:.3f} mm** without feet and\n**{size[0]:.3f} × {size[1]:.3f} × {size[2]+8.8:.3f} mm** with intended feet.\nThe total is computed from actual exported vertices, not hard-coded as a target.\n\n'
    text+=f'Pi intake **{intake:.3f} mm²**, rear exhaust **{exhaust:.3f} mm²**, ratio\n**{exhaust/intake:.3f}**. Exhaust is {(1-exhaust/intake)*100:.2f}% smaller than intake.\nBoth meet 700/600 mm² minima. SVG projections integrate actual unblocked\nthrough-openings, including capsule ends and protected lands; connector and\nspeaker openings are excluded. This is geometric area, not measured airflow.\n\n'
    text+='''The 70 × 50 × 22 mm cooler envelope ends at Z=36.6, with 26.4 mm nominal
clear air above. This bounding envelope is a reservation for a **Pi-5-compatible
open cooler**, not a solid rectangular part guaranteed to mate with all onboard
connectors. The selected cooler must provide GPIO/header/connector relief and
its intended mounts; remove it first for Pi screws if it blocks their drivers.
Do not run bundles across its fan. Pi mounting bay follows the taper; its
rectangular nominal bounds are not a claim that every point of that box is empty.

## Required validation table

PASS below applies to the stated CAD reservations. Unmeasured hardware is not
silently promoted to physically confirmed fit.

| # | Requirement | Result | Evidence / scope |
|---:|---|---|---|
'''
    for row in validation:text+=f"| {row['number']} | {row['check']} | {row['result']} | `{row['evidence']}`: {row['scope']} |\n"
    text+='\n## Mesh verification\n\nAll production meshes have positive volume, zero boundary/nonmanifold edges,\nconsistent winding and exactly one connected surface shell. CGAL export logs\nreport simple solids. Tests do not replace a slicer check for print supports.\n\n| Production STL | Print bounds, mm | Volume, mm³ | Shells |\n|---|---|---:|---:|\n'
    for m in meshes:text+=f"| {m['file']} | {' × '.join(str(v) for v in m['size_mm'])} | {m['signed_volume_mm3']} | {m['connected_surface_shells']} |\n"
    text+='\nEight coupon files also pass the same checks: button and camera each have three\nintentional pieces, cover has two, and the other five each have one. Production\nmeshes, fit meshes, final PNGs, source hashes and clearance logs are packaged.\n\n'
    text+='''## What was fixed and what remains unchanged

The six original head-related STLs are byte-identical. Fresh exports were compared
by exact oriented facets (allowing only triangle-list ordering), then original
bytes retained. SHA256 values are in `final_dimensions_and_stability.json`.
No change to the TFT offset, bosses, neck shape, head outline, rear cover or
camera parts was needed. The head translates up 1 mm as an assembled unit.

All eight final CAD PNGs are 1600 x 1200. The front-left, front-right and side
views were visually inspected: the 66 mm rounded tapered base remains below
the dominant head, the old side speaker grille is absent and the bottom grille
is hidden in normal desk views. The full black front is a paint finish on the
original shell face plus the original bezel. Cutaway details use actual mesh
crops, with simplified hardware references and transparent service/air reserves.

The repository's old microphone holder was rectangular and its button hole
was sized for a panel switch. New removable adapters accommodate a round board
and tactile switch. The first check found a 54.79 mm³ camera-carrier/round-mic
overlap; moving the same carrier 5 mm lower on its existing slotted rails fixed
it. Remove the round mic adapter before camera-insert withdrawal. The divider
was lowered to Z=54 for the full 8 mm neck bend region, leaving a 9 mm upper gap.
Base screw-boss ramps were clipped to the tapered exterior to eliminate visible
bumps. Final checks use these corrected meshes.

Nineteen named CGAL checks cover real exported parts and conservative component,
plug, driver, acoustic and cable volumes. Zero-volume seating contacts use a
0.02 mm numerical displacement. Motion checks sample nine tray, five carrier,
ten TFT and eight camera poses. A further tray check uses continuous conservative
vertical sweeps for hardware/upper structure and four low-lip positions. TFT and
camera removal remain discrete checks, without flexible-cable simulation. Remove
the optional camera carrier before TFT withdrawal. Slide the tray vertically while supported.

## Stability sensitivity and physical uncertainties

'''
    text+=facts['stability']['assumptions']+'\n\n'
    text+=f'The {len(cogs)} combinations give COM bounds {[[round(v,3) for v in p] for p in cog_bounds]} mm,\nminimum margin **{margin:.3f} mm** inside the conservative foot-centre rectangle\n(136 × 84 mm), and minimum ideal static tilt angle **{tip_angle:.2f}°**.\nThe 10° head tilt is included in every transformed mesh centroid. These are\nassumed mass sensitivity results; real mass, rubber compression/friction, cable\npull and dynamic knock tests were not measured. No unconditional stability\ncertification is claimed.\n\n'
    text+='''Before the full print order, measure the actual speaker frame, depth, terminals
and cone excursion; print its 79 mm gauge / actual-carrier coupon with the
2.5 mm gasket. Static envelope-to-grille clearance is 10.5 mm, and nominal desk
gap is 8.8 mm. Confirm those after foam/foot compression. Measure microphone
diameter, pickup side and connector heights; switch body/height/travel; camera
lens/FOV/ribbon; plug mouldings; screw lengths and printer shrinkage. Test
temperature under sustained load, fan breathing, speech, feedback and rattling.
The actual maximum-size cooler and switch remain unspecified hardware choices.

## Source control

Work is restricted to `hardware/enclosure/`. `hardware/enclosure.zip` was already
untracked outside the permitted scope and was not touched. The enclosure folder
was also untracked at the start, so `git diff` does not show these new files.
See `output/git_final.txt` and `output/changed_files.json` for final status and
the baseline-to-final file inventory. No commit. No push.
'''
    (ROOT/'VALIDATION.md').write_text(text,encoding='utf-8')
    print(json.dumps({k:v for k,v in facts.items() if k not in {'printed_part_assembled_properties','frozen_head_sha256'}},indent=2))

if __name__=='__main__':main()
