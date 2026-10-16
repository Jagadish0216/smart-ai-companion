"""Reproducible Option A/B CAD-volume, mass-sensitivity, hash and fit reporting.

Run after build.py, check_clearances.py and measure_ventilation.py.
No mesh volume is presented as a sliced filament estimate.
"""
from pathlib import Path
import hashlib,itertools,json,math,struct,zipfile,re
from build import ROOT,OUT,PARTS,FROZEN_PARTS,mesh_report

def parameter(name):
    s=(ROOT/'dimensions.scad').read_text()
    return float(re.search(r'^'+name+r'\s*=\s*([\d.]+);',s,re.M)[1])

def properties(path,transform):
    data=path.read_bytes();volume=0;moment=[0,0,0]
    lo=[math.inf]*3;hi=[-math.inf]*3
    for off in range(84,len(data),50):
        t=struct.unpack_from('<12fH',data,off)
        a,b,c=[transform(t[i:i+3]) for i in (3,6,9)]
        v=(a[0]*(b[1]*c[2]-b[2]*c[1])+a[1]*(b[2]*c[0]-b[0]*c[2])+a[2]*(b[0]*c[1]-b[1]*c[0]))/6
        volume+=v
        for k in range(3):
            moment[k]+=v*(a[k]+b[k]+c[k])/4
            lo[k]=min(lo[k],a[k],b[k],c[k]);hi[k]=max(hi[k],a[k],b[k],c[k])
    return {'volume_mm3':volume,'centroid':[v/volume for v in moment],'bounds':[lo,hi]}

def stability(output,height,speaker_z):
    def head(p):
        x,y,z=p;a=math.radians(-100)
        return [x,-32+y*math.cos(a)-z*math.sin(a),135+height-65+y*math.sin(a)+z*math.cos(a)]
    transforms={
        'head_shell':head,
        'head_rear_cover':lambda p:head([p[0],-p[1],68-p[2]]),
        'front_bezel':lambda p:head([p[0],p[1],p[2]+0.2]),
        'camera_lens_insert':lambda p:head([p[0],p[1]-36.5,p[2]+0.6]),
        'camera_carrier':lambda p:head([p[0],p[1]-25.5,p[2]+32]),
        'base_shell':lambda p:[p[0],-p[1],height-p[2]],
        'base_bottom_cover':lambda p:p,
        'universal_speaker_mount':lambda p:[p[0]-38,p[1],p[2]+speaker_z],
        'round_microphone_holder':lambda p:head([p[0],-41.5+p[2],44-p[1]]),
        'button_actuator':lambda p:head([70-p[2],27+p[1],41+p[0]]),
        'tactile_button_holder':lambda p:head([52+p[2],27+p[1],41-p[0]]),
    }
    parts={n:properties(output/(n+'.stl'),t) for n,t in transforms.items()}
    groups=[]
    for names in [set(parts)-{'base_shell','base_bottom_cover','universal_speaker_mount'}, {'base_shell','base_bottom_cover','universal_speaker_mount'}]:
        v=sum(parts[n]['volume_mm3'] for n in names)
        cog=[sum(parts[n]['volume_mm3']*parts[n]['centroid'][k] for n in names)/v for k in range(3)]
        groups.append((v*0.00125*0.55,v*0.00125,cog))
    groups += [(60,110,head([0,12.5,10])),(8,16,head([-24,12,54])),
               (2,6,head([0,-38.7,44])),(4,10,head([0,-36.5,20])),
               (1,5,head([57,27,41])),(40,70,[52,10.5,15]),
               (10,60,[50,4,25.6]),(2,10,[-38,-47,17])]
    # Preserve the original sensitivity range relative to the speaker envelope.
    shift=0 if height==66 else parameter('speaker_reserved_z')-13.5
    cogs=[]
    for weights in itertools.product(*[(g[0],g[1]) for g in groups]):
        for mass,x,y,z in itertools.product([80,500],[-48,-28],[-10,10],[26+shift,50+shift]):
            cogs.append([(sum(w*g[2][k] for w,g in zip(weights,groups))+mass*[x,y,z][k])/(sum(weights)+mass) for k in range(3)])
    margin=min(min(68-abs(p[0]),42-abs(p[1])) for p in cogs)
    angle=min(math.degrees(math.atan(min(68-abs(p[0]),42-abs(p[1]))/(p[2]+8.8))) for p in cogs)
    return {'minimum_foot_centre_margin_mm':margin,'minimum_static_tilt_angle_deg':angle,
            'cases':len(cogs),'COM_bounds_mm':[[min(p[k] for p in cogs) for k in range(3)],[max(p[k] for p in cogs) for k in range(3)]],
            'assembled_bounds_mm':[[min(p['bounds'][0][k] for p in parts.values()) for k in range(3)],[max(p['bounds'][1][k] for p in parts.values()) for k in range(3)]],
            'status':'PASS (CAD mass sensitivity); not a physical tipping test',
            'assumptions':'Original 16384-case model: printed mass 55–100% of solid volume at 1.25 g/cm3, NOT a slicing estimate; TFT 60–110g, ESP32 8–16g, mic 2–6g, camera 4–10g, switch 1–5g, Pi 40–70g, cooler 10–60g, amp 2–10g; speaker 80–500g, x=-48..-28,y=-10..10,z=26..50 shifted with the speaker. Feet omitted from mass; 8.8 mm ground gap included. Camera lens/carrier installed; spare blank excluded from assembly.'}

def main():
    checks=json.loads((OUT/'clearance_validation.json').read_text())
    assert len(checks)==19 and all(v=='clear' for v in checks.values()),checks
    height=parameter('base_h'); panel=parameter('base_panel_t')
    old_out=ROOT.parent/'output'
    meshes=[mesh_report(OUT/(n+'.stl')) for n in PARTS]
    old=[mesh_report(old_out/(n+'.stl')) for n in PARTS]
    frozen={}
    with zipfile.ZipFile(old_out/'archive/Smart_Companion_66mm_Original_Backup.zip') as z:
        for n in FROZEN_PARTS:
            a=hashlib.sha256(z.read('output/'+n+'.stl')).hexdigest()
            b=hashlib.sha256((OUT/(n+'.stl')).read_bytes()).hexdigest()
            assert a==b,n
            frozen[n]={'original':a,'optimized':b,'result':'BYTE IDENTICAL'}
    vents=json.loads((OUT/'ventilation_current.json').read_text())
    intake=vents['intake']['through_area_mm2'];exhaust=vents['exhaust']['through_area_mm2']
    air=height-3-10-4.6-22
    gap=parameter('speaker_reserved_z')-panel
    assert air>=20 and gap>=7 and intake>=700 and exhaust>=600
    new_stability=stability(OUT,height,parameter('speaker_mount_z'))
    old_stability=stability(old_out,66,8)
    assert new_stability['minimum_foot_centre_margin_mm']>0
    volumes=[]
    for a,b in zip(old,meshes):
        v=a['signed_volume_mm3']/1000;w=b['signed_volume_mm3']/1000
        volumes.append({'part':a['file'],'original_cm3':v,'optimized_cm3':w,'saved_cm3':v-w,'reduction_pct':100*(v-w)/v})
    v=sum(r['original_cm3'] for r in volumes);w=sum(r['optimized_cm3'] for r in volumes)
    facts={'base_mm':[174,114,height],'head_mm':[138,112,68],'head_tilt_deg':10,
           'speaker_envelope_mm':[78,78,40],'speaker_to_grille_mm':gap,'desk_gap_mm':8.8,
           'cooler_reservation_mm':[70,50,22],'cooler_minimum_free_air_mm':air,
           'intake_mm2':intake,'exhaust_mm2':exhaust,'stability':new_stability,'original_stability':old_stability,
           'original_CAD_cm3':v,'optimized_CAD_cm3':w,'CAD_reduction_pct':100*(v-w)/v,'parts':volumes,
           'frozen_head_sha256':frozen,'slicer':'No authoritative slicer estimate was available.'}
    (OUT/'cost_and_validation.json').write_text(json.dumps(facts,indent=2)+'\n',encoding='utf-8')
    (OUT/'mesh_validation.json').write_text(json.dumps(meshes,indent=2)+'\n')
    rows=json.loads((old_out/'requirements_validation.json').read_text())
    replacements={9:'Full 78 × 78 × 40 envelope: X=-77..1, Y=-39..39, Z=10.8..50.8; unchanged speaker, lowered 2.7 mm.',
        14:f'{gap:.2f} mm above 1.8 mm grille; ≥7 mm requirement.',
        15:'Five upward withdrawal positions; tray removed first; all carrier screws accessible after speaker removal.',
        19:f'{air:.2f} mm minimum above cooler to the local 3 mm roof reinforcement; broad 1.8 mm roof gives 23.6 mm.',
        20:f'{intake:.3f} mm² actual through-area; independent of speaker grille; no rib crosses intake.',
        21:f'{exhaust:.3f} mm² actual through-area; no major cooling passage blocked.',
        30:f"{new_stability['minimum_foot_centre_margin_mm']:.3f} mm margin / {new_stability['minimum_static_tilt_angle_deg']:.3f}° ideal tip angle, 16384 mass cases."}
    for row in rows:
        if row['number'] in replacements:row['scope']=replacements[row['number']]
    rows += [dict(number=31,check='Thin walls structurally reasonable',result='PASS (design review)',evidence='Dimensions + mesh + geometry review',scope='1.8 mm skins; 3 mm neck land; sparse ribs; 3 mm foot/pilot lands; retained 7 mm base screw engagement. No FEA or printed load test.'),
             dict(number=32,check='Speaker mount has no obvious flex/rattle issue',result='PASS (design review)',evidence='Connected frame + four supports + gasket',scope='86 mm outer frame, 2.2 mm webs/ring, 2.8 mm screw pads, four braced posts and flush countersinks. Continuous 2.5 mm gasket on available lands and ties; physical flex/audio test still required.')]
    (OUT/'requirements_validation.json').write_text(json.dumps(rows,indent=2)+'\n',encoding='utf-8')
    validation=f'''# Cost-optimized validation — 2026-10-16

62 mm was tested first. All 19 executed OpenSCAD CGAL collision/service checks pass.
All 12 freshly exported STLs pass nonzero-file, positive-volume, watertight,
consistent-winding and exactly-one-connected-surface-shell checks.
Nine head-related STLs were freshly exported, compared by exact oriented facets,
then original bytes retained. Their SHA-256 hashes match Option A.

Base: **174 × 114 × {height:g} mm**, top **160 × 102 mm**. Head: **138 × 112 × 68 mm**, 10°.
The entire frozen head/neck moves down 4 mm with the roof; its geometry does not change.
Speaker: unchanged **78 × 78 × 40 mm**, horizontal, cone down, lower-left.
Static grille gap **{gap:.3f} mm**; nominal desk gap **8.8 mm**.
Cooler: **70 × 50 × 22 mm**, minimum upper free air **{air:.3f} mm**.
Intake **{intake:.3f} mm²** / exhaust **{exhaust:.3f} mm²** (speaker grille excluded).

## Requirement checks

PASS (CAD) refers to the modeled envelopes, not measured hardware. The two
structural rows are a dimensional/design review, not structural certification.

| # | Requirement | Result | Executed evidence / scope |
|---:|---|---|---|
'''
    for r in rows:validation+=f"| {r['number']} | {r['check']} | {r['result']} | {r['evidence']}: {r['scope']} |\n"
    validation+='\n## Mesh results\n\n| STL | CAD cm³ | Closed / winding / shells |\n|---|---:|---|\n'
    for m in meshes:validation+=f"| {m['file']} | {m['signed_volume_mm3']/1000:.3f} | PASS / PASS / 1 |\n"
    validation+=f'''
## Stability and limitations

Original: **{old_stability['minimum_foot_centre_margin_mm']:.3f} mm / {old_stability['minimum_static_tilt_angle_deg']:.3f}°**.
Optimized: **{new_stability['minimum_foot_centre_margin_mm']:.3f} mm / {new_stability['minimum_static_tilt_angle_deg']:.3f}°**.
{new_stability['assumptions']}

This is a CAD/model result, not a physical tipping test. Removing low base mass
can offset the benefit of lowering the head; the calculated result governs.
Rubber compression, friction, wire pull, dynamic knocks and actual masses remain untested.

Collision checks use exported production meshes and the accepted conservative
hardware envelopes. Intended seating contacts use 0.02 mm numerical displacement.
Tray checks include continuous conservative sweeps; carrier, camera and TFT
motion use sampled positions. Flexible cables are not simulated. Keep the full
28 × 20 neck throat and 8 mm bend zone clear; only 0.2 mm separates the top of the
speaker envelope from that reserved bend zone. Actual bundles must stay inside it.

Measure real speaker terminals, frame, cone excursion and gasket compression;
test carrier rigidity and audio rattle. Confirm microphone diameter, switch travel,
camera FOV/ribbon, actual plug mouldings, fasteners, printer shrinkage and hole fit.
Use a Pi-5-compatible cooler; its reservation does not guarantee mating to every
connector or fan design. Perform sustained-load thermal tests, especially for PLA/PLA+.
No printed assembly, FEA, thermal simulation or physical load test was performed.

No authoritative slicer estimate was available. CAD volume is not sliced filament
mass; the 220–260 g ideal and ≤300 g target remain unverified. The shop must quote
all parts and supports before printing. Both camera inserts are supplied/quoted;
only one is installed. Head supports remain necessary as documented in PRINTING.md.

All evidence is in the full project ZIP. Original 66 mm source and production
files remain untouched. No commit. No push.
'''
    (ROOT/'VALIDATION.md').write_text(validation,encoding='utf-8')
    report='''# Cost optimization — Option A versus Option B

Option A is preserved in `output/archive/Smart_Companion_66mm_Original_Backup.zip`.
Option B is isolated under `optimized/`. The first tested height, 62 mm, passes;
63/64 mm fallback candidates were therefore unnecessary. Head, neck, components,
base width/depth and top footprint are preserved. Only three STL geometries change.

## Measured solid CAD volumes

Totals include all twelve supplied STLs, including both alternative camera inserts.
These are closed-mesh volumes, NOT infill/support/toolpath estimates.

| Part | Original cm³ | Optimized cm³ | Saved cm³ | Saved % |
|---|---:|---:|---:|---:|
'''
    for r in volumes:report+=f"| {r['part']} | {r['original_cm3']:.3f} | {r['optimized_cm3']:.3f} | {r['saved_cm3']:.3f} | {r['reduction_pct']:.2f}% |\n"
    report+=f'| **Total** | **{v:.3f}** | **{w:.3f}** | **{v-w:.3f}** | **{100*(v-w)/v:.2f}%** |\n'
    report+='''
## Engineering changes

- Base shell: side skin 2.6 → 1.8 mm; broad roof 3 → 1.8 mm; retain 3 mm local
  neck load land and two 1.6 mm ribs to perimeter. Local 2.6 mm port-edge bands.
  Accepted tapered footprint and corner radius retained; height 66 → 62 mm.
- Bottom: panel 3 → 1.8 mm; 3 mm local perimeter, feet and screw lands. Sparse
  1.6 × 3 mm ribs around speaker and Pi mounts; no ribs over acoustic/vent openings.
  Pi position/orientation and 7 mm standoffs unchanged; no solid Pi platform.
- Speaker: unchanged 78 × 78 × 40 envelope moved down 2.7 mm. Carrier 86 × 86,
  3 → 2.2 mm nominal, local 2.8 mm pads; outer frame + Ø54 acoustic ring + four
  diagonal webs replace the broad plate. Existing slots and front amplifier relief
  retained. Four posts have 4.5 mm pilot depth above 1.6 mm closed local floors.
- Divider: 3 → 1.6 mm, top Z54 → Z50; two thin triangular gussets. The divider
  stays between speaker/Pi, with the existing cable notch and top service gap.
- Amplifier: solid 32 × 28 × 3 panel becomes 2.2 mm rails with a 17 × 20 mm
  central opening; zip-tie slots retained and no broad supporting shelf.
- Base bosses: diameter 9 → 8 mm, straight height 9 → 8 mm; original 7 mm screw
  pilot engagement and 12 mm wall ramps retained. No blind decorative mass.
- Supports: roof-down base uses existing wall ramps/open port notches. New ribs,
  feet lands and carrier frame print from the bed. No new support-heavy platforms.
  Frozen head/camera supports remain; actual support savings cannot be quantified
  without a slicer. LED/foot-pocket bridges require shop review.

## Slicer and price

No authoritative slicer estimate was available. PATH, Windows installed-program
registry, Program Files, Program Files (x86), LocalAppData/Programs and Downloads
were checked for PrusaSlicer, OrcaSlicer, CuraEngine, Bambu Studio and Slic3r.
No software was installed. Filament grams, support grams, print hours, price and
the ≤300 g target are unverified. Do not multiply CAD volume by density and call
it a sliced estimate. Quote both options with identical settings: PLA/PLA+, 0.4 mm,
0.20 mm layers, 3 walls, 10% gyroid/grid, 4 top/bottom layers, supports only as needed.

## Final comparison

| Attribute | Original | Optimized |
|---|---|---|
| Base width | 174 mm | 174 mm |
| Base depth | 114 mm | 114 mm |
| Base height | 66 mm | 62 mm |
| Head dimensions | 138 × 112 × 68 mm | Identical; nine STL hashes match |
| Speaker | S.R.C. 4 Ω, 78 × 78 × 40 mm | Same; cone down |
'''
    for label,a,b in [('CAD volume',f'{v:.3f} cm³',f'{w:.3f} cm³'),('Pi cooler free air','26.4 mm',f'{air:.3f} mm'),('Speaker grille gap','10.5 mm',f'{gap:.3f} mm'),('Desk gap','8.8 mm','8.8 mm'),('Intake','854.826 mm²',f'{intake:.3f} mm²'),('Exhaust','688.915 mm²',f'{exhaust:.3f} mm²'),('Stability margin',f"{old_stability['minimum_foot_centre_margin_mm']:.3f} mm",f"{new_stability['minimum_foot_centre_margin_mm']:.3f} mm"),('Ideal tipping angle',f"{old_stability['minimum_static_tilt_angle_deg']:.3f}°",f"{new_stability['minimum_static_tilt_angle_deg']:.3f}°"),('Validation','Accepted CAD baseline','19 collision checks + 12 meshes PASS; structural review PASS; physical tests pending')]:
        report+=f'| {label} | {a} | {b} |\n'
    (ROOT/'COST_OPTIMIZATION.md').write_text(report,encoding='utf-8')
    (ROOT.parent/'COST_OPTIMIZATION.md').write_text(report,encoding='utf-8')
    print(json.dumps({k:v for k,v in facts.items() if k not in {'parts','frozen_head_sha256'}},indent=2),flush=True)

if __name__=='__main__':main()
